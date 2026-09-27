#!/usr/bin/env python
"""Fail when a staged artifact carries the builder's home directory.

A home directory names the person who built the release. Rust embeds source paths as panic
locations and C embeds them through `__FILE__`, so every crate compiled out of the cargo registry
(`~/.cargo/registry/src/...`) carries the builder's home into the binary unless the build remaps it
(`scripts/path_hygiene.sh`). 1.1.0's Windows artifacts shipped about 1,200 such strings. This is the
check that makes it a failure instead of a discovery.

    python scripts/check_no_local_paths.py dist/staging/<dir> [--forbid PREFIX ...]

Without --forbid, forbids this machine's home directory (none inside a container running as root).
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


def forbidden_prefixes(home: str | None = None, windows: bool | None = None) -> list[str]:
    """The prefixes that identify the builder: their home directory, unless it is a container's
    `/root`, which names nobody."""
    home = home if home is not None else os.path.expanduser("~")
    windows = os.name == "nt" if windows is None else windows
    if not home or (not windows and home.rstrip("/") == "/root"):
        return []
    return [home.rstrip("/\\")]


def _pattern(prefix: str) -> re.Pattern[bytes]:
    # Separators may be `/`, `\` or an escaped `\\`; case varies on Windows. The prefix must end
    # at a separator or a non-name byte, so `C:\Users\al` does not match `C:\Users\alice`.
    parts = [re.escape(p.encode()) for p in re.split(r"[/\\]+", prefix) if p]
    sep = rb"[/\\]+"
    body = sep.join(parts)
    if re.match(r"^[A-Za-z]:", prefix) is None:
        body = sep + body
    return re.compile(body + rb"(?![A-Za-z0-9_.-])", re.IGNORECASE)


def hits(data: bytes, prefixes: list[str]) -> int:
    return sum(len(_pattern(p).findall(data)) for p in prefixes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="a staged artifact directory or a single file")
    ap.add_argument("--forbid", action="append", help="a prefix to forbid (repeatable)")
    args = ap.parse_args(argv)

    prefixes = args.forbid or forbidden_prefixes()
    if not prefixes:
        print("check_no_local_paths: nothing to forbid here (container root); skipped")
        return 0
    root = Path(args.path)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    bad = [(f, n) for f in files if (n := hits(f.read_bytes(), prefixes))]
    if bad:
        print("ERROR: staged files carry the builder's home directory (it names who built this):")
        for f, n in bad:
            print(f"  {n:6d}  {f}")
        print("Build with scripts/build_native_kind.sh, which remaps it (scripts/path_hygiene.sh).")
        return 1
    print(f"check_no_local_paths: {len(files)} files, no builder home directory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
