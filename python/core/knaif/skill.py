"""Skill loader: reads a skill directory and wires it into CommandAgent."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    pass


@dataclass
class Skill:
    name: str
    description: str
    tools_yaml_path: Path
    handlers: dict[str, Any]
    expanders: dict[str, Any]
    summarizers: dict[str, Any]
    preflights: dict[str, Any]
    result_formatter: Any | None
    artifact_runner: Any | None
    system_header: str | None
    examples_block: str | None
    prompt_examples: list[dict]  # raw example dicts from prompt.yaml for selective filtering
    arg_value_sets: dict[str, frozenset[str]]
    unsafe_phrases: tuple[str, ...]
    recommended_model: str | None
    skill_dir: Path
    pipeline_inject: list[str]  # injector names declared in pipeline: inject: [...]
    external_tools: tuple[dict[str, Any], ...]  # dependencies.external_tools (installer/preflight)
    runtimes: dict[str, Any]  # runtimes: metadata (which runtimes/crate implement the skill)
    # OOP path (set when skill_class: is present in skill.yaml; None for legacy)
    tool_map: dict[str, Any] | None = None
    skill_instance: Any | None = None

    @classmethod
    def load(cls, skill_dir: Path | str, overrides: list[Path | str] | None = None) -> Skill:
        skill_dir = Path(skill_dir).resolve()
        # Bundle layout: declarative YAML + data live at the bundle root (skill_dir), and
        # the handler package lives in `python/`. Flat layout (handlers alongside the YAML)
        # stays supported so both can coexist. `ctx.skill_dir` is always the bundle root so
        # handlers resolve profiles/data from there — never from their own `__file__`.
        python_dir = skill_dir / "python"
        handler_dir = python_dir if python_dir.is_dir() else skill_dir
        manifest_path = skill_dir / "skill.yaml"

        with manifest_path.open(encoding="utf-8") as fh:
            manifest = yaml.safe_load(fh)

        # App-owned override deltas: deep-merge over the base manifest, in order. These may
        # only tune/disable declarative fields — the handler package always loads from the
        # bundle's python/, so a delta can never inject executable behavior.
        for override in overrides or []:
            with Path(override).open(encoding="utf-8") as fh:
                delta = yaml.safe_load(fh) or {}
            _deep_merge_manifest(manifest, delta)
        disabled_tools: list[str] = list(manifest.pop("disabled_tools", None) or [])

        name: str = manifest["name"]
        description: str = manifest.get("description", "")

        tools_yaml_path = skill_dir / manifest.get("tools", "tools.yaml")

        system_header, examples_block, prompt_examples = _load_prompt(
            skill_dir / manifest.get("prompt", "prompt.yaml")
        )

        raw_sets: dict[str, list[str]] = manifest.get("arg_value_sets") or {}
        arg_value_sets = {k: frozenset(v) for k, v in raw_sets.items()}

        safety = manifest.get("safety") or {}
        unsafe_phrases = tuple(safety.get("unsafe_phrases") or [])
        recommended_model = manifest.get("recommended_model")
        if recommended_model is not None and not isinstance(recommended_model, str):
            recommended_model = None

        pipeline_inject = _parse_pipeline_inject(manifest.get("pipeline") or [])

        dependencies = manifest.get("dependencies") or {}
        external_tools = tuple(dependencies.get("external_tools") or [])
        runtimes = manifest.get("runtimes") or {}

        skill_class_ref: str | None = manifest.get("skill_class")
        if not skill_class_ref:
            raise ValueError(
                f"skill.yaml at {manifest_path} must declare 'skill_class:' "
                f"(e.g. 'skill_class: handlers.MySkill'). The legacy HANDLERS/EXPANDERS "
                f"dict form is no longer supported."
            )

        # tool_map is the source of truth. The legacy dict views
        # (handlers/expanders/summarizers) and skill-level callables are derived from
        # it so external consumers (e.g. evalsuite/cli.py reads skill.artifact_runner)
        # and the existing skill test suites keep working. preflights stays empty: the
        # wildcard preflight runs via skill_instance.preflight in the agent's OOP
        # dispatch, and a non-empty dict here would double-run.
        skill_instance, tool_map = _load_oop_skill(
            skill_dir, handler_dir, skill_class_ref, tools_yaml_path
        )
        handlers, expanders, summarizers = _derive_oop_views(skill_instance, tool_map)
        _apply_disabled_tools(disabled_tools, name, tool_map, handlers, expanders, summarizers)
        preflights: dict[str, Any] = {}
        result_formatter = _skill_override(skill_instance, "format_results")
        artifact_runner = _skill_override(skill_instance, "run_artifact")

        return cls(
            name=name,
            description=description,
            tools_yaml_path=tools_yaml_path,
            handlers=handlers,
            expanders=expanders,
            summarizers=summarizers,
            preflights=preflights,
            result_formatter=result_formatter,
            artifact_runner=artifact_runner,
            system_header=system_header,
            examples_block=examples_block,
            prompt_examples=prompt_examples,
            arg_value_sets=arg_value_sets,
            unsafe_phrases=unsafe_phrases,
            recommended_model=recommended_model,
            skill_dir=skill_dir,
            pipeline_inject=pipeline_inject,
            external_tools=external_tools,
            runtimes=runtimes,
            tool_map=tool_map,
            skill_instance=skill_instance,
        )


def _deep_merge_manifest(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge an override delta into ``base`` (mutated in place).

    Dicts merge recursively; lists and scalars replace; a ``None`` value removes the key
    (the explicit-removal directive, since a merge cannot otherwise delete).
    """
    for key, value in override.items():
        if value is None:
            base.pop(key, None)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge_manifest(base[key], value)
        else:
            base[key] = value
    return base


def _apply_disabled_tools(
    disabled: list[str],
    skill_name: str,
    tool_map: dict[str, Any],
    handlers: dict[str, Any],
    expanders: dict[str, Any],
    summarizers: dict[str, Any],
) -> None:
    """Subtract override-disabled tools from the tool map and its derived views.

    Core control tools (clarify/reject/done/wait_for_confirmation) cannot be disabled —
    the pipeline depends on them. An unknown name is a typo and is rejected.
    """
    from .core_tools import CORE_STEP_MAP

    core_names = set(CORE_STEP_MAP)
    for name in disabled:
        if name in core_names:
            raise ValueError(f"Cannot disable core control tool {name!r} via an override")
        if name not in tool_map:
            raise ValueError(
                f"disabled_tools names unknown tool {name!r} (not found in skill {skill_name!r})"
            )
        for view in (tool_map, handlers, expanders, summarizers):
            view.pop(name, None)


def _load_oop_skill(
    skill_dir: Path,
    handler_dir: Path,
    skill_class_ref: str,
    tools_yaml_path: Path,
) -> tuple[Any, dict[str, Any]]:
    """Load a Skill subclass via skill_class: and build the name→Tool map.

    skill_class_ref may be either ``"ClassName"`` (defaults to handlers.py) or
    ``"module.ClassName"`` (module is relative to the handler directory).

    ``skill_dir`` is the bundle root (used for module-key naming); ``handler_dir`` is
    where the handler package actually lives (``skill_dir/python`` for a bundle, or
    ``skill_dir`` for the flat layout).
    """
    from .core_tools import CORE_STEP_MAP
    from .tool import Intent, Step

    if "." in skill_class_ref:
        module_stem, class_name = skill_class_ref.rsplit(".", 1)
    else:
        module_stem, class_name = "handlers", skill_class_ref

    module = _import_skill_handler_module(skill_dir, handler_dir, module_stem)
    skill_cls = getattr(module, class_name)
    skill_instance = skill_cls()

    with tools_yaml_path.open(encoding="utf-8") as fh:
        tools_yaml: dict[str, Any] = yaml.safe_load(fh) or {}

    tool_map: dict[str, Any] = {}
    for tool_cls in skill_instance.tools:
        if not (isinstance(tool_cls, type) and issubclass(tool_cls, (Step, Intent))):
            raise TypeError(f"{tool_cls!r} in {class_name}.tools is not a Step or Intent subclass")
        t_name = getattr(tool_cls, "name", None)
        if t_name not in tools_yaml:
            raise ValueError(
                f"Tool {t_name!r} in {class_name}.tools has no metadata in {tools_yaml_path}"
            )
        if t_name in tool_map:
            raise ValueError(f"Duplicate tool name {t_name!r} in {class_name}.tools")
        tool_map[t_name] = tool_cls()

    # Merge core steps (clarify / reject / done / wait_for_confirmation)
    for t_name, step in CORE_STEP_MAP.items():
        tool_map[t_name] = step

    return skill_instance, tool_map


def _legacy_module_key(skill_dir: Path, module_stem: str) -> str:
    """Back-compat module key, e.g. ``_skill_oop_ffmpeg_handlers``.

    Leaf-name-based; safe only because in-repo skill leaf names are unique. It is
    a compat alias for existing tests, *not* the collision-safe path — that is the
    canonical key (see ``_canonical_pkg_key``).
    """
    return f"_skill_oop_{skill_dir.name}_{module_stem}"


def _canonical_pkg_key(bundle_dir: Path, handler_dir: Path) -> str:
    """Path-unique package key so two same-leaf handler dirs never collide.

    Named after the bundle root (readable, e.g. ``_skill_oop_ffmpeg_…``) but the hash is
    over the *handler* dir's resolved path, so distinct bundles with same-named handler
    subfolders (every bundle's ``python/``) still get distinct package modules in one
    process.
    """
    import hashlib

    digest = hashlib.sha1(str(handler_dir.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"_skill_oop_{bundle_dir.name}_{digest}"


def _import_skill_handler_module(bundle_dir: Path, handler_dir: Path, module_stem: str) -> Any:
    """Import a skill's handler module, returning the loaded module object.

    ``bundle_dir`` is the bundle root (drives the readable module-key names, which are
    unique per skill); ``handler_dir`` is where the handler ``.py`` files actually live
    (``bundle_dir/python`` for a bundle, or ``bundle_dir`` for the flat layout).

    Two modes:

    * **Package** (handler dir has ``__init__.py``): the dir is loaded as a real
      Python package under a path-unique canonical key, ``__init__.py`` is
      executed, and the handler module is loaded as a submodule so relative
      imports (``from ._recipe import ...``) resolve. The legacy leaf-name key is
      registered as an *alias* (same object) of that submodule. Popping the alias
      before re-loading forces a genuine reload of the package + submodule, so
      monkeypatched globals do not leak (the reload contract existing tests rely
      on).
    * **Flat file** (no ``__init__.py``): the historical behavior, verbatim — the
      handler ``.py`` is exec'd under the legacy key with no package context.
    """
    bundle_dir = bundle_dir.resolve()
    handler_dir = handler_dir.resolve()
    legacy_key = _legacy_module_key(bundle_dir, module_stem)
    init_path = handler_dir / "__init__.py"
    module_path = handler_dir / f"{module_stem}.py"

    if not init_path.exists():
        # Flat-file fallback — identical to the pre-package loader.
        spec = importlib.util.spec_from_file_location(legacy_key, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load OOP skill module from {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[legacy_key] = module
        spec.loader.exec_module(module)
        return module

    # Package path.
    canonical = _canonical_pkg_key(bundle_dir, handler_dir)
    submodule_key = f"{canonical}.{module_stem}"

    # Reload contract: when the legacy alias has been popped, discard any cached
    # package/submodule too so the next load re-executes fresh (no stale globals).
    if legacy_key not in sys.modules:
        sys.modules.pop(submodule_key, None)
        sys.modules.pop(canonical, None)

    # (1) The package itself — executing __init__.py. A relative re-export inside
    # __init__.py (``from .handlers import X``) will load the submodule as a side
    # effect, which step 2 then reuses.
    if canonical not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            canonical, init_path, submodule_search_locations=[str(handler_dir)]
        )
        if pkg_spec is None or pkg_spec.loader is None:
            raise ImportError(f"Cannot load skill package from {init_path}")
        pkg = importlib.util.module_from_spec(pkg_spec)
        sys.modules[canonical] = pkg
        pkg_spec.loader.exec_module(pkg)

    # (2) The handler submodule (unless __init__.py already imported it).
    if submodule_key not in sys.modules:
        sub_spec = importlib.util.spec_from_file_location(submodule_key, module_path)
        if sub_spec is None or sub_spec.loader is None:
            raise ImportError(f"Cannot load OOP skill module from {module_path}")
        submodule = importlib.util.module_from_spec(sub_spec)
        sys.modules[submodule_key] = submodule
        sub_spec.loader.exec_module(submodule)
    submodule = sys.modules[submodule_key]

    # (3) Legacy alias points at the *same object* as the submodule.
    sys.modules[legacy_key] = submodule
    return submodule


def _derive_oop_views(
    skill_instance: Any,
    tool_map: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Derive legacy handlers/expanders/summarizers dicts from the tool_map.

    Only the skill's *domain* tools (those declared in ``skill_instance.tools``)
    are included — core control steps (clarify/reject/done/...) are merged into
    the agent's handlers separately, mirroring the legacy CORE_HANDLERS split.
    Values are bound methods on the instantiated tools (single source of truth).
    """
    from .tool import Intent, Step

    handlers: dict[str, Any] = {}
    expanders: dict[str, Any] = {}
    summarizers: dict[str, Any] = {}
    for tool_cls in skill_instance.tools:
        name = tool_cls.name
        tool_obj = tool_map[name]
        if isinstance(tool_obj, Step):
            handlers[name] = tool_obj.handle
        elif isinstance(tool_obj, Intent):
            expanders[name] = tool_obj.expand
            summarizers[name] = tool_obj.summarize
    return handlers, expanders, summarizers


def _skill_override(skill_instance: Any, method: str) -> Any | None:
    """Return the bound skill method only if the subclass overrides the base.

    Mirrors the preflight precedence test elsewhere: an unoverridden default
    (which returns None) leaves the slot None so callers fall back to generic
    behaviour (e.g. the CLI's built-in result rendering).
    """
    from .skill_base import Skill as SkillBase

    if getattr(type(skill_instance), method) is getattr(SkillBase, method):
        return None
    return getattr(skill_instance, method)


def _load_prompt(prompt_path: Path) -> tuple[str | None, str | None, list[dict]]:
    if not prompt_path.exists():
        return None, None, []

    with prompt_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        return None, None, []

    system_header: str | None = data.get("system_header")
    raw_examples: list[dict] = [e for e in (data.get("examples") or []) if isinstance(e, dict)]
    examples_block: str | None = _render_examples(raw_examples)
    return system_header, examples_block, raw_examples


def _parse_pipeline_inject(pipeline: list) -> list[str]:
    """Extract injector names from the pipeline's inject step, if present.

    Supports:
        pipeline:
          - inject:
              - host_files
          - intent
          - execute

    Returns a list of injector names, or [] when no inject step is declared.
    """
    for step in pipeline or []:
        if isinstance(step, dict) and "inject" in step:
            raw = step["inject"]
            if isinstance(raw, list):
                return [str(s) for s in raw if s]
            if isinstance(raw, str):
                return [raw]
    return []


def _render_examples(examples: list[dict]) -> str | None:
    if not examples:
        return None
    lines = ["Examples:"]
    for ex in examples:
        request = ex.get("request", "")
        output = ex.get("output")
        import json

        lines.append(f'  request: "{request}"')
        if output is not None:
            lines.append(f"  output:  {json.dumps(output, separators=(', ', ': '))}")
        lines.append("")
    return "\n".join(lines)
