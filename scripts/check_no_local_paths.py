#!/usr/bin/env python
"""Fail when a staged artifact carries the builder's home directory.

A home directory names the person who built the release. Rust embeds source paths as panic
locations and C embeds them through `__FILE__`, so every crate compiled out of the cargo registry
(`~/.cargo/registry/src/...`) carries the builder's home into the binary unless the build remaps it
(`scripts/path_hygiene.sh`). 1.1.0's Windows artifacts shipped about 1,200 such strings. This is the
check that makes it a failure instead of a discovery.

    python scripts/check_no_local_paths.py dist/staging/<dir> [--forbid PREFIX ...]
    python scripts/check_no_local_paths.py --checkout FILE ...     (the pre-commit hook)

Without --forbid, forbids this machine's home directory (none inside a container running as root,
or on a GitHub-hosted runner).
--checkout also forbids the checkout's own absolute path: right for files committed to the public
repo, wrong for binaries, where llama.cpp compiles in its backend folder under the build tree.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

# The accounts GitHub-hosted runners build as. Like a container's `/root` they name nobody, and the
# prebuilt PDFium every macOS package ships was itself built on one: its libpdfium.dylib carries
# `/Users/runner/work/pdfium-binaries/...` ~650 times, which no remap of ours can reach. Exempt only
# under GitHub Actions, so a person who happens to be called `runner` is still protected.
RUNNER_HOMES = ("/Users/runner", "/home/runner", "C:\\Users\\runneradmin")


def forbidden_prefixes(
    home: str | None = None, windows: bool | None = None, ci: bool | None = None
) -> list[str]:
    """The prefixes that identify the builder: their home directory, unless it is a container's
    `/root` or a GitHub-hosted runner's account, which name nobody."""
    home = home if home is not None else os.path.expanduser("~")
    windows = os.name == "nt" if windows is None else windows
    ci = os.environ.get("GITHUB_ACTIONS") == "true" if ci is None else ci
    if not home or (not windows and home.rstrip("/") == "/root"):
        return []
    home = home.rstrip("/\\")
    if not home:  # `/` (some service accounts): nothing to forbid, and "" would match every path
        return []
    if ci and home.replace("/", "\\").lower() in (
        h.replace("/", "\\").lower() for h in RUNNER_HOMES
    ):
        return []
    return [home]


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
    ap.add_argument("paths", nargs="+", help="artifact directories and/or files")
    ap.add_argument("--forbid", action="append", help="a prefix to forbid (repeatable)")
    ap.add_argument(
        "--checkout", action="store_true", help="also forbid this checkout's absolute path"
    )
    args = ap.parse_args(argv)

    prefixes = args.forbid or forbidden_prefixes()
    if args.checkout:
        prefixes = [*prefixes, str(Path.cwd().resolve())]
    if not prefixes:
        print(
            "check_no_local_paths: nothing to forbid here (container root or GitHub runner); skipped"
        )
        return 0
    files: list[Path] = []
    for raw in args.paths:
        root = Path(raw)
        files += [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    bad = [(f, n) for f in files if (n := hits(f.read_bytes(), prefixes))]
    if bad:
        print("ERROR: these files carry a local path (a home directory names a person):")
        for f, n in bad:
            print(f"  {n:6d}  {f}")
        if args.checkout:
            print("Write paths relative to the repo, or as <repo>/... and ~/... (AGENTS.md).")
        elif hits(str(Path.cwd().resolve()).encode(), prefixes):
            # No remap reaches llama.cpp's backend folder, which it compiles in as a value.
            print("This checkout is inside the home directory, and llama.cpp compiles its backend")
            print("folder (under target/) into the binaries. Build from a checkout outside it,")
            print("for example under /Users/Shared (docs/RELEASE.md).")
        else:
            print("Build with scripts/build_native_kind.sh (scripts/path_hygiene.sh remaps it).")
        return 1
    print(f"check_no_local_paths: {len(files)} files, no builder home directory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
