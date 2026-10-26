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
    # Whether the plan must ask for an alert (`alert_when_done`). False: it must not, so an
    # alert on such a row is a false alert and a wrong outcome.
    expected_alert: bool = False
    # The known folder the plan must write into (`knaif.known_folders`), e.g. "downloads".
    expected_folder: str | None = None
    # Folder rows (`fixture` ends in "/"): after the run, the row's folder holds files matching
    # each entry's `name` glob (exactly `count` when given, else at least one), each passing its
    # `criteria`; every `expect_gone` glob matches nothing; `expected_failures` files of the
    # batch fail (a broken input) while the rest finish.
    expect_files: list[dict[str, Any]] | None = None
    expect_gone: list[str] | None = None
    expected_failures: int | None = None

    @property
    def is_folder_row(self) -> bool:
        """True when the fixture is a folder (`batch/`), copied whole into the row's folder."""
        return isinstance(self.fixture, str) and self.fixture.endswith("/")

    def __post_init__(self) -> None:
        if self.expected_outcome not in _VALID_OUTCOMES:
            raise ValueError(
                f"expected_outcome must be one of {sorted(_VALID_OUTCOMES)!r}, "
                f"got {self.expected_outcome!r}"
            )
        if self.grade not in _VALID_GRADES:
            raise ValueError(f"grade must be one of {sorted(_VALID_GRADES)!r}, got {self.grade!r}")
        if not isinstance(self.expected_alert, bool):
            raise ValueError(
                f"expected_alert must be true or false, got {self.expected_alert!r} "
                f"in row {self.id!r}"
            )
        if self.expected_folder is not None:
            from knaif.known_folders import KNOWN_FOLDERS

            if self.expected_folder not in KNOWN_FOLDERS:
                raise ValueError(
                    f"expected_folder must be one of {list(KNOWN_FOLDERS)!r}, "
                    f"got {self.expected_folder!r} in row {self.id!r}"
                )
        if not self.utterances:
            raise ValueError(f"utterances must be a non-empty list for row {self.id!r}")
        self._check_folder_fields()
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

    def _check_folder_fields(self) -> None:
        folder_fields = (self.expect_files, self.expect_gone, self.expected_failures)
        if all(f is None for f in folder_fields):
            return
        if not self.is_folder_row:
            raise ValueError(
                f"row {self.id!r}: expect_files / expect_gone / expected_failures need a "
                f"folder fixture (ending in '/'), got {self.fixture!r}"
            )
        if self.expected_outcome != "plan":
            raise ValueError(f"row {self.id!r}: folder expectations need expected_outcome 'plan'")
        for i, entry in enumerate(self.expect_files or []):
            name = entry.get("name") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name:
                raise ValueError(f"expect_files[{i}] of row {self.id!r} needs a 'name' glob")
            count = entry.get("count")
            if count is not None and (
                isinstance(count, bool) or not isinstance(count, int) or count < 1
            ):
                raise ValueError(
                    f"expect_files[{i}] of row {self.id!r}: count must be a positive integer"
                )
        for pattern in self.expect_gone or []:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"expect_gone of row {self.id!r} holds an empty pattern")
        failures = self.expected_failures
        if failures is not None and (
            isinstance(failures, bool) or not isinstance(failures, int) or failures < 0
        ):
            raise ValueError(
                f"row {self.id!r}: expected_failures must be a count, got {failures!r}"
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
        if self.expected_alert:
            d["expected_alert"] = True
        if self.expected_folder is not None:
            d["expected_folder"] = self.expected_folder
        if self.expect_files is not None:
            d["expect_files"] = self.expect_files
        if self.expect_gone is not None:
            d["expect_gone"] = self.expect_gone
        if self.expected_failures is not None:
            d["expected_failures"] = self.expected_failures
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
            expected_alert=data.get("expected_alert", False),
            expected_folder=data.get("expected_folder"),
            expect_files=(
                list(data["expect_files"]) if data.get("expect_files") is not None else None
            ),
            expect_gone=list(data["expect_gone"]) if data.get("expect_gone") is not None else None,
            expected_failures=data.get("expected_failures"),
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


def save_row(path: Path | str, row: CorpusRow) -> None:
    """Write *row* back into the corpus at *path*, changing only its own line.

    Every other line keeps its bytes and its line ending. Kept rows are immutable once a
    review has pinned them (`data/eval_kept.sha256`), and re-serialising the whole file, as
    `save_corpus` does, would change lines nobody edited.
    """
    path = Path(path)
    text = path.read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        if not body.strip() or body.lstrip().startswith("#"):
            continue
        if str(json.loads(body)["id"]) == row.id:
            ending = line[len(body) :] or ("\r\n" if "\r\n" in text else "\n")
            lines[i] = json.dumps(row.to_dict(), ensure_ascii=False) + ending
            path.write_bytes("".join(lines).encode("utf-8"))
            return
    raise ValueError(f"row {row.id!r} is not in {path}")
