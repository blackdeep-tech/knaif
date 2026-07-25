"""Skill.load(overrides=[...]) deep-merges app-owned YAML deltas over a base skill.yaml.

Apps that need platform-specific tweaks supply small override files instead of forking a
bundle. Deep-merge adds/replaces; a null value removes a key; a top-level `disabled_tools:`
list subtracts tools from the map. Overrides may only tune/disable — handler code always
comes from the bundle's python/ package, so a delta can never inject executable behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from knaif.skill import Skill

DOCUMENTS = Path("skills/documents")


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "override.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_scalar_override_replaces(tmp_path):
    base = Skill.load(DOCUMENTS)
    assert base.recommended_model == "knaif-qwen3-4b-v1"
    ov = _write(tmp_path, {"recommended_model": "mobile-lane-q6"})
    skill = Skill.load(DOCUMENTS, overrides=[ov])
    assert skill.recommended_model == "mobile-lane-q6"


def test_null_value_removes_key(tmp_path):
    ov = _write(tmp_path, {"recommended_model": None})
    skill = Skill.load(DOCUMENTS, overrides=[ov])
    assert skill.recommended_model is None


def test_deep_merge_keeps_sibling_keys(tmp_path):
    # Replacing one arg_value_set must not drop the others (dicts merge, lists replace).
    ov = _write(tmp_path, {"arg_value_sets": {"compress_quality": ["tiny"]}})
    skill = Skill.load(DOCUMENTS, overrides=[ov])
    assert skill.arg_value_sets["compress_quality"] == frozenset({"tiny"})
    assert "to_format" in skill.arg_value_sets  # sibling preserved
    assert "pdf" in skill.arg_value_sets["to_format"]


def test_disabled_tools_subtracts_from_map(tmp_path):
    base = Skill.load(DOCUMENTS)
    domain_tools = [
        n for n in base.tool_map if n not in {"clarify", "reject", "done", "wait_for_confirmation"}
    ]
    victim = domain_tools[0]
    ov = _write(tmp_path, {"disabled_tools": [victim]})
    skill = Skill.load(DOCUMENTS, overrides=[ov])
    assert victim not in skill.tool_map
    assert victim not in skill.handlers and victim not in skill.expanders
    # untouched tools remain
    assert len(skill.tool_map) == len(base.tool_map) - 1


def test_disabling_core_tool_is_rejected(tmp_path):
    ov = _write(tmp_path, {"disabled_tools": ["clarify"]})
    with pytest.raises(ValueError, match="core"):
        Skill.load(DOCUMENTS, overrides=[ov])


def test_disabling_unknown_tool_is_rejected(tmp_path):
    ov = _write(tmp_path, {"disabled_tools": ["no_such_tool"]})
    with pytest.raises(ValueError, match="unknown|not found|no_such_tool"):
        Skill.load(DOCUMENTS, overrides=[ov])


def test_multiple_overrides_apply_in_order(tmp_path):
    ov1 = tmp_path / "a.yaml"
    ov1.write_text(yaml.safe_dump({"recommended_model": "first"}), encoding="utf-8")
    ov2 = tmp_path / "b.yaml"
    ov2.write_text(yaml.safe_dump({"recommended_model": "second"}), encoding="utf-8")
    skill = Skill.load(DOCUMENTS, overrides=[ov1, ov2])
    assert skill.recommended_model == "second"


def test_no_overrides_is_unchanged():
    a = Skill.load(DOCUMENTS)
    b = Skill.load(DOCUMENTS, overrides=[])
    assert a.recommended_model == b.recommended_model
    assert set(a.tool_map) == set(b.tool_map)
