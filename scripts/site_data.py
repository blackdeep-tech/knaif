#!/usr/bin/env python3
"""Generate `site/data/site-data.json` — the catalog both websites read.

Run with `just site-data`. The output is **committed**, and
`python/core/tests/test_site_data.py` regenerates it and fails on any difference — the
same drift-guard pattern `just sync-runtime` uses for `contracts/runtime/core_tools.yaml`.

Why committed rather than generated at build time: a contributor touching only the site
can then build it without a Python environment, and Amplify needs no Python step.

Why one extractor rather than each site parsing YAML itself: at tens of skills the real
risk is not the renderer, it is two hand-rolled parsers of the same contract drifting
apart. This goes through the runtime's **own skill loader** (`Skill.load`), not just
`load_registry`, because only the loader proves a model-visible tool is actually backed by
a `Step`/`Intent` class. A tool that would fail at runtime therefore cannot reach the
website.

Sources:
    skills/*/skill.yaml          display copy, status, deps, runtimes
    skills/*/tools.yaml          tool registry (via the loader)
    skills/*/prompt.yaml         curated example utterances (already model-facing)
    contracts/models/model-manifest.yaml
    contracts/release/platforms.yaml

Deliberately NOT a source: `models.yaml`, which is a *runtime backend config* (paths,
n_ctx, n_gpu_layers) and omits the released 1.7B entirely.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "python" / "core"))

from knaif import list_skills  # noqa: E402
from knaif.core_tools import CORE_TOOL_DEFS  # noqa: E402
from knaif.registry import ArgSchema, load_registry  # noqa: E402
from knaif.skill import Skill  # noqa: E402

SCHEMA_VERSION = 1
OUT_PATH = REPO / "site" / "data" / "site-data.json"

# The core control tools are merged into every skill's registry, so they are not
# capabilities *of* a skill and must not appear on its card.
CORE_TOOL_NAMES = frozenset(CORE_TOOL_DEFS)

DISPLAY_KEYS = ("title", "tagline", "category")

# How finished a skill is, as shown in the catalog.
#   stable  — full card
#   preview — shown, badged "in development"
#   hidden  — not published at all
VALID_STAGES = ("stable", "preview", "hidden")


class ExtractError(Exception):
    """A skill cannot be published. Fail the build rather than emit a broken card."""


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _arg_schema_json(schema: ArgSchema) -> dict[str, Any]:
    """Serialize an ArgSchema, omitting unset fields so thin schemas stay small."""
    out: dict[str, Any] = {"type": schema.type}
    for key in ("items", "min", "max", "help", "path_role"):
        value = getattr(schema, key)
        if value is not None:
            out[key] = value
    if schema.enum:
        out["enum"] = list(schema.enum)
    return out


def _tool_json(tool: Any) -> dict[str, Any]:
    """One model-visible tool.

    ffmpeg's arg_schemas are thin, so most tools emit little beyond their args. That is
    accepted (plan §3) rather than fixed by enriching a production skill's contract for
    the website's benefit.
    """
    out: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "safety_category": tool.safety_category,
        "required_args": list(tool.required_args),
        "optional_args": list(tool.optional_args),
    }
    if tool.any_of_args:
        out["any_of_args"] = list(tool.any_of_args)
    if tool.arg_schemas:
        out["arg_schemas"] = {
            name: _arg_schema_json(schema) for name, schema in sorted(tool.arg_schemas.items())
        }
    return out


def _examples_json(skill: Skill) -> list[dict[str, Any]]:
    """Curated utterances from prompt.yaml.

    These already exist and are already maintained — they are what the model is shown, so
    they are the truest statement of what a skill understands. Each carries the tool its
    plan routes to (null for clarify/reject examples), so the catalog can show only
    action-producing utterances while the developer reference can group by tool.
    """
    examples: list[dict[str, Any]] = []
    for ex in skill.prompt_examples:
        request = ex.get("request")
        if not request:
            continue
        plan = (ex.get("output") or {}).get("plan") or []
        tool = plan[0].get("tool") if plan and isinstance(plan[0], dict) else None
        examples.append({"request": request, "tool": tool})
    return examples


def _display(manifest: dict[str, Any], name: str) -> dict[str, str]:
    """Read the required `display:` block.

    Missing copy fails the build. A broken card in a public catalog is a worse failure
    than a failed build, and a silent fallback to `name`/`description` is exactly how a
    new skill ships with placeholder copy nobody noticed. See docs/TOOL_SCHEMA.md.
    """
    display = manifest.get("display") or {}
    missing = [k for k in DISPLAY_KEYS if not display.get(k)]
    if missing:
        # ASCII only: this goes to stderr, and a Windows console (cp1252) mangles em
        # dashes and arrows into noise right where the reader needs the instructions.
        raise ExtractError(
            f"skills/{name}/skill.yaml is missing display.{{{','.join(missing)}}}.\n"
            f"Every published skill needs end-user catalog copy. "
            f"See docs/TOOL_SCHEMA.md -> 'Display metadata'. Add:\n"
            f"  display:\n"
            f"    title: <Human Name>\n"
            f'    tagline: "<one sentence a non-developer understands>"\n'
            f"    category: <media|documents|...>"
        )
    return {k: display[k] for k in DISPLAY_KEYS}


def _stage(manifest: dict[str, Any], skill_dir: Path, name: str) -> str:
    """How finished this skill is, derived from evidence unless explicitly overridden.

    The default is derived rather than declared because `status:` defaults to `active` —
    so a half-finished skill dropped into `skills/` would otherwise advertise itself on
    knaif.org as though it were production-ready, and nobody would have had to make a
    wrong decision for that to happen.

    The evidence is `data/eval_snapshot.json`, the locked acceptance bar. AGENTS.md already
    defines a skill as done when its snapshot is locked with an executing verifier, so this
    reuses the project's own definition of ready instead of inventing a second one.

    `display.stage:` overrides it for the cases where the derivation is wrong. It cannot
    publish a `status: stale` skill — those are filtered upstream by `list_skills()`,
    because the website must never resurface what the runtime hides.
    """
    declared = (manifest.get("display") or {}).get("stage")
    if declared is not None:
        if declared not in VALID_STAGES:
            raise ExtractError(
                f"skills/{name}/skill.yaml has display.stage: {declared!r}; "
                f"expected one of {', '.join(VALID_STAGES)}"
            )
        return str(declared)
    return "stable" if (skill_dir / "data" / "eval_snapshot.json").is_file() else "preview"


def _skill_json(name: str) -> dict[str, Any] | None:
    """One catalog entry, or None if the skill is not published."""
    skill_dir = REPO / "skills" / name
    manifest = _load_yaml(skill_dir / "skill.yaml")

    # Before requiring catalog copy: a skill that will not be shown does not need any.
    stage = _stage(manifest, skill_dir, name)
    if stage == "hidden":
        return None

    # Through the loader, not load_registry: only this validates skill.yaml and proves
    # every visible tool is backed by a Step/Intent class.
    skill = Skill.load(skill_dir)

    # The registry carries the metadata; tool_map carries the proof that a Step/Intent
    # class actually backs each name. Publishing the intersection means the site can never
    # advertise a tool the runtime would reject — a registry entry with no class, or a
    # class with no entry, is caught here rather than by a visitor.
    registry = load_registry(skill.tools_yaml_path)
    backed = set(skill.tool_map or {})

    tools = []
    for tool_name, tool_def in sorted(registry.items()):
        if tool_name in CORE_TOOL_NAMES or tool_def.internal:
            continue
        if tool_name not in backed:
            raise ExtractError(
                f"skill {name!r} declares model-visible tool {tool_name!r} in tools.yaml, "
                f"but no Step/Intent class implements it. The runtime would reject a plan "
                f"using it, so it must not be published."
            )
        tools.append(_tool_json(tool_def))

    if not tools:
        raise ExtractError(f"skill {name!r} exposes no model-visible tools — nothing to publish")

    native = (skill.runtimes.get("native") or {}).get("status")

    return {
        "name": name,
        "stage": stage,
        **_display(manifest, name),
        # Developer-facing; distinct from `tagline`, which is written for an end user.
        "description": skill.description,
        "recommended_model": skill.recommended_model,
        "runtimes": {
            "python": "python" in skill.runtimes,
            "native": native,
        },
        "external_tools": [
            {
                "name": t.get("name"),
                "required": bool(t.get("required")),
                "commands": list(t.get("commands") or []),
            }
            for t in skill.external_tools
        ],
        "examples": _examples_json(skill),
        "tools": tools,
    }


def _models_json() -> dict[str, Any]:
    """Public model facts, from the manifest — not from models.yaml.

    `url`/`sha256` are deliberately omitted: the site never serves a model download, and
    copying a hash into a second place creates a second thing that can rot.
    """
    manifest = _load_yaml(REPO / "contracts" / "models" / "model-manifest.yaml")
    recommendations = manifest.get("recommendations") or {}
    default = recommendations.get("default")
    models = [
        {
            "id": key,
            "file": entry.get("file"),
            "size_bytes": entry.get("size_bytes"),
            "license": entry.get("license"),
            "base_model": entry.get("base_model"),
            "skills": list(entry.get("skills") or []),
            "default": key == default,
        }
        for key, entry in sorted((manifest.get("models") or {}).items())
    ]
    return {"models": models, "recommendations": recommendations}


def build() -> dict[str, Any]:
    names = list_skills()  # already excludes `status: stale` — io stays off the catalog
    skills = [entry for entry in (_skill_json(n) for n in names) if entry is not None]
    if not skills:
        raise ExtractError("no publishable skills found")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "scripts/site_data.py",
        "skills": skills,
        **_models_json(),
        "platforms": _load_yaml(REPO / "contracts" / "release" / "platforms.yaml"),
    }


def render(data: dict[str, Any]) -> str:
    # sort_keys + a fixed indent keep the drift guard meaningful; ensure_ascii=False keeps
    # the multilingual example utterances readable in review.
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    try:
        text = render(build())
    except ExtractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" so the committed file is byte-identical on Windows and Linux —
    # otherwise the drift guard fails for whoever is not on the platform that wrote it.
    with OUT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    rel = OUT_PATH.relative_to(REPO).as_posix()
    print(f"wrote {rel} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
