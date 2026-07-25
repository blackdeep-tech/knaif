"""App — the knaif.cli runtime that wires commands to a CommandAgent."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from knaif.agent import CommandAgent
from knaif.cli.build import build_registry

if TYPE_CHECKING:
    from knaif.orchestrator import InferenceOrchestrator


class App:
    """Entry point for a knaif.cli-powered natural-language CLI.

    Example::

        import knaif.cli as nk

        @nk.command(help="Return the current time")
        def now(tz: str = "UTC") -> dict:
            ...

        app = nk.App([now])
        app.run()          # reads sys.argv[1] as the natural-language utterance
        app.invoke("what time is it in Tokyo")  # programmatic call
    """

    def __init__(
        self,
        commands: list[Callable],
        *,
        orchestrator: InferenceOrchestrator | None = None,
        sandbox: Path | str | None = None,
        root: Path | str | None = None,
        system_header: str | None = None,
        confirmer: Any | None = None,
        show_plan: bool = False,
        require_approval: bool = False,
        plan_display: Any | None = None,
        plan_confirmer: Any | None = None,
    ) -> None:
        self.registry, self._tool_map = build_registry(commands)
        self._agent = CommandAgent.from_registry(
            self.registry,
            tool_map=self._tool_map,
            orchestrator=orchestrator,
            sandbox=sandbox,
            root=root,
            system_header=system_header,
            confirmer=confirmer,
            show_plan=show_plan,
            require_approval=require_approval,
            plan_display=plan_display,
            plan_confirmer=plan_confirmer,
        )

    def invoke(
        self,
        utterance: str,
        *,
        dry_run: bool = False,
        confirmed: bool = False,
    ) -> list[dict[str, Any]]:
        """Run *utterance* through the agent and return step results.

        Parameters
        ----------
        utterance:
            Free-text natural-language command from the user.
        dry_run:
            Pass through to :meth:`CommandAgent.execute_plan`.  Destructive
            steps require either ``dry_run=True`` or ``confirmed=True``.
        confirmed:
            Mark this call as user-confirmed (e.g. after showing a plan
            preview and receiving approval).
        """
        use_mock = self._agent.orchestrator is None
        return self._agent.run(
            utterance,
            use_mock=use_mock,
            dry_run=dry_run,
            confirmed=confirmed,
        )

    def run(self, argv: list[str] | None = None) -> None:
        """Parse *argv* (default: ``sys.argv[1:]``) and run as a CLI.

        The first argument is treated as the natural-language utterance.
        Prints each step result to stdout.
        """
        args = argv if argv is not None else sys.argv[1:]
        if not args:
            print("Usage: <app> <natural-language command>", file=sys.stderr)
            sys.exit(1)

        utterance = " ".join(args)
        results = self.invoke(utterance)
        import json

        for entry in results:
            # run() returns history dicts: {"step": {...}, "result": ..., ...}
            step = entry.get("step", {})
            tool = step.get("tool", "")
            result = entry.get("result")
            if tool == "clarify":
                question = result.get("question", "") if isinstance(result, dict) else ""
                print("Clarification needed:", question)
            elif tool == "reject":
                reason = result.get("reason", "") if isinstance(result, dict) else ""
                print("Rejected:", reason)
            elif tool == "done":
                continue  # internal terminal marker — nothing to print
            elif result is not None:
                print(json.dumps(result, indent=2, default=str))
