"""Protocols and shared data types for the knaif eval suite.

Skills implement Verifier and Reviewer in their own eval/ subpackages.
The framework only depends on these interfaces — no skill-specific code here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class VerifyResult:
    score: float  # 0.0–1.0
    matched: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    verifier_kind: str = "plan"  # "command" | "output" | "plan"


@runtime_checkable
class Verifier(Protocol):
    """Compare a model-produced artifact against a reference and return a score."""

    def __call__(
        self,
        artifact_path: Path,
        reference_path: Path,
        tolerances: dict[str, Any],
        sandbox: Path,
    ) -> VerifyResult: ...


@runtime_checkable
class Reviewer(Protocol):
    """Render a per-row HTML card for the triage report."""

    def render_row(
        self,
        row: dict[str, Any],
        arm_outputs: dict[str, Path | None],
        reference: Path | None,
    ) -> str: ...


@runtime_checkable
class ReferenceLoader(Protocol):
    """Load the reference artifact path for a corpus row."""

    def __call__(self, row: dict[str, Any]) -> Path | None: ...
