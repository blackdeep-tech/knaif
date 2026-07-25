"""End-to-end test for `evalsuite run --verifier output_diff`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from knaif.evalsuite.cli import main
from knaif.evalsuite.corpus import CorpusRow, save_corpus
from knaif.evalsuite.protocols import VerifyResult
from knaif.evalsuite.runner import AgentOutput


def _run(args: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["knaif.evalsuite"] + args
    try:
        main()
    finally:
        sys.argv = old_argv


def _fake_agent_output(tmp_path: Path, row_id: str = "r001") -> AgentOutput:
    art = tmp_path / "sandbox" / "mock" / f"{row_id}__0" / "out.mp4"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_bytes(b"artifact")
    return AgentOutput(
        id=row_id,
        utterance="convert to mp4",
        plan={"plan": [{"tool": "convert_video", "args": {}}]},
        artifact="ffmpeg -y -i input.mp4 -c:v libx264 out.mp4",
        outcome="plan",
        latency_ms=10.0,
        artifact_path=art,
        utterance_idx=0,
    )


# ── run --verifier output_diff writes scoreboard JSON ────────────────────────


def test_run_output_diff_writes_scoreboard(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    rows = [
        CorpusRow(
            id="r001",
            utterances=["convert to mp4"],
            expected_outcome="plan",
            fixture="clip.mp4",
            baseline={"command": "ffmpeg -y -i input.mp4 -c:v libx264 baseline.mp4"},
            expected_tool="convert_video",
            tags=["convert"],
        ),
    ]
    save_corpus(rows, corpus_path)

    save_dir = tmp_path / "scores"
    output = _fake_agent_output(tmp_path)

    baseline_path = tmp_path / "sandbox" / "mock" / "baselines" / "r001" / "baseline.mp4"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_bytes(b"baseline")

    fake_result = VerifyResult(
        score=0.9, matched=["container=mp4"], failed=[], verifier_kind="output"
    )

    with (
        patch("knaif.evalsuite.cli._make_agent"),
        patch("knaif.evalsuite.cli.run_corpus", return_value=[output]),
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=baseline_path),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=({"output_diff": MagicMock(return_value=fake_result)}, {}),
        ),
    ):
        _run(
            [
                "run",
                "--skill",
                "ffmpeg",
                "--verifier",
                "output_diff",
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(fixture_dir),
                "--sandbox",
                str(tmp_path / "sandbox"),
                "--save",
                str(save_dir),
                "--config",
                str(tmp_path / "no_backends.yaml"),
            ]
        )

    saved = list(save_dir.glob("*.json"))
    assert len(saved) == 1, f"Expected 1 JSON file, got {[f.name for f in saved]}"
    data = json.loads(saved[0].read_text(encoding="utf-8"))
    assert data["verifier"] == "output_diff"
    assert data["total"] == 1
    assert data["rows"][0]["knaif_score"] == pytest.approx(0.9)


def test_run_output_diff_skips_row_without_baseline(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"fake")

    rows = [
        CorpusRow(
            id="r001",
            utterances=["convert to mp4"],
            expected_outcome="plan",
            fixture="clip.mp4",
            baseline=None,
            expected_tool="convert_video",
            tags=["convert"],
        ),
    ]
    save_corpus(rows, corpus_path)

    save_dir = tmp_path / "scores"
    output = _fake_agent_output(tmp_path)

    mock_diff = MagicMock(return_value=VerifyResult(score=1.0, verifier_kind="output"))

    with (
        patch("knaif.evalsuite.cli._make_agent"),
        patch("knaif.evalsuite.cli.run_corpus", return_value=[output]),
        patch("knaif.evalsuite.cli._execute_against_fixture", return_value=None),
        patch(
            "knaif.evalsuite.cli._load_skill_verifiers",
            return_value=({"output_diff": mock_diff}, {}),
        ),
    ):
        _run(
            [
                "run",
                "--skill",
                "ffmpeg",
                "--verifier",
                "output_diff",
                "--corpus",
                str(corpus_path),
                "--fixture-dir",
                str(fixture_dir),
                "--sandbox",
                str(tmp_path / "sandbox"),
                "--save",
                str(save_dir),
            ]
        )

    mock_diff.assert_not_called()
    data = json.loads(list(save_dir.glob("*.json"))[0].read_text(encoding="utf-8"))
    assert data["rows"][0]["knaif_score"] is None


def test_run_output_diff_choice_in_parser():
    """The CLI parser must accept output_diff as a verifier choice."""
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    p_run = sub.add_parser("run")
    p_run.add_argument("--skill")
    p_run.add_argument("--verifier", choices=["cheap", "honest", "output_diff"])
    args = parser.parse_args(["run", "--skill", "ffmpeg", "--verifier", "output_diff"])
    assert args.verifier == "output_diff"
