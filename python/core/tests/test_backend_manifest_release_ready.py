"""A backend payload declared published must actually be downloadable.

Mirrors `test_model_manifest_release_ready.py`, but the trigger is different and so is the
consequence. The model guard keys on `recommendations`, because a recommended model is the default
path and a `url: TODO` there is a shipped outage. Nothing recommends a backend payload — CUDA is
opt-in — so the trigger here is the explicit `status: published` flag: flipping it is the act that
promises users the assets exist.

Backends are **native-only**; the Python runtime does not manage them. This is therefore a guard
test rather than a second reader, which is why it asserts against the YAML rather than importing
anything.

The stakes are higher than for a model. A model that fails to download reports a download error. A
backend that downloads but does not match its checksum, or half-installs, fails inside ggml and
presents to the user as a driver bug — so this also asserts that every file carries a real sha256,
which `BackendStore` refuses to install without.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(".").resolve()
MANIFEST = ROOT / "contracts" / "backends" / "backend-manifest.yaml"

# Mirrors `is_real` in native/crates/knaif-models/src/manifest.rs, shared by both manifests.
PLACEHOLDERS = {None, "", "TODO"}

KNOWN_STATUSES = {"published", "unpublished"}


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _backends() -> dict:
    return _manifest().get("backends") or {}


def _published_files() -> list[tuple[str, str, dict]]:
    """(backend, platform, file) for every file of every published payload."""
    out = []
    for name, spec in _backends().items():
        if (spec or {}).get("status") != "published":
            continue
        for platform, entry in (spec.get("platforms") or {}).items():
            for file in (entry or {}).get("files") or []:
                out.append((name, platform, file))
    return out


def test_manifest_declares_backends() -> None:
    # Guard the guard: an empty manifest would pass everything below vacuously.
    assert _backends(), "backend manifest declares no backends"


def test_manifest_binds_a_knaif_version() -> None:
    # BackendStore stamps this into the install receipt and the loader refuses a payload carrying a
    # different one. Absent, every install would be stamped with the crate version instead, which is
    # right by luck rather than by declaration.
    assert _manifest().get("knaif_version"), "backend manifest declares no knaif_version"


def test_every_backend_declares_a_known_status() -> None:
    # A typo would deserialize to the `unpublished` default and silently make a payload
    # un-installable, which reads as "the download is broken".
    for name, spec in _backends().items():
        status = (spec or {}).get("status")
        assert (
            status in KNOWN_STATUSES
        ), f"{name}: status={status!r}, expected one of {KNOWN_STATUSES}"


def test_every_backend_declares_at_least_one_platform() -> None:
    for name, spec in _backends().items():
        assert (spec or {}).get("platforms"), f"{name}: declares no platforms"


def test_every_platform_declares_files() -> None:
    for name, spec in _backends().items():
        for platform, entry in ((spec or {}).get("platforms") or {}).items():
            assert (entry or {}).get("files"), f"{name}/{platform}: declares no files"


@pytest.mark.parametrize("field", ["url", "sha256"])
def test_published_payload_files_are_real(field: str) -> None:
    for name, platform, file in _published_files():
        value = file.get(field)
        assert value not in PLACEHOLDERS, (
            f"{name}/{platform}/{file.get('name')} has {field}={value!r} but the payload is marked "
            f"`status: published`. Either upload the assets and fill this in, or set "
            f"`status: unpublished` — `knaif backend install` refuses a payload with a placeholder, "
            f"so shipping this way means a command that can never succeed."
        )


def test_published_payload_files_pin_an_immutable_url() -> None:
    # A release-asset URL must name a tag, never a moving ref. The whole point of the split tags is
    # that a tag-scoped URL structurally cannot serve a newer lib to an older exe.
    for name, platform, file in _published_files():
        url = file.get("url") or ""
        assert "/releases/latest/" not in url, (
            f"{name}/{platform}/{file.get('name')} resolves `latest`: {url}. A backend manifest is a "
            f"bill of materials and must never resolve latest — an ABI mismatch reads as a driver bug."
        )


def _assets() -> dict[tuple[str, str], list[tuple[str, str, dict]]]:
    """(tag, filename) -> [(backend, platform, file)], across every backend and platform.

    A GitHub release asset name is unique within its tag, so this key is the identity of the
    thing that actually gets uploaded — not the per-platform manifest entry.
    """
    out: dict[tuple[str, str], list[tuple[str, str, dict]]] = {}
    for name, spec in _backends().items():
        for platform, entry in ((spec or {}).get("platforms") or {}).items():
            for file in (entry or {}).get("files") or []:
                out.setdefault((file.get("tag"), file.get("name")), []).append(
                    (name, platform, file)
                )
    return out


@pytest.mark.parametrize("field", ["sha256", "url"])
def test_one_asset_name_per_tag_means_one_set_of_bytes(field: str) -> None:
    """Two platforms may share an asset, but then they must agree on what it is.

    Release asset names are unique per tag, so two files with the same name on the same tag are
    not two files — they are one upload. If the manifest gives them different checksums, at most
    one platform can be right, and the other's `backend install` fails its checksum after
    downloading. If it gives them different URLs, one of them points at an asset that cannot
    exist.

    This is not hypothetical. Both licence texts are staged into *both* payloads from the same
    tracked file, and a Windows checkout converted them to CRLF while the Linux container kept
    LF — one file, two sha256 values, one asset name per tag. Fixed at the source by pinning
    `installers/licenses/**` to LF (see `test_backend_payload_manifest.py`); this asserts the
    consequence directly, so any future divergence fails here rather than at a user's download.
    """
    for (tag, filename), entries in _assets().items():
        values = {str(f.get(field)) for _, _, f in entries}
        if len(values) == 1:
            continue
        where = ", ".join(f"{b}/{p}" for b, p, _ in entries)
        pytest.fail(
            f"{filename!r} on tag {tag!r} is declared by {where} with {len(values)} different "
            f"{field} values: {sorted(values)}. One asset name per tag is one upload, so these "
            f"cannot all be served. Either make the file byte-identical across platforms and "
            f"share one asset, or give each platform a distinct asset name and keep `name:` as "
            f"the on-disk name (docs/RELEASE.md §7)."
        )


def test_cuda_declares_the_driver_floor_the_nudge_gates_on() -> None:
    # The floor lives beside the payload so bumping the toolkit is one edit, not two. If it moved
    # into code, the manifest and the gate could disagree with nothing to catch it.
    requires = ((_backends().get("cuda") or {}).get("requires")) or {}
    assert requires.get("vendor") == "nvidia"
    assert isinstance(
        requires.get("min_driver"), int
    ), "cuda.requires.min_driver must be an integer major version (R580+ is the CUDA 13 floor)"
