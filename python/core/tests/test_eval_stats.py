"""Tests for the paired-comparison statistics behind `evalsuite compare` and the error split."""

from __future__ import annotations

import pytest

from knaif.evalsuite.stats import format_paired, mcnemar_p, paired_flips


def test_mcnemar_with_no_disagreement_is_one():
    assert mcnemar_p(0, 0) == 1.0


def test_mcnemar_balanced_flips_is_one():
    assert mcnemar_p(5, 5) == 1.0


def test_mcnemar_is_the_exact_two_sided_binomial():
    assert mcnemar_p(10, 0) == pytest.approx(2 * 0.5**10)
    assert mcnemar_p(0, 10) == pytest.approx(2 * 0.5**10)
    # 1 vs 6: 2 * P(X <= 1 | n=7) = 2 * 8/128
    assert mcnemar_p(1, 6) == pytest.approx(16 / 128)


def _board(*correct):
    return {
        "rows": [
            {"id": f"r{i}", "utterance_idx": 0, "outcome_correct": c} for i, c in enumerate(correct)
        ]
    }


def test_paired_flips_counts_rows_only_one_side_got_right():
    a = _board(True, True, False, False)
    b = _board(True, False, True, True)
    assert paired_flips(a, b) == {"n": 4, "both": 1, "neither": 0, "only_a": 1, "only_b": 2}


def test_paired_flips_uses_only_rows_both_boards_have():
    a = _board(True, True)
    b = {"rows": [{"id": "r1", "utterance_idx": 0, "outcome_correct": False}]}
    assert paired_flips(a, b)["n"] == 1


def test_format_paired_lists_each_pair_with_its_p_value():
    out = format_paired({"v1": _board(False, False, True), "v2": _board(True, True, True)})
    assert "v1" in out and "v2" in out
    assert "p=0.5" in out
