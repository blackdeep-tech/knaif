"""Workbench internals — the logic the notebook displays but does not contain.

Cells configure and render; everything else lives here as ordinary modules with unit tests,
because a bug inside a notebook cell is invisible to `just check`. It is also what makes the
side-by-side callable from a script later.

See docs/plans/2026-09-21-skill-prompt-workbench.md.
"""

from .runners import (
    NativeRunner,
    PythonRunner,
    RunResult,
    Timings,
    parse_native_timings,
    rendered_commands,
)

__all__ = [
    "NativeRunner",
    "PythonRunner",
    "RunResult",
    "Timings",
    "parse_native_timings",
    "rendered_commands",
]
