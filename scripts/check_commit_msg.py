#!/usr/bin/env python3
"""Validate a commit message against the knaif commit convention.

The convention is Conventional-Commits-shaped::

    type(scope)!: subject

    optional body

    optional footers

Run as a ``commit-msg`` hook (pre-commit installs it there), so the argument is
the path to the file git has staged as the message::

    python scripts/check_commit_msg.py .git/COMMIT_EDITMSG

Messages git generates or rewrites itself are skipped, not rejected: merges,
``git revert``'s default subject, and ``fixup!``/``squash!``/``amend!`` commits
destined for ``--autosquash``. Enforcing a shape on those would only teach
contributors to reach for ``--no-verify``.

The full convention — including which type to reach for — is documented in
CONTRIBUTING.md under "Git conventions"; keep the two in step.
"""

from __future__ import annotations

import argparse
import re
import sys

# Types, grouped as they are documented in CONTRIBUTING.md. Keep both lists in step.
CODE_TYPES = ["feat", "fix", "perf", "refactor"]
SUPPORT_TYPES = ["docs", "test", "build", "ci", "chore", "deps", "revert"]
DOMAIN_TYPES = ["eval", "corpus", "snapshot", "model"]
TYPES = CODE_TYPES + SUPPORT_TYPES + DOMAIN_TYPES

MAX_SUBJECT = 72

# type(scope)!: subject  — scope and the breaking-change "!" are both optional.
HEADER_RE = re.compile(
    r"^(?P<type>[a-z]+)"
    r"(?:\((?P<scope>[a-z0-9][a-z0-9._/-]*)\))?"
    r"(?P<breaking>!)?"
    r": "
    r"(?P<subject>.+)$"
)

# Subjects git writes on its own, or that are consumed by a later rebase.
SKIP_PREFIXES = ("fixup!", "squash!", "amend!")
SKIP_RE = re.compile(
    r"^(?:Merge\s|Revert\s\"|Applying\s|Rebase\s)",
)

# `git commit --verbose` appends the staged diff below this marker.
SCISSORS = "# ------------------------ >8 ------------------------"


def strip_comments(raw: str) -> str:
    """Drop git's comment lines and the --verbose diff, as git itself would."""
    lines: list[str] = []
    for line in raw.splitlines():
        if line.rstrip() == SCISSORS:
            break
        if line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip("\n")


def check(message: str) -> list[str]:
    """Return a list of problems; empty means the message conforms."""
    lines = message.splitlines()
    if not lines or not lines[0].strip():
        return ["the commit message is empty"]

    header = lines[0]

    if header.startswith(SKIP_PREFIXES) or SKIP_RE.match(header):
        return []

    problems: list[str] = []
    match = HEADER_RE.match(header)

    if not match:
        problems.append(
            f"subject line does not match `type(scope): subject`\n" f"    got: {header!r}"
        )
        return problems

    commit_type = match.group("type")
    subject = match.group("subject")

    if commit_type not in TYPES:
        problems.append(f"unknown type {commit_type!r} - use one of: {', '.join(TYPES)}")

    if len(header) > MAX_SUBJECT:
        problems.append(f"subject line is {len(header)} characters; keep it within {MAX_SUBJECT}")

    if subject.endswith("."):
        problems.append("subject should not end with a period")

    if len(lines) > 1 and lines[1].strip():
        problems.append("leave a blank line between the subject and the body")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "message_file",
        help="path to the file holding the commit message (git passes .git/COMMIT_EDITMSG)",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.message_file, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        print(f"could not read the commit message: {exc}", file=sys.stderr)
        return 1

    problems = check(strip_comments(raw))
    if not problems:
        return 0

    print("Commit message rejected:\n", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\n"
        "  Format:  type(scope): subject\n"
        f"  Types:   {', '.join(TYPES)}\n"
        "  Scope:   optional - core, cli, sdk, native, training, contracts,\n"
        "           installers, evals, or a skill name (ffmpeg, documents, io)\n"
        "\n"
        "  Examples:\n"
        "    fix(sdk): validate Arg schemas on Python 3.10\n"
        "    feat(ffmpeg): add a batch-convert intent\n"
        "    snapshot(documents): re-lock the bar at 0.91 success\n"
        "    refactor(native)!: rename HandlerContext::sandbox to root\n"
        "\n"
        "  See CONTRIBUTING.md -> Git conventions. To amend: git commit --amend\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
