"""Declared effects: what a tool may do to the world.

Every skill and toolkit tool declares ``effects:`` in ``tools.yaml``. The vocabulary:

* ``fs.read``: reads files (including a toolkit lookup that looks at the disk);
* ``fs.write``: writes, moves or deletes files, through the fs toolkit;
* ``net``: reaches the network;
* ``exec:<program>``: runs an external program (``exec:ffmpeg``, ``exec:soffice``).

``effects: []`` declares a tool with none (an Intent that only expands, a pure path rule). The
runtime holds a step to its declaration: ``ctx.exec`` refuses a program the step did not declare,
and ``ctx.call_tool`` refuses a tool whose effects the step does not declare. The native runtime
reads and validates the same declarations (``knaif_core::effects``). Contract:
``contracts/parity/effects_cases.json``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_FIXED = frozenset({"fs.read", "fs.write", "net"})
_EXEC = re.compile(r"^exec:[a-z0-9_-]+$")


def is_effect(value: Any) -> bool:
    """True for one word of the vocabulary."""
    return isinstance(value, str) and (value in _FIXED or bool(_EXEC.match(value)))


def parse_effects(tool: str, raw: Any) -> tuple[str, ...] | None:
    """A ``tools.yaml`` ``effects:`` value as a sorted tuple; ``None`` when it is absent."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"tool {tool!r}: effects must be a list, got {raw!r}")
    for value in raw:
        if not is_effect(value):
            raise ValueError(
                f"tool {tool!r}: unknown effect {value!r} "
                "(use fs.read, fs.write, net or exec:<program>)"
            )
    return tuple(sorted(set(raw)))


def check_declared(registry: Mapping[str, Any], *, source: str) -> None:
    """Refuse a skill's or toolkit's registry in which a tool declares no effects."""
    for name, tool_def in registry.items():
        if getattr(tool_def, "effects", None) is None:
            raise ValueError(
                f"{source}: tool {name!r} declares no effects "
                "(add `effects: [...]`, or `effects: []` for a tool with none)"
            )


def program_effect(program: str) -> str:
    """The effect that allows running *program*."""
    return f"exec:{program}"


class EffectNotDeclared(PermissionError):
    """A step tried something its tool does not declare (``effects:`` in ``tools.yaml``)."""


def require(tool: str | None, declared: frozenset[str] | None, needed: set[str], what: str) -> None:
    """Refuse *what* unless every effect in *needed* is declared. ``None`` declares no limits
    (a context built outside the agent, or a tool loaded without a bundle)."""
    if declared is None:
        return
    missing = sorted(needed - declared)
    if missing:
        raise EffectNotDeclared(
            f"tool {tool!r} cannot {what}: it does not declare {', '.join(missing)} "
            "(add it to the tool's `effects:` in tools.yaml)"
        )
