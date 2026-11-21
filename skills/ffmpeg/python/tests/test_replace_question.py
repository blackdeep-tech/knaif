"""Replacing a file: the tree shows what will run before it asks, and a yes goes on.

The owner's terminal check (2026-10-25): native draws the plan, the step, its command and
`⚠ out.mkv already exists`, then asks `Replace out.mkv? [y/N]`; Python asked before drawing
anything. Now Python draws them first, and after a yes the run carries on from the question
without drawing the plan, the step or its command again. With nobody to ask, the same lines
come before the stop (`contracts/parity/cli_output_cases.json`).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

import knaif.app as app

REPO = Path(__file__).parents[4]
PLAN = {"plan": [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "output": "out.mkv"}}]}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_a_yes_goes_on_without_drawing_the_step_again(tmp_path, monkeypatch):
    fixture = REPO / "sandbox" / "fixtures" / "ffmpeg" / "clip.mp4"
    if not fixture.is_file():
        pytest.skip("fixture clip.mp4 missing (regenerate the ffmpeg fixtures)")
    monkeypatch.chdir(tmp_path)
    shutil.copy(fixture, tmp_path / "clip.mp4")
    (tmp_path / "out.mkv").write_bytes(b"")
    asked: list[str] = []

    def yes(question: str, default: bool) -> bool:
        asked.append(question)
        return True

    monkeypatch.setattr(app, "_ask_yes_no", yes)
    env = {
        "KNAIF_LLM_BACKEND": "mock",
        "KNAIF_LLM_MOCK_RESPONSE": json.dumps(PLAN),
        "KNAIF_VIEW": "rich",
        "NO_COLOR": "1",
    }
    result = CliRunner().invoke(app.cli, ["run", "ffmpeg", "convert clip.mp4 to out.mkv"], env=env)
    out = result.output
    assert result.exit_code == 0, out
    assert asked == ["Replace out.mkv?"]
    for line in ("Plan · 1 step", "Step 1/1 · convert_video", "$ ffmpeg -y -i clip.mp4"):
        assert out.count(line) == 1, out
    assert out.index("⚠ out.mkv already exists") < out.index("✓ out.mkv"), out
    assert (tmp_path / "out.mkv").stat().st_size > 0
