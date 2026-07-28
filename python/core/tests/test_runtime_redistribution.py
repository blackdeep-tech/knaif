"""The runtime libraries we redistribute must match what we say we redistribute.

knaif ships a handful of third-party runtime libraries it did not write: the VC++
CRT on Windows and `libgomp` on Linux. Redistributing them is permitted, but only
under conditions — Microsoft's Distributable List excludes `debug_nonredist/`,
and NVIDIA's EULA requires its licence text to travel with the CUDA payload.

Those conditions live in two places that can drift apart: the prose in
`docs/PROVENANCE.md` and the code in `installers/package.sh`. A licence
determination that describes a file set the build no longer stages is worse than
none, because it reads as though someone checked. So assert the two agree, and
assert the boundary itself is still enforced.

This is a text lint over a shell script, deliberately — the alternative is
running `package.sh`, which needs a Windows build box with Visual Studio and so
would never run in the one place a mistake is cheap to catch.
"""

import re
from pathlib import Path

ROOT = Path(".").resolve()
PACKAGE_SH = ROOT / "installers" / "package.sh"
PROVENANCE = ROOT / "docs" / "PROVENANCE.md"


def _package_sh() -> str:
    return PACKAGE_SH.read_text(encoding="utf-8")


def _provenance() -> str:
    return PROVENANCE.read_text(encoding="utf-8")


def _staged_crt_dlls() -> list[str]:
    """The DLL list package.sh actually iterates when staging the VC++ runtime."""
    match = re.search(r"^\s*for dll in ([^;]+); do", _package_sh(), re.M)
    assert match, "no `for dll in ...` CRT staging loop found in package.sh"
    return sorted(match.group(1).split())


def _documented_crt_dlls() -> list[str]:
    """DLL names from the Windows *table* in PROVENANCE.md's bundled-runtime section.

    Table rows only — the surrounding prose names DLLs we deliberately do NOT ship
    (`ucrtbase.dll`, an OS component), and sweeping those in would assert the
    opposite of what this file is for.
    """
    section = _provenance().split("### Windows — the Visual C++ runtime", 1)
    assert len(section) == 2, "PROVENANCE.md has no Windows VC++ runtime section"
    body = section[1].split("###", 1)[0]
    names: list[str] = []
    for line in body.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        names += re.findall(r"`([A-Za-z0-9_]+\.dll)`", line)
    assert names, "no DLL rows found in the Windows runtime table"
    return sorted(names)


def test_documented_crt_matches_what_is_staged() -> None:
    # Adding a fifth DLL is a new redistribution decision, not a build detail — it
    # must land in the provenance record in the same change.
    assert _staged_crt_dlls() == _documented_crt_dlls(), (
        f"package.sh stages {_staged_crt_dlls()} but PROVENANCE.md documents "
        f"{_documented_crt_dlls()}"
    )


def test_crt_is_sourced_from_the_redist_tree() -> None:
    text = _package_sh()
    # The Distributable List grants files under VC\redist — not the VS install at
    # large. VCToolsRedistDir is that tree for the active toolset; the documented
    # fallback scan must stay inside VC/Redist/MSVC.
    assert "VCToolsRedistDir" in text, "package.sh no longer prefers VCToolsRedistDir"
    assert "VC/Redist/MSVC" in text, (
        "the CRT fallback scan is no longer rooted at VC/Redist/MSVC — it may now "
        "reach files outside Microsoft's Distributable List"
    )


def test_debug_nonredist_is_refused() -> None:
    text = _package_sh()
    # The single carve-out in the grant. llama.cpp's own release workflow copies a
    # runtime out of debug_nonredist/, so this is a live mistake in the ecosystem
    # rather than a hypothetical one.
    assert "debug_nonredist" in text, "the debug_nonredist guard is gone from package.sh"
    guard = re.search(r"\*debug_nonredist\*\).*?exit 1", text, re.S)
    assert guard, (
        "package.sh mentions debug_nonredist but no longer REFUSES it — the guard "
        "must hard-fail, not warn"
    )
    assert "debug_nonredist" in _provenance(), (
        "PROVENANCE.md must record the debug_nonredist carve-out; it is the one "
        "condition on the grant that a loosened glob could silently violate"
    )


def test_linux_openmp_runtime_is_staged_and_documented() -> None:
    assert "libgomp.so.1" in _package_sh(), "libgomp.so.1 is no longer staged on Linux"
    assert (
        "libgomp.so.1" in _provenance()
    ), "libgomp.so.1 ships in the Linux artifact but is undocumented in PROVENANCE.md"
    # The GCC Runtime Library Exception is the whole reason a GPLv3 library can ship
    # inside a permissively licensed artifact. Naming GPLv3 without it reads as a
    # licence violation to anyone auditing the tree.
    assert (
        "Runtime Library Exception" in _provenance()
    ), "PROVENANCE.md names libgomp without the GCC Runtime Library Exception"


def test_cuda_payload_requires_the_nvidia_eula() -> None:
    text = _package_sh()
    assert (
        "NVIDIA-CUDA-EULA.txt" in text
    ), "the CUDA payload no longer references the NVIDIA EULA text"
    # Must be a hard failure: a warning scrolls past in a build log and ships anyway.
    eula_guard = re.search(r"NVIDIA-CUDA-EULA\.txt.*?exit 1", text, re.S)
    assert eula_guard, "staging the CUDA payload without the NVIDIA EULA must hard-fail"
