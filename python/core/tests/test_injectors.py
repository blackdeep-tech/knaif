"""Tests for knaif.injectors — injector registry and skill pipeline parsing."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from knaif.injectors import BUILTIN_INJECTORS, resolve_injected_files

# ── built-in injectors ────────────────────────────────────────────────────────


def test_host_files_passthrough_returns_filenames():
    fn = BUILTIN_INJECTORS["host_files"]
    result = fn({"clip_4k.mp4", "intro.mp4"})
    assert result == {"clip_4k.mp4", "intro.mp4"}


def test_host_files_passthrough_none_returns_empty():
    fn = BUILTIN_INJECTORS["host_files"]
    assert fn(None) == set()


def test_host_files_passthrough_empty_set():
    fn = BUILTIN_INJECTORS["host_files"]
    assert fn(set()) == set()


def test_host_files_strips_directory_components():
    # Injector should return only filenames, not full paths.
    fn = BUILTIN_INJECTORS["host_files"]
    result = fn({"/tmp/uploads/clip_4k.mp4", "/tmp/uploads/intro.mp4"})
    assert result == {"clip_4k.mp4", "intro.mp4"}


# ── resolve_injected_files ────────────────────────────────────────────────────


def test_resolve_no_injectors_returns_none():
    # No inject step declared → injection OFF.
    result = resolve_injected_files([], host_input={"clip.mp4"})
    assert result is None


def test_resolve_host_files_injector():
    result = resolve_injected_files(["host_files"], host_input={"clip_4k.mp4"})
    assert result == {"clip_4k.mp4"}


def test_resolve_multiple_injectors_union():
    # Multiple injectors accumulate their outputs.
    result = resolve_injected_files(["host_files", "host_files"], host_input={"clip.mp4"})
    assert result == {"clip.mp4"}


def test_resolve_unknown_injector_raises():
    with pytest.raises(ValueError, match="Unknown injector"):
        resolve_injected_files(["no_such_injector"], host_input=set())


def test_resolve_host_files_no_input_returns_empty_set():
    # inject step declared but host passed nothing → empty set (injection ON, 0 files).
    result = resolve_injected_files(["host_files"], host_input=None)
    assert result == set()


# ── Skill.load() reads pipeline ───────────────────────────────────────────────


def test_skill_load_parses_inject_step(tmp_path):
    """A skill declaring pipeline: inject: [host_files] exposes it as pipeline_inject."""
    from knaif.skill import Skill

    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()

    (skill_dir / "skill.yaml").write_text(textwrap.dedent("""\
        name: myskill
        description: test skill
        skill_class: handlers.InjectSkill
        pipeline:
          - inject:
              - host_files
          - intent
          - execute
    """))
    (skill_dir / "tools.yaml").write_text(textwrap.dedent("""\
        clarify:
          description: ask a question
          required_args: [question]
          safety_category: safe
          mock_args:
            question: what?
    """))
    (skill_dir / "handlers.py").write_text(textwrap.dedent("""\
        from knaif.skill_base import Skill
        class InjectSkill(Skill):
            tools = []   # only the core 'clarify' tool, merged automatically
    """))

    skill = Skill.load(skill_dir)
    assert skill.pipeline_inject == ["host_files"]


def test_skill_load_no_pipeline_has_empty_inject(tmp_path):
    """A skill without a pipeline section has pipeline_inject=[]."""
    from knaif.skill import Skill

    skill_dir = tmp_path / "myskill2"
    skill_dir.mkdir()

    (skill_dir / "skill.yaml").write_text(textwrap.dedent("""\
        name: myskill2
        description: test skill
        skill_class: handlers.InjectSkill
    """))
    (skill_dir / "tools.yaml").write_text(textwrap.dedent("""\
        clarify:
          description: ask a question
          required_args: [question]
          safety_category: safe
          mock_args:
            question: what?
    """))
    (skill_dir / "handlers.py").write_text(textwrap.dedent("""\
        from knaif.skill_base import Skill
        class InjectSkill(Skill):
            tools = []   # only the core 'clarify' tool, merged automatically
    """))

    skill = Skill.load(skill_dir)
    assert skill.pipeline_inject == []


def test_skill_ffmpeg_has_no_pipeline_inject():
    """The ffmpeg skill ships with no inject step (injection OFF)."""
    from knaif.skill import Skill

    skill = Skill.load(Path("skills/ffmpeg"))
    assert skill.pipeline_inject == []
