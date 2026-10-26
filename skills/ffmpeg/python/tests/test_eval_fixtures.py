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
        "clip_long.mp4",
        # The folder rows' folder: five short clips, one with no audio, one long 1080p.
        "batch/beach.mov",
        "batch/city.mov",
        "batch/dog.mp4",
        "batch/party.mp4",
        "batch/garden.mkv",
        "batch/silent.mov",
        "batch/concert.mp4",
        # One-file folder rows: the folder holds only the file the request names, so "this video"
        # has one answer. one_long/clip.mp4 is the 75 s clip the "every 10 seconds" rows need.
        "one_clip/clip.mp4",
        "one_mov/clip1.mov",
        "one_long/clip.mp4",
        # "merge all the clips in this folder into one": numbered so natural order shows.
        "clips/clip1.mp4",
        "clips/clip2.mp4",
        "clips/clip10.mp4",
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
