"""The two Linux build images must fix the same runtime floor.

`installers/linux/Dockerfile` builds the published artifact; `Dockerfile.cuda` builds the opt-in
CUDA payload that artifact `dlopen`s. They run as **one process** on a user's machine, so the
payload's floor cannot be higher than the artifact's.

That failure mode is invisible everywhere it would be caught. A payload built against a newer glibc
loads fine on the build box, fine on the maintainer's machine, and fine on any modern distro — it
fails only on the older systems the release image exists to support, and it fails as a `dlopen`
error inside ggml, which reaches the user as "CUDA didn't work". So the pins are asserted here
rather than left to a comment saying "keep these in step".

A text lint over two Dockerfiles, deliberately: the alternative is building both images, which needs
Docker and a CUDA toolkit download and would therefore never run where a mistake is cheap to catch.
"""

import re
from pathlib import Path

ROOT = Path(".").resolve()
RELEASE = ROOT / "installers" / "linux" / "Dockerfile"
CUDA = ROOT / "installers" / "linux" / "Dockerfile.cuda"

# Pins that decide the floor, or the toolchain that produces it. Not every ARG is shared: the CUDA
# image has no Vulkan or appimagetool pins, because it builds neither.
SHARED_ARGS = ["APT_SNAPSHOT", "RUST_VERSION"]


def _args(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    return dict(re.findall(r"^ARG\s+([A-Z_0-9]+)=(\S+)", text, re.M))


def test_both_dockerfiles_exist() -> None:
    # Guard the guard: a missing file would make every comparison below vacuous.
    assert RELEASE.is_file(), "installers/linux/Dockerfile is missing"
    assert CUDA.is_file(), "installers/linux/Dockerfile.cuda is missing"


def test_shared_pins_agree() -> None:
    release, cuda = _args(RELEASE), _args(CUDA)
    for name in SHARED_ARGS:
        assert name in release, f"{name} is no longer declared in the release Dockerfile"
        assert name in cuda, f"{name} is no longer declared in Dockerfile.cuda"
        assert release[name] == cuda[name], (
            f"{name} drifted between the two Linux build images:\n"
            f"  Dockerfile:      {release[name]}\n"
            f"  Dockerfile.cuda: {cuda[name]}\n"
            f"The CUDA payload is dlopen'ed by the exe from the release image, so a mismatch here "
            f"fails only on old systems and only as a driver-looking error."
        )


def test_both_images_target_the_same_ubuntu_release() -> None:
    # The glibc floor follows from the distro release, so this is the pin behind the pins. Both
    # must be jammy (22.04 / glibc 2.35).
    for path in (RELEASE, CUDA):
        text = path.read_text(encoding="utf-8")
        assert "jammy" in text, f"{path.name} no longer points at the jammy apt snapshot"
    assert "ubuntu22.04" in CUDA.read_text(encoding="utf-8"), (
        "Dockerfile.cuda's base image is no longer an ubuntu22.04 one — that silently raises the "
        "payload's glibc floor above the release artifact's"
    )


def _instructions(path: Path) -> str:
    """The Dockerfile with comment lines stripped — what actually runs, not what it says about
    itself. Checking raw text would match a comment explaining why something is *absent*."""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_cuda_image_does_not_pull_in_vulkan() -> None:
    # The payload is ggml-cuda plus NVIDIA's redistributables. The Vulkan packages the release image
    # needs would be dead weight here, and carrying them would blur why the two images cannot
    # simply be one.
    text = _instructions(CUDA).lower()
    for pkg in ("lunarg", "vulkan-headers", "libvulkan-dev", "shaderc", "glslang-tools"):
        assert pkg not in text, f"Dockerfile.cuda installs {pkg}, which the payload has no use for"


def test_the_cuda_image_exposes_cuda_path() -> None:
    # installers/package.sh reads $CUDA_PATH to find both the redistributable libraries it stages
    # and the cuobjdump it verifies the fatbin with. NVIDIA's images set CUDA_HOME, not this.
    assert re.search(r"^ENV CUDA_PATH=", CUDA.read_text(encoding="utf-8"), re.M), (
        "Dockerfile.cuda must set CUDA_PATH — package.sh reads it to locate the redist libs and "
        "cuobjdump, and NVIDIA's images set CUDA_HOME instead"
    )
