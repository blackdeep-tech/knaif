"""Shared pytest fixtures for knaif core tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.registry import load_registry

_MEDIA_BINARIES = {"ffmpeg", "ffprobe"}


@pytest.fixture(autouse=True)
def _no_media_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Core tests must never shell out to ffmpeg/ffprobe.

    Several core tests build the *real* ffmpeg skill — it is the repo's richest
    expander, so it is the honest fixture for testing stem resolution, the NL
    clarify gate and eval-suite sandbox wiring. None of them care about media:
    every one runs ``dry_run=True`` against a zero-byte ``.mp4``, which ffprobe
    rejects ("moov atom not found"), so they already take the ``_dummy_probe``
    preview path on a developer box.

    The binary was therefore doing nothing but changing which exception was
    raised — ``RuntimeError`` where it exists, ``FFmpegNotAvailable`` where it
    does not, and ``InspectMediaStep`` re-raises only the latter. That is why
    seven core tests failed the first time CI ran them on a clean runner, and
    why nobody had noticed: every machine that ever ran this suite happened to
    have ffmpeg installed.

    Guarding ``subprocess.run`` rather than patching the skill's ``_deps`` module
    is deliberate. The skill loader imports handlers under a synthetic package
    key (``_skill_oop_ffmpeg_<hash>``), so the module object a test could reach
    by name is not necessarily the one the loaded skill holds; the guard sits
    below that and cannot be defeated by it, applies before any skill loads, and
    covers tests not yet written.

    Preview paths swallow the error and stub the probe — the behaviour these
    tests already exercise. An *executing* path surfaces it loudly, which is the
    correct outcome: a core test that genuinely needs to run ffmpeg belongs in
    ``skills/ffmpeg/python/tests/``, next to the skill it is testing.
    """
    real_run = subprocess.run

    def guarded(cmd, *args, **kwargs):
        first = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd
        if isinstance(first, (str, Path)) and Path(str(first)).stem.lower() in _MEDIA_BINARIES:
            raise RuntimeError(
                f"core tests must not invoke {Path(str(first)).stem!r}: stub the probe, "
                "or move the test to skills/ffmpeg/python/tests/ where the binary is a "
                "declared dependency"
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """Create a seeded sandbox directory under tmp_path."""
    sb = tmp_path / "sandbox"
    sb.mkdir()
    reports = sb / "reports"
    reports.mkdir()
    tmp = sb / "tmp"
    tmp.mkdir()
    (reports / "jan_report.txt").write_text("jan", encoding="utf-8")
    (reports / "feb_report.txt").write_text("feb", encoding="utf-8")
    (tmp / "cache.tmp").write_text("cache", encoding="utf-8")
    (tmp / "old.log").write_text("log", encoding="utf-8")
    bin_dir = sb / "bin"
    bin_dir.mkdir()
    (bin_dir / "app.exe").write_bytes(b"")
    (bin_dir / "script.sh").write_bytes(b"")
    return sb


TOOLS_YAML = Path("skills") / "io" / "tools.yaml"
IO_SKILL_DIR = Path("skills") / "io"


@pytest.fixture()
def registry():
    """Return the ToolDef registry loaded from skills/io/tools.yaml."""
    return load_registry(TOOLS_YAML)


@pytest.fixture()
def agent(sandbox: Path, tmp_path: Path) -> CommandAgent:
    """Return a CommandAgent configured against the test sandbox."""
    return CommandAgent.from_skill(
        skill_dir=IO_SKILL_DIR,
        sandbox=sandbox,
        root=tmp_path,
    )
