"""Paired comparison of two scoreboards over the same corpus rows.

Two models scored on one corpus differ only where one got a row right and the other did not.
McNemar's test asks whether those flips lean one way more than chance would: at the corpus
sizes here (ffmpeg 861, documents 164 utterances) a gap under ~1.5 points is not evidence on
its own, and the p-value says so per comparison instead of by habit.
"""

from __future__ import annotations

from itertools import combinations
from math import comb
from typing import Any


def mcnemar_p(only_a: int, only_b: int) -> float:
    """Exact two-sided McNemar p-value from the two discordant counts."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(comb(n, i) for i in range(k + 1)) / (1 << n)
    return min(1.0, 2 * tail)


def _correct_by_key(board: dict[str, Any]) -> dict[str, bool]:
    return {
        f"{r['id']}#{r.get('utterance_idx', 0)}": bool(r["outcome_correct"]) for r in board["rows"]
    }


def paired_flips(a: dict[str, Any], b: dict[str, Any]) -> dict[str, int]:
    """Count agreement and flips on the rows both scoreboards scored."""
    ca, cb = _correct_by_key(a), _correct_by_key(b)
    keys = ca.keys() & cb.keys()
    out = {"n": len(keys), "both": 0, "neither": 0, "only_a": 0, "only_b": 0}
    for k in keys:
        if ca[k] and cb[k]:
            out["both"] += 1
        elif ca[k]:
            out["only_a"] += 1
        elif cb[k]:
            out["only_b"] += 1
        else:
            out["neither"] += 1
    return out


def format_paired(boards: dict[str, dict[str, Any]]) -> str:
    """One line per pair of named scoreboards: flips each way and the McNemar p-value."""
    lines = ["Paired outcome flips (McNemar, exact two-sided):"]
    for (na, a), (nb, b) in combinations(boards.items(), 2):
        f = paired_flips(a, b)
        p = mcnemar_p(f["only_a"], f["only_b"])
        lines.append(
            f"  {na} vs {nb}: n={f['n']}  only {na} right={f['only_a']}  "
            f"only {nb} right={f['only_b']}  p={p:.4g}"
        )
    return "\n".join(lines)
