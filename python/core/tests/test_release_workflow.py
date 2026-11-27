"""Guard the macOS half of the Package workflow (`.github/workflows/release.yml`).

A signed and notarized macOS file is never byte-identical twice: every signature carries a
timestamp. So the files the evidence, the clean room and the testers check must be the very files
published (owner, 2026-10-10). The macOS job therefore signs only when run by hand on a release
branch, at the freeze, and keeps what it made long enough to be tested; the tag never re-signs, and
the files are attached to the draft by hand, as Windows' are. These tests fail if the job drifts
back to signing at the tag.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_macos_signs_only_by_hand_on_a_release_branch() -> None:
    cond = _jobs()["macos"]["if"]
    assert "github.event_name == 'workflow_dispatch'" in cond
    assert "startsWith(github.ref, 'refs/heads/release/')" in cond
    assert "vars.MACOS_SIGNING == 'enabled'" in cond
    assert (
        "refs/tags" not in cond
    ), "a tag must never re-sign: the published files are the tested ones"


def test_macos_reads_its_secrets_from_the_protected_environment() -> None:
    assert _jobs()["macos"]["environment"] == "release"


def test_the_signed_files_outlive_the_evidence() -> None:
    # Round 2, the clean room and the testers all run on these files before the tag; 14 days was
    # the PR artifacts' retention, too short for a release cycle.
    uploads = [s for s in _jobs()["macos"]["steps"] if "upload-artifact" in s.get("uses", "")]
    files = next(s for s in uploads if s["with"]["name"].startswith("knaif-macos-${{"))
    assert files["with"]["retention-days"] >= 90


# ── which pull requests are packaged ──────────────────────────────────────────


def _pr_paths() -> list[str]:
    # PyYAML reads the `on:` key as the boolean True.
    on = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))[True]
    return on["pull_request"]["paths"]


def _glob(pattern: str) -> re.Pattern[str]:
    """GitHub's path glob: `**` crosses folders, `*` and `?` do not."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def _packaged(path: str) -> bool:
    """GitHub's rule: patterns in order; the last one that matches decides, `!` excludes."""
    hit = False
    for pattern in _pr_paths():
        negate = pattern.startswith("!")
        if _glob(pattern.lstrip("!")).match(path):
            hit = not negate
    return hit


def _skill_files_in_a_package() -> list[str]:
    """The skill files installers/package.sh copies into a package, read from its copy loop."""
    text = (REPO / "installers" / "package.sh").read_text(encoding="utf-8")
    loop = re.search(r"for f in ([a-z. ]+); do", text)
    assert loop, "package.sh no longer copies skill files in a `for f in ...` loop"
    return loop.group(1).split()


def test_the_package_copies_a_skills_runtime_files_only() -> None:
    files = _skill_files_in_a_package()
    assert "skill.yaml" in files and "tools.yaml" in files
    assert not any(f.endswith(".py") for f in files)


@pytest.mark.parametrize("name", ["skill.yaml", "tools.yaml", "prompt.yaml", "vocab.yaml"])
def test_a_skill_file_a_package_carries_is_packaged(name: str) -> None:
    if name not in _skill_files_in_a_package():
        pytest.skip(f"{name} is not packaged")
    assert _packaged(f"skills/ffmpeg/{name}")


@pytest.mark.parametrize(
    "path",
    [
        "skills/ffmpeg/profiles/email.yaml",
        "skills/ffmpeg/native/src/engine.rs",
        "skills/documents/native/Cargo.toml",
        "apps/cli/src/main.rs",
        "installers/package.sh",
        "contracts/runtime/core_tools.yaml",
    ],
)
def test_what_can_change_a_package_is_packaged(path: str) -> None:
    assert _packaged(path)


@pytest.mark.parametrize(
    "path",
    [
        "skills/ffmpeg/python/tests/test_agent_vs_knaif_media_checks.py",
        "skills/ffmpeg/python/_engine.py",
        "skills/documents/python/handlers.py",
        "docs/RELEASE.md",
        "site/org/src/pages/vs.astro",
    ],
)
def test_what_no_package_contains_is_not_packaged(path: str) -> None:
    """A skill's Python is never copied into a package (2026-11-27: a skill test queued the
    27-minute packaging build)."""
    assert not _packaged(path)


def test_the_draft_never_takes_macos_files_from_the_tag_run() -> None:
    draft = _jobs()["draft"]
    assert "macos" not in draft["needs"]
    assert "macos" not in draft["if"]
    assert "knaif-macos" not in yaml.safe_dump(draft["steps"])
