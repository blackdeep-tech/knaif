"""Retrieval-quality harness — measures whether ``retrieve_tools`` surfaces the
expected tool, independent of any model.

Retrieval is upstream of the planner: if the correct tool is not in the top-k the
model can never route to it, so a retrieval miss is *not* a model failure. This
module scores recall@k and MRR over each skill's ``data/eval.jsonl``, sliced by
script (ascii / latin-multilingual / cjk) so tokenization gaps (CJK, non-English)
are visible. See docs/plans/2026-07-02-retrieval-overhaul.md (Phase 0).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..registry import load_registry, retrieve_tools
from .corpus import load_corpus

# Core-control tools are merged automatically and always available to the model;
# an expected_tool of clarify/reject/done is a routing decision, not a retrieval
# question. Excluded from the retrieval metric.
_CORE = {"clarify", "reject", "done", "wait_for_confirmation"}
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def language_slice(utterance: str) -> str:
    """ascii | latin | cjk — the script bucket a tokenizer must handle."""
    if _CJK.search(utterance):
        return "cjk"
    if all(ord(c) < 128 for c in utterance):
        return "ascii"
    return "latin"


def _skill_paths(skill: str) -> tuple[Path, Path]:
    base = Path("skills") / skill
    return base / "tools.yaml", base / "data" / "eval.jsonl"


def evaluate_skill(skill: str, top_k: int = 5) -> dict[str, Any]:
    """Return recall@k / MRR for one skill, overall and per language slice."""
    tools_yaml, eval_path = _skill_paths(skill)
    registry = load_registry(tools_yaml)
    n_tools = len(registry)

    # buckets: slice -> [hits...], [reciprocal_ranks...]
    hits: dict[str, list[int]] = {}
    rr: dict[str, list[float]] = {}
    misses: list[dict[str, Any]] = []

    for row in load_corpus(eval_path):
        expected = row.expected_tool
        if not expected or expected in _CORE:
            continue
        for utt in row.utterances:
            sl = language_slice(utt)
            # Full ranked order (min_score=0 returns every scored tool, ranked).
            ranked = list(retrieve_tools(utt, registry, top_k=n_tools + 8))
            rank = ranked.index(expected) + 1 if expected in ranked else None
            hit = 1 if (rank is not None and rank <= top_k) else 0
            hits.setdefault(sl, []).append(hit)
            rr.setdefault(sl, []).append(1.0 / rank if rank else 0.0)
            if not hit:
                misses.append({"id": row.id, "tool": expected, "slice": sl, "utterance": utt})

    def agg(bucket_h: list[int], bucket_r: list[float]) -> dict[str, Any]:
        n = len(bucket_h)
        return {
            "n": n,
            "recall_at_k": round(sum(bucket_h) / n, 4) if n else None,
            "mrr": round(sum(bucket_r) / n, 4) if n else None,
        }

    all_h = [h for b in hits.values() for h in b]
    all_r = [r for b in rr.values() for r in b]
    return {
        "skill": skill,
        "top_k": top_k,
        "overall": agg(all_h, all_r),
        "by_slice": {sl: agg(hits.get(sl, []), rr.get(sl, [])) for sl in ("ascii", "latin", "cjk")},
        "misses": misses,
    }


def evaluate(skills: list[str], top_k: int = 5) -> dict[str, Any]:
    return {"top_k": top_k, "skills": {s: evaluate_skill(s, top_k) for s in skills}}


def check_regression(
    current: dict[str, Any], baseline: dict[str, Any], tol: float = 0.02
) -> list[tuple[str, str, float, float]]:
    """Return (skill, slice, baseline_recall, current_recall) tuples that regressed.

    A slice regresses when current recall@k drops more than ``tol`` below the locked
    baseline. Compares overall + each script slice per skill.
    """
    regressions: list[tuple[str, str, float, float]] = []
    for skill, cur in current["skills"].items():
        base = baseline.get("skills", {}).get(skill)
        if not base:
            continue
        buckets = [("overall", cur["overall"], base["overall"])]
        for sl in ("ascii", "latin", "cjk"):
            buckets.append((sl, cur["by_slice"].get(sl, {}), base["by_slice"].get(sl, {})))
        for name, c, b in buckets:
            cr, br = c.get("recall_at_k"), b.get("recall_at_k")
            if cr is not None and br is not None and cr < br - tol:
                regressions.append((skill, name, br, cr))
    return regressions


def format_report(results: dict[str, Any]) -> str:
    k = results["top_k"]
    lines = [f"Retrieval quality (recall@{k} / MRR) — expected tool in top-{k}?", ""]
    header = f"{'skill':12s} {'slice':8s} {'n':>4}  {'recall':>7}  {'mrr':>6}"
    lines.append(header)
    lines.append("-" * len(header))
    for skill, r in results["skills"].items():
        o = r["overall"]
        lines.append(
            f"{skill:12s} {'ALL':8s} {o['n']:>4}  {str(o['recall_at_k']):>7}  {str(o['mrr']):>6}"
        )
        for sl in ("ascii", "latin", "cjk"):
            s = r["by_slice"][sl]
            if s["n"]:
                lines.append(
                    f"{'':12s} {sl:8s} {s['n']:>4}  {str(s['recall_at_k']):>7}  {str(s['mrr']):>6}"
                )
    return "\n".join(lines)
