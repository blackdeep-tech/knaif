"""Skill base class — skill authors subclass this and set tools = [...]."""

from __future__ import annotations

from typing import Any

from .tool import Intent, Step


class Skill:
    """Base class for OOP-style skill implementations.

    Subclass, declare ``tools = [StepClass, IntentClass, ...]``, and optionally
    override ``preflight``, ``format_results``, and ``run_artifact``.
    """

    tools: list[type[Step | Intent]] = []

    #: Renames a skill's `resolve_output_collisions` applied to the current plan, as
    #: ``{"step", "requested", "used"}`` records. Rebound per plan, never accumulated.
    output_substitutions: list[dict[str, str]] = []

    def preflight(self, tool: str, args: dict[str, Any], **kw: Any) -> list[str]:
        return []

    def format_results(self, results: list[dict[str, Any]], *, dry_run: bool) -> None:
        return None

    def run_artifact(self, cmd: Any, fixture: Any, out_dir: Any) -> Any:
        return None

    def resolve_output_collisions(
        self, plan: list[dict[str, Any]], *, sandbox: Any = None
    ) -> list[dict[str, Any]]:
        """Rewrite plan outputs that would destroy a file, and rebind their consumers.

        Called once per plan, after stem resolution and before expansion — the first point
        where both the whole plan and real disk state are visible. Default: no-op.

        The rule a skill implements here, if it implements one at all:

            A name an earlier step declares it will write binds, for every later step, to
            what that step actually wrote. Substitute the old output name with the resolved
            one across steps **strictly after** the producer; never re-infer which file was
            meant.

        Why it is a hook and not core behaviour: *whether* an output collides at all, and
        what a free replacement is called, are skill policy. ffmpeg renders every command
        with ``-y``, so an output equal to its own input truncates the source before ffmpeg
        reads it; a skill whose handlers write to a temporary file and move it has no such
        problem. Core supplies the extension point and keeps no naming logic of its own.

        See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T5b.
        """
        return plan
