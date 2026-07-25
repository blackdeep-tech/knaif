"""Injector registry for the skill pipeline inject step.

An injector takes host-supplied input (typically a set of filenames provided by
the frontend, e.g. files the user dropped in a UI) and returns a normalised
set[str] of bare filenames available to the NL clarify gate.

Injection OFF = the skill's pipeline declares no inject step → injected_files=None.
Injection ON  = the skill declares inject:[<names>] → injected_files=set[str].

Built-in injectors ship here; skill authors may register custom ones by adding
their injector functions to their handlers.py and naming them in the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _host_files(host_input: Any) -> set[str]:
    """Pass through the host-supplied file set, returning bare filenames only."""
    if not host_input:
        return set()
    return {Path(f).name for f in host_input}


BUILTIN_INJECTORS: dict[str, Any] = {
    "host_files": _host_files,
}


def resolve_injected_files(
    pipeline_inject: list[str],
    *,
    host_input: Any = None,
    custom_injectors: dict[str, Any] | None = None,
) -> set[str] | None:
    """Run the declared injectors and return the accumulated file set.

    Returns None when pipeline_inject is empty (injection OFF).
    Returns set[str] (possibly empty) when any injectors are declared (injection ON).

    Parameters
    ----------
    pipeline_inject:
        The list of injector names declared in the skill's pipeline inject step.
        Empty list → injection OFF.
    host_input:
        The value the host provides (e.g. the set of files dropped by the UI).
    custom_injectors:
        Skill-registered injectors that extend or override the built-ins.
    """
    if not pipeline_inject:
        return None

    registry = {**BUILTIN_INJECTORS, **(custom_injectors or {})}
    accumulated: set[str] = set()

    for name in pipeline_inject:
        if name not in registry:
            raise ValueError(f"Unknown injector: {name!r}. " f"Available: {sorted(registry)}")
        result = registry[name](host_input)
        if result:
            accumulated |= result

    return accumulated
