"""Tests for ffmpeg eval fixture definitions."""

from __future__ import annotations

from skills.ffmpeg.eval.fixtures import FIXTURES


def test_all_expected_fixtures_present():
    expected = {
        "clip.mp4",
        "clip2.mp4",
        "clip_no_audio.mp4",
        "clip_4k.mp4",
        "clip_ctr.mp4",
        "clip.mov",
        "audio.mp3",
    }
    assert expected == set(FIXTURES.keys())


def test_all_fixtures_have_lavfi_input():
    for name, cmd in FIXTURES.items():
        assert "-f lavfi" in cmd, f"{name!r} command missing '-f lavfi'"


def test_all_fixtures_have_output_placeholder():
    for name, cmd in FIXTURES.items():
        assert "{output}" in cmd, f"{name!r} command missing '{{output}}' placeholder"


def test_all_fixtures_start_with_ffmpeg():
    for name, cmd in FIXTURES.items():
        assert cmd.strip().startswith("ffmpeg"), f"{name!r} command does not start with 'ffmpeg'"


def test_clip_mov_key_has_mov_extension():
    assert "clip.mov" in FIXTURES
    assert FIXTURES["clip.mov"].strip().startswith("ffmpeg")


def test_audio_key_has_mp3_extension():
    assert "audio.mp3" in FIXTURES


def test_no_subprocess_in_fixtures_module():
    import ast
    from pathlib import Path

    src = (Path(__file__).parents[2] / "eval" / "fixtures.py").read_text()
    tree = ast.parse(src)
    imports = [
        node.names[0].name if isinstance(node, ast.Import) else node.module
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert "subprocess" not in imports, "fixtures.py must not import subprocess (pure data)"
