"""Handler API: shared context dataclass for all tool handlers."""

from __future__ import annotations

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

    def confirm(self, prompt: str, preview: dict[str, Any] | None = None) -> bool:
        """Ask the user whether to proceed past a preview gate.

        In non-interactive runs the default confirmer simply returns
        ``self.confirmed``. CLI/notebook code can inject a ``confirmer``
        callable that displays the preview and prompts the user.
        """
        if self.confirmer is not None:
            return bool(self.confirmer(prompt, preview))
        return self.confirmed
