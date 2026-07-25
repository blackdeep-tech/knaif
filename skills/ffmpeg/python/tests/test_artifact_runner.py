"""Tests for the ffmpeg skill ARTIFACT_RUNNER hook."""

from __future__ import annotations

from pathlib import Path

from knaif.skill import Skill

SKILL_DIR = Path(__file__).parents[2]


def test_artifact_runner_registered_on_skill():
    """The ffmpeg skill must export an ARTIFACT_RUNNER that is callable."""
    skill = Skill.load(SKILL_DIR)
    assert skill.artifact_runner is not None
    assert callable(skill.artifact_runner)


def test_artifact_runner_returns_none_for_non_ffmpeg_command(tmp_path: Path):
    """Passing a non-ffmpeg command returns None without invoking subprocess."""
    skill = Skill.load(SKILL_DIR)
    runner = skill.artifact_runner
    result = runner("echo hello", tmp_path / "fixture.mp4", tmp_path / "out")
    assert result is None
