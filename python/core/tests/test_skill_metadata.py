"""Loader exposes skill.yaml `runtimes:` + `dependencies:` metadata.

Declarative metadata consumed by the native runtime (which crate implements a skill)
and the installer (which external tools to detect/offer). The Python loader parses and
exposes it so it can be tested and, later, drive dependency preflight.
"""

from __future__ import annotations

from pathlib import Path

from knaif.skill import Skill

FFMPEG = Path("skills/ffmpeg")
DOCUMENTS = Path("skills/documents")


def _tool(skill: Skill, name: str) -> dict:
    match = [t for t in skill.external_tools if t.get("name") == name]
    assert match, f"{name!r} not in external_tools: {skill.external_tools}"
    return match[0]


def test_ffmpeg_declares_ffmpeg_binaries_required():
    skill = Skill.load(FFMPEG)
    tool = _tool(skill, "ffmpeg")
    assert tool["required"] is True
    assert set(tool["commands"]) == {"ffmpeg", "ffprobe"}


def test_ffmpeg_runtimes_mark_native_supported_with_crate():
    skill = Skill.load(FFMPEG)
    assert skill.runtimes["native"]["status"] == "supported"
    assert skill.runtimes["native"]["crate"] == "knaif-skill-ffmpeg"


def test_documents_declares_optional_external_tools():
    skill = Skill.load(DOCUMENTS)
    names = {t["name"] for t in skill.external_tools}
    assert {"ghostscript", "libreoffice", "tesseract"} <= names
    # documents degrades gracefully without them, so they are optional.
    assert all(t["required"] is False for t in skill.external_tools)
    assert "tesseract" in _tool(skill, "tesseract")["commands"]


def test_documents_runtimes_native_crate():
    skill = Skill.load(DOCUMENTS)
    assert skill.runtimes["native"]["crate"] == "knaif-skill-documents"


def test_skill_without_metadata_defaults_empty():
    # io is stale and declares no runtimes/dependencies — loader must default cleanly.
    skill = Skill.load(Path("skills/io"))
    assert skill.external_tools == ()
    assert skill.runtimes == {}
