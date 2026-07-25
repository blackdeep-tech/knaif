"""Tests for `evalsuite seed-baselines --skill <name>`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from knaif.evalsuite.cli import main
from knaif.evalsuite.corpus import CorpusRow, save_corpus


def _run(args: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["knaif.evalsuite"] + args
    try:
        main()
    finally:
        sys.argv = old_argv


def _make_agent(command: str = "ffmpeg -y -i input.mp4 out.mp4") -> MagicMock:
    agent = MagicMock()
    agent.infer.return_value = {
        "plan": [{"tool": "convert_video", "args": {"inputs": ["input.mp4"], "container": "mp4"}}]
    }
    agent.execute_plan.return_value = [
        {"tool": "run_batch", "result": {"command": command.split()}}
    ]
    return agent


def _rows_from_file(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# ── seed-baselines populates command for rows without one ──────────────────────


def test_seed_baselines_populates_command(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    rows = [
        CorpusRow(id="r001", utterances=["convert to mp4"], expected_outcome="plan"),
        CorpusRow(id="r002", utterances=["strip audio"], expected_outcome="plan"),
    ]
    save_corpus(rows, corpus_path)

    agent = _make_agent("ffmpeg -y -i input.mp4 out.mp4")

    with patch("knaif.evalsuite.cli._make_agent", return_value=agent):
        _run(["seed-baselines", "--skill", "ffmpeg", "--corpus", str(corpus_path)])

    saved = _rows_from_file(corpus_path)
    assert saved[0]["baseline"]["command"] == "ffmpeg -y -i input.mp4 out.mp4"
    assert saved[1]["baseline"]["command"] == "ffmpeg -y -i input.mp4 out.mp4"


def test_seed_baselines_skips_rows_with_existing_command(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    rows = [
        CorpusRow(
            id="r001",
            utterances=["convert to mp4"],
            expected_outcome="plan",
            baseline={"command": "ffmpeg -y -i input.mp4 -c:v libx264 existing.mp4"},
        ),
        CorpusRow(id="r002", utterances=["strip audio"], expected_outcome="plan"),
    ]
    save_corpus(rows, corpus_path)

    agent = _make_agent("ffmpeg -y -i input.mp4 new.mp4")

    with patch("knaif.evalsuite.cli._make_agent", return_value=agent):
        _run(["seed-baselines", "--skill", "ffmpeg", "--corpus", str(corpus_path)])

    saved = _rows_from_file(corpus_path)
    # Row with existing command is not overwritten
    assert saved[0]["baseline"]["command"] == "ffmpeg -y -i input.mp4 -c:v libx264 existing.mp4"
    # Row without command gets populated
    assert saved[1]["baseline"]["command"] == "ffmpeg -y -i input.mp4 new.mp4"


def test_seed_baselines_force_overwrites_existing(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    rows = [
        CorpusRow(
            id="r001",
            utterances=["convert to mp4"],
            expected_outcome="plan",
            baseline={"command": "old command"},
        ),
    ]
    save_corpus(rows, corpus_path)

    agent = _make_agent("ffmpeg -y -i input.mp4 new.mp4")

    with patch("knaif.evalsuite.cli._make_agent", return_value=agent):
        _run(["seed-baselines", "--skill", "ffmpeg", "--corpus", str(corpus_path), "--force"])

    saved = _rows_from_file(corpus_path)
    assert saved[0]["baseline"]["command"] == "ffmpeg -y -i input.mp4 new.mp4"


def test_seed_baselines_validated_rows_never_overwritten(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    rows = [
        CorpusRow(
            id="r001",
            utterances=["convert to mp4"],
            expected_outcome="plan",
            baseline={"command": "validated cmd", "validated_by": "human"},
        ),
    ]
    save_corpus(rows, corpus_path)

    agent = _make_agent("ffmpeg -y -i input.mp4 new.mp4")

    with patch("knaif.evalsuite.cli._make_agent", return_value=agent):
        _run(["seed-baselines", "--skill", "ffmpeg", "--corpus", str(corpus_path), "--force"])

    saved = _rows_from_file(corpus_path)
    # validated rows are never overwritten, even with --force
    assert saved[0]["baseline"]["command"] == "validated cmd"


def test_seed_baselines_limit(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    rows = [
        CorpusRow(id=f"r{i:03d}", utterances=[f"utterance {i}"], expected_outcome="plan")
        for i in range(5)
    ]
    save_corpus(rows, corpus_path)

    agent = _make_agent("ffmpeg -y -i input.mp4 out.mp4")

    with patch("knaif.evalsuite.cli._make_agent", return_value=agent):
        _run(["seed-baselines", "--skill", "ffmpeg", "--corpus", str(corpus_path), "--limit", "2"])

    saved = _rows_from_file(corpus_path)
    seeded = [r for r in saved if (r.get("baseline") or {}).get("command")]
    assert len(seeded) == 2


def test_seed_baselines_no_artifact_leaves_row_unchanged(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    rows = [
        CorpusRow(id="r001", utterances=["do something unclear"], expected_outcome="plan"),
    ]
    save_corpus(rows, corpus_path)

    # Agent returns no usable command (clarify outcome)
    agent = MagicMock()
    agent.infer.return_value = {"plan": [{"tool": "clarify", "args": {"message": "unclear"}}]}
    agent.execute_plan.return_value = []

    with patch("knaif.evalsuite.cli._make_agent", return_value=agent):
        _run(["seed-baselines", "--skill", "ffmpeg", "--corpus", str(corpus_path)])

    saved = _rows_from_file(corpus_path)
    assert saved[0].get("baseline") is None


def test_seed_baselines_idempotent(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    rows = [
        CorpusRow(id="r001", utterances=["convert to mp4"], expected_outcome="plan"),
    ]
    save_corpus(rows, corpus_path)

    agent = _make_agent("ffmpeg -y -i input.mp4 out.mp4")

    with patch("knaif.evalsuite.cli._make_agent", return_value=agent):
        _run(["seed-baselines", "--skill", "ffmpeg", "--corpus", str(corpus_path)])
        _run(["seed-baselines", "--skill", "ffmpeg", "--corpus", str(corpus_path)])

    saved = _rows_from_file(corpus_path)
    assert saved[0]["baseline"]["command"] == "ffmpeg -y -i input.mp4 out.mp4"
    # Ran twice — infer only called once (skipped on second pass)
    assert agent.infer.call_count == 1
