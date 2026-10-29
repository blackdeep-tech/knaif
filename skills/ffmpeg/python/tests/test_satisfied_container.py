""" "Convert clip_tiny.mov to mp4 and make it smaller" plans a compress (manual test, 2026-10-29).

The 4B answered `compress_video` with `container: "mp4"`. compress_video has no `container` arg
because it always writes mp4, so the plan asked "I can't convert video that way" instead
(ffmpeg_size_003 #0). tools.yaml now declares the mp4 as satisfied; any other container, and an
mp4 beside a `target` (archive writes mkv), still asks, because dropping it would hand back
another format than the one asked for (owner, 2026-10-29).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from knaif.agent import CommandAgent

FFMPEG_SKILL_DIR = Path(__file__).parents[2]

_UTTERANCE = "convert clip_tiny.mov to mp4 and make it smaller"


def _run(tmp_path: Path, args: dict) -> list[dict]:
    (tmp_path / "clip_tiny.mov").write_bytes(b"")
    orchestrator = MagicMock()
    orchestrator.infer.return_value = json.dumps(
        {"plan": [{"tool": "compress_video", "args": {"inputs": ["clip_tiny.mov"], **args}}]}
    )
    agent = CommandAgent.from_skill(
        FFMPEG_SKILL_DIR, sandbox=tmp_path, root=tmp_path, orchestrator=orchestrator
    )
    payload = agent.infer(_UTTERANCE, use_mock=False)
    return agent.execute_plan(payload, utterance=_UTTERANCE, skip_execution=True)


def test_an_mp4_container_on_compress_is_the_compress(tmp_path):
    results = _run(tmp_path, {"container": "mp4"})
    assert "clarify" not in [r["tool"] for r in results]


@pytest.mark.parametrize("args", [{"container": "mkv"}, {"container": "mp4", "target": "archive"}])
def test_another_container_still_asks(tmp_path, args):
    results = _run(tmp_path, args)
    assert [r["tool"] for r in results] == ["clarify"]
    assert "isn't supported" in results[0]["args"]["question"]
