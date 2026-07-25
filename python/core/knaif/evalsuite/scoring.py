"""Dispatch to per-skill verifiers; aggregate intent + outcome scores into a scoreboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knaif.evaluator import compute_metrics

from .corpus import CorpusRow
from .protocols import Verifier, VerifyResult
from .runner import AgentOutput

# Re-exported for backward compatibility
__all__ = ["VerifyResult", "score_corpus", "score_corpus_output_diff"]

# Verifiers that execute the plan and materialize artifacts. Only these may grade a
# row's `outputs` via grade_outputs (which needs produced files). Under a non-executing
# verifier (cheap), an `outputs` row falls back to the plan-level verifier instead of
# scoring 0.0 against artifacts that were never produced.
_EXECUTING_VERIFIERS = frozenset({"success", "honest", "output_diff"})


def _latency_aggregate(
    rows: list[dict[str, Any]], *, plan_only: bool = True
) -> dict[str, Any] | None:
    """Aggregate time-to-artifact (wall-clock ms from utterance → ready command string).

    Excludes rows marked ``is_warmup=True`` so the first inference of a run
    (which pays model-load + cold KV-cache costs) does not skew the mean.

    By default restricts to ``outcome == "plan"`` rows — clarify/reject/error
    rows have different cost profiles and conflate "the model was slow" with
    "the model refused / failed."  Pass ``plan_only=False`` to include all rows.
    """
    series: list[float] = []
    for r in rows:
        if r.get("is_warmup"):
            continue
        if plan_only and r.get("actual_outcome") != "plan":
            continue
        ms = r.get("latency_ms")
        if ms is None:
            continue
        series.append(float(ms))
    if not series:
        return None
    s = sorted(series)
    n = len(s)

    def _q(p: float) -> float:
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return s[idx]

    return {
        "count": n,
        "mean_ms": sum(s) / n,
        "p50_ms": _q(0.50),
        "p95_ms": _q(0.95),
        "max_ms": s[-1],
        "total_s": sum(s) / 1000.0,
    }


def _mark_warmup(rows: list[dict[str, Any]]) -> None:
    """Mark the first row as warmup so it's excluded from latency aggregates.

    Only applied when there are >=2 rows — with a single row, dropping it would
    leave no data.  Mutates rows in place.
    """
    if len(rows) >= 2:
        rows[0]["is_warmup"] = True


def _outcome_correct(output: AgentOutput, row: CorpusRow) -> bool:
    return output.outcome == row.expected_outcome


def _intent_result(output: AgentOutput, row: CorpusRow) -> dict[str, Any]:
    """Build an evaluator-compatible result row for compute_metrics."""
    steps = (output.plan or {}).get("plan") or []
    pred_tool = steps[0]["tool"] if steps else None
    pred_args = steps[0].get("args", {}) if steps else {}

    exp_tool = row.expected_tool
    # clarify/reject rows store expected_tool=null; infer from the outcome type
    # so that tool_correct and _prf() counts are meaningful.
    if exp_tool is None and row.expected_outcome in ("clarify", "reject"):
        exp_tool = row.expected_outcome
    tool_correct = pred_tool == exp_tool if exp_tool is not None else False

    return {
        "utterance": output.utterance,
        "category": (row.tags[0] if row.tags else "uncategorized"),
        "expected_tool": exp_tool,
        "expected_args": {},
        "predicted_tool": pred_tool,
        "predicted_args": pred_args,
        "tool_correct": tool_correct,
        "args_correct": None,
        "schema_valid": output.outcome != "error",
    }


def score_corpus(
    outputs: list[AgentOutput],
    corpus: list[CorpusRow],
    verifiers: dict[str, Verifier],
    verifier_name: str,
    sandbox_dir: Path,
) -> dict[str, Any]:
    """
    Score agent outputs against the corpus.

    Returns a scoreboard dict with:
    - intent_metrics: from compute_metrics (tool/arg accuracy)
    - rows: per-row outcome + verify scores
    - summary: overall outcome_accuracy, avg_knaif_score, avg_baseline_score
    - by_tag: per-tag breakdown
    """
    verifier = verifiers.get(verifier_name)
    rows_by_id = {r.id: r for r in corpus}

    intent_rows: list[dict[str, Any]] = []
    scored_rows: list[dict[str, Any]] = []

    for output in outputs:
        row = rows_by_id.get(output.id)
        if row is None:
            continue

        intent_rows.append(_intent_result(output, row))
        outcome_correct = _outcome_correct(output, row)

        # Run knaif verifier (only for plan outcomes — clarify/reject have no artifact).
        # Multi-output rows route to grade_outputs (one criteria set per deliverable);
        # single-output rows pass success_criteria to the chosen verifier.
        knaif_result: VerifyResult | None = None
        row_outputs = getattr(row, "outputs", None)
        grade_outputs = verifiers.get("grade_outputs")
        if output.outcome == "plan":
            if row_outputs and grade_outputs and verifier_name in _EXECUTING_VERIFIERS:
                try:
                    knaif_result = grade_outputs(output.artifact_paths, row_outputs, sandbox_dir)  # type: ignore[call-arg, arg-type]
                except Exception as exc:  # noqa: BLE001
                    knaif_result = VerifyResult(score=0.0, failed=[str(exc)])
            elif verifier:
                criteria = getattr(row, "success_criteria", {}) or {}
                if getattr(row, "grade", "full") == "routing":
                    criteria = {**criteria, "grade": "routing"}
                try:
                    knaif_result = verifier(output, criteria, sandbox_dir)  # type: ignore[call-arg, arg-type]
                except Exception as exc:  # noqa: BLE001
                    knaif_result = VerifyResult(score=0.0, failed=[str(exc)])

        scored_rows.append(
            {
                "id": output.id,
                "utterance": output.utterance,
                "expected_outcome": row.expected_outcome,
                "actual_outcome": output.outcome,
                "outcome_correct": outcome_correct,
                "tags": row.tags,
                "latency_ms": output.latency_ms,
                "error": output.error,
                "artifact": output.artifact,
                "plan": output.plan,
                "knaif_score": knaif_result.score if knaif_result else None,
                "knaif_matched": knaif_result.matched if knaif_result else [],
                "knaif_failed": knaif_result.failed if knaif_result else [],
                "verifier_kind": knaif_result.verifier_kind if knaif_result else None,
                "baseline_score": None,  # populated by output_diff runner in T10
            }
        )

    _mark_warmup(scored_rows)

    n = len(scored_rows)
    outcome_acc = sum(1 for r in scored_rows if r["outcome_correct"]) / n if n else 0.0

    scored_with_verify = [r for r in scored_rows if r["knaif_score"] is not None]
    avg_knaif: float | None = (
        sum(r["knaif_score"] for r in scored_with_verify) / len(scored_with_verify)
        if scored_with_verify
        else None
    )

    avg_baseline: float | None = None  # populated by output_diff runner in T10

    by_tag: dict[str, dict[str, Any]] = {}
    for r in scored_rows:
        for tag in r["tags"] or ["untagged"]:
            by_tag.setdefault(
                tag,
                {
                    "total": 0,
                    "outcome_correct": 0,
                    "knaif_scores": [],
                    "baseline_scores": [],
                    "rows": [],
                },
            )
            by_tag[tag]["total"] += 1
            if r["outcome_correct"]:
                by_tag[tag]["outcome_correct"] += 1
            if r["knaif_score"] is not None:
                by_tag[tag]["knaif_scores"].append(r["knaif_score"])
            if r["baseline_score"] is not None:
                by_tag[tag]["baseline_scores"].append(r["baseline_score"])
            by_tag[tag]["rows"].append(r)

    tag_summary: dict[str, Any] = {}
    for tag, data in by_tag.items():
        ks = data["knaif_scores"]
        bs = data["baseline_scores"]
        tag_summary[tag] = {
            "total": data["total"],
            "outcome_accuracy": data["outcome_correct"] / data["total"],
            "avg_knaif_score": sum(ks) / len(ks) if ks else None,
            "avg_baseline_score": sum(bs) / len(bs) if bs else None,
            "time_to_artifact_ms": _latency_aggregate(data["rows"]),
        }

    return {
        "verifier": verifier_name,
        "total": n,
        "outcome_accuracy": outcome_acc,
        "avg_knaif_score": avg_knaif,
        "avg_baseline_score": avg_baseline,
        "time_to_artifact_ms": _latency_aggregate(scored_rows),
        "intent_metrics": compute_metrics(intent_rows) if intent_rows else {},
        "by_tag": tag_summary,
        "rows": scored_rows,
    }


def score_corpus_output_diff(
    outputs: list[AgentOutput],
    corpus: list[CorpusRow],
    output_diff_fn: Any,
    sandbox_dir: Path,
    baseline_paths: dict[str, Path],
) -> dict[str, Any]:
    """Score outputs using the output_diff verifier against pre-generated baseline paths."""
    rows_by_id = {r.id: r for r in corpus}
    intent_rows: list[dict[str, Any]] = []
    scored_rows: list[dict[str, Any]] = []

    for output in outputs:
        row = rows_by_id.get(output.id)
        if row is None:
            continue

        intent_rows.append(_intent_result(output, row))
        outcome_correct = _outcome_correct(output, row)

        knaif_result: VerifyResult | None = None
        if output_diff_fn and output.artifact_path and output.id in baseline_paths:
            try:
                knaif_result = output_diff_fn(
                    output.artifact_path,
                    baseline_paths[output.id],
                    row.tolerances,
                    sandbox_dir,
                )
            except Exception as exc:  # noqa: BLE001
                knaif_result = VerifyResult(score=0.0, failed=[str(exc)])

        scored_rows.append(
            {
                "id": output.id,
                "utterance": output.utterance,
                "utterance_idx": output.utterance_idx,
                "expected_outcome": row.expected_outcome,
                "actual_outcome": output.outcome,
                "outcome_correct": outcome_correct,
                "tags": row.tags,
                "latency_ms": output.latency_ms,
                "error": output.error,
                "artifact": output.artifact,
                "artifact_path": str(output.artifact_path) if output.artifact_path else None,
                "plan": output.plan,
                "knaif_score": knaif_result.score if knaif_result else None,
                "knaif_matched": knaif_result.matched if knaif_result else [],
                "knaif_failed": knaif_result.failed if knaif_result else [],
                "verifier_kind": knaif_result.verifier_kind if knaif_result else None,
                "baseline_score": None,
            }
        )

    _mark_warmup(scored_rows)

    n = len(scored_rows)
    outcome_acc = sum(1 for r in scored_rows if r["outcome_correct"]) / n if n else 0.0
    scored_with_verify = [r for r in scored_rows if r["knaif_score"] is not None]
    avg_knaif: float | None = (
        sum(r["knaif_score"] for r in scored_with_verify) / len(scored_with_verify)
        if scored_with_verify
        else None
    )

    by_tag: dict[str, dict[str, Any]] = {}
    for r in scored_rows:
        for tag in r["tags"] or ["untagged"]:
            by_tag.setdefault(
                tag, {"total": 0, "outcome_correct": 0, "knaif_scores": [], "rows": []}
            )
            by_tag[tag]["total"] += 1
            if r["outcome_correct"]:
                by_tag[tag]["outcome_correct"] += 1
            if r["knaif_score"] is not None:
                by_tag[tag]["knaif_scores"].append(r["knaif_score"])
            by_tag[tag]["rows"].append(r)

    tag_summary: dict[str, Any] = {
        tag: {
            "total": d["total"],
            "outcome_accuracy": d["outcome_correct"] / d["total"],
            "avg_knaif_score": (
                sum(d["knaif_scores"]) / len(d["knaif_scores"]) if d["knaif_scores"] else None
            ),
            "time_to_artifact_ms": _latency_aggregate(d["rows"]),
        }
        for tag, d in by_tag.items()
    }

    return {
        "verifier": "output_diff",
        "total": n,
        "outcome_accuracy": outcome_acc,
        "avg_knaif_score": avg_knaif,
        "avg_baseline_score": None,
        "time_to_artifact_ms": _latency_aggregate(scored_rows),
        "intent_metrics": compute_metrics(intent_rows) if intent_rows else {},
        "by_tag": tag_summary,
        "rows": scored_rows,
    }
