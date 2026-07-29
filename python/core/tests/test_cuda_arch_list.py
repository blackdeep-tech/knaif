"""The CUDA release arch list must be identical in the script that builds it and the doc that
specifies it.

`installers/package.sh` verifies the built fatbin against `CUDA_RELEASE_ARCHS`, and `docs/RELEASE.md`
§3 is where a maintainer reads the list before setting `CUDAARCHS` for the build. If those two drift,
the check passes against the wrong expectation — which is worse than no check, because it reports a
green fatbin that is missing an arch.

Cheap to assert and impossible to notice by eye: the failure is invisible until a user with the
dropped GPU generation hits it, and by then the artifact is published.
"""

import re
from pathlib import Path

ROOT = Path(".").resolve()
PACKAGE_SH = ROOT / "installers" / "package.sh"
RELEASE_MD = ROOT / "docs" / "RELEASE.md"


def _script_archs() -> str:
    text = PACKAGE_SH.read_text(encoding="utf-8")
    match = re.search(r'^CUDA_RELEASE_ARCHS="([^"]+)"', text, re.M)
    assert match, "no CUDA_RELEASE_ARCHS assignment in installers/package.sh"
    return match.group(1)


def _doc_archs() -> str:
    text = RELEASE_MD.read_text(encoding="utf-8")
    match = re.search(r'CUDAARCHS="([^"]+)"', text)
    assert match, "no CUDAARCHS example in docs/RELEASE.md"
    return match.group(1)


def test_arch_list_agrees_between_script_and_release_doc() -> None:
    assert _script_archs() == _doc_archs(), (
        f"CUDA arch list drift:\n"
        f"  installers/package.sh: {_script_archs()}\n"
        f"  docs/RELEASE.md §3:    {_doc_archs()}\n"
        f"package.sh verifies the built fatbin against its own copy, so a drifted list means the "
        f"check asserts the wrong thing."
    )


def test_hopper_ships_sass_not_only_ptx() -> None:
    # `90-virtual` alone leaves Hopper on PTX JIT, and PTX JIT is the documented exception to CUDA's
    # minor-version driver compatibility — exactly the case the R580 floor does not cover.
    archs = _script_archs().split(";")
    assert "90-real" in archs, f"90-real missing; Hopper would be PTX-JIT only: {archs}"
    assert (
        "90-virtual" in archs
    ), f"90-virtual missing; nothing JITs forward past Blackwell: {archs}"


def test_blackwell_forward_compat_ptx_does_not_come_from_a_12x_virtual_arch() -> None:
    # ggml rewrites every `12X` virtual arch to `12Xa`, so `120-virtual` would yield
    # architecture-specific sm_120a PTX that cannot JIT forward. The forward-compat PTX has to come
    # from `90-virtual`, which escapes that rewrite.
    archs = _script_archs().split(";")
    assert "120-virtual" not in archs, (
        "120-virtual yields sm_120a PTX, which is architecture-specific and cannot JIT forward — "
        "use 90-virtual for forward compatibility (see docs/RELEASE.md §3)"
    )
    assert "120-real" in archs, f"Blackwell SASS missing: {archs}"
