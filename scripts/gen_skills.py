#!/usr/bin/env python3
"""Generate the Built-In Skills inventory in README.md from each skill.yaml.

Single source of truth: every skill describes itself in
``skills/<name>/skill.yaml`` via ``name`` + ``description`` (and optional
``status: stale``). This script renders that into the marker-delimited block in
README.md so new skills never require hand-editing the README.

Usage::

    python scripts/gen_skills.py            # rewrite README in place
    python scripts/gen_skills.py --check     # exit 1 if README is out of date

The --check mode is meant for CI: a PR that adds or renames a skill (or edits a
description) without regenerating fails the build.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "skills"
README = REPO_ROOT / "README.md"

BEGIN = "<!-- BEGIN GENERATED SKILLS -->"
END = "<!-- END GENERATED SKILLS -->"


def discover() -> list[tuple[str, str, str]]:
    """Return (name, description, status) for each skill, sorted by name."""
    rows: list[tuple[str, str, str]] = []
    for d in sorted(SKILLS_ROOT.iterdir()):
        manifest = d / "skill.yaml"
        if not d.is_dir() or not manifest.exists():
            continue
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        name = data.get("name", d.name)
        description = data.get("description", "").strip()
        status = data.get("status", "active")
        rows.append((name, description, status))
    return rows


def render(rows: list[tuple[str, str, str]]) -> str:
    lines: list[str] = []
    for name, description, status in rows:
        suffix = " *(stale — under rebuild)*" if status == "stale" else ""
        lines.append(f"- `{name}`{suffix}: {description}")
    return "\n".join(lines)


def splice(readme_text: str, block: str) -> str:
    if BEGIN not in readme_text or END not in readme_text:
        raise SystemExit(
            f"README is missing the {BEGIN} / {END} markers; add them around "
            "the Built-In Skills list first."
        )
    head, rest = readme_text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{block}\n{END}{tail}"


def main() -> int:
    check = "--check" in sys.argv[1:]
    rows = discover()
    block = render(rows)
    current = README.read_text(encoding="utf-8")
    updated = splice(current, block)

    if check:
        if current != updated:
            print(
                "README skill inventory is out of date. Run "
                "`just gen-skills` (or `python scripts/gen_skills.py`) and commit.",
                file=sys.stderr,
            )
            return 1
        print("README skill inventory is up to date.")
        return 0

    if current != updated:
        README.write_text(updated, encoding="utf-8")
        print(f"Updated {README.relative_to(REPO_ROOT)} ({len(rows)} skills).")
    else:
        print("README skill inventory already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
