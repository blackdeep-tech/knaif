"""Versioned JSONL corpus: load, validate, and save CorpusRow objects."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VALID_OUTCOMES = frozenset({"plan", "clarify", "reject"})
_VALID_GRADES = frozenset({"full", "routing"})


@dataclass
class CorpusRow:
    id: str
    utterances: list[str]
    expected_outcome: str  # "plan" | "clarify" | "reject"
    fixture: str | None = None
    baseline: dict[str, Any] | None = None
    expected_tool: str | None = None
    tolerances: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    success_criteria: dict[str, Any] = field(default_factory=dict)
    # Multi-output rows: one entry per deliverable, each a dict with a string
    # "command" (reference ffmpeg command, run in sequence in one dir), optional
    # "criteria" (per-output success_criteria), and optional "tolerances".
    outputs: list[dict[str, Any]] | None = None
    # Who human-reviewed this row's `outputs` chain (separate from
    # baseline.validated_by, which covers the single-command baseline). Lets the
    # authoring reviewer track chain validation independently.
    outputs_validated_by: str | None = None
    # The expected public-tool sequence for multi-intent plans
    # (e.g. ["trim_video", "extract_audio"]); expected_tool stays = expected_tools[0].
    expected_tools: list[str] | None = None
    # Grading mode: "full" grades the produced artifact against success_criteria;
    # "routing" grades on outcome only (for glob/batch rows that can't materialize
    # a single artifact in the success eval — their routing is what we verify).
    grade: str = "full"

    def __post_init__(self) -> None:
        if self.expected_outcome not in _VALID_OUTCOMES:
            raise ValueError(
                f"expected_outcome must be one of {sorted(_VALID_OUTCOMES)!r}, "
                f"got {self.expected_outcome!r}"
            )
        if self.grade not in _VALID_GRADES:
            raise ValueError(f"grade must be one of {sorted(_VALID_GRADES)!r}, got {self.grade!r}")
        if not self.utterances:
            raise ValueError(f"utterances must be a non-empty list for row {self.id!r}")
        if self.outputs is not None:
            if not self.outputs:
                raise ValueError(f"outputs must be a non-empty list for row {self.id!r}")
            if self.expected_outcome != "plan":
                raise ValueError(
                    f"row {self.id!r} declares outputs but expected_outcome is "
                    f"{self.expected_outcome!r}; multi-output rows must be 'plan'"
                )
            for i, out in enumerate(self.outputs):
                if not isinstance(out, dict) or not isinstance(out.get("command"), str):
                    raise ValueError(
                        f"outputs[{i}] of row {self.id!r} must be a dict with a string 'command'"
                    )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "utterances": self.utterances,
            "expected_outcome": self.expected_outcome,
            "fixture": self.fixture,
            "baseline": self.baseline,
            "expected_tool": self.expected_tool,
            "tolerances": self.tolerances,
            "tags": self.tags,
        }
        if self.success_criteria:
            d["success_criteria"] = self.success_criteria
        if self.outputs is not None:
            d["outputs"] = self.outputs
        if self.outputs_validated_by is not None:
            d["outputs_validated_by"] = self.outputs_validated_by
        if self.expected_tools is not None:
            d["expected_tools"] = self.expected_tools
        if self.grade != "full":
            d["grade"] = self.grade
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorpusRow:
        return cls(
            id=data["id"],
            utterances=list(data["utterances"]),
            expected_outcome=data["expected_outcome"],
            fixture=data.get("fixture"),
            baseline=data.get("baseline"),
            expected_tool=data.get("expected_tool"),
            tolerances=dict(data.get("tolerances") or {}),
            tags=list(data.get("tags") or []),
            success_criteria=dict(data.get("success_criteria") or {}),
            outputs=(list(data["outputs"]) if data.get("outputs") is not None else None),
            outputs_validated_by=data.get("outputs_validated_by"),
            expected_tools=(
                list(data["expected_tools"]) if data.get("expected_tools") is not None else None
            ),
            grade=data.get("grade", "full"),
        )


def needs_review(row: CorpusRow) -> bool:
    """True if *row* has un-reviewed reference data the authoring notebook should surface.

    For a ``plan`` row the ``outputs`` chain, when present, is the authoritative
    reference — the single-command ``baseline.command`` cannot represent a
    multi-step deliverable and is treated as a superseded seed. So:
    - a row with ``outputs`` needs review until those outputs are ``outputs_validated_by`` someone
      (its ``baseline.validated_by`` is irrelevant), OTHERWISE
    - a single-command row needs review until its ``baseline.command`` is ``validated_by`` someone.

    This avoids the loop where accepting a chain stamps ``outputs_validated_by`` but the
    row keeps resurfacing because of a leftover, never-validated ``baseline.command``.
    """
    if row.expected_outcome != "plan":
        return False
    if row.outputs:
        return not row.outputs_validated_by
    baseline = row.baseline or {}
    return bool(baseline.get("command")) and not baseline.get("validated_by")


def load_corpus(path: Path | str) -> list[CorpusRow]:
    """Load a JSONL corpus file and return a list of CorpusRow objects."""
    path = Path(path)
    rows: list[CorpusRow] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON parse error at line {lineno} of {path}: {exc}") from exc
            try:
                rows.append(CorpusRow.from_dict(data))
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Schema error at line {lineno} of {path}: {exc}") from exc
    return rows


def save_corpus(rows: list[CorpusRow], path: Path | str) -> None:
    """Write corpus rows to a JSONL file, one row per line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row.to_dict(), ensure_ascii=False) + "\n")
