"""Tests for knaif.skill_base — Skill base class for skill authors."""

from __future__ import annotations

from knaif.skill_base import Skill
from knaif.tool import Intent, Step


class _DummyStep(Step):
    name = "dummy_step"

    def handle(self, args, ctx):
        return {"ok": True}


class _DummyIntent(Intent):
    name = "dummy_intent"

    def expand(self, args):
        return [{"tool": "dummy_step", "args": {}}]


# ── Skill base defaults ────────────────────────────────────────────────────────


def test_skill_base_default_tools_is_empty():
    class MinimalSkill(Skill):
        pass

    assert MinimalSkill.tools == []


def test_skill_base_preflight_returns_empty():
    s = Skill()
    assert s.preflight("any_tool", {}) == []


def test_skill_base_format_results_is_callable():
    s = Skill()
    # Should not raise; returns None by default
    assert s.format_results([], dry_run=False) is None


def test_skill_base_run_artifact_is_callable():
    s = Skill()
    # Should not raise; returns None by default
    assert s.run_artifact(None, None, None) is None


def test_skill_subclass_declares_tools():
    class MySkill(Skill):
        tools = [_DummyStep, _DummyIntent]

    assert MySkill.tools == [_DummyStep, _DummyIntent]


def test_skill_subclass_overrides_preflight():
    class MySkill(Skill):
        def preflight(self, tool, args, **kw):
            return ["missing_input"]

    assert MySkill().preflight("encode", {}) == ["missing_input"]


def test_skill_subclass_overrides_format_results():
    recorded = []

    class MySkill(Skill):
        def format_results(self, results, *, dry_run):
            recorded.append((results, dry_run))

    MySkill().format_results([{"tool": "t", "result": {}}], dry_run=True)
    assert recorded == [([{"tool": "t", "result": {}}], True)]
