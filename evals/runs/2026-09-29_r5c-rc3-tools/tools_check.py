"""RC3: the binary `skills deps` resolves for each tool must be the one PATH gives first.

    uv run python <this> <knaif executable>

On the eval machines every supporting tool is on PATH, and the accepted runs launched them by
bare name, i.e. the first PATH hit. RC3 resolves `$KNAIF_<CMD>_BIN`, then PATH, then the declared
install folders, so on these machines it must pick the very same files. Exit 0 only if every
declared tool is OK and each reported path equals `shutil.which` of that command (compared
case-insensitively on Windows). Written with the rules (run.sh header), before the runs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROW = re.compile(r"^\s+\[(?P<mark>OK|MISS)\s*\]\s+(?P<tool>\S+)\s+\((?:required|optional)\)\s+(?P<rest>.*)$")


def _same(a: str, b: str) -> bool:
    pa, pb = Path(a).resolve(), Path(b).resolve()
    return str(pa).lower() == str(pb).lower() if os.name == "nt" else pa == pb


def main(exe: str) -> int:
    out = subprocess.run([exe, "skills", "deps"], capture_output=True, text=True, check=False)
    print(out.stdout, end="")
    bad = 0
    rows = [m for m in map(ROW.match, out.stdout.splitlines()) if m]
    if not rows:
        print("FAIL: no tool rows in `skills deps`")
        return 1
    for m in rows:
        if m["mark"] != "OK":
            print(f"FAIL: {m['tool']} is MISS")
            bad += 1
            continue
        for path in (p.strip() for p in m["rest"].split(", ")):
            cmd = Path(path).stem
            first = shutil.which(cmd)
            if first is None or not _same(path, first):
                print(f"FAIL: {m['tool']}: deps resolves {path}, PATH gives {first}")
                bad += 1
            else:
                print(f"ok  {m['tool']}: {cmd} -> the first PATH hit")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
