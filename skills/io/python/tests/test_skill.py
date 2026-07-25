"""Tests for Skill loading and CommandAgent.from_skill."""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill

IO_SKILL_DIR = Path(__file__).parents[2]


# ── Skill.load ────────────────────────────────────────────────────────────────


def test_skill_load_reads_name():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.name == "io"


def test_skill_load_reads_description():
    skill = Skill.load(IO_SKILL_DIR)
    assert "file" in skill.description.lower()


def test_skill_load_tools_yaml_path_exists():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.tools_yaml_path.exists()


def test_skill_load_tool_map_contains_domain_tools():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.tool_map is not None
    for name in ("list_files", "find_files", "delete_files", "move_files"):
        assert name in skill.tool_map


def test_skill_load_tool_map_includes_core_tools():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.tool_map is not None
    for name in ("clarify", "reject", "done"):
        assert name in skill.tool_map


def test_skill_load_system_header_is_string():
    skill = Skill.load(IO_SKILL_DIR)
    assert isinstance(skill.system_header, str)
    assert len(skill.system_header) > 0


def test_skill_load_arg_value_sets_contains_file_type():
    skill = Skill.load(IO_SKILL_DIR)
    assert "file_type" in skill.arg_value_sets
    assert "text" in skill.arg_value_sets["file_type"]
    assert "log" in skill.arg_value_sets["file_type"]


def test_skill_load_skill_dir_is_resolved():
    skill = Skill.load(IO_SKILL_DIR)
    assert skill.skill_dir == IO_SKILL_DIR.resolve()


# ── CommandAgent.from_skill ───────────────────────────────────────────────────


def test_from_skill_creates_agent(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    assert isinstance(agent, CommandAgent)


def test_from_skill_registry_has_io_tools(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    assert "list_files" in agent.registry
    assert "find_files" in agent.registry
    assert "delete_files" in agent.registry
    assert "move_files" in agent.registry


def test_from_skill_tool_map_includes_domain_and_core(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    assert agent.tool_map is not None
    for name in ("list_files", "clarify", "reject"):
        assert name in agent.tool_map


def test_from_skill_system_header_is_applied(sandbox):
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


# ── from_skill functional: execute_plan ──────────────────────────────────────


def test_from_skill_execute_list_files(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    payload = {"plan": [{"tool": "list_files", "args": {"path": str(sandbox / "reports")}}]}
    results = agent.execute_plan(payload, dry_run=True)
    assert results[0]["result"]["count"] == 2


def test_from_skill_execute_delete_dry_run(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    payload = {
        "plan": [
            {"tool": "delete_files", "args": {"path": str(sandbox / "tmp"), "pattern": "*.tmp"}}
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    assert results[0]["result"]["mode"] == "dry_run"
    assert (sandbox / "tmp" / "cache.tmp").exists()


def test_from_skill_destructive_requires_confirmation(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    payload = {"plan": [{"tool": "delete_files", "args": {"path": str(sandbox / "tmp")}}]}
    with pytest.raises(ValueError, match="confirmed=True"):
        agent.execute_plan(payload, dry_run=False, confirmed=False)


def test_from_skill_invalid_file_type_rejected(sandbox):
    agent = CommandAgent.from_skill(IO_SKILL_DIR, sandbox=sandbox)
    payload = {
        "plan": [
            {"tool": "list_files", "args": {"path": str(sandbox), "file_type": "unknown_type"}}
        ]
    }
    with pytest.raises(ValueError):
        agent.execute_plan(payload, dry_run=True)


# ── data files smoke test ─────────────────────────────────────────────────────


def test_io_skill_train_jsonl_is_valid():
    import json

    train_path = IO_SKILL_DIR / "data" / "train.jsonl"
    assert train_path.exists()
    for line in train_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            assert "utterance" in row
            assert "plan" in row


def test_io_skill_safety_test_jsonl_is_valid():
    import json

    safety_path = IO_SKILL_DIR / "data" / "safety_test.jsonl"
    assert safety_path.exists()
    for line in safety_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            assert "utterance" in row
            assert "plan" in row
