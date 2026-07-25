"""Tests for geometry helpers and the resize_video integration path.

Task 1.1 — RED: _geometry_vf unit tests.
Task 1.3 — RED: schema tests for fit/aspect on resize_video.
Task 1.5 — RED: expander + recipe + render integration tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def media_root(tmp_path: Path) -> Path:
    (tmp_path / "clip.mp4").write_bytes(b"")
    return tmp_path


@pytest.fixture()
def stub_ffmpeg(monkeypatch):
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    from knaif.skill import Skill as _Skill

    _Skill.load(FFMPEG_SKILL_DIR)
    mod = sys.modules["_skill_oop_ffmpeg_handlers"]

    monkeypatch.setattr(
        mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mp4", "duration": "60.0", "size": "100000000"},
        },
    )
    monkeypatch.setattr(
        mod._deps, "run_ffmpeg", lambda c: {"returncode": 0, "stdout": "", "stderr": ""}
    )
    return mod


def _run_resize(media_root: Path, args: dict[str, Any]) -> list[str]:
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    results = agent.execute_plan(
        {"plan": [{"tool": "resize_video", "args": args}]},
        dry_run=True,
        confirmed=True,
    )
    batch = next(r for r in results if r["tool"] == "run_batch")
    return batch["result"]["outputs"][0]["command"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_geometry_vf():
    """Load the skill and return _geometry_vf from the handlers module."""
    Skill.load(FFMPEG_SKILL_DIR)
    return sys.modules["_skill_oop_ffmpeg_handlers"]._geometry_vf


# ─────────────────────────────────────────────────────────────────────────────
# Task 1.1 — _geometry_vf unit tests (RED until Task 1.2 is green)
# ─────────────────────────────────────────────────────────────────────────────


def test_single_height_proportional():
    assert _get_geometry_vf()(None, 720, None, None) == "scale=-2:720"


def test_single_width_proportional():
    assert _get_geometry_vf()(1280, None, None, None) == "scale=min(1280\\,iw):-2"


def test_both_dims_default_crop():
    """Decision 1: both dims, no fit → cover-crop (not stretch)."""
    assert _get_geometry_vf()(1080, 1920, None, None) == (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    )


def test_both_dims_fit_pad():
    assert _get_geometry_vf()(1080, 1920, "pad", None) == (
        "scale=1080:1920:force_original_aspect_ratio=decrease," "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    )


def test_both_dims_fit_stretch():
    assert _get_geometry_vf()(1920, 1080, "stretch", None) == "scale=1920:1080"


def test_aspect_square_no_dims_pure_crop():
    assert _get_geometry_vf()(None, None, "crop", "1:1") == (
        "crop=min(iw\\,ih*1/1):min(ih\\,iw*1/1)"
    )


def test_aspect_9x16_pure_crop():
    # aw=9, ah=16 → W=min(iw,ih*9/16), H=min(ih,iw*16/9)
    assert _get_geometry_vf()(None, None, "crop", "9:16") == (
        "crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9)"
    )


def test_no_geometry_returns_none():
    assert _get_geometry_vf()(None, None, None, None) is None


def test_both_dims_take_precedence_over_aspect():
    """aspect is honored only when both dims are absent; both-dims → cover-crop."""
    assert _get_geometry_vf()(1080, 1080, "crop", "9:16") == (
        "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"
    )


def test_aspect_accepts_slash_separator():
    assert _get_geometry_vf()(None, None, "crop", "9/16") == (
        "crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9)"
    )


def test_invalid_aspect_raises():
    with pytest.raises(ValueError):
        _get_geometry_vf()(None, None, "crop", "square")


# ─────────────────────────────────────────────────────────────────────────────
# Task 1.5 — integration: fit/aspect threaded through expander→recipe→flags
# (RED until Task 1.6 wires fit/aspect through the expander and _build_flags)
# ─────────────────────────────────────────────────────────────────────────────


def test_resize_both_dims_emits_cover_crop(media_root, stub_ffmpeg):
    cmd = _run_resize(
        media_root,
        {"inputs": [str(media_root / "clip.mp4")], "width": 1080, "height": 1920},
    )
    cmd_str = " ".join(str(c) for c in cmd)
    assert "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" in cmd_str


def test_resize_square_via_aspect(media_root, stub_ffmpeg):
    cmd = _run_resize(
        media_root,
        {"inputs": [str(media_root / "clip.mp4")], "aspect": "1:1", "fit": "crop"},
    )
    cmd_str = " ".join(str(c) for c in cmd)
    assert "crop=min(iw\\,ih*1/1):min(ih\\,iw*1/1)" in cmd_str


def test_resize_single_height_unchanged(media_root, stub_ffmpeg):
    cmd = _run_resize(
        media_root,
        {"inputs": [str(media_root / "clip.mp4")], "height": 720},
    )
    cmd_str = " ".join(str(c) for c in cmd)
    assert "scale=-2:720" in cmd_str
    assert "crop" not in cmd_str


def test_resize_fit_pad_threads_to_pad_filter(media_root, stub_ffmpeg):
    cmd = _run_resize(
        media_root,
        {"inputs": [str(media_root / "clip.mp4")], "width": 1080, "height": 1920, "fit": "pad"},
    )
    cmd_str = " ".join(str(c) for c in cmd)
    assert "force_original_aspect_ratio=decrease" in cmd_str
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2" in cmd_str


def test_resize_fit_stretch_emits_plain_scale(media_root, stub_ffmpeg):
    cmd = _run_resize(
        media_root,
        {"inputs": [str(media_root / "clip.mp4")], "width": 1280, "height": 720, "fit": "stretch"},
    )
    cmd_str = " ".join(str(c) for c in cmd)
    assert "scale=1280:720" in cmd_str
    assert "crop" not in cmd_str
    assert "force_original_aspect_ratio" not in cmd_str


def test_resize_keep_aspect_ratio_false_stretches(media_root, stub_ffmpeg):
    """Legacy backward-compat: keep_aspect_ratio=False on both dims → stretch, not cover-crop."""
    cmd = _run_resize(
        media_root,
        {
            "inputs": [str(media_root / "clip.mp4")],
            "width": 1280,
            "height": 720,
            "keep_aspect_ratio": False,
        },
    )
    cmd_str = " ".join(str(c) for c in cmd)
    assert "scale=1280:720" in cmd_str
    assert "crop" not in cmd_str


def test_resize_branch_still_emits_encode_flags(media_root, stub_ffmpeg):
    """The resize-specific _build_flags branch must keep the encode flags alongside -vf."""
    cmd = _run_resize(
        media_root,
        {"inputs": [str(media_root / "clip.mp4")], "width": 1080, "height": 1920, "fit": "crop"},
    )
    assert "-c:v" in cmd
    assert "-crf" in cmd
