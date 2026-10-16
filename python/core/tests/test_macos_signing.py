"""Guard `scripts/check_macos_signing.py` — the per-binary signature and notarization-log checks.

F3 and F3b of the 2026-08-02 macOS support plan: every Mach-O must carry our Developer ID
signature with the hardened runtime and a secure timestamp, and the notarization log must be read
even when Apple says "Accepted" and must list every Mach-O's CDHash. The parsers are pure, so they
are tested here, on any host, against captured output — the Mac only supplies the real text.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(".").resolve()
SCRIPT = ROOT / "scripts" / "check_macos_signing.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_macos_signing", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sig = _load()

TEAM = "ABCDE12345"

# `codesign -dv --verbose=4 <file>` (stderr) for a Developer ID, hardened-runtime, timestamped
# binary — the shape F3 requires.
SIGNED = f"""\
Executable=/tmp/knaif-1.3.0-macos-arm64/bin/knaif
Identifier=knaif
Format=Mach-O thin (arm64)
CodeDirectory v=20500 size=51234 flags=0x10000(runtime) hashes=1590+7 location=embedded
Hash type=sha256 size=32
CandidateCDHash sha256=0123456789abcdef0123456789abcdef01234567
CandidateCDHashFull sha256=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
Hash choices=sha256
CMSDigest=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
CMSDigestType=2
CDHash=0123456789abcdef0123456789abcdef01234567
Signature size=9041
Authority=Developer ID Application: Example Ltd ({TEAM})
Authority=Developer ID Certification Authority
Authority=Apple Root CA
Timestamp=1 Oct 2026 at 10:00:00
Info.plist=not bound
TeamIdentifier={TEAM}
Runtime Version=15.0.0
Sealed Resources=none
Internal requirements count=1 size=180
"""

# What the linker leaves on every arm64 binary before we sign: ad-hoc, no team, no timestamp.
ADHOC = """\
Executable=/tmp/bin/libllama.dylib
Identifier=libllama
Format=Mach-O thin (arm64)
CodeDirectory v=20400 size=1234 flags=0x20002(adhoc,linker-signed) hashes=33+0 location=embedded
Hash type=sha256 size=32
CandidateCDHash sha256=fedcba9876543210fedcba9876543210fedcba98
CDHash=fedcba9876543210fedcba9876543210fedcba98
Signature=adhoc
Info.plist=not bound
TeamIdentifier=not set
Sealed Resources=none
Internal requirements count=0 size=12
"""


def test_a_developer_id_hardened_timestamped_signature_passes() -> None:
    parsed = sig.parse_codesign_display(SIGNED)
    assert parsed.team == TEAM
    assert parsed.runtime and parsed.timestamped and not parsed.adhoc
    assert parsed.cdhash == "0123456789abcdef0123456789abcdef01234567"
    assert sig.signature_problems("knaif", parsed, TEAM) == []


def test_the_linkers_adhoc_signature_fails_on_every_count() -> None:
    problems = sig.signature_problems("libllama.dylib", sig.parse_codesign_display(ADHOC), TEAM)
    text = "\n".join(problems)
    assert "ad-hoc" in text
    assert "Team ID" in text
    assert "hardened runtime" in text
    assert "timestamp" in text


def test_another_teams_signature_fails() -> None:
    other = SIGNED.replace(f"TeamIdentifier={TEAM}", "TeamIdentifier=ZZZZZ99999")
    problems = sig.signature_problems("knaif", sig.parse_codesign_display(other), TEAM)
    assert any("ZZZZZ99999" in p for p in problems)


def test_a_signature_without_the_runtime_flag_fails() -> None:
    no_runtime = SIGNED.replace("flags=0x10000(runtime)", "flags=0x0(none)")
    problems = sig.signature_problems("knaif", sig.parse_codesign_display(no_runtime), TEAM)
    assert any("hardened runtime" in p for p in problems)


def test_a_signature_without_a_secure_timestamp_fails() -> None:
    no_ts = SIGNED.replace("Timestamp=1 Oct 2026 at 10:00:00\n", "")
    problems = sig.signature_problems("knaif", sig.parse_codesign_display(no_ts), TEAM)
    assert any("timestamp" in p for p in problems)


def test_an_unsigned_binary_fails() -> None:
    problems = sig.signature_problems(
        "knaif", sig.parse_codesign_display("knaif: code object is not signed at all\n"), TEAM
    )
    assert problems


# --------------------------------------------------------------------------------------
# The notarization log (F3b): read it even on success
# --------------------------------------------------------------------------------------

CDHASHES = {
    "knaif": "0123456789abcdef0123456789abcdef01234567",
    "libllama.dylib": "fedcba9876543210fedcba9876543210fedcba98",
}


def notary_log(**overrides) -> dict:
    log = {
        "logFormatVersion": 1,
        "jobId": "00000000-0000-0000-0000-000000000000",
        "status": "Accepted",
        "statusSummary": "Ready for distribution",
        "statusCode": 0,
        "archiveFilename": "knaif-1.3.0-macos-arm64.zip",
        "ticketContents": [
            {
                "path": "knaif-1.3.0-macos-arm64.zip/knaif-1.3.0-macos-arm64/bin/knaif",
                "digestAlgorithm": "SHA-256",
                "cdhash": "0123456789abcdef0123456789abcdef01234567",
                "arch": "arm64",
            },
            {
                "path": "knaif-1.3.0-macos-arm64.zip/knaif-1.3.0-macos-arm64/bin/libllama.dylib",
                "digestAlgorithm": "SHA-256",
                "cdhash": "FEDCBA9876543210FEDCBA9876543210FEDCBA98",
                "arch": "arm64",
            },
        ],
        "issues": None,
    }
    log.update(overrides)
    return log


def test_an_accepted_log_covering_every_binary_passes() -> None:
    assert sig.notary_problems(notary_log(), CDHASHES) == []


def test_a_rejected_submission_fails() -> None:
    problems = sig.notary_problems(notary_log(status="Invalid"), CDHASHES)
    assert any("Invalid" in p for p in problems)


def test_an_accepted_log_with_warnings_still_fails() -> None:
    # Accepted with issues is the case F3b exists for: warnings become failures on a later OS.
    issue = {
        "severity": "warning",
        "code": None,
        "path": "knaif-1.3.0-macos-arm64.zip/knaif-1.3.0-macos-arm64/bin/libllama.dylib",
        "message": "The signature does not include a secure timestamp.",
        "docUrl": None,
        "architecture": "arm64",
    }
    problems = sig.notary_problems(notary_log(issues=[issue]), CDHASHES)
    assert any("secure timestamp" in p for p in problems)


def test_a_binary_missing_from_the_ticket_fails() -> None:
    log = notary_log()
    log["ticketContents"] = log["ticketContents"][:1]
    problems = sig.notary_problems(log, CDHASHES)
    assert any("libllama.dylib" in p for p in problems)


def test_notary_log_cli_reads_both_files(tmp_path: Path, capsys) -> None:
    log_path = tmp_path / "log.json"
    hashes_path = tmp_path / "cdhashes.json"
    log_path.write_text(json.dumps(notary_log()))
    hashes_path.write_text(json.dumps(CDHASHES))
    assert sig.main(["notary-log", str(log_path), "--cdhashes", str(hashes_path)]) == 0
    log_path.write_text(json.dumps(notary_log(status="Invalid")))
    assert sig.main(["notary-log", str(log_path), "--cdhashes", str(hashes_path)]) == 1
