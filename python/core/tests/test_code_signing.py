"""Windows code signing: the config, the installer directives and the stage-signing step.

Every published Windows binary is Authenticode-signed from the maintainer's box (see
docs/plans/2026-07-27-code-signing.md). Three things can go wrong silently, and each has a
guard here:

* **A build that quietly did not sign.** ``installers/sign_stage.sh`` is the step that signs
  a staged tree and then re-reads every signature; it must fail rather than hand back a
  half-signed tree. Its behaviour is tested with a stand-in signer, so no Azure account is
  needed.
* **A publisher mismatch.** Windows shows the certificate subject as the verified publisher
  and Add/Remove Programs shows ``AppPublisher``. A user comparing the two cannot tell a
  benign mismatch from a malicious one, so they must be the same string.
* **An unsigned uninstaller.** Without ``SignedUninstaller=yes`` Inno writes an unsigned
  ``unins000.exe`` even when ``setup.exe`` is signed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(".").resolve()
CONFIG = ROOT / "installers" / "windows" / "signing.json"
ISS = ROOT / "installers" / "windows" / "knaif.iss"
PACKAGE_SH = ROOT / "installers" / "package.sh"
SIGN_STAGE = ROOT / "installers" / "sign_stage.sh"
SIGN_PS1 = ROOT / "scripts" / "sign_windows.ps1"


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _subject_cn() -> str:
    match = re.match(r"CN=([^,]+)", _config()["subject"])
    assert match, f"no CN in the recorded subject: {_config()['subject']!r}"
    return match.group(1)


def _setup_value(key: str) -> str:
    match = re.search(rf"^{key}=(.*)$", ISS.read_text(encoding="utf-8"), flags=re.M)
    assert match, f"{key}= not found in knaif.iss"
    return match.group(1).strip()


# -- the recorded signing profile -------------------------------------------------------------


def test_signing_config_names_a_complete_profile() -> None:
    cfg = _config()
    for key in (
        "provider",
        "endpoint",
        "account",
        "certificate_profile",
        "subject",
        "timestamp_url",
    ):
        assert cfg.get(key), f"signing.json is missing {key!r}"
    assert cfg["provider"] == "azure-artifact-signing"
    assert re.fullmatch(r"https://[a-z0-9]+\.codesigning\.azure\.net", cfg["endpoint"]), cfg[
        "endpoint"
    ]
    # An untimestamped signature dies with the certificate, and Artifact Signing certificates
    # live for days, not years.
    assert cfg["timestamp_url"].startswith("http")


# -- the installer ----------------------------------------------------------------------------


def test_app_publisher_is_the_certificate_subject() -> None:
    cn = _subject_cn()
    assert _setup_value("AppPublisher") == cn
    assert _setup_value("VersionInfoCompany") == cn


def test_installer_signs_setup_and_uninstaller_only_when_asked() -> None:
    text = ISS.read_text(encoding="utf-8")
    block = re.search(r"^#ifdef Sign\s*$(.*?)^#endif", text, flags=re.M | re.S)
    assert block, "knaif.iss has no `#ifdef Sign` block"
    body = block.group(1)
    assert re.search(r"^SignTool=knaifsign\b", body, flags=re.M), body
    assert re.search(r"^SignedUninstaller=yes\s*$", body, flags=re.M), body
    # Outside the block an unsigned local compile must keep working: an undefined sign tool
    # is a compile error in Inno.
    outside = text.replace(block.group(0), "")
    assert not re.search(r"^Sign(Tool|edUninstaller)=", outside, flags=re.M)


# -- package.sh wiring ------------------------------------------------------------------------


def test_package_signs_every_stage_before_it_is_sealed() -> None:
    """Both outputs (the CUDA payload and the full artifact) are signed before the path check
    and the archive, which read the final bytes."""
    lines = PACKAGE_SH.read_text(encoding="utf-8").splitlines()
    signs = [
        i
        for i, line in enumerate(lines)
        if "installers/sign_stage.sh" in line and not line.lstrip().startswith("#")
    ]
    seals = [i for i, line in enumerate(lines) if line.strip() == 'check_no_local_paths "$STAGE"']
    assert len(seals) == 2, seals
    assert len(signs) == 2, signs
    previous = -1
    for sign, seal in zip(signs, seals, strict=True):
        assert previous < sign < seal, (signs, seals)
        previous = seal


# -- the Azure signer ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Authenticode signing is Windows-only")
def test_signer_derives_its_metadata_from_the_recorded_profile() -> None:
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SIGN_PS1),
            "-PrintMetadata",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    meta = json.loads(proc.stdout)
    cfg = _config()
    assert meta["Endpoint"] == cfg["endpoint"]
    assert meta["CodeSigningAccountName"] == cfg["account"]
    assert meta["CertificateProfileName"] == cfg["certificate_profile"]


@pytest.mark.skipif(sys.platform != "win32", reason="Authenticode signing is Windows-only")
def test_signer_finds_its_tools_when_started_by_a_32_bit_host() -> None:
    """Inno's ISCC is 32-bit, so the PowerShell it starts for `knaifsign` is too — and there
    `ProgramFiles` is the (x86) folder, which hid the Azure CLI on the first installer
    compile. Only meaningful on a box that has the signing tools installed."""
    wow = (
        Path(os.environ.get("WINDIR", r"C:\Windows"))
        / "SysWOW64"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    az = (
        Path(os.environ.get("ProgramW6432", r"C:\Program Files"))
        / "Microsoft SDKs"
        / "Azure"
        / "CLI2"
        / "wbin"
    )
    if not wow.exists() or not az.exists():
        pytest.skip("needs 32-bit Windows PowerShell and the Azure CLI")
    proc = subprocess.run(
        [
            str(wow),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SIGN_PS1),
            "-CheckTools",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# -- sign_stage.sh, with a stand-in signer ----------------------------------------------------

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="sign_stage.sh reads Authenticode status via PowerShell"
)


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    return bash


def _stage(tmp_path: Path) -> Path:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "fake.dll").write_bytes(b"MZ not really a PE")
    (stage / "tool.exe").write_bytes(b"MZ not really a PE")
    (stage / "README.txt").write_text("not a binary\n", encoding="utf-8")
    return stage


def _run(stage: Path, sign_cmd: str | None, **env_overrides: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "KNAIF_SIGN_CMD"}
    if sign_cmd is not None:
        env["KNAIF_SIGN_CMD"] = sign_cmd
    env.update(env_overrides)
    return subprocess.run(
        [_bash(), SIGN_STAGE.as_posix(), stage.as_posix()],
        capture_output=True,
        text=True,
        env=env,
    )


@windows_only
def test_unset_signer_leaves_the_tree_unsigned_and_says_so(tmp_path: Path) -> None:
    proc = _run(_stage(tmp_path), None)
    assert proc.returncode == 0, proc.stderr
    assert "UNSIGNED" in proc.stdout


@windows_only
def test_signer_gets_only_the_unsigned_binaries_and_a_failed_verify_is_fatal(
    tmp_path: Path,
) -> None:
    stage = _stage(tmp_path)
    record = tmp_path / "called.txt"
    # A signer that "succeeds" without signing anything: the re-read must catch it.
    signer = tmp_path / "fake_signer.sh"
    signer.write_text(f'printf "%s\\n" "$@" >> "{record.as_posix()}"\n', encoding="utf-8")
    proc = _run(stage, f'bash "{signer.as_posix()}"')
    assert proc.returncode != 0
    called = record.read_text(encoding="utf-8").splitlines()
    assert sorted(Path(p).name for p in called) == ["fake.dll", "tool.exe"]
    assert "fake.dll" in proc.stderr and "tool.exe" in proc.stderr


def _recording_signer(tmp_path: Path) -> tuple[str, Path]:
    record = tmp_path / "called.txt"
    signer = tmp_path / "fake_signer.sh"
    signer.write_text(f'printf "%s\\n" "$@" >> "{record.as_posix()}"\n', encoding="utf-8")
    return f'bash "{signer.as_posix()}"', record


@windows_only
def test_an_unreadable_signature_status_is_fatal_not_signed(tmp_path: Path) -> None:
    """If the status check itself cannot run, nothing may be reported as signed.

    The first live run hit this: the check printed an error, returned no paths, and the
    script concluded every binary was already signed — a fail-open in the one step whose
    job is to refuse an unsigned release. PowerShell missing from PATH stands in for any
    way the check can break."""
    cmd, _ = _recording_signer(tmp_path)
    proc = _run(_stage(tmp_path), cmd, PATH=str(Path(_bash()).parent))
    assert proc.returncode != 0, proc.stdout
    assert "already carries" not in proc.stdout


@windows_only
def test_a_foreign_psmodulepath_does_not_break_the_check(tmp_path: Path) -> None:
    """`just` runs under Windows PowerShell inside pwsh sessions, and the inherited module
    path made Get-AuthenticodeSignature unloadable in the child. The check must not depend
    on the caller's module path."""
    cmd, record = _recording_signer(tmp_path)
    proc = _run(_stage(tmp_path), cmd, PSModulePath=str(tmp_path / "no-modules-here"))
    assert proc.returncode != 0  # the fake signer signs nothing
    called = record.read_text(encoding="utf-8").splitlines()
    assert sorted(Path(p).name for p in called) == ["fake.dll", "tool.exe"]


@windows_only
def test_a_failing_signer_is_fatal(tmp_path: Path) -> None:
    proc = _run(_stage(tmp_path), "false")
    assert proc.returncode != 0
    assert "sign" in proc.stderr.lower()


# ── what the public pages say about signing ─────────────────────────────────────────────────

#: Pages that describe the CURRENT release to users. Release notes are left out: they record
#: what was true for their own version (1.2.0's rightly says "unsigned").
LIVE_PAGES = ("site/org/src", "site/dev/src", "site/data/download-copy.yaml", "README.md")
UNSIGNED_CLAIM = re.compile(
    r"\b(binaries|knaif|installer|artifacts?|downloads?)\s+(is|are)\s+unsigned\b", re.I
)


def test_no_live_page_calls_the_signed_binaries_unsigned() -> None:
    """1.2.1 shipped signed while knaif.org's download page still said "Binaries are unsigned":
    nothing tied the site's wording to the signing pipeline (found 2026-10-03)."""
    assert (ROOT / "installers" / "windows" / "signing.json").is_file(), "signing is configured"
    offenders = []
    for rel in LIVE_PAGES:
        path = ROOT / rel
        files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
        for f in files:
            text = f.read_text(encoding="utf-8", errors="ignore")
            for m in UNSIGNED_CLAIM.finditer(" ".join(text.split())):
                offenders.append(f"{f.relative_to(ROOT).as_posix()}: {m.group(0)!r}")
    assert not offenders, "\n".join(offenders)
