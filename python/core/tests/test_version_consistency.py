"""The release version must agree across every surface that declares it.

`installers/package.sh` derives the artifact version from Cargo.toml's
`[workspace.package]` version, while the Python wheel and the Inno Setup
installer each carry their own copy. If they drift, a release ships artifacts
whose names and reported versions disagree, so assert they move together.

Deliberately asserts agreement rather than pinning a literal, so a version bump
touches only the three declarations and never this file.
"""

import re
from pathlib import Path

ROOT = Path(".").resolve()
CARGO = ROOT / "Cargo.toml"
PYPROJECT = ROOT / "python" / "core" / "pyproject.toml"
ISS = ROOT / "installers" / "windows" / "knaif.iss"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _section(text: str, header: str) -> str:
    """Return the body of a TOML section, stopping at the next section header."""
    after = text.split(header, 1)[1]
    return re.split(r"^\[", after, maxsplit=1, flags=re.M)[0]


def _cargo_version() -> str:
    body = _section(CARGO.read_text(encoding="utf-8"), "[workspace.package]")
    match = re.search(r'^version\s*=\s*"([^"]+)"', body, re.M)
    assert match, "no version in Cargo.toml [workspace.package]"
    return match.group(1)


def _pyproject_version() -> str:
    body = _section(PYPROJECT.read_text(encoding="utf-8"), "[project]")
    match = re.search(r'^version\s*=\s*"([^"]+)"', body, re.M)
    assert match, "no version in python/core/pyproject.toml [project]"
    return match.group(1)


def _iss_version() -> str:
    text = ISS.read_text(encoding="utf-8")
    match = re.search(r'^\s*#define\s+AppVersion\s+"([^"]+)"', text, re.M)
    assert match, "no AppVersion define in installers/windows/knaif.iss"
    return match.group(1)


def test_release_version_is_consistent_across_surfaces() -> None:
    versions = {
        "Cargo.toml [workspace.package]": _cargo_version(),
        "python/core/pyproject.toml [project]": _pyproject_version(),
        "installers/windows/knaif.iss AppVersion": _iss_version(),
    }

    assert len(set(versions.values())) == 1, f"version drift across surfaces: {versions}"


def test_release_version_is_semver() -> None:
    # E0 tags releases `v<major>.<minor>.<patch>`; package.sh and knaif.iss build
    # artifact names from this string, so a non-semver value breaks the tag pattern.
    assert SEMVER.match(_cargo_version()), f"not semver: {_cargo_version()}"
