#!/usr/bin/env python3
"""Generate media/knaif.ico from the square logo.

The Windows icon is a *generated* asset, like the licence reports: `media/logo-square.png`
is the source of truth and the `.ico` is built from it. Keeping the generator in the repo
means replacing the logo is a two-step job (drop in a new PNG, run this) rather than an
archaeology exercise.

A single `.ico` carries several resolutions and Windows picks per context — 16 px in
Explorer's details view and window title, 32 px in Alt+Tab and the Add/Remove Programs
row, 48 px for medium icons, 256 px in Settings' installed-apps list. Shipping one size
and letting Windows rescale looks visibly worse at the small end.

Usage::

    uv run --with pillow python scripts/gen_icon.py
    uv run --with pillow python scripts/gen_icon.py --check   # exit 1 if out of date

Consumers: `apps/cli/build.rs` embeds it into knaif.exe (with rerun-if-changed on it),
and `SetupIconFile` in installers/windows/knaif.iss gives setup.exe the same mark.

Note the source is deliberately a raster: a 256x256 PNG downscales acceptably here. If the
mark ever becomes detailed enough that 16 px turns to mud, hand-tune the small sizes rather
than adding more scaling — 16 px is 256 pixels in total, and no filter rescues a busy mark.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "media" / "logo-square.png"
TARGET = REPO_ROOT / "media" / "knaif.ico"

# 16/32/48/256 are the sizes Windows actually reaches for; 24/64/128 keep intermediate DPI
# scaling from interpolating between distant sizes.
SIZES = [16, 24, 32, 48, 64, 128, 256]


def build(destination: Path) -> None:
    from PIL import Image

    source = Image.open(SOURCE).convert("RGBA")
    if source.width != source.height:
        raise SystemExit(
            f"{SOURCE.name} is {source.width}x{source.height}; an icon source must be square"
        )
    source.save(destination, format="ICO", sizes=[(s, s) for s in SIZES])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed icon differs from what this script would produce",
    )
    args = parser.parse_args(argv)

    if not SOURCE.exists():
        print(f"missing source: {SOURCE}", file=sys.stderr)
        return 1

    if args.check:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "knaif.ico"
            build(candidate)
            if not TARGET.exists():
                print(f"{TARGET.relative_to(REPO_ROOT)} is missing", file=sys.stderr)
                return 1
            if candidate.read_bytes() != TARGET.read_bytes():
                print(
                    f"{TARGET.relative_to(REPO_ROOT)} is out of date — "
                    "run `uv run --with pillow python scripts/gen_icon.py`",
                    file=sys.stderr,
                )
                return 1
        print(f"{TARGET.relative_to(REPO_ROOT)} is up to date.")
        return 0

    build(TARGET)
    print(f"Wrote {TARGET.relative_to(REPO_ROOT)} ({', '.join(str(s) for s in SIZES)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
