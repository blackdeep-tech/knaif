"""resize and rotate need a picture: on an audio-only input they must fail, not exit 0.

Found 2026-09-23 in the workbench: a chain whose trim was pointed at `clip_audio.aac` carried no
video from then on, and `resize_video` rendered `-vf scale=…` against a file with no video stream.
ffmpeg ignored the filter, re-muxed the audio and exited 0 — "resized" to nothing, reported as a
success. The message is byte-identical in skills/ffmpeg/native/src/engine.rs (`needs_video`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def engine():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + "._engine"]


def _audio_only(name: str = "clip_rev.mkv") -> dict:
    return {"file": f"/sb/{name}", "audio_codec": "aac", "has_audio": True, "duration": 2.0}


def _video(name: str = "clip.mkv") -> dict:
    return {**_audio_only(name), "video_codec": "h264", "width": 1920, "height": 1080}


@pytest.mark.parametrize(("mode", "verb"), [("resize", "resize"), ("rotate", "rotate")])
def test_a_picture_operation_on_audio_only_says_why(engine, mode, verb):
    assert engine._needs_video({"mode": mode}, _audio_only()) == (
        f"Can't {verb} clip_rev.mkv: it has no video stream, only audio. "
        "Check which file this step should start from."
    )


@pytest.mark.parametrize("mode", ["resize", "rotate"])
def test_a_picture_operation_on_video_is_allowed(engine, mode):
    assert engine._needs_video({"mode": mode}, _video()) is None


@pytest.mark.parametrize("mode", ["reverse", "adjust_speed", "adjust_volume", "trim"])
def test_operations_that_mean_something_for_audio_are_left_alone(engine, mode):
    """Reversing, retiming or trimming a sound is a real request."""
    assert engine._needs_video({"mode": mode}, _audio_only()) is None
