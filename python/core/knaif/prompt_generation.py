"""The prompt generation: which served prompt a model was trained for.

A model learns the prompt it is trained on: the headers, rules, examples and the model-visible
tool definitions. When knaif changes them, a model trained before works worse, and nothing used
to say so. ``contracts/runtime/prompt_generation.yaml`` declares a generation number and the
fingerprint of everything the model can be served; a test fails when the served prompt changes
without a bump (owner, 2026-10-26). A published model records the generation it was trained
for (``trained_for`` in the model manifest, ``models.yaml``), and a CLI says when a model
predates the prompt it serves, once a model trained for that prompt exists to pull.

Retrieval is not part of it: keywords and triggers decide which tools a request sees, not what
they say, and a model trained on the same texts copes with another selection. Native reads the
number only; the fingerprint is the reference runtime's (the prompt parity contracts hold the
two runtimes' prompts together).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .core_tools import _resolve_runtime_yaml
from .prompt import _SYSTEM_TOOLS

FILENAME = "prompt_generation.yaml"


@dataclass(frozen=True)
class Generation:
    generation: int
    fingerprint: str


def declared() -> Generation:
    """The generation this checkout (or wheel) serves."""
    data = yaml.safe_load(_resolve_runtime_yaml(FILENAME).read_text(encoding="utf-8")) or {}
    return Generation(int(data["generation"]), str(data["fingerprint"]))


def served_content(skill: str, skills_root: Path | str | None = None) -> dict[str, Any]:
    """Everything of *skill* that can reach the model, as the prompt renders it.

    Tools carry what ``prompt.build_prompt`` prints: name, description, the argument names in
    order with required/optional and their ``help``. Rules and examples carry their text; the
    conditions that pick them (``tools``, ``when``, ``words``) are selection, like retrieval.
    """
    from . import _DEFAULT_SKILLS_ROOT
    from .agent import CommandAgent

    root = Path(skills_root) if skills_root else _DEFAULT_SKILLS_ROOT
    with tempfile.TemporaryDirectory() as sandbox:
        agent = CommandAgent.from_skill(root / skill, sandbox=sandbox)
    tools = []
    for name, tool in agent.registry.items():
        if tool.internal or name in _SYSTEM_TOOLS:
            continue
        schemas = tool.arg_schemas
        args = [
            [arg, kind, (schemas[arg].help if arg in schemas else None)]
            for kind, names in (("required", tool.required_args), ("optional", tool.optional_args))
            for arg in names
        ]
        tools.append({"name": name, "description": tool.description, "args": args})
    return {
        "header": agent.system_header,
        "rules": [rule.get("text") for rule in agent.prompt_rules],
        "examples": [
            {"request": ex.get("request"), "output": ex.get("output")}
            for ex in agent.prompt_examples
        ],
        "tools": tools,
    }


def fingerprint_of(contents: dict[str, dict[str, Any]]) -> str:
    """sha256 of the served content of every skill, keyed by skill name."""
    text = json.dumps(contents, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(skills: list[str] | None = None) -> str:
    """The fingerprint of the prompt served today, over the active skills."""
    from . import list_skills

    names = sorted(skills) if skills else list_skills()
    return fingerprint_of({name: served_content(name) for name in names})


def note_for(
    trained_for: int | None, generation: int, model: str, *, pull: str | None
) -> str | None:
    """One line for the terminal when *model* was not trained for *generation*, else None.

    *trained_for* is the model's declared generation (None: a model from before generations were
    counted). *pull* names the model trained for this generation, when there is one to get;
    without one, an older model runs quietly.
    """
    if trained_for == generation:
        return None
    when = "a newer" if trained_for is not None and trained_for > generation else "an earlier"
    # An older model is noted only once there is a model for this generation to pull: a release
    # that serves the shipped model on a newer prompt must not warn on every run until then
    # (owner, 2026-10-26). A model trained for a newer prompt always says so (knaif is behind).
    if when == "an earlier" and pull is None:
        return None
    note = (
        f"note: {model} was trained for {when} prompt than this knaif serves "
        f"(generation {generation}); results may be worse."
    )
    if pull and pull != model:
        note += f" The model for it: {pull}."
    return note
