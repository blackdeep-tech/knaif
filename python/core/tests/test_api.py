"""Tests for the public create_agent / list_skills API."""

import pytest

from knaif import CommandAgent, create_agent, list_skills


def test_list_skills_returns_builtin_skills():
    skills = list_skills()
    assert "ffmpeg" in skills
    assert "documents" in skills
    assert skills == sorted(skills)


def test_list_skills_excludes_stale_by_default():
    # io is marked `status: stale` in its skill.yaml; it must not appear by default.
    assert "io" not in list_skills()
    assert "io" in list_skills(include_stale=True)


def test_list_skills_stale_filter_with_custom_root(tmp_path):
    (tmp_path / "active").mkdir()
    (tmp_path / "active" / "skill.yaml").write_text("name: active\n")
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "skill.yaml").write_text("name: old\nstatus: stale\n")

    assert list_skills(tmp_path) == ["active"]
    assert list_skills(tmp_path, include_stale=True) == ["active", "old"]


def test_list_skills_custom_root(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "skill.yaml").write_text("name: alpha\n")
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "skill.yaml").write_text("name: beta\n")
    (tmp_path / "not_a_skill").mkdir()  # no skill.yaml — should be excluded

    result = list_skills(tmp_path)
    assert result == ["alpha", "beta"]


def test_list_skills_missing_root_returns_empty(tmp_path):
    assert list_skills(tmp_path / "nonexistent") == []


def test_create_agent_io_skill(tmp_path):
    agent = create_agent("io", sandbox=tmp_path)
    assert isinstance(agent, CommandAgent)
    assert "list_files" in agent.registry
    assert "find_files" in agent.registry


def test_create_agent_ffmpeg_skill(tmp_path):
    agent = create_agent("ffmpeg", sandbox=tmp_path)
    assert isinstance(agent, CommandAgent)
    assert len(agent.registry) > 0


def test_create_agent_unknown_raises_with_hint(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        create_agent("nonexistent_skill", sandbox=tmp_path)


def test_create_agent_unknown_lists_available(tmp_path):
    with pytest.raises(ValueError, match="io"):
        create_agent("nonexistent_skill", sandbox=tmp_path)


def test_create_agent_custom_skills_root(tmp_path):
    skill_dir = tmp_path / "myskill"

    # Copy the io skill bundle (YAML at the top + python/ handlers) as a minimal stand-in
    import shutil
    from pathlib import Path as P

    io_src = P("skills") / "io"
    shutil.copytree(io_src, skill_dir)

    agent = create_agent("myskill", sandbox=tmp_path, skills_root=tmp_path)
    assert isinstance(agent, CommandAgent)


# ── create_agent forwards plan-preview / approval params ─────────────────────


def test_create_agent_defaults_have_flags_off(tmp_path):
    agent = create_agent("io", sandbox=tmp_path)
    assert agent.show_plan is False
    assert agent.require_approval is False
    assert agent.plan_display is None
    assert agent.plan_confirmer is None
    assert agent.last_plan_summary == ""


def test_create_agent_forwards_show_plan(tmp_path):
    agent = create_agent("io", sandbox=tmp_path, show_plan=True)
    assert agent.show_plan is True


def test_create_agent_forwards_require_approval(tmp_path):
    agent = create_agent("io", sandbox=tmp_path, require_approval=True)
    assert agent.require_approval is True


def test_create_agent_forwards_plan_display(tmp_path):
    fn = lambda _s: None  # noqa: E731
    agent = create_agent("io", sandbox=tmp_path, plan_display=fn)
    assert agent.plan_display is fn


def test_create_agent_forwards_plan_confirmer(tmp_path):
    fn = lambda _s: True  # noqa: E731
    agent = create_agent("io", sandbox=tmp_path, plan_confirmer=fn)
    assert agent.plan_confirmer is fn


def test_create_agent_ffmpeg_loads_summarizers(tmp_path):
    agent = create_agent("ffmpeg", sandbox=tmp_path)
    # ffmpeg skill registers SUMMARIZERS — verify they propagate through
    assert "convert_video" in agent.summarizers
    assert callable(agent.summarizers["convert_video"])
