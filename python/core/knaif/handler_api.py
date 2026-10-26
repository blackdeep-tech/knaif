"""Handler API: shared context dataclass for all tool handlers."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _default_confirmer(prompt: str, preview: dict[str, Any] | None, *, confirmed: bool) -> bool:
    """Default confirmer: honors the ``confirmed`` flag passed to ``execute_plan``."""
    return confirmed


@dataclass(frozen=True)
class HandlerContext:
    root: Path
    dry_run: bool
    confirmed: bool
    skill_dir: Path
    sandbox: Path | None = None
    confirmer: Callable[[str, dict[str, Any] | None], bool] | None = None
    skip_execution: bool = False
    """When True, subprocess calls (ffmpeg) are suppressed but probe calls
    (ffprobe) run normally.  Used for the planning phase when the user has
    dry-run disabled: real metadata is collected so commands are accurate,
    but nothing is written to disk until the user clicks Execute."""
    work_dir: Path | None = None
    """This run's scratch folder (``knaif.scratch``): intermediates, preview clips and outputs
    awaiting verification go here, never into the user's folder. Deleted when the run ends. In
    a dry run it is a path that does not exist, so steps can still render commands naming it."""
    tool_caller: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
    tool: str | None = None
    """The tool this step runs, for messages."""
    effects: frozenset[str] | None = None
    """What the step's tool declares (``knaif.effects``). ``exec`` and ``call_tool`` refuse
    anything outside it; ``None`` (a context built outside the agent) is not restricted."""

    def exec(self, program: str, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        """Run the external *program* (``argv[0]`` may be its full path): ``subprocess.run``,
        refused unless the step's tool declares ``exec:<program>``. The one way a step runs a
        process, so a tool's ``effects:`` says every program it can start."""
        from .effects import program_effect, require

        require(self.tool, self.effects, {program_effect(program)}, f"run {program}")
        return subprocess.run(argv, **kwargs)  # noqa: S603 - the program is declared

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run another tool of this skill (its own, a declared toolkit's, or core) in this run.

        This is how a handler reuses a toolkit tool: the toolkit stays the one home of the
        behavior, and the call shares this context (same run, same scratch, same dry run).
        """
        if self.tool_caller is None:
            raise RuntimeError("call_tool: this context was built without a tool dispatcher")
        return self.tool_caller(name, args)

    def confirm(self, prompt: str, preview: dict[str, Any] | None = None) -> bool:
        """Ask the user whether to proceed past a preview gate.

        In non-interactive runs the default confirmer simply returns
        ``self.confirmed``. CLI/notebook code can inject a ``confirmer``
        callable that displays the preview and prompts the user.
        """
        if self.confirmer is not None:
            return bool(self.confirmer(prompt, preview))
        return self.confirmed
