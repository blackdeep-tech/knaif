"""Tests for P3 report hardening: URL-encoding, file-existence, triage sort."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_the_row_detail_prints_no_local_path(tmp_path):
    """The verbose log prints a failing row's command, and a chain intermediate renders in the
    run scratch, under the home folder on Windows: the printed line names the person (seen in run
    logs, 2026-10-21). It is redacted like the saved scoreboard."""
    import io
    import os

    from knaif.evalsuite.report import print_row_detail
    from knaif.scratch import scratch_root

    home = os.path.expanduser("~")
    work = str(scratch_root() / "run-8520-7ace9782" / "clip_1080p.mp4")
    row = {
        "id": "ffmpeg_001",
        "utterance": "make clip.mp4 smaller",
        "actual_outcome": "plan",
        "expected_outcome": "plan",
        "outcome_correct": False,
        "artifact": f"ffmpeg -y -i {work} {os.path.join(home, 'out.mp4')}",
    }
    buf = io.StringIO()
    print_row_detail(row, file=buf)
    text = buf.getvalue()
    assert home not in text, text
    assert "<work>" in text, text


@pytest.mark.parametrize("command", ["run", "safety"])
def test_the_saved_path_is_printed_without_the_machine(command, monkeypatch, capsys):
    """`evalsuite run` and `safety` print where they saved, and a run folder commits that log:
    the line named this checkout's absolute path (seen in an A/B run's logs, 2026-10-27). Saved
    inside the checkout, as a run folder is, from the checkout, as a run is started."""
    import os
    import shutil
    import sys

    from knaif.evalsuite import cli

    repo = Path(__file__).resolve().parents[3]
    save = repo / "sandbox" / f"saved-path-test-{os.getpid()}-{command}"
    target = str(save) if command == "run" else str(save / "safety.json")
    argv = ["evalsuite", command, "--skill", "ffmpeg", "--config", str(repo / "eval_backends.yaml")]
    argv += ["--backends", "mock", "--save", target]
    if command == "run":
        argv += ["--verifier", "cheap", "--limit", "1"]
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "argv", argv)
    try:
        cli.main()
    except SystemExit:
        pass
    finally:
        shutil.rmtree(save, ignore_errors=True)
    out = capsys.readouterr().out
    assert "saved" in out.lower(), out[-400:]
    for form in (str(repo), repo.as_posix()):
        assert form not in out, out[-400:]
