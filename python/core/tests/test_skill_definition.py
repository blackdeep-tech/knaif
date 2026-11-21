"""The skill/toolkit definition model.

Every tool is defined once in YAML (``tools.yaml``) and once as a class (a ``Handler`` that
executes or an ``Intent`` that expands). The loader discovers the classes by ``name``,
strictly 1:1, and merges the tools of every toolkit a skill declares (``toolkits: [fs]``).
Toolkit tools are internal unless the skill lists them under ``expose:``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill

HANDLER_A = """
class AStep(Handler):
    name = "tool_a"

    def handle(self, args, ctx):
        return {"ok": "a"}
"""

INTENT_B = """
class BIntent(Intent):
    name = "tool_b"

    def expand(self, args):
        return [{"tool": "tool_a", "args": {}}]
"""

KIT_TOOL = """
from knaif.tool import Handler


class KitTool(Handler):
    name = "kit_tool"

    def handle(self, args, ctx):
        return {"ok": "kit"}
"""

TOOL_A_YAML = 'tool_a:\n  effects: []\n  description: "Tool A."\n  safety_category: safe\n'
TOOL_B_YAML = 'tool_b:\n  effects: []\n  description: "Tool B."\n  safety_category: safe\n'
KIT_YAML = 'kit_tool:\n  effects: []\n  description: "Kit tool."\n  safety_category: safe\n  readonly: true\n'


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _skill(
    root: Path,
    *,
    tools_yaml: str = TOOL_A_YAML + TOOL_B_YAML,
    code: str = HANDLER_A + INTENT_B,
    manifest_extra: str = "",
    name: str = "demo",
) -> Path:
    skill_dir = root / "skills" / name
    _write(skill_dir / "skill.yaml", f'name: {name}\ndescription: "Demo skill."\n{manifest_extra}')
    _write(skill_dir / "tools.yaml", tools_yaml)
    _write(skill_dir / "python" / "__init__.py", "")
    _write(
        skill_dir / "python" / "handlers.py",
        "from knaif.tool import Handler, Intent\n" + code,
    )
    return skill_dir


def _toolkit(root: Path, name: str = "kit", *, tools_yaml: str = KIT_YAML) -> Path:
    kit_dir = root / "toolkits" / name
    _write(kit_dir / "tools.yaml", tools_yaml)
    _write(kit_dir / "python" / "__init__.py", "")
    _write(kit_dir / "python" / "tools.py", KIT_TOOL)
    return kit_dir


# ── discovery: one YAML entry ↔ one class ─────────────────────────────────────────────


def test_discovers_handlers_and_intents_by_name(tmp_path: Path) -> None:
    skill = Skill.load(_skill(tmp_path))
    assert set(skill.tool_map) >= {"tool_a", "tool_b"}
    assert set(skill.registry) >= {"tool_a", "tool_b"}
    assert "tool_a" in skill.handlers
    assert "tool_b" in skill.expanders


def test_skill_yaml_needs_no_skill_class(tmp_path: Path) -> None:
    # skill_class / runtimes.python.handlers / tools: are gone; the manifest above has none.
    Skill.load(_skill(tmp_path))


def test_yaml_entry_without_class_is_an_error(tmp_path: Path) -> None:
    skill_dir = _skill(tmp_path, code=HANDLER_A)  # tool_b has YAML but no class
    with pytest.raises(ValueError, match="tool_b"):
        Skill.load(skill_dir)


def test_class_without_yaml_entry_is_an_error(tmp_path: Path) -> None:
    skill_dir = _skill(tmp_path, tools_yaml=TOOL_A_YAML)  # BIntent has no YAML entry
    with pytest.raises(ValueError, match="tool_b"):
        Skill.load(skill_dir)


def test_two_classes_with_one_name_is_an_error(tmp_path: Path) -> None:
    dup = HANDLER_A.replace("class AStep", "class AStepAgain")
    skill_dir = _skill(tmp_path, code=HANDLER_A + INTENT_B + dup)
    with pytest.raises(ValueError, match="tool_a"):
        Skill.load(skill_dir)


def test_classes_imported_from_elsewhere_are_not_discovered(tmp_path: Path) -> None:
    # A core class imported into a skill module belongs to core, not to the skill.
    code = "from knaif.core_tools import ClarifyHandler\n" + HANDLER_A + INTENT_B
    skill = Skill.load(_skill(tmp_path, code=code))
    assert "clarify" in skill.tool_map  # merged as a core tool, not discovered as the skill's


# ── toolkits ──────────────────────────────────────────────────────────────────────────


def test_toolkit_tools_are_merged_and_internal(tmp_path: Path) -> None:
    _toolkit(tmp_path)
    skill = Skill.load(_skill(tmp_path, manifest_extra="toolkits: [kit]\n"))
    assert "kit_tool" in skill.tool_map
    assert "kit_tool" in skill.handlers
    assert skill.registry["kit_tool"].internal is True


def test_exposed_toolkit_tool_is_model_visible(tmp_path: Path) -> None:
    _toolkit(tmp_path)
    skill = Skill.load(_skill(tmp_path, manifest_extra="toolkits: [kit]\nexpose: [kit_tool]\n"))
    assert skill.registry["kit_tool"].internal is False


def test_expose_must_name_a_toolkit_tool(tmp_path: Path) -> None:
    _toolkit(tmp_path)
    skill_dir = _skill(tmp_path, manifest_extra="toolkits: [kit]\nexpose: [no_such_tool]\n")
    with pytest.raises(ValueError, match="no_such_tool"):
        Skill.load(skill_dir)


def test_unknown_toolkit_is_an_error(tmp_path: Path) -> None:
    skill_dir = _skill(tmp_path, manifest_extra="toolkits: [missing]\n")
    with pytest.raises(ValueError, match="missing"):
        Skill.load(skill_dir)


def test_name_collision_between_skill_and_toolkit_is_an_error(tmp_path: Path) -> None:
    _toolkit(tmp_path, tools_yaml=KIT_YAML.replace("kit_tool", "tool_a"))
    # The kit's class still says kit_tool, so make its YAML and class agree on tool_a.
    kit_code = tmp_path / "toolkits" / "kit" / "python" / "tools.py"
    kit_code.write_text(KIT_TOOL.replace('"kit_tool"', '"tool_a"'), encoding="utf-8")
    skill_dir = _skill(tmp_path, manifest_extra="toolkits: [kit]\n")
    with pytest.raises(ValueError, match="tool_a"):
        Skill.load(skill_dir)


def test_agent_registry_includes_toolkit_tools(tmp_path: Path) -> None:
    _toolkit(tmp_path)
    agent = CommandAgent.from_skill(_skill(tmp_path, manifest_extra="toolkits: [kit]\n"))
    assert "kit_tool" in agent.registry
    assert agent.registry["kit_tool"].internal is True


# ── the optional Skill subclass (hooks only) ──────────────────────────────────────────


def test_no_skill_subclass_uses_defaults(tmp_path: Path) -> None:
    skill = Skill.load(_skill(tmp_path))
    assert skill.result_formatter is None
    assert skill.artifact_runner is None


def test_skill_subclass_supplies_hooks(tmp_path: Path) -> None:
    hooks = (
        "\nfrom knaif.skill_base import Skill\n\n\n"
        "class DemoSkill(Skill):\n"
        "    def format_results(self, results, *, dry_run):\n"
        "        return None\n"
    )
    skill = Skill.load(_skill(tmp_path, code=HANDLER_A + INTENT_B + hooks))
    assert skill.result_formatter is not None
    assert type(skill.skill_instance).__name__ == "DemoSkill"


def test_two_skill_subclasses_is_an_error(tmp_path: Path) -> None:
    hooks = (
        "\nfrom knaif.skill_base import Skill\n\n\n"
        "class OneSkill(Skill):\n    pass\n\n\n"
        "class TwoSkill(Skill):\n    pass\n"
    )
    with pytest.raises(ValueError, match="Skill subclass"):
        Skill.load(_skill(tmp_path, code=HANDLER_A + INTENT_B + hooks))


# ── vocabulary: Handler replaces Step ─────────────────────────────────────────────────


def test_handler_replaces_step() -> None:
    import knaif.tool

    assert hasattr(knaif.tool, "Handler")
    assert not hasattr(knaif.tool, "Step")


# ── the repo: fs toolkit, both skills migrated ────────────────────────────────────────


@pytest.mark.parametrize("skill_name", ["ffmpeg", "documents"])
def test_repo_skills_take_resolve_inputs_from_the_fs_toolkit(skill_name: str) -> None:
    skill = Skill.load(Path("skills") / skill_name)
    assert "resolve_inputs" in skill.tool_map
    assert skill.registry["resolve_inputs"].internal is True
    own_yaml = (Path("skills") / skill_name / "tools.yaml").read_text(encoding="utf-8")
    assert "resolve_inputs:" not in own_yaml


def test_resolve_inputs_contract_exists_once() -> None:
    assert (Path("toolkits") / "fs" / "tools.yaml").exists()
    assert not Path("contracts/runtime/steps.yaml").exists()
    with pytest.raises(ImportError):
        import knaif.steps  # noqa: F401


# ── carried over from the old loader tests ────────────────────────────────────────────


def test_flat_layout_without_python_dir_loads(tmp_path: Path) -> None:
    # handlers at the bundle root (no python/ package) are discovered the same way.
    skill_dir = tmp_path / "skills" / "flat"
    _write(skill_dir / "skill.yaml", 'name: flat\ndescription: "Flat skill."\n')
    _write(skill_dir / "tools.yaml", TOOL_A_YAML)
    _write(skill_dir / "handlers.py", "from knaif.tool import Handler\n" + HANDLER_A)
    skill = Skill.load(skill_dir)
    assert "tool_a" in skill.handlers


def test_summarizer_view_forwards_kwargs(tmp_path: Path) -> None:
    intent = INTENT_B + (
        "\n    def summarize(self, args, **kw):\n"
        "        return 'with skill_dir' if 'skill_dir' in kw else 'without'\n"
    )
    skill = Skill.load(_skill(tmp_path, code=HANDLER_A + intent))
    assert skill.summarizers["tool_b"]({}, skill_dir=tmp_path) == "with skill_dir"


def test_skill_subclass_supplies_artifact_runner(tmp_path: Path) -> None:
    hooks = (
        "\nfrom knaif.skill_base import Skill\n\n\n"
        "class DemoSkill(Skill):\n"
        "    def run_artifact(self, cmd, fixture, out_dir):\n"
        "        return None\n"
    )
    skill = Skill.load(_skill(tmp_path, code=HANDLER_A + INTENT_B + hooks))
    assert skill.artifact_runner is not None


# ── declared effects ───────────────────────────────────────────────────────────────────────


def test_a_skill_tool_without_effects_is_refused(tmp_path: Path) -> None:
    bare = 'tool_a:\n  description: "Tool A."\n  safety_category: safe\n'
    skill_dir = _skill(tmp_path, tools_yaml=bare + TOOL_B_YAML)
    with pytest.raises(ValueError, match="'tool_a' declares no effects"):
        Skill.load(skill_dir)


def test_a_toolkit_tool_without_effects_is_refused(tmp_path: Path) -> None:
    _toolkit(tmp_path, tools_yaml='kit_tool:\n  description: "Kit tool."\n  readonly: true\n')
    skill_dir = _skill(tmp_path, manifest_extra="toolkits: [kit]\n")
    with pytest.raises(ValueError, match="'kit_tool' declares no effects"):
        Skill.load(skill_dir)
