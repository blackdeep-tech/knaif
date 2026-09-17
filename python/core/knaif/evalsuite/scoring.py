"""Dispatch to per-skill verifiers; aggregate intent + outcome scores into a scoreboard.

**The shared scoring contract.** Both runtimes' records are graded by one definition,
stamped with `scoring_policy` (see `outcomes.POLICY_VERSION`) so a later change to the
rules cannot leave old records looking compliant:

| the runtime...                          | outcome_accuracy    | avg_knaif_score |
|-----------------------------------------|---------------------|-----------------|
| produced a plan, artifact graded        | correct iff `plan`  | the graded score|
| produced a plan, grading raised         | correct iff `plan`  | 0.0             |
| correctly refused (`clarify`/`reject`)  | **correct**         | **excluded**    |
| wrongly refused, or capability unbuilt  | **failure**         | **excluded**    |

The two metrics therefore have **different denominators**, deliberately: outcome accuracy
is over every row, the quality average only over rows that produced something to grade.
Folding refusals into the average as zeros would punish a runtime for refusing correctly,
and folding unattempted rows in would mean a score drop could no longer be read as a
quality regression rather than a coverage one.

Excluding unattempted rows is only honest because **coverage is reported beside the
average**, aggregate and per slice. If that reporting is ever dropped, this decision has
to be reopened — on its own, exclusion flatters a partial port.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (L4d).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knaif.evaluator import compute_metrics

from .corpus import CorpusRow
from .outcomes import POLICY_VERSION, is_capability_gap
from .protocols import Verifier, VerifyResult
from .runner import AgentOutput

# Re-exported for backward compatibility
__all__ = ["VerifyResult", "score_corpus", "score_corpus_output_diff"]

# Verifiers that execute the plan and materialize artifacts. Only these may grade a
# row's `outputs` via grade_outputs (which needs produced files). Under a non-executing
# verifier (cheap), an `outputs` row falls back to the plan-level verifier instead of
# scoring 0.0 against artifacts that were never produced.
_EXECUTING_VERIFIERS = frozenset({"success", "honest", "output_diff"})


def _outcome_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many rows landed in each outcome bucket, for the acceptance record."""
    counts: dict[str, int] = {}
    for r in rows:
        key = r.get("actual_outcome") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _coverage_aggregate(rows: list[dict[str, Any]]) -> tuple[float, int]:
    """Return (coverage, unattempted) over scored rows.

    A deliberate refusal counts as attempted — the runtime did its job. Only a capability
    it does not have reduces coverage.
    """
    if not rows:
        return 0.0, 0
    unattempted = sum(1 for r in rows if is_capability_gap(r.get("actual_outcome") or ""))
    return (len(rows) - unattempted) / len(rows), unattempted


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
        # `parse_error` is a *separate* outcome from `error`, so testing only against
        # "error" let unparseable model output score 1.0 on the metric whose whole job
        # is to say whether the JSON was well-formed. Both are schema failures.
        "schema_valid": output.outcome not in ("error", "parse_error"),
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
                # `id` alone is NOT a per-row key — a corpus row expands to several
                # utterances. Without this, joining two runs on id silently keeps one
                # utterance per row and drops the rest (846 ffmpeg rows -> 313), so
                # regression evidence taken from a cheap/success run was lossy.
                # score_corpus_output_diff always emitted it; this path did not.
                "utterance_idx": output.utterance_idx,
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
        tag_coverage, tag_unattempted = _coverage_aggregate(data["rows"])
        tag_summary[tag] = {
            "total": data["total"],
            "outcome_accuracy": data["outcome_correct"] / data["total"],
            "avg_knaif_score": sum(ks) / len(ks) if ks else None,
            "avg_baseline_score": sum(bs) / len(bs) if bs else None,
            "coverage": tag_coverage,
            "unattempted": tag_unattempted,
            "time_to_artifact_ms": _latency_aggregate(data["rows"]),
        }

    run_coverage, unattempted = _coverage_aggregate(scored_rows)

    return {
        "verifier": verifier_name,
        "scoring_policy": POLICY_VERSION,
        "total": n,
        "outcome_accuracy": outcome_acc,
        "avg_knaif_score": avg_knaif,
        "avg_baseline_score": avg_baseline,
        # Reported beside the average, never folded into it: excluding unattempted rows
        # from a quality score is only honest while the coverage gap is visible.
        "coverage": run_coverage,
        "unattempted": unattempted,
        "by_outcome": _outcome_counts(scored_rows),
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
            "coverage": _coverage_aggregate(d["rows"])[0],
            "unattempted": _coverage_aggregate(d["rows"])[1],
            "time_to_artifact_ms": _latency_aggregate(d["rows"]),
        }
        for tag, d in by_tag.items()
    }

    run_coverage, unattempted = _coverage_aggregate(scored_rows)

    return {
        "verifier": "output_diff",
        "scoring_policy": POLICY_VERSION,
        "total": n,
        "outcome_accuracy": outcome_acc,
        "avg_knaif_score": avg_knaif,
        "avg_baseline_score": None,
        "coverage": run_coverage,
        "unattempted": unattempted,
        "by_outcome": _outcome_counts(scored_rows),
        "time_to_artifact_ms": _latency_aggregate(scored_rows),
        "intent_metrics": compute_metrics(intent_rows) if intent_rows else {},
        "by_tag": tag_summary,
        "rows": scored_rows,
    }
