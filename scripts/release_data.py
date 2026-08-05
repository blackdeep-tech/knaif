#!/usr/bin/env python3
"""Refresh `site/data/release.json` from the latest PUBLISHED GitHub release.

Run with `just release-data`, **after** publishing a release (RELEASE.md §5).

WHY THIS EXISTS RATHER THAN READING Cargo.toml. The version bump merges to `main` before
the release is published, so a version-derived download URL advertises assets that do not
exist yet — a dead download button for however long that gap lasts. This snapshots what is
actually downloadable right now.

WHY IT IS COMMITTED. The websites must build without network access or a Python
environment, and Amplify rebuilds on every push to `main` — including pushes that have
nothing to do with a release. A committed snapshot means an unrelated docs commit cannot
change what the download buttons point at.

WHY NO CHECKSUMS ARE COPIED. `SHA256SUMS` is published as a release asset; the site links
to it. Copying the hashes here would create a second copy that can rot, and the site is
not what anyone should trust for integrity anyway.

Until CI exists this is a manual step — see the note in RELEASE.md §5. When
docs/plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md lands `release.yml`, fold it in there.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT_PATH = REPO / "site" / "data" / "release.json"

GITHUB_REPO = "blackdeep-tech/knaif"
API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"

SCHEMA_VERSION = 1
TIMEOUT = 30

# Which artifact serves which platform. Matched as a substring of the asset name so the
# version can float. Kept beside the platform ids in contracts/release/platforms.yaml —
# a name that stops matching is a hard error below, not a silently missing button.
ASSET_MATCHERS = {
    "windows-x64-setup.exe": ("windows-x64", "installer"),
    "windows-x64.zip": ("windows-x64", "portable"),
    "linux-x64.tar.gz": ("linux-x64", "portable"),
    "linux-x86_64.AppImage": ("linux-x64", "appimage"),
}


class ReleaseError(Exception):
    """The snapshot cannot be produced. Keep the previous one rather than write junk."""


def fetch(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "knaif-release-data",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:  # pragma: no cover - network
        raise ReleaseError(f"GitHub API returned {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network
        raise ReleaseError(f"could not reach the GitHub API: {exc.reason}") from exc


def build() -> dict[str, Any]:
    release = fetch(API)

    if release.get("draft"):
        raise ReleaseError("latest release is still a draft - publish it first")
    if release.get("prerelease"):
        raise ReleaseError("latest release is a prerelease - not advertising it")

    tag = release.get("tag_name")
    if not tag:
        raise ReleaseError("release has no tag_name")

    assets: dict[str, Any] = {}
    checksums: str | None = None

    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        url = asset.get("browser_download_url")
        if name == "SHA256SUMS":
            checksums = url
            continue
        for suffix, (platform, kind) in ASSET_MATCHERS.items():
            if name.endswith(suffix):
                assets.setdefault(platform, {})[kind] = {
                    "name": name,
                    "url": url,
                    "size_bytes": asset.get("size"),
                }
                break

    if not assets:
        raise ReleaseError(
            f"release {tag} published no recognised assets. Either the release is broken "
            f"or the naming changed - update ASSET_MATCHERS in this script and the "
            f"artifact templates in contracts/release/platforms.yaml together."
        )
    if checksums is None:
        # Every artifact table in RELEASE.md ships SHA256SUMS; its absence means an
        # incomplete publish, and the download page would have nothing to verify against.
        raise ReleaseError(f"release {tag} published no SHA256SUMS asset")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "scripts/release_data.py",
        "tag": tag,
        "version": tag.lstrip("v"),
        "published_at": release.get("published_at"),
        "release_page": release.get("html_url") or RELEASES_PAGE,
        "releases_page": RELEASES_PAGE,
        "checksums_url": checksums,
        "assets": assets,
    }


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    try:
        text = render(build())
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" so the committed file is byte-identical on Windows and Linux.
    with OUT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {OUT_PATH.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
