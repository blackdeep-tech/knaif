"""Compose an L4 cell from a measured cell and rows measured elsewhere (release plan R5c).

Owner rule, 2026-09-27: reuse what holds reliably, run the rest. Two uses:

* a CPU cell = the measured CUDA cell, with every row whose CPU plan differs from CUDA re-run and
  re-graded on the CPU (the rest execute identically: execution code is backend-independent);
* a Linux CPU cell = the Windows CPU cell, when a sample run on Linux planned every sampled row
  alike; the sample's rows replace their counterparts, and the cell names the Linux binary.

The result is scored by `aggregate_scored_rows` — the aggregation a measured run uses — and marked
`composed` with its sources, so `accept-native` records it as composed and never as a full run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .scoring import aggregate_scored_rows

#: Fields that describe WHERE and ON WHAT a run executed. A composed cell takes them from the
#: replacement run: its backend, OS and binary are what the cell claims.
_FROM_REPLACEMENT = (
    "compute_backend",
    "compute_placement",
    "compute_device_enumerated",
    "os",
    "binary_sha256",
    "lane",
    "lane_entry_point",
    "git_sha",
    "git_dirty",
)
#: Fields that must agree, or the rows being mixed are not about the same thing.
_MUST_MATCH = ("model_sha256", "backend_public_name", "lane_kind", "verifier", "scoring_policy")


#: What identifies a source board: the tree, binary and model it ran, and where and how it was
#: graded. Both sources' fingerprints travel with the composed cell, so neither can be swapped out
#: from under it without the record saying so.
_FINGERPRINT = (
    "git_sha",
    "git_dirty",
    "binary_sha256",
    "model_sha256",
    "scoring_policy",
    "verifier",
    "compute_backend",
    "os",
    "lane",
    "total",
)
#: Control tools whose arguments are prose: two plans that both ask agree, whatever the wording
#: (as `scripts/flip_rate.py` compares them).
_PROSE_TOOLS = frozenset({"clarify", "reject", "done"})

Key = tuple[str, int]


def _key(row: dict[str, Any], side: str) -> Key:
    idx = row.get("utterance_idx")
    if not isinstance(idx, int) or isinstance(idx, bool):
        raise ValueError(
            f"cannot compose: {side} row {row.get('id')!r} has no utterance_idx; reading it as 0 "
            "would replace another utterance's row"
        )
    return (str(row["id"]), idx)


def _keyed(rows: list[dict[str, Any]], side: str) -> dict[Key, dict[str, Any]]:
    out: dict[Key, dict[str, Any]] = {}
    for row in rows:
        key = _key(row, side)
        if key in out:
            raise ValueError(f"cannot compose: duplicate {side} row {list(key)}")
        out[key] = row
    return out


def _source(given: Any, board: dict[str, Any]) -> dict[str, Any]:
    described = dict(given) if isinstance(given, dict) else {"path": given}
    return {**described, **{name: board.get(name) for name in _FINGERPRINT}}


def compose_cell(
    base: dict[str, Any], replacements: dict[str, Any], *, sources: dict[str, Any]
) -> dict[str, Any]:
    """*base* with *replacements*' rows swapped in, re-aggregated, and marked composed.

    *sources* names the two boards (`base`, `replacements`: a path or a dict such as
    `{"path", "file_sha256"}`) and the reuse rule applied (`note`).
    """
    for name in _MUST_MATCH:
        if base.get(name) != replacements.get(name):
            raise ValueError(
                f"cannot compose: {name} differs ({base.get(name)!r} vs "
                f"{replacements.get(name)!r}); the rows would not describe one model and grader"
            )
    if not (base.get("packaged_layout") and replacements.get("packaged_layout")):
        raise ValueError("cannot compose: both runs must come from the packaged layout")
    if not replacements.get("rows"):
        raise ValueError("cannot compose: the replacement board has no rows")

    base_rows = _keyed(base.get("rows") or [], "base")
    new_rows = _keyed(replacements["rows"], "replacement")
    missing = sorted(k for k in new_rows if k not in base_rows)
    if missing:
        raise ValueError(f"cannot compose: replacement rows not in the base: {missing[:5]}")

    rows = [new_rows.get(key, row) for key, row in base_rows.items()]
    board = aggregate_scored_rows(rows, [], str(base.get("verifier")))
    # The rows were graded under the sources' policy (checked equal above), not necessarily the
    # one this code would stamp today.
    board["scoring_policy"] = base.get("scoring_policy")
    # The rows ran on two backends (or two OSes): a latency over both describes neither.
    board["time_to_artifact_ms"] = None
    for tag in board["by_tag"].values():
        tag["time_to_artifact_ms"] = None
    for name in (
        "lane_kind",
        "backend",
        "backend_public_name",
        "model_sha256",
        "model_sha256_prefix",
        "packaged_layout",
    ):
        if name in base:
            board[name] = base[name]
    for name in _FROM_REPLACEMENT:
        if name in replacements:
            board[name] = replacements[name]
    board["composed"] = True
    board["composed_from"] = {
        "note": sources.get("note"),
        "base": _source(sources.get("base"), base),
        "replacements": _source(sources.get("replacements"), replacements),
        "replaced_rows": [list(k) for k in sorted(new_rows)],
    }
    return board


# ── which rows a composed cell must re-run ────────────────────────────────────────────────


def _steps(plan: Any) -> list[dict[str, Any]] | None:
    """A plan's steps, from a board row (`{"plan": [...]}`) or a plans file (`[...]`); None
    when there is no plan at all, which is not the same as an empty one."""
    steps = plan.get("plan") if isinstance(plan, dict) else plan
    return None if steps is None else list(steps)


def _full_plan(steps: list[dict[str, Any]]) -> tuple:
    """The whole plan, every step field included (file arguments, output bindings); prose tools
    compare by tool alone."""
    out: list[tuple[Any, ...]] = []
    for step in steps:
        tool = step.get("tool")
        out.append((tool,) if tool in _PROSE_TOOLS else (tool, json.dumps(step, sort_keys=True)))
    return tuple(out)


@dataclass
class RerunSet:
    """The rows of a base board that reused plans cannot vouch for."""

    flipped: list[Key] = field(default_factory=list)  # planned differently (full plan)
    unplanned: list[Key] = field(default_factory=list)  # no plan on one side to compare

    @property
    def keys(self) -> list[Key]:
        return sorted(self.flipped + self.unplanned)


def rerun_set(base: dict[str, Any], plans: dict[Key, Any]) -> RerunSet:
    """Every base row whose reused plan differs from the base's in full (not only in decision),
    or that has no plan to compare on either side (a corpus row added since the plans were
    taken, a null plan): nothing vouches for such a row, so it is re-run."""
    out = RerunSet()
    for key, row in _keyed(base.get("rows") or [], "base").items():
        mine = _steps(row.get("plan"))
        reused = _steps(plans.get(key))
        if mine is None or reused is None:
            out.unplanned.append(key)
        elif _full_plan(mine) != _full_plan(reused):
            out.flipped.append(key)
    out.flipped.sort()
    out.unplanned.sort()
    return out
