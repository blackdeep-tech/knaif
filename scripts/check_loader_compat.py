#!/usr/bin/env python3
"""Assert every active skill bundle loads in BOTH runtimes. Run with `just loader-check`.

C2 of docs/plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md.

A skill bundle's declarative half — `skill.yaml`, `tools.yaml`, `prompt.yaml` — is read by
two independent loaders written in two languages. Nothing makes them agree except that both
are kept correct, and a bundle that parses in Python and not in Rust is invisible until
someone runs the native binary against it. That is the drift this checks.

It compares what each loader *reports*, not that each exits 0:

  discovery     the set of active skills, and that `status: stale` is filtered identically
  runtimes:     which runtime implements a skill, and the crate name when native does
  external      each skill's declared external tools, and which are required

Deliberately NOT a parity check. `just parity <skill>` pins both runtimes to one GGUF and
diffs rendered commands; that needs a model and minutes. This needs neither and answers a
different question — not "do they agree on the answer" but "can they both read the file".

Needs the native binary built (`cargo build -p knaif-cli`); pass --native-bin to point at
one elsewhere.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "python" / "core"))

# `native:<crate>` / `native:<status>` / `native:-` — the shape cmd_skills_list prints.
_LIST_ROW = re.compile(r"^(?P<name>\S+)\s+native:(?P<native>\S+)\s+(?P<rest>.*)$")
_DEPS_HEADER = re.compile(r"^(?P<name>\S+):$")
_DEPS_ROW = re.compile(
    r"^\s+\[(?P<mark>OK|MISS)\s*\]\s+(?P<tool>\S+)\s+\((?P<kind>required|optional)\)"
)


def _run_native(binary: Path, args: list[str]) -> str:
    result = subprocess.run(
        [str(binary), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(
            f"FAIL native `{' '.join(args)}` exited {result.returncode}\n{result.stderr.strip()}"
        )
    return result.stdout


def native_skills(binary: Path, include_stale: bool) -> dict[str, str]:
    """name -> the `native:` field, as the Rust loader reports it."""
    args = ["skills", "list"] + (["--include-stale"] if include_stale else [])
    found = {}
    for line in _run_native(binary, args).splitlines():
        match = _LIST_ROW.match(line.rstrip())
        if match:
            found[match.group("name")] = match.group("native")
    return found


def native_external_tools(binary: Path) -> dict[str, dict[str, bool]]:
    """skill -> {tool: required}. Detection results are ignored — installed-or-not is a
    property of the machine, and this check must give the same answer on every one."""
    found: dict[str, dict[str, bool]] = {}
    current: str | None = None
    for line in _run_native(binary, ["skills", "deps"]).splitlines():
        header = _DEPS_HEADER.match(line.rstrip())
        if header:
            current = header.group("name")
            found[current] = {}
            continue
        row = _DEPS_ROW.match(line.rstrip())
        if row and current:
            found[current][row.group("tool")] = row.group("kind") == "required"
    return found


def _list_skills(include_stale: bool, problems: list[str]) -> list[str]:
    """`list_skills` parses every skill.yaml, so one malformed bundle takes the whole
    enumeration down. Report that as a finding and carry on with an empty list: a YAML the
    Python loader cannot read while the Rust one can is precisely a loader disagreement,
    and a traceback would hide it behind a stack instead of naming it."""
    from knaif import list_skills

    try:
        return list_skills(include_stale=include_stale)
    except Exception as exc:  # noqa: BLE001 - any parse failure is the finding
        problems.append(
            f"the Python loader could not enumerate skills: {type(exc).__name__}: {exc}"
        )
        return []


def python_skills(include_stale: bool, problems: list[str]) -> dict[str, str]:
    from knaif.skill import Skill

    found = {}
    for name in _list_skills(include_stale, problems):
        # Skill.load, not a YAML read: it instantiates the skill class and builds the
        # name -> Tool map, so a tools.yaml entry with no class behind it fails here
        # exactly as it would at plan time.
        #
        # A raising loader is a FINDING, not a crash. It is the asymmetric case this check
        # exists for — the Rust side happily lists a bundle whose Python half cannot load —
        # and a traceback would bury that under the exception instead of naming the skill.
        try:
            bundle = Skill.load(REPO / "skills" / name)
        except Exception as exc:  # noqa: BLE001 - any loader failure is the finding
            problems.append(f"{name}: the Python loader raised {type(exc).__name__}: {exc}")
            continue
        native = bundle.runtimes.get("native") or {}
        crate = native.get("crate")
        status = native.get("status")
        found[name] = crate or status or "-"
    return found


def python_external_tools(problems: list[str]) -> dict[str, dict[str, bool]]:
    from knaif.skill import Skill

    found: dict[str, dict[str, bool]] = {}
    for name in _list_skills(False, problems):
        try:
            bundle = Skill.load(REPO / "skills" / name)
        except Exception:  # noqa: BLE001 - already reported by python_skills
            continue
        found[name] = {
            tool["name"]: bool(tool.get("required", False))
            for tool in bundle.external_tools
            if tool.get("name")
        }
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--native-bin",
        type=Path,
        default=REPO / "target" / "debug" / ("knaif.exe" if sys.platform == "win32" else "knaif"),
    )
    args = parser.parse_args()

    if not args.native_bin.is_file():
        print(
            f"FAIL native binary not found at {args.native_bin}\n"
            "     build it first: cargo build -p knaif-cli",
            file=sys.stderr,
        )
        return 1

    problems: list[str] = []

    py_active = python_skills(include_stale=False, problems=problems)
    rs_active = native_skills(args.native_bin, include_stale=False)

    if set(py_active) != set(rs_active):
        only_py = sorted(set(py_active) - set(rs_active))
        only_rs = sorted(set(rs_active) - set(py_active))
        if only_py:
            problems.append(f"loads in Python but not discovered by the Rust loader: {only_py}")
        if only_rs:
            problems.append(f"discovered by the Rust loader but not by Python: {only_rs}")

    # `status: stale` must hide a skill on both sides, or the native runtime offers a
    # bundle the Python one has withdrawn. io is the case that exists today.
    py_stale = set(python_skills(include_stale=True, problems=[])) - set(py_active)
    rs_stale = set(native_skills(args.native_bin, include_stale=True)) - set(rs_active)
    if py_stale != rs_stale:
        problems.append(
            f"the loaders disagree about which skills are stale: python={sorted(py_stale)} "
            f"rust={sorted(rs_stale)}"
        )
    if not py_stale:
        problems.append(
            "no skill is marked stale in either loader - the stale-filtering assertion above "
            "is passing vacuously; drop it or restore a stale bundle"
        )

    for name in sorted(set(py_active) & set(rs_active)):
        if py_active[name] != rs_active[name]:
            problems.append(
                f"{name}: runtimes: disagree - skill.yaml says {py_active[name]!r} to Python, "
                f"the Rust loader reports {rs_active[name]!r}"
            )

    py_tools = python_external_tools(problems=[])
    rs_tools = native_external_tools(args.native_bin)
    for name in sorted(set(py_active) & set(rs_active)):
        want, got = py_tools.get(name, {}), rs_tools.get(name, {})
        if want != got:
            problems.append(f"{name}: external tools disagree - python={want} rust={got}")

    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1

    print(
        f"ok  {len(py_active)} active skills load in both runtimes "
        f"({', '.join(sorted(py_active))}); stale filtered identically ({', '.join(sorted(py_stale))}); "
        "runtimes: and external tools agree"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
