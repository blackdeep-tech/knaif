"""Golden-prompt regression gate (Phase 0 of the OOP skill-architecture plan).

Locks the exact rendered system message for each skill so the OOP migration —
which relocates handler/expander *code* but must not touch ``tools.yaml`` or
``prompt.yaml`` — cannot silently change what the model sees.

The migration keeps ``prompt.py`` / ``registry.py`` untouched, so these goldens
must stay byte-identical through every phase. The ``io`` goldens are transitional
(io is rebuilt in Phase 3); regenerate them when io is rewritten:

    KNAIF_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_prompt.py

See docs/plans/2026-06-16-oop-skill-architecture.md.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.registry import retrieve_tools

_REPO = Path(".").resolve()
_SKILLS = _REPO / "skills"
_GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# (skill dir name, golden basename, utterance, use_retrieval)
_CASES = [
    ("ffmpeg", "ffmpeg_system_full", "x", False),
    ("ffmpeg", "ffmpeg_system_retrieved_convert", "convert clip.mp4 to mkv", True),
    ("io", "io_system_full", "x", False),
]


def _skill_dirs() -> set[str]:
    return {p.name for p in _SKILLS.iterdir() if p.is_dir() and (p / "skill.yaml").exists()}


def _render_system(skill_dir: Path, utterance: str, use_retrieval: bool, tmp_path: Path) -> str:
    agent = CommandAgent.from_skill(skill_dir=skill_dir, sandbox=tmp_path, root=tmp_path)
    override = None
    if use_retrieval:
        override = retrieve_tools(utterance, agent.registry, min_score=3)
    system, _ = agent.build_prompt(utterance, registry_override=override)
    return system.replace("\r\n", "\n")


@pytest.mark.parametrize("skill_name,basename,utterance,use_retrieval", _CASES)
def test_golden_prompt(
    skill_name: str, basename: str, utterance: str, use_retrieval: bool, tmp_path: Path
) -> None:
    if skill_name not in _skill_dirs():
        pytest.skip(f"skill {skill_name!r} not present (expected during/after migration)")

    rendered = _render_system(_SKILLS / skill_name, utterance, use_retrieval, tmp_path)
    golden_path = _GOLDEN_DIR / f"{basename}.txt"

    if os.environ.get("KNAIF_UPDATE_GOLDEN") == "1":
        _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(rendered, encoding="utf-8", newline="\n")
        pytest.skip(f"updated golden {golden_path.name}")

    assert (
        golden_path.exists()
    ), f"missing golden {golden_path}; regenerate with KNAIF_UPDATE_GOLDEN=1"
    expected = golden_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if rendered != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                rendered.splitlines(),
                fromfile=f"{basename}.txt (golden)",
                tofile="rendered",
                lineterm="",
            )
        )
        raise AssertionError(
            f"Rendered prompt for {skill_name!r} drifted from golden.\n"
            f"If intentional, regenerate with KNAIF_UPDATE_GOLDEN=1.\n\n{diff}"
        )
