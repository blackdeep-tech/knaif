"""Tests for package-style skill loading (skill dir with an ``__init__.py``).

These exercise the loader contract from
``docs/plans/2026-06-26-skill-package-loader.md``:

1. Handlers can be split across modules via relative imports.
2. ``__init__.py`` is a true entry point — it is executed.
3. The legacy ``_skill_oop_<leaf>_<stem>`` key is registered as an *alias*
   (same object) of the canonical package's ``handlers`` submodule.
4. Popping the legacy alias forces a genuine reload (no stale patched globals).
5. Two package skills with the *same leaf dir name* under different parents get
   distinct package modules (path-unique canonical key).

Tests introspect ``type(skill.skill_instance).__module__`` / ``__package__``
instead of hard-coding the canonical-key scheme, so they assert the *contract*
not the implementation detail.
"""

from __future__ import annotations

import sys
import textwrap

from knaif.skill import Skill

# ── fixture writers ───────────────────────────────────────────────────────────

_TOOLS = textwrap.dedent("""\
    do_thing:
      description: "Do a thing."
      required_args: []
      safety_category: safe
    """)


def _write_package_skill(
    skill_dir,
    *,
    init_body: str,
    handlers_body: str,
    extra_files: dict[str, str] | None = None,
) -> None:
    """Write a package-style OOP skill (skill dir contains ``__init__.py``)."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        textwrap.dedent("""\
            name: pkg_skill
            description: "package-style skill"
            skill_class: handlers.FooSkill
            """),
        encoding="utf-8",
    )
    (skill_dir / "tools.yaml").write_text(_TOOLS, encoding="utf-8")
    (skill_dir / "__init__.py").write_text(textwrap.dedent(init_body), encoding="utf-8")
    (skill_dir / "handlers.py").write_text(textwrap.dedent(handlers_body), encoding="utf-8")
    for fname, body in (extra_files or {}).items():
        (skill_dir / fname).write_text(textwrap.dedent(body), encoding="utf-8")


_PLAIN_INIT = """\
    from .handlers import FooSkill
    __all__ = ["FooSkill"]
    """

_PLAIN_HANDLERS = """\
    from knaif.tool import Step
    from knaif.skill_base import Skill as SkillBase

    class DoThingStep(Step):
        name = "do_thing"
        def handle(self, args, ctx):
            return {"did": True}

    class FooSkill(SkillBase):
        tools = [DoThingStep]
    """


def _handlers_modname(skill) -> str:
    """The sys.modules name of the loaded handlers module for a skill."""
    return type(skill.skill_instance).__module__


def _package_module(skill):
    """The loaded package module object that owns the handlers submodule."""
    pkg_name = _handlers_modname(skill).rsplit(".", 1)[0]
    return sys.modules[pkg_name]


# ── 1. relative import across submodules ──────────────────────────────────────


def test_package_skill_allows_relative_import(tmp_path):
    """A handler module doing ``from ._recipe import VALUE`` resolves."""
    _write_package_skill(
        tmp_path / "foo",
        init_body=_PLAIN_INIT,
        handlers_body="""\
            from knaif.tool import Step
            from knaif.skill_base import Skill as SkillBase
            from ._recipe import RECIPE_VALUE

            class DoThingStep(Step):
                name = "do_thing"
                def handle(self, args, ctx):
                    return {"recipe": RECIPE_VALUE}

            class FooSkill(SkillBase):
                tools = [DoThingStep]
            """,
        extra_files={"_recipe.py": 'RECIPE_VALUE = "from-sibling"\n'},
    )

    skill = Skill.load(tmp_path / "foo")

    from unittest.mock import MagicMock

    result = skill.tool_map["do_thing"].handle({}, MagicMock())
    assert result == {"recipe": "from-sibling"}


# ── 2. __init__.py is actually executed ───────────────────────────────────────


def test_package_init_is_executed(tmp_path):
    """The package ``__init__.py`` runs — not just the handlers submodule."""
    _write_package_skill(
        tmp_path / "foo",
        init_body="""\
            INIT_RAN = True
            from .handlers import FooSkill
            __all__ = ["FooSkill", "INIT_RAN"]
            """,
        handlers_body=_PLAIN_HANDLERS,
    )

    skill = Skill.load(tmp_path / "foo")

    pkg = _package_module(skill)
    assert getattr(pkg, "INIT_RAN", False) is True


# ── 3. legacy alias identity ──────────────────────────────────────────────────


def test_legacy_alias_is_same_object_as_submodule(tmp_path):
    """``sys.modules[legacy] is <pkg>.handlers`` — the alias, not a copy."""
    _write_package_skill(
        tmp_path / "foo",
        init_body=_PLAIN_INIT,
        handlers_body=_PLAIN_HANDLERS,
    )

    skill = Skill.load(tmp_path / "foo")

    legacy_key = "_skill_oop_foo_handlers"
    assert legacy_key in sys.modules
    submodule = sys.modules[_handlers_modname(skill)]
    assert sys.modules[legacy_key] is submodule


# ── 4. reload contract (popping the alias yields a fresh module) ───────────────


def test_pop_legacy_alias_forces_fresh_reload(tmp_path):
    """Patched globals must not leak after pop(legacy) + reload."""
    _write_package_skill(
        tmp_path / "foo",
        init_body=_PLAIN_INIT,
        handlers_body="""\
            from knaif.tool import Step
            from knaif.skill_base import Skill as SkillBase

            _SENTINEL = "original"

            class DoThingStep(Step):
                name = "do_thing"
                def handle(self, args, ctx):
                    return {"did": True}

            class FooSkill(SkillBase):
                tools = [DoThingStep]
            """,
    )

    Skill.load(tmp_path / "foo")
    legacy_key = "_skill_oop_foo_handlers"
    sys.modules[legacy_key]._SENTINEL = "patched"

    sys.modules.pop(legacy_key, None)
    Skill.load(tmp_path / "foo")

    assert sys.modules[legacy_key]._SENTINEL == "original"


# ── 5. same-leaf collision safety ─────────────────────────────────────────────


def test_same_leaf_skills_get_distinct_package_modules(tmp_path):
    """Two skills named ``same`` under different parents do not collide."""
    _write_package_skill(
        tmp_path / "a" / "same",
        init_body=_PLAIN_INIT,
        handlers_body=_PLAIN_HANDLERS,
        extra_files={"_marker.py": 'WHICH = "a"\n'},
    )
    _write_package_skill(
        tmp_path / "b" / "same",
        init_body=_PLAIN_INIT,
        handlers_body=_PLAIN_HANDLERS,
        extra_files={"_marker.py": 'WHICH = "b"\n'},
    )

    skill_a = Skill.load(tmp_path / "a" / "same")
    skill_b = Skill.load(tmp_path / "b" / "same")

    mod_a = _handlers_modname(skill_a)
    mod_b = _handlers_modname(skill_b)
    assert mod_a != mod_b
    assert sys.modules[mod_a] is not sys.modules[mod_b]
