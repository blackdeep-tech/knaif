#!/usr/bin/env python3
"""Fail if a generated mirror copy has drifted from its canonical source.

Three files exist twice in this repo on purpose, and each pair has a canonical
half that must be the one edited:

- ``contracts/runtime/core_tools.yaml`` is the cross-language source of truth,
  but the file is import-critical, so a byte-identical copy ships next to the
  module inside the wheel.
- ``LICENSE`` / ``NOTICE`` must travel with every distributed copy, and
  setuptools resolves ``license-files`` relative to ``python/core/`` (PEP 639
  forbids ``..``), so the wheel needs its own copies.

The test suite already guards all three (``test_runtime_data.py``,
``test_license_files.py``) — those remain the authoritative gate. This script is
the same check in a form fast enough to run on every commit, so drift is caught
before it is committed rather than at the end of a full pytest run::

    python scripts/check_mirrors.py

Exits non-zero listing the ``just`` recipe that repairs each drifted pair.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (canonical, mirror, recipe that regenerates the mirror)
MIRRORS: list[tuple[str, str, str]] = [
    (
        "contracts/runtime/core_tools.yaml",
        "python/core/knaif/core_tools.yaml",
        "just sync-runtime",
    ),
    ("LICENSE", "python/core/LICENSE", "just sync-license"),
    ("NOTICE", "python/core/NOTICE", "just sync-license"),
]


def main() -> int:
    problems: list[str] = []

    for canonical_rel, mirror_rel, recipe in MIRRORS:
        canonical = REPO_ROOT / canonical_rel
        mirror = REPO_ROOT / mirror_rel

        if not canonical.exists():
            problems.append(f"{canonical_rel} is missing - it is the canonical file")
            continue
        if not mirror.exists():
            problems.append(f"{mirror_rel} is missing - run `{recipe}`")
            continue

        # Compare bytes: the drift guard in the test suite does too, and a
        # line-ending change is real drift for a file shipped in a wheel.
        if canonical.read_bytes() != mirror.read_bytes():
            problems.append(
                f"{mirror_rel} has drifted from {canonical_rel} - "
                f"edit the canonical file, then run `{recipe}`"
            )

    if not problems:
        return 0

    print("Generated-copy drift:\n", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\n  Edit the canonical file, never the mirror; re-run the recipe and "
        "stage the result.\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
