"""Regression: the eval agent must resolve filename stems against the fixture dir.

Cat A of the 2026-06-18 prompt-optimization diagnosis: extension-less corpus
names (e.g. ``clip_4k``) live in ``<sandbox>/fixtures/`` but the agent was built
with ``<sandbox>/`` as its sandbox, so ``resolve_stems`` (which globs the agent
sandbox root) raised ``StemNotFoundError`` and the plan was forced to clarify.
The agent's stem-resolution root must be the directory that actually holds the
input fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from knaif.evalsuite.cli import main
from knaif.evalsuite.corpus import CorpusRow, save_corpus


def _run(args: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["knaif.evalsuite"] + args
    try:
        main()
    finally:
        sys.argv = old_argv


class _StopAfterCapture(Exception):
    """Raised from the run_corpus stub to halt cmd_run once we have the agent."""


def test_run_wires_agent_sandbox_to_fixture_dir(tmp_path: Path):
    corpus_path = tmp_path / "eval.jsonl"
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip_4k.mp4").write_bytes(b"fake")
    save_corpus(
        [
            CorpusRow(
                id="r001",
                utterances=["downscale clip_4k to 1920x1080"],
                expected_outcome="plan",
                fixture="clip_4k.mp4",
                expected_tool="resize_video",
                tags=["resize"],
            )
        ],
        corpus_path,
    )

    captured: dict[str, object] = {}

    def _capture(agent, *args, **kwargs):
        captured["agent"] = agent
        raise _StopAfterCapture

    with patch("knaif.evalsuite.cli.run_corpus", side_effect=_capture):
        with pytest.raises(_StopAfterCapture):
            _run(
                [
                    "run",
                    "--skill",
                    "ffmpeg",
                    "--verifier",
                    "cheap",
                    "--corpus",
                    str(corpus_path),
                    "--fixture-dir",
                    str(fixture_dir),
                    "--sandbox",
                    str(tmp_path / "sandbox"),
                    "--config",
                    str(tmp_path / "no_backends.yaml"),  # missing → mock backend
                ]
            )

    agent = captured["agent"]
    assert (
        agent.sandbox == fixture_dir.resolve()
    ), f"agent stem-resolution sandbox should be the fixture dir, got {agent.sandbox}"


def test_extensionless_stem_resolves_when_sandbox_is_fixture_dir(tmp_path: Path):
    """Contract check: with the correct sandbox, an extension-less input does not clarify."""
    from knaif import create_agent

    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip_4k.mp4").write_bytes(b"fake")

    agent = create_agent("ffmpeg", sandbox=fixture_dir)
    payload = {
        "plan": [
            {"tool": "resize_video", "args": {"inputs": ["clip_4k"], "width": 1920, "height": 1080}}
        ]
    }
    results = agent.execute_plan(payload, utterance="downscale clip_4k to 1920x1080", dry_run=True)
    assert results[0]["tool"] != "clarify"
