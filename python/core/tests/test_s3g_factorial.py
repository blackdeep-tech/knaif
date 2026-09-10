"""The S3g factorial's pairing and statistics (`scripts/s3g_factorial.py`).

The experiment is only as good as its comparison. Two things are easy to get wrong and
silently destroy the result: joining two runs on `id` (a corpus row expands to several
utterances, so most of the corpus is dropped), and comparing two independent percentages
instead of pairing row by row (which throws away the pairing that makes a small, real
difference detectable at this corpus size).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "s3g_factorial", REPO_ROOT / "scripts" / "s3g_factorial.py"
)
assert _spec and _spec.loader
s3g = importlib.util.module_from_spec(_spec)
# Registered before exec: its dataclasses resolve their annotations through sys.modules.
sys.modules["s3g_factorial"] = s3g
_spec.loader.exec_module(s3g)


def _row(rid: str, idx: int = 0, correct: bool = True, score: float | None = 1.0) -> dict:
    return {"id": rid, "utterance_idx": idx, "outcome_correct": correct, "knaif_score": score}


# -- the design ---------------------------------------------------------------


def test_the_factorial_covers_both_factors_on_both_skills() -> None:
    plan = s3g.cells(["ffmpeg", "documents"])
    assert len(plan) == 2 * len(s3g.EXAMPLES_LEVELS) * len(s3g.TOP_K_LEVELS)
    assert sum(1 for c in plan if c.is_baseline) == 2, "one shipped baseline per skill"


def test_the_baseline_cell_is_the_shipped_configuration() -> None:
    from knaif.registry import DEFAULT_TOP_K

    assert s3g.BASELINE_CELL == ("selected", DEFAULT_TOP_K)


# -- pairing ------------------------------------------------------------------


def test_rows_are_keyed_per_utterance_not_per_corpus_row() -> None:
    assert s3g.row_key(_row("a", 0)) != s3g.row_key(_row("a", 1))


def test_pairing_keeps_every_utterance_of_a_multi_utterance_row() -> None:
    a = [_row("r1", 0), _row("r1", 1), _row("r1", 2)]
    b = [_row("r1", 0), _row("r1", 1), _row("r1", 2)]
    assert s3g.paired(a, b)[2] == 3


def test_a_wrong_artifact_is_not_a_success_even_when_routing_was_right() -> None:
    assert not s3g.row_success(_row("a", score=0.5))
    assert s3g.row_success(_row("a", score=1.0))


def test_a_correct_refusal_counts_on_its_outcome_alone() -> None:
    assert s3g.row_success(_row("a", score=None))
    assert not s3g.row_success(_row("a", correct=False, score=None))


def test_only_discordant_rows_count_as_wins() -> None:
    a = [_row("r1", 0), _row("r2", 0, correct=False), _row("r3", 0)]
    b = [_row("r1", 0), _row("r2", 0), _row("r3", 0, correct=False)]
    wins, losses, n = s3g.paired(a, b)
    assert (wins, losses, n) == (1, 1, 3)


def test_unmatched_rows_are_left_out_rather_than_guessed() -> None:
    a = [_row("r1", 0), _row("r9", 0)]
    b = [_row("r1", 0)]
    assert s3g.paired(a, b)[2] == 1


# -- statistics ---------------------------------------------------------------


def test_no_discordant_pairs_is_no_evidence() -> None:
    assert s3g.mcnemar_exact(0, 0) == 1.0


def test_a_symmetric_split_is_not_significant() -> None:
    assert s3g.mcnemar_exact(10, 10) == 1.0


def test_a_lopsided_split_is_significant() -> None:
    assert s3g.mcnemar_exact(16, 1) < 0.001
    assert s3g.mcnemar_exact(8, 1) < 0.05


def test_the_test_is_two_sided() -> None:
    assert s3g.mcnemar_exact(1, 16) == pytest.approx(s3g.mcnemar_exact(16, 1))


def test_a_single_win_proves_nothing() -> None:
    assert s3g.mcnemar_exact(1, 0) == 1.0
