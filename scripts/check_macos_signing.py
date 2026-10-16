#!/usr/bin/env python3
"""Verify a signed macOS artifact per binary, and read its notarization log even on success.

F3 and F3b of the 2026-08-02 macOS support plan. Two subcommands:

``codesign <bindir> --team <TEAMID> [--cdhashes-out FILE]``
    For every Mach-O ``check_macho_deps.py --list`` finds (the same enumeration, so the two checks
    cannot disagree about what is in the artifact), run ``codesign --verify --strict`` and
    ``codesign -dv --verbose=4`` and assert: a valid signature, **not** ad-hoc, the expected Team
    ID, the hardened runtime flag, and a secure timestamp. ``--cdhashes-out`` writes
    ``{file name: CDHash}`` for the log check below. macOS only (it runs ``codesign``).

``notary-log <log.json> --cdhashes FILE``
    Read ``xcrun notarytool log <id>`` output. Fail unless the status is ``Accepted``, there are
    **no** issues (a warning accepted today is a failure on a later OS or policy), and every CDHash
    from ``--cdhashes`` appears in ``ticketContents`` — the only direct evidence that each nested
    binary was covered, which matters most for the ``.zip``, whose ticket cannot be stapled.

The parsers are pure functions, tested on any host in ``test_macos_signing.py``; only the
``codesign`` subcommand needs a Mac.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass
class Signature:
    """What ``codesign -dv --verbose=4`` says about one file."""

    team: str | None = None
    runtime: bool = False
    timestamped: bool = False
    adhoc: bool = False
    cdhash: str | None = None
    signed: bool = False


def parse_codesign_display(text: str) -> Signature:
    """Parse ``codesign -dv --verbose=4`` output (it writes to stderr)."""
    out = Signature()
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key == "CodeDirectory v":
            out.signed = True
            flags = re.search(r"flags=0x[0-9a-fA-F]+\(([^)]*)\)", line)
            names = set(flags.group(1).split(",")) if flags else set()
            out.runtime = "runtime" in names
            out.adhoc = out.adhoc or "adhoc" in names
        elif key == "Signature" and value == "adhoc":
            out.adhoc = True
        elif key == "TeamIdentifier" and value != "not set":
            out.team = value
        elif key == "Timestamp":
            out.timestamped = True
        elif key == "CDHash":
            out.cdhash = value.lower()
    return out


def signature_problems(name: str, sig: Signature, team: str) -> list[str]:
    """Everything wrong with one file's signature, against F3's four requirements."""
    if not sig.signed:
        return [f"{name}: not signed"]
    problems = []
    if sig.adhoc:
        problems.append(
            f"{name}: ad-hoc signature — the linker's placeholder, not a Developer ID one"
        )
    if sig.team != team:
        problems.append(f"{name}: Team ID is {sig.team or 'not set'}, expected {team}")
    if not sig.runtime:
        problems.append(f"{name}: hardened runtime not enabled (sign with --options runtime)")
    if not sig.timestamped:
        problems.append(f"{name}: no secure timestamp (sign with --timestamp)")
    return problems


def _ticket_hashes(log: dict) -> dict[str, str]:
    return {
        str(entry.get("cdhash", "")).lower(): str(entry.get("path", ""))
        for entry in log.get("ticketContents") or []
    }


def _covered(expected: str, ticket: dict[str, str]) -> bool:
    # A CDHash is a SHA-256 truncated to 20 bytes in both places; compare by prefix so a full
    # 32-byte form on either side still matches.
    expected = expected.lower()
    return any(h.startswith(expected) or expected.startswith(h) for h in ticket if h)


def notary_problems(log: dict, cdhashes: dict[str, str]) -> list[str]:
    """Everything wrong with a notarization log, per F3b."""
    problems = []
    status = log.get("status")
    if status != "Accepted":
        problems.append(f"notarization status is {status!r}: {log.get('statusSummary', '')}")
    for issue in log.get("issues") or []:
        problems.append(
            f"notary {issue.get('severity', 'issue')}: {issue.get('path', '?')}: "
            f"{issue.get('message', '')}"
        )
    ticket = _ticket_hashes(log)
    for name, cdhash in sorted(cdhashes.items()):
        if not _covered(cdhash, ticket):
            problems.append(
                f"{name}: CDHash {cdhash} is not in the ticket — this binary was not notarized"
            )
    return problems


def _signing_order(bindir: Path) -> list[Path]:
    spec = importlib.util.spec_from_file_location("check_macho_deps", HERE / "check_macho_deps.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.signing_order(bindir)


def _cmd_codesign(bindir: Path, team: str, cdhashes_out: Path | None) -> int:
    files = _signing_order(bindir)
    if not files:
        print(f"FAIL: no Mach-O in {bindir}", file=sys.stderr)
        return 1
    problems: list[str] = []
    cdhashes: dict[str, str] = {}
    for path in files:
        verify = subprocess.run(
            ["codesign", "--verify", "--strict", "--verbose=2", str(path)],
            capture_output=True,
            text=True,
        )
        if verify.returncode != 0:
            problems.append(f"{path.name}: codesign --verify failed: {verify.stderr.strip()}")
        shown = subprocess.run(
            ["codesign", "-dv", "--verbose=4", str(path)], capture_output=True, text=True
        )
        parsed = parse_codesign_display(shown.stderr)
        problems.extend(signature_problems(path.name, parsed, team))
        if parsed.cdhash:
            cdhashes[path.name] = parsed.cdhash
    if cdhashes_out is not None:
        cdhashes_out.write_text(json.dumps(cdhashes, indent=2, sort_keys=True) + "\n")
    if problems:
        print(f"FAIL: {len(problems)} signature problem(s)", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"  ok  {len(files)} Mach-O signed by {team}: hardened runtime, secure timestamp")
    return 0


def _cmd_notary_log(log_path: Path, cdhashes_path: Path) -> int:
    log = json.loads(log_path.read_text())
    cdhashes = json.loads(cdhashes_path.read_text())
    problems = notary_problems(log, cdhashes)
    if problems:
        print(f"FAIL: {log.get('archiveFilename', log_path.name)}", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(
        f"  ok  {log.get('archiveFilename', log_path.name)}: Accepted, no issues, "
        f"all {len(cdhashes)} CDHashes in the ticket"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    cs = sub.add_parser("codesign", help="verify every Mach-O's signature (macOS only)")
    cs.add_argument("bindir", type=Path)
    cs.add_argument("--team", required=True, help="the expected 10-character Team ID")
    cs.add_argument("--cdhashes-out", type=Path, help="write {file name: CDHash} as JSON")

    nl = sub.add_parser("notary-log", help="check a notarytool log against the CDHashes")
    nl.add_argument("log", type=Path)
    nl.add_argument("--cdhashes", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "codesign":
        return _cmd_codesign(args.bindir, args.team, args.cdhashes_out)
    return _cmd_notary_log(args.log, args.cdhashes)


if __name__ == "__main__":
    sys.exit(main())
