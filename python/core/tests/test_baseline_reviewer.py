"""The baseline reviewer's run output: what it calls an output, and how it previews one.

A row's run directory is provisioned with every fixture before the command runs, so "every file
in the directory" is mostly fixture copies. Only what the command wrote is an output.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import ipywidgets as widgets

_SHARED = Path("notebooks") / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from baseline_reviewer import _preview_widget, _produced_files, _snapshot  # noqa: E402


def _touch(path: Path, data: bytes = b"x", mtime_ns: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def test_provisioned_fixtures_are_not_outputs(tmp_path: Path) -> None:
    _touch(tmp_path / "clip.mp4", mtime_ns=1_000_000_000)
    _touch(tmp_path / "sub" / "nested.mp4", mtime_ns=1_000_000_000)
    before = _snapshot(tmp_path)
    out = _touch(tmp_path / "clip_louder_audio.mp3")
    assert _produced_files(tmp_path, before) == [out]


def test_a_fixture_the_command_overwrote_is_an_output(tmp_path: Path) -> None:
    fixture = _touch(tmp_path / "clip.mp4", mtime_ns=1_000_000_000)
    before = _snapshot(tmp_path)
    _touch(fixture, b"rewritten", mtime_ns=2_000_000_000)
    assert _produced_files(tmp_path, before) == [fixture]


def test_outputs_in_subdirectories_are_found(tmp_path: Path) -> None:
    before = _snapshot(tmp_path)
    out = _touch(tmp_path / "exports" / "clip.mp4")
    assert _produced_files(tmp_path, before) == [out]


def test_audio_previews_as_a_widget(tmp_path: Path) -> None:
    w = _preview_widget(_touch(tmp_path / "a.mp3", b"ID3"))
    assert isinstance(w, widgets.Audio) and w.format == "mp3" and w.value == b"ID3"


def test_video_and_image_preview_as_widgets(tmp_path: Path) -> None:
    assert isinstance(_preview_widget(_touch(tmp_path / "v.mp4")), widgets.Video)
    img = _preview_widget(_touch(tmp_path / "t.jpg"))
    assert isinstance(img, widgets.Image) and img.format == "jpeg"


def test_other_files_have_no_preview(tmp_path: Path) -> None:
    assert _preview_widget(_touch(tmp_path / "notes.txt")) is None
