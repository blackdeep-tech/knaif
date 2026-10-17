"""`evalsuite row-diff`: two scoreboards compared row by row (plan 2026-10-14, G3 and T0).

Aggregates hide which rows moved, and two boards over different populations are not one
number. The row diff keys on `(id, utterance_idx)`, so it compares exactly the rows both boards
measured and says what differs on each: the grade, the plan, the command.
"""

from __future__ import annotations

import json

import pytest

from knaif.evalsuite.rowdiff import row_diff


def _row(rid, idx=0, *, correct=True, score=1.0, tool="convert_video", outcome="plan", cmd="ff"):
    return {
        "id": rid,
        "utterance_idx": idx,
        "actual_outcome": outcome,
        "outcome_correct": correct,
        "knaif_score": score,
        "plan": {"plan": [{"tool": tool, "args": {}}]} if tool else None,
        "artifact": cmd,
    }


def test_identical_boards_differ_nowhere() -> None:
    rows = [_row("a"), _row("b"), _row("b", 1)]
    d = row_diff({"rows": rows}, {"rows": json.loads(json.dumps(rows))})
    assert (d["common"], d["same_grade"], d["same_plan"], d["same_command"]) == (3, 3, 3, 3)
    assert d["grade_differs"] == [] and d["plan_differs"] == [] and d["command_differs"] == []


def test_a_grade_change_names_the_row_and_both_sides() -> None:
    d = row_diff(
        {"rows": [_row("a"), _row("b")]}, {"rows": [_row("a"), _row("b", correct=False, score=0.4)]}
    )
    assert d["same_grade"] == 1
    (diff,) = d["grade_differs"]
    assert (diff["id"], diff["utterance_idx"]) == ("b", 0)
    assert diff["a"] == {"outcome": "plan", "correct": True, "score": 1.0}
    assert diff["b"] == {"outcome": "plan", "correct": False, "score": 0.4}


def test_a_plan_change_with_the_same_grade_is_still_reported() -> None:
    d = row_diff({"rows": [_row("a")]}, {"rows": [_row("a", tool="trim_video")]})
    assert d["same_grade"] == 1
    assert d["plan_differs"] == [["a", 0]]


def test_a_command_change_is_reported_separately() -> None:
    d = row_diff({"rows": [_row("a")]}, {"rows": [_row("a", cmd="ff -y")]})
    assert d["command_differs"] == [["a", 0]]
    assert d["plan_differs"] == []


def test_only_rows_both_boards_measured_are_compared() -> None:
    d = row_diff({"rows": [_row("a"), _row("b")]}, {"rows": [_row("b"), _row("c")]})
    assert d["common"] == 1
    assert d["only_a"] == [["a", 0]] and d["only_b"] == [["c", 0]]


def test_a_board_with_a_row_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        row_diff({"rows": [_row("a"), _row("a")]}, {"rows": [_row("a")]})


def test_the_cli_prints_a_summary_and_can_save_the_diff(tmp_path, capsys) -> None:
    import argparse

    from knaif.evalsuite import cli

    a, b, out = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "d.json"
    a.write_text(json.dumps({"rows": [_row("a"), _row("b")]}), encoding="utf-8")
    b.write_text(json.dumps({"rows": [_row("a"), _row("b", correct=False)]}), encoding="utf-8")
    cli.cmd_row_diff(argparse.Namespace(a=str(a), b=str(b), out=str(out)))
    printed = capsys.readouterr().out
    assert "1 / 2 same grade" in printed and "b[0]" in printed
    assert json.loads(out.read_text(encoding="utf-8"))["same_grade"] == 1
