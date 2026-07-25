"""Tests for skill-scoped eval fixture directories."""

from __future__ import annotations

from pathlib import Path


def test_default_fixture_dir_is_scoped_by_skill():
    from knaif.evalsuite.cli import _default_fixture_dir

    assert (
        _default_fixture_dir(Path("sandbox"), "ffmpeg") == Path("sandbox") / "fixtures" / "ffmpeg"
    )
    assert _default_fixture_dir(Path("sandbox"), "documents") == (
        Path("sandbox") / "fixtures" / "documents"
    )
