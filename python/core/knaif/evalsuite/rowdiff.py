"""Two scoreboards compared row by row (plan 2026-10-14-faster-release-evidence, G3).

An aggregate says how much moved, not what. Keyed on `(id, utterance_idx)`, this compares the
rows both boards measured and, for each, whether the grade, the plan and the command are the
same. It is how a batch-planned cell is checked against the cell measured live (T0, T3), and
how two corpora are compared across a review: on the kept rows only.
"""

from __future__ import annotations

from typing import Any

Key = tuple[str, int]


def _by_key(board: dict[str, Any], side: str) -> dict[Key, dict[str, Any]]:
    rows: dict[Key, dict[str, Any]] = {}
    for row in board.get("rows") or []:
        key = (str(row["id"]), int(row.get("utterance_idx", 0)))
        if key in rows:
            raise ValueError(f"board {side} has row {key[0]}[{key[1]}] twice")
        rows[key] = row
    return rows


def _grade(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "outcome": row.get("actual_outcome"),
        "correct": row.get("outcome_correct"),
        "score": row.get("knaif_score"),
    }


def row_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """What differs between boards *a* and *b* on the rows both measured, in *a*'s order."""
    ra, rb = _by_key(a, "a"), _by_key(b, "b")
    common = [k for k in ra if k in rb]
    grade_differs = []
    plan_differs: list[list[Any]] = []
    command_differs: list[list[Any]] = []
    for key in common:
        x, y = ra[key], rb[key]
        if _grade(x) != _grade(y):
            grade_differs.append(
                {"id": key[0], "utterance_idx": key[1], "a": _grade(x), "b": _grade(y)}
            )
        if x.get("plan") != y.get("plan"):
            plan_differs.append([key[0], key[1]])
        if x.get("artifact") != y.get("artifact"):
            command_differs.append([key[0], key[1]])
    return {
        "rows_a": len(ra),
        "rows_b": len(rb),
        "common": len(common),
        "only_a": [[k[0], k[1]] for k in ra if k not in rb],
        "only_b": [[k[0], k[1]] for k in rb if k not in ra],
        "same_grade": len(common) - len(grade_differs),
        "same_plan": len(common) - len(plan_differs),
        "same_command": len(common) - len(command_differs),
        "grade_differs": grade_differs,
        "plan_differs": plan_differs,
        "command_differs": command_differs,
    }
