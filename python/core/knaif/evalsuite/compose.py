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


def _key(row: dict[str, Any]) -> tuple[str, int]:
    return (str(row["id"]), int(row.get("utterance_idx") or 0))


def compose_cell(
    base: dict[str, Any], replacements: dict[str, Any], *, sources: dict[str, Any]
) -> dict[str, Any]:
    """*base* with *replacements*' rows swapped in, re-aggregated, and marked composed."""
    for field in _MUST_MATCH:
        if base.get(field) != replacements.get(field):
            raise ValueError(
                f"cannot compose: {field} differs ({base.get(field)!r} vs "
                f"{replacements.get(field)!r}); the rows would not describe one model and grader"
            )
    if not (base.get("packaged_layout") and replacements.get("packaged_layout")):
        raise ValueError("cannot compose: both runs must come from the packaged layout")

    new_rows = {_key(r): r for r in replacements.get("rows") or []}
    base_keys = {_key(r) for r in base.get("rows") or []}
    missing = sorted(k for k in new_rows if k not in base_keys)
    if missing:
        raise ValueError(f"cannot compose: replacement rows not in the base: {missing[:5]}")

    rows = [new_rows.get(_key(r), r) for r in base.get("rows") or []]
    board = aggregate_scored_rows(rows, [], str(base.get("verifier")))
    for field in (
        "lane_kind",
        "backend",
        "backend_public_name",
        "model_sha256",
        "model_sha256_prefix",
        "packaged_layout",
    ):
        if field in base:
            board[field] = base[field]
    for field in _FROM_REPLACEMENT:
        if field in replacements:
            board[field] = replacements[field]
    board["composed"] = True
    board["composed_from"] = {
        **sources,
        "replaced_rows": [list(k) for k in sorted(new_rows)],
        "base_compute_backend": base.get("compute_backend"),
        "base_os": base.get("os"),
    }
    return board
