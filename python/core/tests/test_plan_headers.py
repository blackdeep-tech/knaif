"""Lint the durable-plan docs so the index, headers, and statuses can't drift.

Guards the header contract documented in ``docs/plans/README.md`` (*Plan header
format*): every plan under ``docs/plans/YYYY-MM-DD-*.md`` opens with a ``Status``
(from a fixed legend), a ``Created`` date, a ``Completed`` field (a date or ``—``),
and carries a one-line ``**Goal:**``. Also checks the README index table so its
status column can only hold legend values, and the release fields: each plan's
``**Release:**``, each release index's header, and that an index's scope and its
plans name each other.

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


def _plan_files(plans_dir: Path = PLANS_DIR) -> list[Path]:
    files = sorted(plans_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.md"))
    assert files, f"no plan files found under {plans_dir} (wrong CWD?)"
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
        if not re.match(r"^\|\s*\d{4}-", line):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0] is empty (leading pipe); date, plan, status, notes follow.
        if len(cells) < 4:
            continue
        status = cells[3].split()[0] if cells[3] else ""
        if status not in LEGAL_STATUSES:
            violations.append(f"README index row for {cells[2]}: illegal status '{cells[3]}'")
    assert not violations, "README index status violations:\n" + "\n".join(violations)


# ── Release fields (docs/plans/README.md, *Release index*) ────────────────────
#
# The branch rules behind these fields are in docs/RELEASE.md, *Branches and release lanes*.
# A release index lives on its release branch, so a checkout without it (main, or a feature
# branch started from main) skips the scope check for that release.

# Plans created from this date on must say which release they ship in.
RELEASE_FIELD_CUTOFF = "2026-09-30"
_RELEASE_RE = re.compile(r"\*\*Release:\*\*\s*([^\s·]+)")
_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")
_INDEX_NAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}-release-(\d+\.\d+\.\d+)\.md")
_LANE_RE = re.compile(r"\*\*Lane:\*\*\s*(minor|patch)\b")
_BRANCH_RE = re.compile(r"\*\*Branch:\*\*\s*`([^`]+)`")
_PLAN_LINK_RE = re.compile(r"\]\((\d{4}-\d{2}-\d{2}-[^)#\s]+\.md)\)")


def _release_value(text: str) -> str | None:
    m = _RELEASE_RE.search(text)
    return m.group(1) if m else None


def _scope_links(text: str) -> set[str]:
    """Plan files linked from the index's ``## Scope`` section (and nowhere else)."""
    m = re.search(r"^## Scope\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return set(_PLAN_LINK_RE.findall(m.group(1))) if m else set()


def _index_violations(name: str, version: str, text: str, release: str | None) -> list[str]:
    out: list[str] = []
    if release is not None and release != version:
        out.append(f"{name}: '**Release:**' must be {version}")
    lane = _LANE_RE.search(text)
    if lane is None:
        out.append(f"{name}: missing or illegal '**Lane:**' (minor or patch)")
    elif lane.group(1) == "minor" and not version.endswith(".0"):
        out.append(f"{name}: Lane 'minor' needs a version ending in .0")
    elif lane.group(1) == "patch" and version.endswith(".0"):
        out.append(f"{name}: Lane 'patch' needs a version not ending in .0")
    branch = _BRANCH_RE.search(text)
    if branch is None or branch.group(1) != f"release/{version}":
        out.append(f"{name}: '**Branch:**' must be `release/{version}`")
    return out


def _release_violations(plans_dir: Path) -> list[str]:
    texts = {f.name: f.read_text(encoding="utf-8") for f in _plan_files(plans_dir)}
    releases = {name: _release_value(text) for name, text in texts.items()}
    indexes: dict[str, str] = {}  # version -> index file name
    for name in texts:
        m = _INDEX_NAME_RE.fullmatch(name)
        if m:
            indexes[m.group(1)] = name

    violations: list[str] = []
    for name, text in texts.items():
        release = releases[name]
        if release is None:
            if name[:10] >= RELEASE_FIELD_CUTOFF:
                violations.append(f"{name}: missing '**Release:**'")
        elif release not in ("—", "main") and not _SEMVER_RE.fullmatch(release):
            violations.append(f"{name}: illegal Release '{release}' (X.Y.Z, '—' or 'main')")
            release = None

        index_match = _INDEX_NAME_RE.fullmatch(name)
        if index_match:
            version = index_match.group(1)
            violations += _index_violations(name, version, text, release)
            for linked in sorted(_scope_links(text)):
                if linked in texts and releases[linked] != version:
                    violations.append(
                        f"{name}: scope lists {linked}, whose Release is '{releases[linked]}'"
                    )
        elif release in indexes and name not in _scope_links(texts[indexes[release]]):
            violations.append(f"{name}: Release {release} but not in {indexes[release]}'s scope")
    return violations


def _plan(created: str, release: str | None = None, extra: str = "") -> str:
    lines = [
        "# A plan",
        "",
        f"**Status:** Active · **Created:** {created} · **Completed:** —",
        "**Owner:** core · **Ref:** —",
    ]
    if release is not None:
        lines.append(f"**Release:** {release}")
    lines += ["", "**Goal:** Do a thing.", "", extra]
    return "\n".join(lines)


def _index(created: str, version: str, lane: str, scope: list[str], branch: str = "") -> str:
    rows = "\n".join(f"| [{s}]({s}.md) | `feat/x` | Active | a line |" for s in scope)
    return _plan(
        created,
        version,
        extra=(
            f"**Lane:** {lane} · **Branch:** `{branch or 'release/' + version}`\n\n"
            "## Scope\n\n| Plan | Branch | Status | User-facing line |\n|---|---|---|---|\n"
            f"{rows}\n\n## Decisions\n\n- [not scope](2026-10-01-other.md)\n"
        ),
    )


def _write(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_real_plans_have_no_release_violations() -> None:
    assert _release_violations(PLANS_DIR) == []


def test_plan_files_are_not_limited_to_one_year(tmp_path: Path) -> None:
    _write(tmp_path, {"2027-01-02-later.md": _plan("2027-01-02", "main")})
    assert [f.name for f in _plan_files(tmp_path)] == ["2027-01-02-later.md"]


def test_release_field_required_from_cutoff(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "2026-09-29-old.md": _plan("2026-09-29"),
            "2026-09-30-new.md": _plan("2026-09-30"),
        },
    )
    assert _release_violations(tmp_path) == ["2026-09-30-new.md: missing '**Release:**'"]


def test_release_field_values(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "2026-10-01-a.md": _plan("2026-10-01", "1.3.0"),
            "2026-10-01-b.md": _plan("2026-10-01", "—"),
            "2026-10-01-c.md": _plan("2026-10-01", "main"),
            "2026-10-01-d.md": _plan("2026-10-01", "1.3"),
            "2026-09-01-e.md": _plan("2026-09-01", "next"),
        },
    )
    assert _release_violations(tmp_path) == [
        "2026-09-01-e.md: illegal Release 'next' (X.Y.Z, '—' or 'main')",
        "2026-10-01-d.md: illegal Release '1.3' (X.Y.Z, '—' or 'main')",
    ]


def test_release_index_header(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "2026-10-01-release-1.3.0.md": _index("2026-10-01", "1.3.0", "minor", []),
            "2026-10-02-release-1.2.1.md": _index("2026-10-02", "1.2.1", "minor", []),
            "2026-10-03-release-1.4.0.md": _index("2026-10-03", "1.4.0", "fast", []),
            "2026-10-04-release-1.5.0.md": _index(
                "2026-10-04", "1.5.0", "minor", [], branch="release/1.5"
            ),
        },
    )
    assert _release_violations(tmp_path) == [
        "2026-10-02-release-1.2.1.md: Lane 'minor' needs a version ending in .0",
        "2026-10-03-release-1.4.0.md: missing or illegal '**Lane:**' (minor or patch)",
        "2026-10-04-release-1.5.0.md: '**Branch:**' must be `release/1.5.0`",
    ]


def test_release_index_release_field_matches_its_version(tmp_path: Path) -> None:
    text = _index("2026-10-01", "1.3.0", "minor", []).replace(
        "**Release:** 1.3.0", "**Release:** main"
    )
    _write(tmp_path, {"2026-10-01-release-1.3.0.md": text})
    assert _release_violations(tmp_path) == [
        "2026-10-01-release-1.3.0.md: '**Release:**' must be 1.3.0"
    ]


def test_patch_lane_needs_a_patch_version(tmp_path: Path) -> None:
    _write(tmp_path, {"2026-10-01-release-1.3.0.md": _index("2026-10-01", "1.3.0", "patch", [])})
    assert _release_violations(tmp_path) == [
        "2026-10-01-release-1.3.0.md: Lane 'patch' needs a version not ending in .0"
    ]


def test_release_scope_and_plans_agree(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "2026-10-01-release-1.3.0.md": _index(
                "2026-10-01",
                "1.3.0",
                "minor",
                ["2026-10-02-listed", "2026-10-02-wrong", "2026-10-02-unmerged"],
            ),
            "2026-10-02-listed.md": _plan("2026-10-02", "1.3.0"),
            "2026-10-02-wrong.md": _plan("2026-10-02", "—"),
            "2026-10-02-missing.md": _plan("2026-10-02", "1.3.0"),
            # Links outside the Scope section do not count as scope.
            "2026-10-01-other.md": _plan("2026-10-01", "main"),
        },
    )
    assert _release_violations(tmp_path) == [
        "2026-10-01-release-1.3.0.md: scope lists 2026-10-02-wrong.md, whose Release is '—'",
        "2026-10-02-missing.md: Release 1.3.0 but not in 2026-10-01-release-1.3.0.md's scope",
    ]


def test_release_without_index_in_checkout_is_not_checked(tmp_path: Path) -> None:
    # On a feature branch started from main, the release index is on another branch.
    _write(tmp_path, {"2026-10-02-feature.md": _plan("2026-10-02", "1.3.0")})
    assert _release_violations(tmp_path) == []
