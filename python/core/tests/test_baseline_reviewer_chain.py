"""Tests for the baseline reviewer's multi-output command chain helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from knaif.evalsuite.chain import run_command_chain


def _ok_run(produced: list[Path]):
    """Patch subprocess.run to succeed and create each command's output file."""

    def _fake(toks, *a, **k):
        out = Path(toks[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        produced.append(out)

        class _R:
            returncode = 0
            stderr = ""

        return _R()

    return patch.object(subprocess, "run", side_effect=_fake)


def test_chain_runs_each_command(tmp_path: Path):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"src")
    out_dir = tmp_path / "out"
    commands = [
        "ffmpeg -y -ss 3 -to 5 -i clip.mp4 -c:v libx264 clip_trimmed.mp4",
        "ffmpeg -y -i clip_trimmed.mp4 -vn -c:a libmp3lame clip_trimmed.mp3",
    ]
    with _ok_run([]):
        results = run_command_chain(commands, fixture_dir, out_dir)
    assert len(results) == 2
    assert all(r["returncode"] == 0 for r in results)


def test_chain_outputs_land_in_out_dir(tmp_path: Path):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"src")
    out_dir = tmp_path / "out"
    commands = [
        "ffmpeg -y -i clip.mp4 clip_trimmed.mp4",
        "ffmpeg -y -i clip_trimmed.mp4 clip_trimmed.mp3",
    ]
    with _ok_run([]):
        results = run_command_chain(commands, fixture_dir, out_dir)
    assert results[0]["output"] == out_dir / "clip_trimmed.mp4"
    assert results[1]["output"] == out_dir / "clip_trimmed.mp3"


def test_chain_first_input_resolves_to_fixture(tmp_path: Path):
    """The first command's -i must be rewritten to the fixture file path."""
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"src")
    out_dir = tmp_path / "out"
    seen_inputs: list[str] = []

    def _fake(toks, *a, **k):
        i = toks.index("-i")
        seen_inputs.append(toks[i + 1])
        Path(toks[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(toks[-1]).write_bytes(b"x")

        class _R:
            returncode = 0
            stderr = ""

        return _R()

    with patch.object(subprocess, "run", side_effect=_fake):
        run_command_chain(["ffmpeg -y -i clip.mp4 clip_trimmed.mp4"], fixture_dir, out_dir)
    assert seen_inputs[0] == str(fixture_dir / "clip.mp4")


def test_chain_second_input_resolves_to_prior_output(tmp_path: Path):
    """A later command's -i referencing a prior output resolves into out_dir."""
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"src")
    out_dir = tmp_path / "out"
    seen_inputs: list[str] = []

    def _fake(toks, *a, **k):
        i = toks.index("-i")
        seen_inputs.append(toks[i + 1])
        out = Path(toks[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")

        class _R:
            returncode = 0
            stderr = ""

        return _R()

    commands = [
        "ffmpeg -y -i clip.mp4 clip_trimmed.mp4",
        "ffmpeg -y -i clip_trimmed.mp4 clip_trimmed.mp3",
    ]
    with patch.object(subprocess, "run", side_effect=_fake):
        run_command_chain(commands, fixture_dir, out_dir)
    assert seen_inputs[1] == str(out_dir / "clip_trimmed.mp4")


def test_chain_stops_after_failure(tmp_path: Path):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "clip.mp4").write_bytes(b"src")
    out_dir = tmp_path / "out"
    calls: list[int] = []

    def _fake(toks, *a, **k):
        calls.append(1)

        class _R:
            returncode = 1
            stderr = "boom"

        return _R()

    commands = [
        "ffmpeg -y -i clip.mp4 clip_trimmed.mp4",
        "ffmpeg -y -i clip_trimmed.mp4 clip_trimmed.mp3",
    ]
    with patch.object(subprocess, "run", side_effect=_fake):
        results = run_command_chain(commands, fixture_dir, out_dir)
    # Only the first command should have run; the chain halts on failure.
    assert len(calls) == 1
    assert len(results) == 1
    assert results[0]["returncode"] == 1
