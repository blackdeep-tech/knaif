"""Update the site pnpm pin, replacing incompatible Corepack shims when necessary."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"{name} is not on PATH. Install Node.js and npm first.")
    return path


def is_corepack_shim(path: str) -> bool:
    # Windows npm shims (.cmd) and Unix symlinks/scripts both reference Corepack.
    resolved = Path(path).resolve()
    if "corepack" in resolved.parts:
        return True
    with resolved.open("rb") as stream:
        return b"corepack" in stream.read(16384).lower()


def run(*args: str) -> None:
    print("Running: " + " ".join(args), flush=True)
    # cwd matters: Corepack chooses a version before pnpm processes --dir.
    subprocess.run(args, cwd=SITE, check=True)


def update(version: str) -> None:
    # Only versions and dist-tags, including prereleases; no shell metacharacters
    # may reach Windows .cmd launchers.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", version):
        raise ValueError("Use a pnpm version or dist-tag, such as 12.4.1 or latest.")

    pnpm = shutil.which("pnpm")
    corepack = executable("corepack") if pnpm and is_corepack_shim(pnpm) else None
    if pnpm is None or corepack:
        npm = executable("npm")
        if corepack:
            print("Replacing the Corepack pnpm launcher with standalone pnpm.", flush=True)
            run(corepack, "disable", "pnpm")
        try:
            run(npm, "install", "--global", f"pnpm@{version}")
        except subprocess.CalledProcessError:
            if corepack:
                print("Installation failed; restoring the Corepack launcher.", flush=True)
                run(corepack, "enable", "pnpm")
            raise
        pnpm = executable("pnpm")

    run(pnpm, "self-update", version)
    run(pnpm, "install")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", default="latest")
    args = parser.parse_args()
    try:
        update(args.version)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
