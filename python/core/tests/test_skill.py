"""Tests for knaif.skill — Skill.load() summarizer views derived from Intents."""

from __future__ import annotations

import textwrap

from knaif.skill import Skill


def _write_minimal_skill(skill_dir, *, with_intent: bool = False) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        textwrap.dedent("""\
            name: test_skill
            description: "test"
            skill_class: handlers.TestSkill
            """),
        encoding="utf-8",
    )
    tools = textwrap.dedent("""\
        do_thing:
          description: "Do a thing."
          required_args: []
          safety_category: safe
        """)
    if with_intent:
        tools += textwrap.dedent("""\
            make_thing:
              description: "Make a thing."
              required_args: []
              safety_category: safe
            """)
    (skill_dir / "tools.yaml").write_text(tools, encoding="utf-8")

    intent_cls = textwrap.dedent("""\
            class MakeThingIntent(Intent):
                name = "make_thing"
                def expand(self, args):
                    return [{"tool": "do_thing", "args": {}}]
                def summarize(self, args, **kw):
                    return "do the thing"
            """) if with_intent else ""
    tools_list = "[DoThingStep, MakeThingIntent]" if with_intent else "[DoThingStep]"
    (skill_dir / "handlers.py").write_text(
        textwrap.dedent("""\
            from knaif.skill_base import Skill
            from knaif.tool import Intent, Step

            class DoThingStep(Step):
                name = "do_thing"
                def handle(self, args, ctx):
                    return {"ok": True}

        """) + intent_cls + f"\nclass TestSkill(Skill):\n    tools = {tools_list}\n",
        encoding="utf-8",
    )


def test_skill_load_step_only_has_no_summarizers(tmp_path):
    _write_minimal_skill(tmp_path / "s1")
    skill = Skill.load(tmp_path / "s1")
    assert skill.summarizers == {}
    assert "do_thing" in skill.handlers


def test_skill_load_intent_summarizer_is_derived(tmp_path):
    _write_minimal_skill(tmp_path / "s2", with_intent=True)
    skill = Skill.load(tmp_path / "s2")
    assert "make_thing" in skill.summarizers
    assert skill.summarizers["make_thing"]({}) == "do the thing"
