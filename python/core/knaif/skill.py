"""Skill loader: reads a skill directory and wires it into CommandAgent."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
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
    # file_kinds: as extension -> kind; chain threading never crosses kinds (see parse_file_kinds)
    file_kinds: dict[str, str] = field(default_factory=dict)
    # batch_kinds: kinds whose batch glob covers the whole kind (knaif.batch_chain.widen_kind_globs)
    batch_kinds: list[str] = field(default_factory=list)
    # `scratch_intermediates:` route chain intermediates into the run scratch (both runtimes).
    scratch_intermediates: bool = False
    # `url_inputs:` the content types and size cap of http(s) URL inputs (needs `net`).
    url_inputs: Any | None = None
    tool_map: dict[str, Any] | None = None
    skill_instance: Any | None = None
    # The skill's tools plus every declared toolkit's (core control tools are merged by the
    # agent). Toolkit tools are internal unless the skill lists them under `expose:`.
    registry: dict[str, Any] = field(default_factory=dict)
    # `rules:` from prompt.yaml: each {tools, text}, added to the header only when one of its
    # tools is in the prompt (CommandAgent.build_prompt).
    prompt_rules: list[dict] = field(default_factory=list)

    @classmethod
    def load(
        cls,
        skill_dir: Path | str,
        overrides: list[Path | str] | None = None,
        toolkits_root: Path | str | None = None,
    ) -> Skill:
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

        for retired in ("skill_class", "tools", "prompt"):
            if retired in manifest:
                raise ValueError(
                    f"skill.yaml at {manifest_path} declares {retired!r}, which is no longer "
                    f"used: tool classes and the Skill subclass are discovered, and the files "
                    f"are always tools.yaml and prompt.yaml."
                )
        tools_yaml_path = skill_dir / "tools.yaml"

        system_header, examples_block, prompt_examples = _load_prompt(skill_dir / "prompt.yaml")
        prompt_rules = _load_prompt_rules(skill_dir / "prompt.yaml")

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
        file_kinds = parse_file_kinds(manifest.get("file_kinds"))
        batch_kinds = parse_batch_kinds(manifest.get("batch_kinds"), file_kinds)
        scratch_intermediates = manifest.get("scratch_intermediates", False)
        if not isinstance(scratch_intermediates, bool):
            raise ValueError(
                f"scratch_intermediates must be true or false, got {scratch_intermediates!r}"
            )

        from .urls import parse_url_inputs

        url_inputs = parse_url_inputs(
            manifest.get("url_inputs"), list(manifest.get("toolkits") or [])
        )

        # tool_map is the source of truth. The dict views (handlers/expanders/summarizers)
        # and skill-level callables are derived from it, because the agent and evalsuite
        # read them. preflights stays empty: the wildcard preflight runs via
        # skill_instance.preflight in the agent's dispatch, and a non-empty dict here would
        # double-run.
        skill_instance, tool_map, registry = _load_skill_tools(
            skill_dir,
            handler_dir,
            tools_yaml_path,
            toolkits=list(manifest.get("toolkits") or []),
            expose=list(manifest.get("expose") or []),
            toolkits_root=_resolve_toolkits_root(skill_dir, toolkits_root),
        )
        handlers, expanders, summarizers = _derive_tool_views(tool_map)
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
            prompt_rules=prompt_rules,
            arg_value_sets=arg_value_sets,
            unsafe_phrases=unsafe_phrases,
            recommended_model=recommended_model,
            skill_dir=skill_dir,
            pipeline_inject=pipeline_inject,
            external_tools=external_tools,
            runtimes=runtimes,
            file_kinds=file_kinds,
            batch_kinds=batch_kinds,
            scratch_intermediates=scratch_intermediates,
            url_inputs=url_inputs,
            tool_map=tool_map,
            skill_instance=skill_instance,
            registry=registry,
        )


def parse_file_kinds(raw: Any) -> dict[str, str]:
    """``file_kinds:`` (kind -> extensions) as an extension -> kind map.

    The skill says which files are the same kind of thing (a video in another container)
    and which are not (a thumbnail of that video). Core only compares the kinds: chain
    threading never rewrites a reference onto a file of a different kind. Extensions are
    lower-cased with any leading dot dropped; one listed under two kinds is an error,
    since the comparison would then depend on which kind was read last. Absent means no
    kinds, and an extension no kind lists is unrestricted.
    """
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"file_kinds must map a kind to a list of extensions, got {raw!r}")
    kinds: dict[str, str] = {}
    for kind, exts in raw.items():
        if not isinstance(exts, list):
            raise ValueError(f"file_kinds.{kind} must be a list of extensions, got {exts!r}")
        for ext in exts:
            key = str(ext).lower().lstrip(".")
            if key in kinds and kinds[key] != kind:
                raise ValueError(f"file_kinds lists {key!r} under both {kinds[key]!r} and {kind!r}")
            kinds[key] = str(kind)
    return kinds


def parse_batch_kinds(raw: Any, file_kinds: dict[str, str]) -> list[str]:
    """``batch_kinds:``, the kinds whose batch glob covers the whole kind: "every video" read
    as every video, not only the ``*.mp4`` the model wrote (``knaif.batch_chain.widen_kind_globs``).
    Each must be a kind ``file_kinds:`` declares. Absent means none."""
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"batch_kinds must be a list of kinds, got {raw!r}")
    declared = set(file_kinds.values())
    for kind in raw:
        if kind not in declared:
            raise ValueError(f"batch_kinds lists {kind!r}, which file_kinds does not declare")
    return [str(k) for k in raw]


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
    from .core_tools import CORE_TOOL_MAP

    core_names = set(CORE_TOOL_MAP)
    for name in disabled:
        if name in core_names:
            raise ValueError(f"Cannot disable core control tool {name!r} via an override")
        if name not in tool_map:
            raise ValueError(
                f"disabled_tools names unknown tool {name!r} (not found in skill {skill_name!r})"
            )
        for view in (tool_map, handlers, expanders, summarizers):
            view.pop(name, None)


def _resolve_toolkits_root(skill_dir: Path, explicit: Path | str | None) -> Path:
    """Where toolkits live: an explicit root, then ``$KNAIF_TOOLKITS_ROOT``, then the
    ``toolkits/`` directory beside the skill's ``skills/`` directory (``<repo>/toolkits``)."""
    import os

    if explicit is not None:
        return Path(explicit).resolve()
    env = os.environ.get("KNAIF_TOOLKITS_ROOT")
    if env:
        return Path(env).resolve()
    return skill_dir.parent.parent / "toolkits"


def _load_skill_tools(
    skill_dir: Path,
    handler_dir: Path,
    tools_yaml_path: Path,
    *,
    toolkits: list[str],
    expose: list[str],
    toolkits_root: Path,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Build the skill's registry and name→tool map from YAML plus discovered classes.

    Every tool is defined once in YAML and once as a class (a ``Handler`` or an ``Intent``),
    matched by ``name`` strictly 1:1. A declared toolkit contributes all of its tools, which
    are internal unless listed under ``expose:``. Core control tools are merged into the
    tool map here and into the registry by the agent. A YAML entry for a core tool (a skill
    may restate one) needs no class: the core class serves it.
    """
    from dataclasses import replace

    from .core_tools import CORE_TOOL_MAP
    from .effects import check_declared
    from .registry import load_registry
    from .skill_base import Skill as SkillBase

    core = set(CORE_TOOL_MAP)
    registry: dict[str, Any] = dict(load_registry(tools_yaml_path))
    # Every tool says what it may do (`knaif.effects`); a restated core tool is core's.
    check_declared(
        {n: td for n, td in registry.items() if n not in core}, source=str(tools_yaml_path)
    )
    classes, skill_classes = _discover_classes(skill_dir, handler_dir)
    _check_one_to_one(set(registry) - core, classes, tools_yaml_path)
    owner = dict.fromkeys(registry, "the skill")

    toolkit_tools: set[str] = set()
    for kit in toolkits:
        kit_dir = toolkits_root / kit
        kit_yaml = kit_dir / "tools.yaml"
        if not kit_yaml.exists():
            raise ValueError(f"unknown toolkit {kit!r}: no {kit_yaml}")
        kit_registry = load_registry(kit_yaml)
        check_declared(kit_registry, source=str(kit_yaml))
        kit_classes, kit_skill_classes = _discover_classes(kit_dir, kit_dir / "python")
        if kit_skill_classes:
            raise ValueError(f"toolkit {kit!r} defines a Skill subclass; toolkits have no hooks")
        _check_one_to_one(set(kit_registry), kit_classes, kit_yaml)
        for name, tool_def in kit_registry.items():
            if name in owner or name in core:
                where = "a core tool" if name in core else owner[name]
                raise ValueError(f"tool {name!r} from toolkit {kit!r} collides with {where}")
            owner[name] = f"toolkit {kit!r}"
            registry[name] = replace(tool_def, internal=name not in expose)
            toolkit_tools.add(name)
        classes.update(kit_classes)

    unknown = [name for name in expose if name not in toolkit_tools]
    if unknown:
        raise ValueError(f"expose: names {unknown} that no declared toolkit provides")

    if len(skill_classes) > 1:
        names = ", ".join(sorted(cls.__name__ for cls in skill_classes))
        raise ValueError(f"{skill_dir.name} defines more than one Skill subclass: {names}")
    skill_instance = skill_classes[0]() if skill_classes else SkillBase()

    tool_map: dict[str, Any] = {name: cls() for name, cls in classes.items()}
    tool_map.update(CORE_TOOL_MAP)
    return skill_instance, tool_map, registry


def _check_one_to_one(entries: set[str], classes: dict[str, type], yaml_path: Path) -> None:
    missing = sorted(entries - set(classes))
    if missing:
        raise ValueError(
            f"{yaml_path} declares {missing} but no Handler/Intent class has that name"
        )
    orphans = sorted(set(classes) - entries)
    if orphans:
        raise ValueError(f"tool classes named {orphans} have no entry in {yaml_path}")


def _discover_classes(bundle_dir: Path, handler_dir: Path) -> tuple[dict[str, type], list[type]]:
    """Find the tool classes and Skill subclasses a bundle defines.

    Imports every top-level module in ``handler_dir`` (``handlers`` first) and collects the
    ``Handler``/``Intent`` subclasses with a ``name`` and the ``Skill`` subclasses that are
    *defined* in the bundle. A class imported from elsewhere (core, another bundle) is not the
    bundle's. Two classes with one name are an error.
    """
    import inspect

    from .skill_base import Skill as SkillBase
    from .tool import Handler, Intent

    if not handler_dir.is_dir():
        return {}, []
    stems = sorted(
        p.stem
        for p in handler_dir.glob("*.py")
        if p.stem != "__init__" and not p.stem.startswith("test_")
    )
    stems.sort(key=lambda stem: stem != "handlers")
    # The reload contract (see _import_skill_handler_module) applies once, to the first
    # module. The rest are imported into that same package, or reused if the first import
    # already loaded them, so no module ever holds classes from a discarded copy.
    modules = [
        (
            _import_skill_handler_module(bundle_dir, handler_dir, stem)
            if i == 0
            else _import_skill_submodule(bundle_dir, handler_dir, stem)
        )
        for i, stem in enumerate(stems)
    ]

    if (handler_dir / "__init__.py").exists():
        prefix = _canonical_pkg_key(bundle_dir.resolve(), handler_dir.resolve())
        own_modules = {
            name for name in sys.modules if name == prefix or name.startswith(prefix + ".")
        }
    else:
        own_modules = {module.__name__ for module in modules}

    classes: dict[str, type] = {}
    skill_classes: list[type] = []
    seen: set[type] = set()
    for module_name in sorted(own_modules):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for obj in vars(module).values():
            if not isinstance(obj, type) or obj in seen or obj.__module__ not in own_modules:
                continue
            seen.add(obj)
            if issubclass(obj, SkillBase) and obj is not SkillBase:
                skill_classes.append(obj)
            elif issubclass(obj, (Handler, Intent)) and not inspect.isabstract(obj):
                name = getattr(obj, "name", None)
                if not isinstance(name, str):
                    continue
                if name in classes and classes[name] is not obj:
                    raise ValueError(
                        f"two classes are named {name!r}: "
                        f"{classes[name].__qualname__} and {obj.__qualname__}"
                    )
                classes[name] = obj
    return classes, skill_classes


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


def _import_skill_submodule(bundle_dir: Path, handler_dir: Path, module_stem: str) -> Any:
    """Import one more module of an already-loaded bundle, without the reload contract."""
    bundle_dir = bundle_dir.resolve()
    handler_dir = handler_dir.resolve()
    if not (handler_dir / "__init__.py").exists():
        return _import_skill_handler_module(bundle_dir, handler_dir, module_stem)
    key = f"{_canonical_pkg_key(bundle_dir, handler_dir)}.{module_stem}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, handler_dir / f"{module_stem}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load skill module {handler_dir / module_stem}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    sys.modules[_legacy_module_key(bundle_dir, module_stem)] = sys.modules[key]
    return sys.modules[key]


def _derive_tool_views(
    tool_map: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Derive the handlers/expanders/summarizers dicts from the tool_map.

    Only the skill's and its toolkits' tools are included. Core control tools
    (clarify/reject/done/...) are merged into the agent's handlers separately. Values are
    bound methods on the instantiated tools (single source of truth).
    """
    from .core_tools import CORE_TOOL_MAP
    from .tool import Handler, Intent

    handlers: dict[str, Any] = {}
    expanders: dict[str, Any] = {}
    summarizers: dict[str, Any] = {}
    for name, tool_obj in tool_map.items():
        if name in CORE_TOOL_MAP and tool_obj is CORE_TOOL_MAP[name]:
            continue
        if isinstance(tool_obj, Handler):
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


def _load_prompt_rules(prompt_path: Path) -> list[dict]:
    """``rules:`` from prompt.yaml, in order: ``{"tools": [...], "text": "..."}`` joins a prompt
    that lists one of its tools; ``{"when": "many", "text": "..."}`` one whose request asks for
    many files (the per-file batch rule); ``words`` also needs one of them in the request."""
    if not prompt_path.exists():
        return []
    with prompt_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    rules = data.get("rules") if isinstance(data, dict) else None
    out: list[dict] = []
    for rule in rules or []:
        if not isinstance(rule, dict) or not isinstance(rule.get("text"), str):
            raise ValueError(f"{prompt_path}: each rule needs `tools` and `text`")
        when = rule.get("when")
        if when is not None and when != "many":
            raise ValueError(f"{prompt_path}: a rule's `when` can only be `many`, not {when!r}")
        out.append(
            {
                "tools": [str(t) for t in rule.get("tools") or []],
                "when": when,
                "words": [str(w) for w in rule.get("words") or []],
                "text": rule["text"],
            }
        )
    return out


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
