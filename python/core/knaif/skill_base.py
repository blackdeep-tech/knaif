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

    def preflight(self, tool: str, args: dict[str, Any], **kw: Any) -> list[str]:
        return []

    def format_results(self, results: list[dict[str, Any]], *, dry_run: bool) -> None:
        return None

    def run_artifact(self, cmd: Any, fixture: Any, out_dir: Any) -> Any:
        return None
