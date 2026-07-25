"""Tests for P3 report hardening: URL-encoding, file-existence, triage sort."""

from __future__ import annotations

from pathlib import Path

from knaif.evalsuite.report import _path_fwd, _rel_path, _triage_key

# ── URL encoding in path helpers ──────────────────────────────────────────────


def test_path_fwd_encodes_spaces():
    result = _path_fwd("path/with spaces/out file.mp4")
    assert " " not in result
    assert "%20" in result


def test_path_fwd_encodes_hash():
    result = _path_fwd("path/clip#1.mp4")
    assert "#" not in result
    assert "%23" in result


def test_path_fwd_normal_path_unchanged():
    result = _path_fwd("evals/local/out.mp4")
    assert result == "evals/local/out.mp4"


def test_path_fwd_empty_string_returns_empty():
    assert _path_fwd("") == ""


def test_path_fwd_none_returns_empty():
    assert _path_fwd(None) == ""


def test_rel_path_encodes_spaces(tmp_path: Path):
    subdir = tmp_path / "results"
    subdir.mkdir()
    target = tmp_path / "sandbox" / "out file.mp4"
    result = _rel_path(str(target), subdir)
    assert " " not in result
    assert "%20" in result


def test_rel_path_normal_path_unchanged(tmp_path: Path):
    subdir = tmp_path / "results"
    subdir.mkdir()
    target = tmp_path / "sandbox" / "out.mp4"
    result = _rel_path(str(target), subdir)
    assert " " not in result
    assert result.endswith("out.mp4")


# ── triage sort key ───────────────────────────────────────────────────────────


def _make_arm_map(
    score: float | None,
    outcome: str = "plan",
    outcome_correct: bool = True,
) -> dict:
    """Build a minimal cross-arm map for _triage_key testing."""
    from knaif.evalsuite.report import ArmEntry

    entry = ArmEntry(
        id="r001",
        utterance_idx=0,
        utterance="test",
        score=score,
        actual_outcome=outcome,
        outcome_correct=outcome_correct,
        tags=[],
        matched=[],
        failed=[],
        artifact_path=None,
        baseline_path=None,
        latency_ms=100.0,
    )
    return {"arm1": entry}


def test_triage_key_error_outcome_first():
    error_key = _triage_key(_make_arm_map(None, outcome="error", outcome_correct=False))
    ok_key = _triage_key(_make_arm_map(1.0, outcome="plan", outcome_correct=True))
    assert error_key < ok_key


def test_triage_key_wrong_outcome_before_correct():
    wrong_key = _triage_key(_make_arm_map(0.8, outcome="plan", outcome_correct=False))
    right_key = _triage_key(_make_arm_map(0.8, outcome="plan", outcome_correct=True))
    assert wrong_key < right_key


def test_triage_key_low_score_before_high_score():
    low_key = _triage_key(_make_arm_map(0.2))
    high_key = _triage_key(_make_arm_map(0.95))
    assert low_key < high_key


def test_triage_key_none_score_before_high_score():
    none_key = _triage_key(_make_arm_map(None, outcome="plan"))
    high_key = _triage_key(_make_arm_map(0.95))
    assert none_key < high_key


def test_triage_key_perfect_score_is_last():
    perfect_key = _triage_key(_make_arm_map(1.0, outcome="plan", outcome_correct=True))
    low_key = _triage_key(_make_arm_map(0.0, outcome="plan", outcome_correct=True))
    assert low_key < perfect_key
