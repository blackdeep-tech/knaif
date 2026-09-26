"""PDFium ships beside the binary in every functional artifact (release plan R0/R2).

OCR and PDF rendering fail without it, and until 1.2.0 the evals only passed because they pointed
`KNAIF_PDFIUM_PATH` at pypdfium2's copy. The build is pinned by version and sha256 in
`contracts/release/pdfium.yaml` (chromium/7999, owner decision 2026-09-26: byte-identical to the
pypdfium2 library every accepted L4 run used), fetched and verified by
`installers/fetch_pdfium.sh`, staged by `package.sh` and `build_native_kind.sh`, and asserted by
`installers/smoke.sh`.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
PIN = REPO / "contracts" / "release" / "pdfium.yaml"
FETCH = REPO / "installers" / "fetch_pdfium.sh"
PLATFORMS = ("win-x64", "linux-x64", "mac-arm64")


def _pin() -> dict:
    return yaml.safe_load(PIN.read_text(encoding="utf-8"))


def _entry(platform: str) -> tuple[str, str, str]:
    archive, lib, sha = _pin()[platform].split()
    return archive, lib, sha


def test_the_pin_names_the_owner_s_build() -> None:
    assert _pin()["version"] == "chromium/7999"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_platform_is_pinned_by_sha256(platform: str) -> None:
    archive, lib, sha = _entry(platform)
    assert archive == f"pdfium-{platform}.tgz"
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert lib.endswith(("pdfium.dll", "libpdfium.so", "libpdfium.dylib"))


def test_package_stages_pdfium_for_every_functional_kind() -> None:
    text = (REPO / "installers" / "package.sh").read_text(encoding="utf-8")
    assert "installers/fetch_pdfium.sh" in text


def test_the_dev_build_stages_it_too() -> None:
    """`target/release-<kind>/` is what the dev lanes run; OCR must work there without a flag."""
    text = (REPO / "scripts" / "build_native_kind.sh").read_text(encoding="utf-8")
    assert "installers/fetch_pdfium.sh" in text


def test_smoke_asserts_pdfium_is_present() -> None:
    text = (REPO / "installers" / "smoke.sh").read_text(encoding="utf-8")
    assert "pdfium" in text.lower()


def test_notice_credits_pdfium_and_its_components() -> None:
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    assert "PDFium" in notice and "FreeType" in notice


# -- the fetch script, offline: a cached archive stands in for the download ----------------------


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    return bash


def _fake_archive(lib: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (
            (lib, b"the library"),
            ("LICENSE", b"packaging licence"),
            ("licenses/pdfium.txt", b"BSD-3"),
            ("licenses/freetype.txt", b"FTL"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _run(tmp_path: Path, platform: str, archive_bytes: bytes, pinned_sha: str):
    archive, lib, _ = _entry(platform)
    pin = tmp_path / "pdfium.yaml"
    pin.write_text(
        f'version: "chromium/7999"\n{platform}: "{archive} {lib} {pinned_sha}"\n', encoding="utf-8"
    )
    cache = tmp_path / "cache" / "chromium-7999"
    cache.mkdir(parents=True)
    (cache / archive).write_bytes(archive_bytes)
    bin_dir, lic_dir = tmp_path / "bin", tmp_path / "licenses"
    env = {
        "PATH": str(Path(_bash()).parent) + ":/usr/bin:/bin",
        "KNAIF_PDFIUM_PIN": pin.as_posix(),
        "KNAIF_PDFIUM_CACHE": (tmp_path / "cache").as_posix(),
        "KNAIF_PDFIUM_OFFLINE": "1",
    }
    import os

    env = {**os.environ, **env}
    proc = subprocess.run(
        [_bash(), FETCH.as_posix(), platform, bin_dir.as_posix(), lic_dir.as_posix()],
        capture_output=True,
        text=True,
        env=env,
    )
    return proc, bin_dir, lic_dir, Path(lib).name


@pytest.mark.parametrize("platform", ["win-x64", "linux-x64"])
def test_fetch_stages_the_verified_library_and_its_notices(tmp_path: Path, platform: str) -> None:
    _, lib, _ = _entry(platform)
    data = _fake_archive(lib)
    proc, bin_dir, lic_dir, name = _run(tmp_path, platform, data, hashlib.sha256(data).hexdigest())
    assert proc.returncode == 0, proc.stderr
    assert (bin_dir / name).read_bytes() == b"the library"
    assert (lic_dir / "PDFium" / "LICENSE").is_file()
    assert (lic_dir / "PDFium" / "licenses" / "freetype.txt").is_file()


def test_fetch_refuses_an_archive_that_does_not_match_the_pin(tmp_path: Path) -> None:
    _, lib, _ = _entry("linux-x64")
    proc, bin_dir, _, name = _run(tmp_path, "linux-x64", _fake_archive(lib), "0" * 64)
    assert proc.returncode != 0
    assert "sha256" in proc.stderr.lower()
    assert not (bin_dir / name).exists()
