"""knaif 1.3.0 Windows clean room verdict, on the host: the Sandbox's checks, all required.

Reads sandbox/r130/cleanroom/out (gitignored; written by the Sandbox) and writes the verdict next
to this file (grade.txt), which is committed. The manual part (report.md) is graded by a person.

    uv run --no-sync python evals/runs/2026-10-13_r130-windows_success/cleanroom/grade.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[3] / "sandbox" / "r130" / "cleanroom" / "out"
REQUIRED = (
    "zip_binary_is_frozen",
    "zip_version_is_130",
    "zip_skills_list",
    "zip_every_binary_signed",
    "fresh_install",
    "fresh_publisher",
    "fresh_every_binary_signed",
    "fresh_real_request",
    "fresh_uninstall",
    "install_121",
    "installed_121_is_published",
    "cli_121_holds_mutex",
    "upgrade_refused_while_running",
    "upgrade_installs",
    "upgrade_same_folder",
    "upgrade_no_folder_exists_prompt",
    "upgraded_binary_is_frozen",
    "upgraded_version_is_130",
    "upgrade_no_leftover_libraries",
    "upgraded_every_binary_signed",
    "daemon_started",
    "reinstall_over_daemon",
    "daemon_started_again",
    "uninstall_over_daemon",
    "uninstall_keeps_user_models",
    "skills_deps_names_missing_tools",
)


def main() -> int:
    path = OUT / "results.json"
    if not path.is_file():
        print(f"no results: {path.name} missing (did the Sandbox script finish?)")
        return 1
    results = json.loads(path.read_text(encoding="utf-8-sig"))
    log = (OUT / "cleanroom.log").read_text(encoding="utf-8-sig", errors="replace")
    lines = [f"Smart App Control enforcing at start: {'Smart App Control at start: 1 ' in log}"]
    for key in REQUIRED:
        r = results.get(key)
        mark = "MISSING" if r is None else ("PASS" if r["pass"] else "FAIL")
        lines.append(f"{mark:7} {key}: {'' if r is None else r['detail']}")
    passed = all(results.get(k, {}).get("pass") for k in REQUIRED)
    n = sum(bool(results.get(k, {}).get("pass")) for k in REQUIRED)
    lines.append(
        f"{'CLEAN ROOM PASS' if passed else 'CLEAN ROOM FAIL'} ({n}/{len(REQUIRED)}; the manual part is report.md)"
    )
    text = "\n".join(lines) + "\n"
    print(text, end="")
    (HERE / "grade.txt").write_text(text, encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
