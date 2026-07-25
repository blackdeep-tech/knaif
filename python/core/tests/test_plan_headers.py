"""Lint the durable-plan docs so the index, headers, and statuses can't drift.

Guards the header contract documented in ``docs/plans/README.md`` (*Plan header
format*): every plan under ``docs/plans/YYYY-MM-DD-*.md`` opens with a ``Status``
(from a fixed legend), a ``Created`` date, a ``Completed`` field (a date or ``—``),
and carries a one-line ``**Goal:**``. Also checks the README index table so its
status column can only hold legend values.

Run as part of ``just check`` (via the full pytest suite). If it fails, fix the
plan header — do not loosen the lint.
"""

from __future__ import annotations

import re
from pathlib import Path

PLANS_DIR = Path("docs") / "plans"
LEGAL_STATUSES = {"Done", "Active", "Draft", "Planning", "Superseded"}
# Completed may be a date or an em dash / hyphen placeholder ("no date recorded").
_COMPLETED_RE = re.compile(r"\*\*Completed:\*\*\s*(\d{4}-\d{2}-\d{2}|—|-)")
_CREATED_RE = re.compile(r"\*\*Created:\*\*\s*(\d{4}-\d{2}-\d{2})")
_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*(?:·|$)", re.MULTILINE)


def _plan_files() -> list[Path]:
    files = sorted(PLANS_DIR.glob("2026-*.md"))
    assert files, f"no plan files found under {PLANS_DIR} (wrong CWD?)"
    return files


def _status_value(text: str) -> str | None:
    m = _STATUS_RE.search(text)
    if not m:
        return None
    # First token of the status cell is the canonical value.
    return m.group(1).strip().split()[0] if m.group(1).strip() else None


def test_every_plan_header_is_conformant() -> None:
    violations: list[str] = []
    for f in _plan_files():
        text = f.read_text(encoding="utf-8")
        name = f.name
        status = _status_value(text)
        if status is None:
            violations.append(f"{name}: missing '**Status:**' line")
        elif status not in LEGAL_STATUSES:
            violations.append(
                f"{name}: illegal Status '{status}' (allowed: {sorted(LEGAL_STATUSES)})"
            )
        if not _CREATED_RE.search(text):
            violations.append(f"{name}: missing '**Created:** YYYY-MM-DD'")
        if not _COMPLETED_RE.search(text):
            violations.append(f"{name}: missing '**Completed:**' (a date or '—')")
        if "**Goal:**" not in text:
            violations.append(f"{name}: missing one-line '**Goal:**'")
    assert not violations, "plan-header violations:\n" + "\n".join(violations)


def test_readme_index_statuses_are_legal() -> None:
    readme = (PLANS_DIR / "README.md").read_text(encoding="utf-8")
    violations: list[str] = []
    for line in readme.splitlines():
        # Index rows look like: | 2026-.. | [name](file.md) | Status | notes |
        if not re.match(r"^\|\s*2026-", line):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0] is empty (leading pipe); date, plan, status, notes follow.
        if len(cells) < 4:
            continue
        status = cells[3].split()[0] if cells[3] else ""
        if status not in LEGAL_STATUSES:
            violations.append(f"README index row for {cells[2]}: illegal status '{cells[3]}'")
    assert not violations, "README index status violations:\n" + "\n".join(violations)
