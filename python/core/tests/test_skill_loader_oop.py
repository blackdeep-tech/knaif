"""Tests for the new OOP loader path (skill_class: in skill.yaml).

These use real temp skill directories to exercise the actual file-based loader,
since inline tool classes can't exercise the module-relative import logic.
"""

from __future__ import annotations

import textwrap

import pytest

from knaif.skill import Skill

# ── helpers ───────────────────────────────────────────────────────────────────


def _write_oop_skill(
    skill_dir,
    *,
    tools_block: str = "",
    handlers_block: str = "",
    skill_class: str = "handlers.FooSkill",
    extra_manifest: str = "",
) -> None:
    """Write a minimal OOP skill (skill_class: present) to skill_dir."""
    skill_dir.mkdir(parents=True, exist_ok=True)

    (skill_dir / "skill.yaml").write_text(
        textwrap.dedent(f"""\
            name: foo_skill
            description: "test oop skill"
            skill_class: {skill_class}
            {extra_manifest}
            """),
        encoding="utf-8",
    )

    default_tools = textwrap.dedent("""\
        do_thing:
          description: "Do a thing."
          required_args: []
          safety_category: safe
        """)
    (skill_dir / "tools.yaml").write_text(tools_block or default_tools, encoding="utf-8")

    default_handlers = textwrap.dedent("""\
        from knaif.tool import Step
        from knaif.skill_base import Skill as SkillBase

        class DoThingStep(Step):
            name = "do_thing"
            def handle(self, args, ctx):
                return {"did": True}

        class FooSkill(SkillBase):
            tools = [DoThingStep]
        """)
    (skill_dir / "handlers.py").write_text(handlers_block or default_handlers, encoding="utf-8")


def _write_skill_without_skill_class(skill_dir) -> None:
    """Write a skill.yaml missing skill_class: (the unsupported legacy form)."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        textwrap.dedent("""\
            name: legacy_skill
            description: "no skill_class"
            """),
        encoding="utf-8",
    )
    (skill_dir / "tools.yaml").write_text(
        textwrap.dedent("""\
            old_tool:
              description: "old"
              required_args: []
              safety_category: safe
            """),
        encoding="utf-8",
    )


# ── OOP path: basic load ──────────────────────────────────────────────────────


def test_oop_skill_loads_tool_map(tmp_path):
    _write_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")

    assert skill.tool_map is not None
    assert "do_thing" in skill.tool_map
    # Core tools are also merged in
    assert "clarify" in skill.tool_map
    assert "reject" in skill.tool_map
    assert "done" in skill.tool_map
    assert "wait_for_confirmation" in skill.tool_map


def test_oop_skill_tool_map_values_callable_via_handle(tmp_path):
    from unittest.mock import MagicMock

    _write_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")

    ctx = MagicMock()
    result = skill.tool_map["do_thing"].handle({}, ctx)
    assert result == {"did": True}


def test_oop_skill_has_skill_instance(tmp_path):
    from knaif.skill_base import Skill as SkillBase

    _write_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")

    assert skill.skill_instance is not None
    assert isinstance(skill.skill_instance, SkillBase)


def test_oop_skill_name_from_manifest(tmp_path):
    _write_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")
    assert skill.name == "foo_skill"


# ── skill_class is required (legacy HANDLERS form removed) ───────────────────


def test_skill_without_skill_class_raises(tmp_path):
    _write_skill_without_skill_class(tmp_path / "leg")
    with pytest.raises(ValueError, match="must declare 'skill_class:'"):
        Skill.load(tmp_path / "leg")


# ── OOP path: fail-fast validation ────────────────────────────────────────────


def test_oop_loader_rejects_non_step_intent_class(tmp_path):
    bad_handlers = textwrap.dedent("""\
        from knaif.skill_base import Skill as SkillBase

        class NotAStep:
            name = "do_thing"

        class FooSkill(SkillBase):
            tools = [NotAStep]
        """)
    _write_oop_skill(tmp_path / "bad", handlers_block=bad_handlers)

    with pytest.raises(TypeError, match="not a Step or Intent subclass"):
        Skill.load(tmp_path / "bad")


def test_oop_loader_rejects_tool_missing_from_yaml(tmp_path):
    handlers_with_extra = textwrap.dedent("""\
        from knaif.tool import Step
        from knaif.skill_base import Skill as SkillBase

        class DoThingStep(Step):
            name = "do_thing"
            def handle(self, args, ctx): return {}

        class GhostStep(Step):
            name = "ghost"
            def handle(self, args, ctx): return {}

        class FooSkill(SkillBase):
            tools = [DoThingStep, GhostStep]
        """)
    _write_oop_skill(tmp_path / "bad", handlers_block=handlers_with_extra)

    with pytest.raises(ValueError, match="no metadata"):
        Skill.load(tmp_path / "bad")


def test_oop_loader_rejects_duplicate_tool_name(tmp_path):
    dup_handlers = textwrap.dedent("""\
        from knaif.tool import Step
        from knaif.skill_base import Skill as SkillBase

        class DoThingStep(Step):
            name = "do_thing"
            def handle(self, args, ctx): return {}

        class DoThingStep2(Step):
            name = "do_thing"
            def handle(self, args, ctx): return {}

        class FooSkill(SkillBase):
            tools = [DoThingStep, DoThingStep2]
        """)
    _write_oop_skill(tmp_path / "bad", handlers_block=dup_handlers)

    with pytest.raises(ValueError, match="Duplicate tool"):
        Skill.load(tmp_path / "bad")


# ── OOP path: module-relative class resolution ───────────────────────────────


def test_oop_loader_resolves_bare_class_name_from_handlers(tmp_path):
    """Bare 'FooSkill' (no dot) defaults to handlers.py."""
    _write_oop_skill(tmp_path / "foo", skill_class="FooSkill")
    skill = Skill.load(tmp_path / "foo")
    assert skill.tool_map is not None


def test_oop_loader_resolves_dotted_class_name(tmp_path):
    """'handlers.FooSkill' is the explicit form."""
    _write_oop_skill(tmp_path / "foo", skill_class="handlers.FooSkill")
    skill = Skill.load(tmp_path / "foo")
    assert skill.tool_map is not None


# ── OOP path: derived compatibility views (handlers/expanders/summarizers) ────
#
# Until Phase 5 removes the legacy dict surface, the OOP loader exposes the same
# accessors derived from tool_map so the existing skill test suites keep working
# and external consumers that read skill.result_formatter / skill.artifact_runner
# (e.g. evalsuite/cli.py) get the skill-instance methods.

_RICH_TOOLS = textwrap.dedent("""\
    do_thing:
      description: "Do a thing."
      required_args: []
      safety_category: safe
    make_thing:
      description: "Expand into a thing."
      required_args: []
      safety_category: safe
    """)

_RICH_HANDLERS = textwrap.dedent("""\
    from pathlib import Path
    from knaif.tool import Step, Intent
    from knaif.skill_base import Skill as SkillBase

    class DoThingStep(Step):
        name = "do_thing"
        def handle(self, args, ctx):
            return {"did": True}

    class MakeThingIntent(Intent):
        name = "make_thing"
        def expand(self, args):
            return [{"tool": "do_thing", "args": {}}]
        def summarize(self, args, **kw):
            # Behaviour must depend on skill_dir presence (mirrors ffmpeg).
            if kw.get("skill_dir"):
                return "make a thing (with profile)"
            return "make a thing"

    class FooSkill(SkillBase):
        tools = [DoThingStep, MakeThingIntent]

        def format_results(self, results, *, dry_run):
            return [{"summary": "formatted"}]

        def run_artifact(self, cmd, fixture, out_dir):
            return Path(out_dir) / "artifact.out"
    """)


def _write_rich_oop_skill(skill_dir) -> None:
    _write_oop_skill(
        skill_dir,
        tools_block=_RICH_TOOLS,
        handlers_block=_RICH_HANDLERS,
    )


def test_oop_loader_populates_handlers_view(tmp_path):
    _write_rich_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")
    # Domain Step appears in the handlers view; core steps are NOT (merged later).
    assert "do_thing" in skill.handlers
    assert "clarify" not in skill.handlers


def test_oop_loader_populates_expanders_view(tmp_path):
    _write_rich_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")
    assert "make_thing" in skill.expanders
    plan = skill.expanders["make_thing"]({})
    assert plan == [{"tool": "do_thing", "args": {}}]


def test_oop_loader_populates_summarizers_view(tmp_path):
    _write_rich_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")
    assert "make_thing" in skill.summarizers
    assert skill.summarizers["make_thing"]({}) == "make a thing"


def test_oop_summarizer_view_forwards_skill_dir(tmp_path):
    """summarize must receive skill_dir kwarg (regression: adapter dropped it)."""
    _write_rich_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")
    fn = skill.summarizers["make_thing"]
    assert fn({}, skill_dir=tmp_path) == "make a thing (with profile)"


def test_oop_loader_wires_result_formatter_when_overridden(tmp_path):
    _write_rich_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")
    assert skill.result_formatter is not None
    assert skill.result_formatter([], dry_run=True) == [{"summary": "formatted"}]


def test_oop_loader_wires_artifact_runner_when_overridden(tmp_path):
    _write_rich_oop_skill(tmp_path / "foo")
    skill = Skill.load(tmp_path / "foo")
    assert skill.artifact_runner is not None


def test_oop_loader_leaves_formatter_none_when_not_overridden(tmp_path):
    """A skill that does not override format_results keeps result_formatter None
    (so the CLI falls back to its generic rendering, as io does)."""
    _write_oop_skill(tmp_path / "plain")  # default FooSkill, no overrides
    skill = Skill.load(tmp_path / "plain")
    assert skill.result_formatter is None
    assert skill.artifact_runner is None
