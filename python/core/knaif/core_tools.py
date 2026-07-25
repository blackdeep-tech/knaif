"""Built-in Step classes for the four core control-flow tools.

These replace the CORE_HANDLERS function dict while keeping identical behavior.
They are merged into every skill's tool_map by the loader (new OOP path).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .handler_api import HandlerContext
from .registry import ToolDef, load_registry
from .tool import Step


class ClarifyStep(Step):
    name = "clarify"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        return {"status": "clarification_needed", "question": args["question"]}


class RejectStep(Step):
    name = "reject"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        return {"status": "rejected", "reason": args["reason"]}


class DoneStep(Step):
    name = "done"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        return {"status": "done"}


class WaitForConfirmationStep(Step):
    name = "wait_for_confirmation"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        prompt = args.get("prompt", "Proceed?")
        preview = args.get("preview")
        ok = ctx.confirm(prompt, preview if isinstance(preview, dict) else None)
        return {"status": "confirmed" if ok else "declined", "prompt": prompt}


CORE_STEPS: list[type[Step]] = [
    ClarifyStep,
    RejectStep,
    DoneStep,
    WaitForConfirmationStep,
]

CORE_STEP_MAP: dict[str, Step] = {cls.name: cls() for cls in CORE_STEPS}


def _resolve_runtime_yaml(filename: str) -> Path:
    """Locate a shared runtime-contract YAML (core_tools.yaml / steps.yaml).

    Canonical source is repo-root ``contracts/runtime/`` (language-neutral, read by the Rust
    runtime too). ``core_tools.yaml`` is also import-critical, so a synced copy ships inside
    the wheel next to this module. Resolution order: the packaged copy first (present in a
    wheel and, kept in sync by ``just sync-runtime``, in a checkout), else ``contracts/runtime/``
    walking up from here (works in a checkout even without the packaged copy).
    """
    packaged = Path(__file__).resolve().parent / filename
    if packaged.exists():
        return packaged
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "contracts" / "runtime" / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"runtime data {filename!r} not found (packaged copy or contracts/runtime/)"
    )


# Metadata for the four core tools, loaded from core_tools.yaml through the same
# registry loader as a skill's tools.yaml. Co-located with the Step classes so
# behavior and metadata stay together. `_merge_core_tool_defs` injects these into
# every skill registry by name.
CORE_TOOLS_YAML: Path = _resolve_runtime_yaml("core_tools.yaml")
CORE_TOOL_DEFS: dict[str, ToolDef] = load_registry(CORE_TOOLS_YAML)
