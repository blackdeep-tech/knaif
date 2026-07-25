"""Shared pytest fixtures for knaif core tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.registry import load_registry


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
