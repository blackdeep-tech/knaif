"""Guard `scripts/release_data.py` and the committed `site/data/release.json`.

The download buttons on knaif.org are built from that snapshot, so the failure modes are
public and embarrassing: a button that 404s, or one that advertises a draft.

These are **offline** tests. Asserting that every asset URL returns 200 is an acceptance
gate (plan §9) run against a built site, not a unit test — a suite that reaches GitHub on
every run fails on a plane and gates releases on someone else's uptime.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(".").resolve()
SCRIPT = ROOT / "scripts" / "release_data.py"
DATA = ROOT / "site" / "data" / "release.json"
PLATFORMS = ROOT / "contracts" / "release" / "platforms.yaml"


def _load():
    spec = importlib.util.spec_from_file_location("release_data", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rd = _load()
snapshot = json.loads(DATA.read_text(encoding="utf-8"))


def test_snapshot_uses_lf_endings():
    assert b"\r\n" not in DATA.read_bytes()


def test_snapshot_has_every_field_the_download_page_reads():
    for key in (
        "tag",
        "version",
        "published_at",
        "release_page",
        "releases_page",
        "checksums_url",
        "assets",
    ):
        assert snapshot.get(key), f"release.json is missing {key!r}"


def test_version_matches_the_tag():
    assert snapshot["version"] == snapshot["tag"].lstrip("v")


def test_every_asset_url_points_at_the_snapshotted_tag():
    """A URL from a different tag means two releases got mixed into one snapshot."""
    tag = snapshot["tag"]
    urls = [a["url"] for kinds in snapshot["assets"].values() for a in kinds.values()]
    urls.append(snapshot["checksums_url"])
    for url in urls:
        assert f"/download/{tag}/" in url, f"{url} does not belong to {tag}"


def test_every_supported_platform_has_a_downloadable_asset():
    """A platform advertised in the contract with no asset renders an empty card."""
    doc = yaml.safe_load(PLATFORMS.read_text(encoding="utf-8"))
    for platform in doc["platforms"]:
        if platform["status"] != "supported":
            continue
        assets = snapshot["assets"].get(platform["id"])
        assert assets, f"{platform['id']} is supported but has no published asset"


def test_contract_artifact_templates_match_the_published_names():
    """`<ver>` templates in the contract must still describe reality.

    If a release renames an artifact, the matcher in release_data.py silently drops it and
    the download button vanishes. This catches the rename instead.
    """
    doc = yaml.safe_load(PLATFORMS.read_text(encoding="utf-8"))
    version = snapshot["version"]
    for platform in doc["platforms"]:
        for artifact in platform.get("artifacts") or []:
            expected = artifact["artifact"].replace("<ver>", version)
            published = snapshot["assets"].get(platform["id"], {}).get(artifact["kind"])
            assert published, (
                f"{platform['id']} declares a {artifact['kind']} artifact "
                f"({expected}) that the release did not publish"
            )
            assert (
                published["name"] == expected
            ), f"contract expects {expected}, release published {published['name']}"


def test_asset_matchers_cover_every_contract_artifact():
    """The script's matchers and the contract's templates must not drift apart."""
    doc = yaml.safe_load(PLATFORMS.read_text(encoding="utf-8"))
    covered = set(rd.ASSET_MATCHERS.values())
    for platform in doc["platforms"]:
        for artifact in platform.get("artifacts") or []:
            pair = (platform["id"], artifact["kind"])
            assert pair in covered, (
                f"contracts/release/platforms.yaml declares {pair} but "
                f"ASSET_MATCHERS in scripts/release_data.py cannot match it"
            )


def test_sizes_are_plausible():
    """A zero-byte asset means a broken upload; the page would offer an empty download."""
    for kinds in snapshot["assets"].values():
        for kind, asset in kinds.items():
            assert asset["size_bytes"] > 1_000_000, f"{kind} is implausibly small"


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_drafts_and_prereleases_are_refused(field, monkeypatch):
    """Publishing is what makes assets exist; a draft has none."""
    monkeypatch.setattr(rd, "fetch", lambda _url: {field: True, "tag_name": "v9.9.9"})
    with pytest.raises(rd.ReleaseError):
        rd.build()


def test_a_release_without_checksums_is_refused(monkeypatch):
    """SHA256SUMS is what the download page tells people to verify against."""
    monkeypatch.setattr(
        rd,
        "fetch",
        lambda _url: {
            "tag_name": "v9.9.9",
            "assets": [
                {
                    "name": "knaif-9.9.9-windows-x64-setup.exe",
                    "browser_download_url": "https://example.invalid/x",
                    "size": 5,
                }
            ],
        },
    )
    with pytest.raises(rd.ReleaseError, match="SHA256SUMS"):
        rd.build()


def test_a_release_with_unrecognised_assets_is_refused(monkeypatch):
    """A rename must fail loudly rather than produce a page with no buttons."""
    monkeypatch.setattr(
        rd,
        "fetch",
        lambda _url: {
            "tag_name": "v9.9.9",
            "assets": [
                {
                    "name": "knaif-9.9.9-renamed.exe",
                    "browser_download_url": "https://example.invalid/x",
                    "size": 5,
                }
            ],
        },
    )
    with pytest.raises(rd.ReleaseError, match="recognised assets"):
        rd.build()
