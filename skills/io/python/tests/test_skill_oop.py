"""OOP-interface tests for the io skill (Phase 3).

These tests verify the new Step/IoSkill implementation.
They are RED until skill.yaml gains skill_class: and handlers.py has IoSkill.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill
from knaif.skill_base import Skill as SkillBase
from knaif.tool import Step

IO_SKILL_DIR = Path(__file__).parents[2]


# ── Skill.load OOP path ───────────────────────────────────────────────────────


def test_io_skill_loads_via_oop_path():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.tool_map is not None, "Expected OOP path (skill_class: in skill.yaml)"


def test_io_skill_tool_map_has_domain_tools():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.tool_map is not None
    for name in ("list_files", "find_files", "delete_files", "move_files"):
        assert name in skill.tool_map, f"{name!r} missing from tool_map"


def test_io_skill_tool_map_has_core_tools():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.tool_map is not None
    for name in ("clarify", "reject", "done", "wait_for_confirmation"):
        assert name in skill.tool_map, f"core tool {name!r} missing"


def test_io_skill_tool_map_values_are_steps():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.tool_map is not None
    for name, tool in skill.tool_map.items():
        assert isinstance(tool, Step), f"tool_map[{name!r}] is not a Step"


def test_io_skill_instance_is_skill_base():
    skill = Skill.load(IO_SKILL_DIR)
    assert isinstance(skill.skill_instance, SkillBase)


def test_io_skill_name_from_manifest():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.name == "io"


def test_io_skill_tools_yaml_path_exists():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.tools_yaml_path.exists()


# ── CommandAgent.from_skill + execute_plan ────────────────────────────────────


def test_from_skill_creates_agent(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    assert isinstance(agent, CommandAgent)
    assert agent.tool_map is not None


def test_from_skill_registry_has_io_tools(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    for name in ("list_files", "find_files", "delete_files", "move_files"):
        assert name in agent.registry


def test_from_skill_tool_map_has_domain_tools(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    assert agent.tool_map is not None
    for name in ("list_files", "find_files"):
        assert name in agent.tool_map


def test_from_skill_system_header_applied(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    assert agent.system_header is not None
    system, _ = agent.build_prompt("list files")
    assert agent.system_header.strip()[:20] in system


def test_from_skill_arg_value_sets_propagated(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    assert "file_type" in agent.arg_value_sets
    assert "text" in agent.arg_value_sets["file_type"]


def test_from_skill_sandbox_resolves(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    assert agent.sandbox == sandbox.resolve()


def test_execute_list_files(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    results = agent.execute_plan(
        {"plan": [{"tool": "list_files", "args": {"path": str(sandbox / "reports")}}]},
        dry_run=True,
    )
    assert results[0]["result"]["count"] == 2


def test_execute_find_files_recursive(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    results = agent.execute_plan(
        {"plan": [{"tool": "find_files", "args": {"path": str(sandbox)}}]},
        dry_run=True,
    )
    assert results[0]["result"]["count"] >= 4


def test_execute_delete_dry_run(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    results = agent.execute_plan(
        {
            "plan": [
                {"tool": "delete_files", "args": {"path": str(sandbox / "tmp"), "pattern": "*.tmp"}}
            ]
        },
        dry_run=True,
    )
    assert results[0]["result"]["mode"] == "dry_run"
    assert (sandbox / "tmp" / "cache.tmp").exists()


def test_execute_move_files_dry_run(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    dst = str(sandbox / "archive")
    results = agent.execute_plan(
        {
            "plan": [
                {
                    "tool": "move_files",
                    "args": {"src": str(sandbox / "tmp"), "dst": dst, "pattern": "*.log"},
                }
            ]
        },
        dry_run=True,
    )
    assert results[0]["result"]["mode"] == "dry_run"


def test_destructive_requires_confirmation(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    with pytest.raises(ValueError, match="confirmed=True"):
        agent.execute_plan(
            {"plan": [{"tool": "delete_files", "args": {"path": str(sandbox / "tmp")}}]},
            dry_run=False,
            confirmed=False,
        )


def test_invalid_file_type_rejected(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    with pytest.raises(ValueError):
        agent.execute_plan(
            {
                "plan": [
                    {
                        "tool": "list_files",
                        "args": {"path": str(sandbox), "file_type": "unknown_type"},
                    }
                ]
            },
            dry_run=True,
        )
