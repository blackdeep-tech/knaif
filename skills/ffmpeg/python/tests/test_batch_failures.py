"""One step over many files: each file's end is reported, and one failure does not end the step.

Owner, 2026-10-13: one failed file does not stop the others, a failure is
printed when it happens, the run ends with counts, time and size. Core does the printing and
counting from ``ctx.file_finished``; the skill's part is to report every file it works on
(`run_batch` each command, `build_recipes` each silent file it skips) and to leave the batch's
failures to that report instead of repeating them in its own result lines.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from knaif.skill import Skill

SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def mods():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + ".steps"], handlers


class _Ctx:
    sandbox = None
    work_dir = None
    skip_execution = False

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.reported: list[tuple[str, str | None]] = []

    def file_finished(self, file: str, failure: str | None = None) -> None:
        self.reported.append((file, failure))


def _commands(*names: str) -> dict:
    return {
        "commands": [
            {"input": f"/sb/{n}", "output": f"/sb/{n}.mp4", "command": ["ffmpeg", n]} for n in names
        ]
    }


def test_run_batch_reports_every_file(mods, monkeypatch):
    steps, _ = mods

    def fake_run(command, output, ctx):
        if command[-1] == "b.mov":
            return {
                "returncode": 1,
                "stderr": "frame=1\nb.mov: Invalid data found when processing input\nConversion failed!",
            }
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(steps, "run_and_commit", fake_run)
    ctx = _Ctx()
    out = steps.RunBatchStep().handle(_commands("a.mov", "b.mov", "c.mov"), ctx)
    assert ctx.reported == [
        ("/sb/a.mov", None),
        ("/sb/b.mov", "ffmpeg failed (exit 1): b.mov: Invalid data found when processing input"),
        ("/sb/c.mov", None),
    ]
    assert [o["returncode"] for o in out["outputs"]] == [0, 1, 0]


def test_a_command_that_raises_in_a_batch_is_that_files_failure(mods, monkeypatch):
    steps, _ = mods

    def fake_run(command, output, ctx):
        if command[-1] == "a.mov":
            raise ValueError("ffmpeg finished but a.mov.mp4 has no audio or video in it")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(steps, "run_and_commit", fake_run)
    ctx = _Ctx()
    out = steps.RunBatchStep().handle(_commands("a.mov", "b.mov"), ctx)
    assert ctx.reported == [
        ("/sb/a.mov", "ffmpeg finished but a.mov.mp4 has no audio or video in it"),
        ("/sb/b.mov", None),
    ]
    assert out["outputs"][0]["returncode"] != 0
    assert out["outputs"][1]["returncode"] == 0


def test_a_command_that_raises_alone_still_raises(mods, monkeypatch):
    steps, _ = mods

    def fake_run(command, output, ctx):
        raise ValueError("nothing in it")

    monkeypatch.setattr(steps, "run_and_commit", fake_run)
    with pytest.raises(ValueError, match="nothing in it"):
        steps.RunBatchStep().handle(_commands("a.mov"), _Ctx())


def test_a_dry_run_reports_nothing(mods):
    steps, _ = mods
    ctx = _Ctx(dry_run=True)
    steps.RunBatchStep().handle(_commands("a.mov", "b.mov"), ctx)
    assert ctx.reported == []


def _probe(name: str, *, has_audio: bool) -> dict[str, Any]:
    return {
        "file": f"/sb/{name}",
        "container": "mp4",
        "duration": 10.0,
        "size_bytes": 100000,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac" if has_audio else None,
        "has_audio": has_audio,
    }


def test_a_skipped_silent_file_is_reported_as_failed(mods):
    steps, _ = mods
    ctx = _Ctx(dry_run=True)
    probes = [_probe("clip.mp4", has_audio=True), _probe("silent.mp4", has_audio=False)]
    steps.BuildRecipesStep().handle(
        {
            "probes": {"count": 2, "probes": probes},
            "options": {"mode": "extract_audio", "audio_format": "mp3"},
        },
        ctx,
    )
    assert ctx.reported == [("/sb/silent.mp4", "has no audio track to extract")]


def _batch_result(*codes: int) -> list[dict[str, Any]]:
    outputs = [
        {"output": f"/tmp/{i}.mp4", "returncode": rc, "stderr_tail": "bad data"}
        for i, rc in enumerate(codes)
    ]
    return [{"tool": "run_batch", "result": {"mode": "execute", "outputs": outputs}}]


def test_the_formatter_leaves_a_batchs_failures_to_the_summary(mods):
    _, handlers = mods
    items = handlers._format_results(_batch_result(0, 1, 0), dry_run=False)
    assert [i["kind"] for i in items] == ["output", "output"]


def test_the_formatter_still_reports_a_single_files_failure(mods):
    _, handlers = mods
    items = handlers._format_results(_batch_result(1), dry_run=False)
    assert [i["kind"] for i in items] == ["error"]


def test_an_unprobeable_file_is_reported_as_failed(mods, monkeypatch, tmp_path):
    steps, _ = mods
    good, bad = tmp_path / "good.mov", tmp_path / "broken.mov"
    good.write_bytes(b"x")
    bad.write_bytes(b"x")

    def fake_probe(path, ctx):
        if path.name == "broken.mov":
            raise RuntimeError("broken.mov: Invalid data found when processing input")
        return {"streams": [{"codec_type": "video"}], "format": {}}

    monkeypatch.setattr(steps._deps, "run_ffprobe", fake_probe)
    ctx = _Ctx()
    out = steps.InspectMediaStep().handle({"files": [str(good), str(bad)]}, ctx)
    assert len(out["probes"]) == 1
    assert ctx.reported == [(str(bad), "broken.mov: Invalid data found when processing input")]


def test_the_formatter_shows_a_batchs_outputs_past_an_unprobeable_file(mods):
    _, handlers = mods
    results = [
        {
            "tool": "inspect_media",
            "result": {"probes": [{}, {}], "errors": [{"file": "/sb/b.mov", "error": "bad"}]},
        },
        *_batch_result(0, 0),
    ]
    items = handlers._format_results(results, dry_run=False)
    assert [i["kind"] for i in items] == ["output", "output"]


def test_the_formatter_still_stops_on_a_single_unprobeable_file(mods):
    _, handlers = mods
    results = [
        {
            "tool": "inspect_media",
            "result": {"probes": [], "errors": [{"file": "/sb/b.mov", "error": "bad"}]},
        },
    ]
    items = handlers._format_results(results, dry_run=False)
    assert items == [{"kind": "error", "message": "bad"}]


def test_an_unprobeable_files_reason_is_ffprobes_own_words(mods, monkeypatch, tmp_path):
    """No path (it names the user's folders) and no ffmpeg context prefix in the reason."""
    steps, _ = mods
    good, bad = tmp_path / "good.mov", tmp_path / "broken.mov"
    good.write_bytes(b"x")
    bad.write_bytes(b"x")

    def fake_probe(path, ctx):
        if path.name == "broken.mov":
            raise RuntimeError(
                f"ffprobe failed for {path}: [mov,mp4,m4a,3gp,3g2,mj2 @ 0000016c29454f00] "
                "moov atom not found"
            )
        return {"streams": [{"codec_type": "video"}], "format": {}}

    monkeypatch.setattr(steps._deps, "run_ffprobe", fake_probe)
    ctx = _Ctx()
    steps.InspectMediaStep().handle({"files": [str(good), str(bad)]}, ctx)
    assert ctx.reported == [(str(bad), "moov atom not found")]
