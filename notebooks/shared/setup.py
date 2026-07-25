"""Shared notebook bootstrap helpers."""

from __future__ import annotations

from pathlib import Path

import ipywidgets as widgets


def find_root(start: Path) -> Path:
    """Walk up from *start* until a .git directory is found."""
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return start


def make_skill_selector(root: Path) -> widgets.Dropdown:
    """Return a Dropdown listing available skills under *root*/src/skills."""
    skills_dir = root / "src" / "skills"
    available = sorted(
        p.name for p in skills_dir.iterdir() if p.is_dir() and (p / "skill.yaml").exists()
    )
    return widgets.Dropdown(
        options=available,
        value="ffmpeg" if "ffmpeg" in available else (available[0] if available else ""),
        description="Skill:",
        style={"description_width": "55px"},
        layout=widgets.Layout(width="220px"),
    )


def default_fixture_dir(sandbox: Path | str, skill: str) -> Path:
    """Return the default generated fixture directory for *skill*."""
    return Path(sandbox) / "fixtures" / skill
