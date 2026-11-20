#!/usr/bin/env python
"""prepare-commit-msg hook: add the committer's DCO sign-off to every commit message.

knaif takes contributions under a DCO sign-off (owner, 2026-11-17), and the owner asked for every
commit made here to carry one without anyone remembering `-s` (2026-11-20). The line is the
committer's own git identity, `Signed-off-by: <user.name> <user.email>`, added once with
`git interpret-trailers`, so a message that already has it (`git commit -s`, an amend, a rebase)
is left alone, and another person's sign-off is kept. Without a git identity it does nothing.

    python scripts/add_signoff.py <message file> [<source> [<sha>]]     (pre-commit passes these)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _config(key: str) -> str:
    out = subprocess.run(["git", "config", "--get", key], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: add_signoff.py <commit message file> [source [sha]]", file=sys.stderr)
        return 2
    msg = Path(argv[0])
    name, email = _config("user.name"), _config("user.email")
    if not name or not email:
        return 0
    trailer = f"Signed-off-by: {name} <{email}>"
    if trailer in msg.read_text(encoding="utf-8", errors="replace").splitlines():
        return 0
    subprocess.run(
        ["git", "interpret-trailers", "--in-place", "--if-exists", "addIfDifferent",
         "--trailer", trailer, str(msg)],
        check=True,
    )  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
