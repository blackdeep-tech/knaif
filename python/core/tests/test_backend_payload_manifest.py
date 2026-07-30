"""The backend manifest must declare every file the packaging actually stages.

`installers/package.sh` builds the CUDA payload; `contracts/backends/backend-manifest.yaml` is what
`knaif backend install` fetches. Nothing links the two at runtime, so they can disagree — and when
they do, the install silently delivers a *different set of files* than the payload contains.

That already happened once, and it is why this file exists. The manifest was written before the
decision that licence texts travel inside the payload, and was never updated: `package.sh` staged 10
files on Windows while the manifest declared 8, so `backend install cuda` would have fetched
NVIDIA's redistributables **without the EULA that permits redistributing them**. Caught by eye, off
a generated manifest fragment, after a 54-minute build — which is not a control.

Every other cross-file duplication in this repo has a guard (the CRT list, the CUDA arch list, the
driver floor, the winget commands, the two Dockerfiles' pins). This is the pair with the most
consequential failure mode and it was the one left unguarded.

A text lint over a shell script, deliberately, for the same reason as
`test_runtime_redistribution.py`: the alternative needs a CUDA toolkit and an hour, so it would
never run where a mistake is cheap to catch.

**What this can and cannot check.** `package.sh` resolves the ggml lib and NVIDIA's redistributables
through globs against the toolkit, so their exact filenames are not knowable statically. The
unconditional `cp` of the licence files and the fixed `VCREDIST_DLLS` list *are*. So this asserts the
statically-knowable set exactly, and the glob-resolved set by shape.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(".").resolve()
MANIFEST = ROOT / "contracts" / "backends" / "backend-manifest.yaml"
PACKAGE_SH = ROOT / "installers" / "package.sh"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _package_sh() -> str:
    return PACKAGE_SH.read_text(encoding="utf-8")


def _platforms() -> dict[str, list[str]]:
    """Declared filenames per platform, for the cuda payload."""
    cuda = (_manifest().get("backends") or {}).get("cuda") or {}
    return {
        platform: [f["name"] for f in (entry or {}).get("files") or []]
        for platform, entry in (cuda.get("platforms") or {}).items()
    }


def _staged_licence_files() -> set[str]:
    """Licence texts package.sh copies into the payload stage, read from the script.

    Matches `cp installers/licenses/<name> "$STAGE/"` — the payload block copies to the stage root,
    whereas the full-artifact block copies to `$STAGE/licenses/`, so the trailing path distinguishes
    them and this cannot accidentally pick up the artifact's notices.
    """
    names = set(re.findall(r'cp installers/licenses/(\S+) "\$STAGE/"', _package_sh()))
    assert names, "no licence files are staged into the CUDA payload by package.sh"
    return names


def _vcredist_dlls() -> set[str]:
    match = re.search(r'^VCREDIST_DLLS="([^"]+)"', _package_sh(), re.M)
    assert match, "no VCREDIST_DLLS declaration in package.sh"
    return set(match.group(1).split())


def test_the_payload_declares_platforms() -> None:
    # Guard the guard: no platforms would make everything below pass vacuously.
    assert _platforms(), "the cuda payload declares no platforms"


def test_every_platform_declares_the_licence_files_that_ship_with_it() -> None:
    """The defect this file was written for.

    NVIDIA's EULA permits redistributing cudart/cublas/cublasLt only *with* the licence text.
    Under loose-file publishing the manifest is the only thing that puts a file on a user's disk,
    so a licence absent from this list is a licence the user never receives.
    """
    staged = _staged_licence_files()
    for platform, declared in _platforms().items():
        missing = staged - set(declared)
        assert not missing, (
            f"{platform} does not declare {sorted(missing)}, but package.sh stages "
            f"{sorted(staged)} into every payload. `backend install` fetches exactly what this "
            f"manifest lists, so those files would never reach the user — and the NVIDIA EULA is "
            f"a condition on redistributing the libraries beside it, not an optional extra."
        )


def test_windows_declares_the_msvc_runtime_it_ships() -> None:
    # package.sh calls stage_vcredist on the Windows payload, so the payload carries these four.
    # Absent from the manifest, `backend install` would leave a payload that depends on whatever
    # the install directory happens to hold.
    declared = set(_platforms().get("windows-x64") or [])
    missing = _vcredist_dlls() - declared
    assert (
        not missing
    ), f"windows-x64 does not declare the staged MSVC runtime files: {sorted(missing)}"


def test_every_platform_declares_a_ggml_cuda_library() -> None:
    # The one file the whole payload exists to deliver. Resolved by glob in package.sh, so matched
    # by shape rather than by exact name (`libggml-cuda.so` vs `ggml-cuda.dll`).
    for platform, declared in _platforms().items():
        assert any(
            re.fullmatch(r"(lib)?ggml-cuda\.(so|dll|dylib)", n) for n in declared
        ), f"{platform} declares no ggml-cuda library: {declared}"


def test_every_platform_declares_the_three_nvidia_redist_libraries() -> None:
    # cudart, cublas and cublasLt are what the EULA covers and what ggml-cuda links. Missing one
    # produces a payload that installs cleanly and then fails to load — the failure mode that
    # reaches a user as "CUDA didn't work".
    for platform, declared in _platforms().items():
        for stem in ("cudart", "cublas", "cublasLt"):
            assert any(
                re.fullmatch(rf"(lib)?{stem}(64)?[._]\S+", n) for n in declared
            ), f"{platform} declares no {stem} library: {declared}"


def test_redistributables_ride_the_toolkit_tag_not_the_product_tag() -> None:
    """NVIDIA's files are keyed to the CUDA toolkit and shared across knaif releases.

    Putting one on the product tag would re-upload ~493 MB every release and, worse, make the
    manifest claim a per-release identity for bytes that never change.
    """
    cuda = (_manifest().get("backends") or {}).get("cuda") or {}
    toolkit = (cuda.get("requires") or {}).get("cuda_toolkit")
    assert toolkit, "cuda.requires.cuda_toolkit is not declared"
    expected = f"redist-cuda-{toolkit}"

    for platform, entry in (cuda.get("platforms") or {}).items():
        for f in (entry or {}).get("files") or []:
            is_nvidia = re.match(r"(lib)?(cudart|cublas|cublasLt)|NVIDIA-CUDA-EULA", f["name"])
            if is_nvidia:
                assert f.get("tag") == expected, (
                    f"{platform}/{f['name']} rides tag {f.get('tag')!r}; NVIDIA's files are keyed "
                    f"to the toolkit and belong on {expected!r}"
                )


def test_the_abi_coupled_library_rides_the_product_tag() -> None:
    # The inverse, and the reason the split exists at all: a tag-scoped URL structurally cannot
    # serve a newer ggml lib to an older exe.
    for platform, entry in (
        ((_manifest().get("backends") or {}).get("cuda") or {}).get("platforms") or {}
    ).items():
        for f in (entry or {}).get("files") or []:
            if re.fullmatch(r"(lib)?ggml-cuda\.(so|dll|dylib)", f["name"]):
                assert (
                    f.get("tag") == "product"
                ), f"{platform}/{f['name']} must ride the product tag, not {f.get('tag')!r}"
