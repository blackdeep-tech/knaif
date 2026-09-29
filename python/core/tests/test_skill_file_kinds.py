"""`file_kinds:` in skill.yaml: the per-skill file kinds chain threading compares.

See docs/plans/2026-09-23-chain-source-threading.md (T2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill, parse_file_kinds

REPO = Path(__file__).resolve().parents[3]


def test_kinds_become_an_extension_map():
    assert parse_file_kinds({"video": ["MP4", ".mkv"], "image": ["jpg"]}) == {
        "mp4": "video",
        "mkv": "video",
        "jpg": "image",
    }


def test_absent_means_no_kinds():
    assert parse_file_kinds(None) == {}


def test_an_extension_in_two_kinds_is_an_error():
    with pytest.raises(ValueError, match="gif"):
        parse_file_kinds({"video": ["gif"], "image": ["gif"]})


def test_a_kind_must_list_extensions():
    with pytest.raises(ValueError, match="video"):
        parse_file_kinds({"video": "mp4"})


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_active_skills_declare_kinds_and_the_agent_carries_them(skill: str, tmp_path: Path):
    loaded = Skill.load(REPO / "skills" / skill)
    assert loaded.file_kinds
    agent = CommandAgent.from_skill(REPO / "skills" / skill, sandbox=tmp_path)
    assert agent.file_kinds == loaded.file_kinds


def test_ffmpeg_keeps_animated_gif_with_video():
    """`convert_video` makes GIFs, so "make a gif of clip.mp4 then resize it" must thread."""
    kinds = Skill.load(REPO / "skills" / "ffmpeg").file_kinds
    assert kinds["gif"] == kinds["mp4"] == "video"
    assert kinds["jpg"] != kinds["mp4"]
