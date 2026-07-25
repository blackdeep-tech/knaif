"""Tests for OOP-path dispatch in CommandAgent.

Uses inline tool classes and a minimal skill dir to verify that when
tool_map is populated the agent routes through tool.handle / tool.expand /
tool.summarize and the preflight precedence rules.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from knaif.agent import CommandAgent

# ── shared fixtures ───────────────────────────────────────────────────────────

TOOLS_YAML = textwrap.dedent("""\
    do_thing:
      description: "Do a thing."
      required_args: []
      safety_category: safe
    expand_thing:
      description: "Expand into sub-steps."
      required_args: []
      safety_category: safe
    """)

SKILL_YAML_TEMPLATE = textwrap.dedent("""\
    name: test_oop
    description: "OOP dispatch test skill"
    skill_class: handlers.TestOopSkill
    """)


def _make_oop_skill_dir(tmp_path: Path, handlers_body: str) -> Path:
    d = tmp_path / "oop_skill"
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.yaml").write_text(SKILL_YAML_TEMPLATE, encoding="utf-8")
    (d / "tools.yaml").write_text(TOOLS_YAML, encoding="utf-8")
    (d / "handlers.py").write_text(handlers_body, encoding="utf-8")
    return d


# ── Step dispatch ─────────────────────────────────────────────────────────────


def test_from_skill_oop_sets_tool_map(tmp_path):
    body = textwrap.dedent("""\
        from knaif.tool import Step
        from knaif.skill_base import Skill as SkillBase

        class DoThingStep(Step):
            name = "do_thing"
            def handle(self, args, ctx): return {"did": True}

        class TestOopSkill(SkillBase):
            tools = [DoThingStep]
        """)
    skill_dir = _make_oop_skill_dir(tmp_path, body)
    agent = CommandAgent.from_skill(skill_dir, sandbox=tmp_path / "sb")
    assert agent.tool_map is not None
    assert "do_thing" in agent.tool_map


def test_from_skill_oop_dispatches_via_handle(tmp_path):
    body = textwrap.dedent("""\
        from knaif.tool import Step
        from knaif.skill_base import Skill as SkillBase

        class DoThingStep(Step):
            name = "do_thing"
            def handle(self, args, ctx): return {"did": True}

        class TestOopSkill(SkillBase):
            tools = [DoThingStep]
        """)
    skill_dir = _make_oop_skill_dir(tmp_path, body)
    agent = CommandAgent.from_skill(skill_dir, sandbox=tmp_path / "sb")
    results = agent.execute_plan(
        {"plan": [{"tool": "do_thing", "args": {}}]},
        dry_run=True,
    )
    assert results[0]["result"] == {"did": True}


# ── Intent dispatch (expansion) ───────────────────────────────────────────────


def test_from_skill_oop_expands_intent(tmp_path):
    body = textwrap.dedent("""\
        from knaif.tool import Step, Intent
        from knaif.skill_base import Skill as SkillBase

        class DoThingStep(Step):
            name = "do_thing"
            def handle(self, args, ctx): return {"did": True}

        class ExpandThingIntent(Intent):
            name = "expand_thing"
            def expand(self, args):
                return [{"tool": "do_thing", "args": {}}]

        class TestOopSkill(SkillBase):
            tools = [DoThingStep, ExpandThingIntent]
        """)
    skill_dir = _make_oop_skill_dir(tmp_path, body)
    agent = CommandAgent.from_skill(skill_dir, sandbox=tmp_path / "sb")
    results = agent.execute_plan(
        {"plan": [{"tool": "expand_thing", "args": {}}]},
        dry_run=True,
    )
    # The intent expanded to do_thing and it ran
    assert any(r["tool"] == "do_thing" for r in results)


def test_from_skill_oop_intent_summarize(tmp_path):
    body = textwrap.dedent("""\
        from knaif.tool import Step, Intent
        from knaif.skill_base import Skill as SkillBase

        class DoThingStep(Step):
            name = "do_thing"
            def handle(self, args, ctx): return {"did": True}

        class ExpandThingIntent(Intent):
            name = "expand_thing"
            def expand(self, args): return [{"tool": "do_thing", "args": {}}]
            def summarize(self, args): return "expand the thing nicely"

        class TestOopSkill(SkillBase):
            tools = [DoThingStep, ExpandThingIntent]
        """)
    skill_dir = _make_oop_skill_dir(tmp_path, body)
    agent = CommandAgent.from_skill(skill_dir, sandbox=tmp_path / "sb")
    # show_plan=True forces summarize to run
    captured = []
    agent.plan_display = captured.append
    agent.show_plan = True
    agent.execute_plan(
        {"plan": [{"tool": "expand_thing", "args": {}}]},
        dry_run=True,
    )
    assert any("expand the thing nicely" in str(c) for c in captured)


# ── Preflight dispatch ────────────────────────────────────────────────────────


def test_oop_dispatch_tool_preflight_override_takes_precedence(tmp_path):
    body = textwrap.dedent("""\
        from knaif.tool import Step
        from knaif.skill_base import Skill as SkillBase

        class DoThingStep(Step):
            name = "do_thing"
            def handle(self, args, ctx): return {"did": True}
            def preflight(self, args, **kw): return ["tool_preflight_ran"]

        class TestOopSkill(SkillBase):
            tools = [DoThingStep]
            def preflight(self, tool, args, **kw): return ["skill_preflight_ran"]
        """)
    skill_dir = _make_oop_skill_dir(tmp_path, body)
    agent = CommandAgent.from_skill(skill_dir, sandbox=tmp_path / "sb")
    results = agent.execute_plan(
        {"plan": [{"tool": "do_thing", "args": {}}]},
        dry_run=False,
    )
    # preflight raised "tool_preflight_ran" → clarify result
    assert results[0]["tool"] == "clarify"
    assert "tool_preflight_ran" in results[0]["result"]["question"]


def test_oop_dispatch_skill_preflight_used_when_tool_does_not_override(tmp_path):
    body = textwrap.dedent("""\
        from knaif.tool import Step
        from knaif.skill_base import Skill as SkillBase

        class DoThingStep(Step):
            name = "do_thing"
            def handle(self, args, ctx): return {"did": True}
            # No preflight override → skill-level preflight should run

        class TestOopSkill(SkillBase):
            tools = [DoThingStep]
            def preflight(self, tool, args, **kw): return ["skill_preflight_ran"]
        """)
    skill_dir = _make_oop_skill_dir(tmp_path, body)
    agent = CommandAgent.from_skill(skill_dir, sandbox=tmp_path / "sb")
    results = agent.execute_plan(
        {"plan": [{"tool": "do_thing", "args": {}}]},
        dry_run=False,
    )
    assert results[0]["tool"] == "clarify"
    assert "skill_preflight_ran" in results[0]["result"]["question"]
