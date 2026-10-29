"""The compress_result corpus rows pass a correct run and fail what the manual test saw.

A row that cannot fail measures nothing: every compress row passed while `compress_video` handed
back a file 10% bigger (2026-10-28). Each row here is run with its correct plan for real, graded
as the eval grades it, then shown the old behaviour's result, which must not pass.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from knaif.agent import CommandAgent
from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.folder_rows import grade_folder
from skills.ffmpeg.eval.verifiers import success

SKILL = Path(__file__).parents[2]
FIXTURES = SKILL.parents[1] / "sandbox" / "fixtures" / "ffmpeg"
_FIELDS = {f.name for f in dataclasses.fields(CorpusRow)}

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH"),
    pytest.mark.skipif(
        not (FIXTURES / "tiny_mov" / "clip_tiny.mov").exists(), reason="needs generated fixtures"
    ),
]


def _row(row_id: str) -> CorpusRow:
    for line in (SKILL / "data" / "eval.jsonl").read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if raw["id"] == row_id:
            return CorpusRow(**{k: v for k, v in raw.items() if k in _FIELDS})
    raise AssertionError(row_id)


def _run(folder: Path, row: CorpusRow, plan: list[dict]) -> None:
    agent = CommandAgent.from_skill(SKILL, sandbox=folder)
    agent.execute_plan({"plan": plan}, utterance=row.utterances[0], dry_run=False, confirmed=True)


def _check(folder: Path):
    def check(path: Path, criteria: dict) -> object:
        return success(SimpleNamespace(artifact=None, artifact_path=path), criteria, folder)

    return check


def _old_compress(src: Path, out: Path) -> None:
    """What `compress_video` rendered before 2026-10-28: the audio at the profile's 96k."""
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(src),
            "-c:v", "libx264", "-crf", "28", "-preset", "slow", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out),
        ],
        check=True,
    )  # fmt: skip


def test_size_001_a_compress_of_clip_mov(tmp_path: Path) -> None:
    row = _row("ffmpeg_size_001")
    shutil.copy2(FIXTURES / "clip.mov", tmp_path / "clip.mov")
    _run(tmp_path, row, [{"tool": "compress_video", "args": {"inputs": ["clip.mov"]}}])
    out = tmp_path / "clip_compressed.mp4"
    assert _check(tmp_path)(out, row.success_criteria).score == 1.0

    _old_compress(tmp_path / "clip.mov", out)
    old = _check(tmp_path)(out, row.success_criteria)
    assert old.score < 1.0, old


def test_size_002_a_clip_that_cannot_shrink(tmp_path: Path) -> None:
    row = _row("ffmpeg_size_002")
    fixture = FIXTURES / "tiny_clip"
    shutil.copytree(fixture, tmp_path, dirs_exist_ok=True)
    _run(tmp_path, row, [{"tool": "compress_video", "args": {"inputs": ["clip_tiny.mp4"]}}])
    assert grade_folder(row, tmp_path, fixture, _check(tmp_path)).score == 1.0

    _old_compress(tmp_path / "clip_tiny.mp4", tmp_path / "clip_tiny_compressed.mp4")
    assert grade_folder(row, tmp_path, fixture, _check(tmp_path)).score < 1.0


def test_size_003_a_converted_clip_that_cannot_shrink(tmp_path: Path) -> None:
    row = _row("ffmpeg_size_003")
    fixture = FIXTURES / "tiny_mov"
    shutil.copytree(fixture, tmp_path, dirs_exist_ok=True)
    plan = [
        {
            "tool": "convert_video",
            "args": {"inputs": ["clip_tiny.mov"], "container": "mp4", "output": "clip_tiny.mp4"},
        },
        {"tool": "compress_video", "args": {"inputs": ["clip_tiny.mp4"]}},
    ]
    _run(tmp_path, row, plan)
    graded = grade_folder(row, tmp_path, fixture, _check(tmp_path))
    assert graded.score == 1.0, graded

    _old_compress(tmp_path / "clip_tiny.mov", tmp_path / "clip_tiny_compressed.mp4")
    assert grade_folder(row, tmp_path, fixture, _check(tmp_path)).score < 1.0


def test_size_003_one_compress_that_names_the_format(tmp_path: Path) -> None:
    """The 4B's plan for "convert clip_tiny.mov to mp4 and make it smaller" (2026-10-29): one
    compress with `container: mp4`. The mp4 was asked for, so the clip that cannot shrink still
    comes out as one; without the container, the same compress leaves the folder as it was."""
    row = _row("ffmpeg_size_003")
    fixture = FIXTURES / "tiny_mov"
    shutil.copytree(fixture, tmp_path, dirs_exist_ok=True)
    plan = [{"tool": "compress_video", "args": {"inputs": ["clip_tiny.mov"], "container": "mp4"}}]
    _run(tmp_path, row, plan)
    graded = grade_folder(row, tmp_path, fixture, _check(tmp_path))
    assert graded.score == 1.0, graded

    plain = tmp_path / "plain"
    shutil.copytree(fixture, plain)
    _run(plain, row, [{"tool": "compress_video", "args": {"inputs": ["clip_tiny.mov"]}}])
    assert not list(plain.glob("clip_tiny_compressed*"))
