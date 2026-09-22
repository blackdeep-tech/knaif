"""A step that produces nothing must fail THERE, not one step later under someone else's name.

Observed 2026-09-22 in the workbench, on a six-step chain the 4B planned as one straight line:

    trim_video input=Test1.mov start=00:00:03 end=00:00:06 output=Test2.mov

`Test1.mov` was the 2-second output of an earlier step. Seeking to 3 s in a 2 s file gives
ffmpeg no frames, and **ffmpeg exits 0 anyway**, writing a 185-byte container with no streams.
`verify_outputs` probed it, found it openable, and marked it `verified: true`. The chain went
on, and the NEXT step failed — `Output file does not contain any stream`, exit -22 — naming a
file the user never asked for, one step away from the actual mistake.

Two guards, one per fact we already hold:

* **Before running** — `inspect_media` has measured the input's duration, so a trim that starts
  at or past the end is a request with no answer. Refuse it, naming the file and its length.
  Execute mode only: in a dry run a missing file gets `_dummy_probe`'s placeholder 60 s, and a
  guard reading an invented duration would refuse real requests.
* **After running** — ffmpeg's exit code is not evidence of output. A file with no audio and no
  video stream is a failure whatever the exit code said.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]
FIXTURE = FFMPEG_SKILL_DIR.parents[1] / "sandbox" / "fixtures" / "ffmpeg" / "clip.mp4"


@pytest.fixture(scope="module")
def steps_mod():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + ".steps"]


def _probe(name: str, duration: float) -> dict:
    return {
        "file": f"/sb/{name}",
        "container": "mov",
        "duration": duration,
        "size_bytes": 892373,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": None,
        "has_audio": False,
    }


class _Ctx:
    sandbox = None
    skip_execution = False

    def __init__(self, *, dry_run: bool) -> None:
        self.dry_run = dry_run


def _build(steps_mod, probe: dict, *, dry_run: bool = False, **trim) -> dict:
    return steps_mod.BuildRecipesStep().handle(
        {"probes": {"count": 1, "probes": [probe]}, "options": {"mode": "trim", **trim}},
        _Ctx(dry_run=dry_run),
    )


# ── before running: a trim past the end ─────────────────────────────────────────────────────


def test_a_trim_starting_past_the_end_is_refused_by_name(steps_mod):
    with pytest.raises(ValueError) as err:
        _build(steps_mod, _probe("Test1.mov", 2.0), start="00:00:03", end="00:00:06")

    message = str(err.value)
    assert "Test1.mov" in message
    assert "2.0s" in message
    assert "00:00:03" in message


def test_a_trim_starting_exactly_at_the_end_is_refused(steps_mod):
    """Seeking to 2.0 in a 2.0 s file leaves no frame either."""
    with pytest.raises(ValueError):
        _build(steps_mod, _probe("Test1.mov", 2.0), start="2", duration="1")


def test_a_trim_inside_the_file_still_builds(steps_mod):
    out = _build(steps_mod, _probe("clip_silent.mp4", 10.0), start="00:00:03", end="00:00:06")
    assert out["count"] == 1


def test_a_from_end_trim_is_not_a_start_past_the_end(steps_mod):
    """`start=-2s` is ffmpeg's from-end offset ("the last 2 seconds"), not a late start."""
    out = _build(steps_mod, _probe("clip.mp4", 10.0), start="-2s")
    assert out["count"] == 1


def test_a_dry_run_does_not_trust_the_placeholder_duration(steps_mod):
    """A missing file probes as a made-up 60 s in dry-run; that is not a measurement."""
    out = _build(steps_mod, _probe("later.mp4", 60.0), dry_run=True, start="00:01:30")
    assert out["count"] == 1


def test_an_unknown_duration_is_not_a_reason_to_refuse(steps_mod):
    out = _build(steps_mod, _probe("stream.ts", None), start="00:00:03")  # type: ignore[arg-type]
    assert out["count"] == 1


# ── after running: exit 0 is not evidence of output ─────────────────────────────────────────


def _run_batch(steps_mod, monkeypatch, tmp_path: Path, *, streams: list) -> dict:
    out = tmp_path / "Test2_intermediate.mov"
    out.write_bytes(b"\0" * 185)
    monkeypatch.setattr(steps_mod._deps, "run_ffmpeg", lambda _cmd: {"returncode": 0, "stderr": ""})
    monkeypatch.setattr(
        steps_mod._deps, "run_ffprobe", lambda _p: {"streams": streams, "format": {}}
    )
    return steps_mod.RunBatchStep().handle(
        {"commands": [{"input": "in.mov", "output": str(out), "command": ["ffmpeg"]}]},
        _Ctx(dry_run=False),
    )


def test_an_output_with_no_streams_fails_the_step_that_wrote_it(steps_mod, monkeypatch, tmp_path):
    with pytest.raises(ValueError) as err:
        _run_batch(steps_mod, monkeypatch, tmp_path, streams=[])
    assert "Test2_intermediate.mov" in str(err.value)
    assert "no audio or video" in str(err.value)


def test_an_output_with_a_stream_passes(steps_mod, monkeypatch, tmp_path):
    result = _run_batch(
        steps_mod, monkeypatch, tmp_path, streams=[{"codec_type": "video", "codec_name": "h264"}]
    )
    assert result["outputs"][0]["returncode"] == 0


def test_real_ffmpeg_exits_zero_on_an_empty_trim_and_the_step_still_fails(steps_mod, tmp_path):
    """The premise, on a real binary: without the guard this command 'succeeds'."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    if not FIXTURE.exists():
        pytest.skip("fixture not generated — run `just eval-fixtures ffmpeg`")
    short = tmp_path / "Test1.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(FIXTURE), "-t", "2", "-an", str(short)],
        check=True,
    )
    empty = tmp_path / "Test2.mov"
    command = ["ffmpeg", "-y", "-ss", "00:00:03", "-to", "00:00:06", "-i", str(short), str(empty)]
    assert subprocess.run(command, capture_output=True).returncode == 0  # the trap

    with pytest.raises(ValueError, match="no audio or video"):
        steps_mod.RunBatchStep().handle(
            {"commands": [{"input": str(short), "output": str(empty), "command": command}]},
            _Ctx(dry_run=False),
        )
