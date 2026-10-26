"""The mock backend takes a canned model output, as native's does.

`KNAIF_LLM_BACKEND=mock` picks the mock and `KNAIF_LLM_MOCK_RESPONSE` is returned verbatim as
the model's output; everything after inference runs for real. One harness then drives both CLIs
on the same plans (`contracts/parity/cli_output_cases.json`.).
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from knaif import create_agent
from knaif.app import cli

PLAN = {"plan": [{"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "s.mp4"}}]}


def test_the_mock_returns_the_canned_output(monkeypatch) -> None:
    monkeypatch.setenv("KNAIF_LLM_MOCK_RESPONSE", json.dumps(PLAN))
    agent = create_agent("ffmpeg")
    assert agent.infer("strip audio from clip.mp4", use_mock=True) == PLAN


def test_the_cli_picks_the_mock_from_the_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    env = {"KNAIF_LLM_BACKEND": "mock", "KNAIF_LLM_MOCK_RESPONSE": json.dumps(PLAN)}
    result = CliRunner().invoke(
        cli, ["run", "ffmpeg", "--dry-run", "strip audio from clip.mp4"], env=env
    )
    assert result.exit_code == 0, result.output
    assert "ffmpeg -y -i clip.mp4 -an -c:v copy s.mp4" in result.output
