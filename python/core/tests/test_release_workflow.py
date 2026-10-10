"""Guard the macOS half of the Package workflow (`.github/workflows/release.yml`).

A signed and notarized macOS file is never byte-identical twice: every signature carries a
timestamp. So the files the evidence, the clean room and the testers check must be the very files
published (owner, 2026-10-10). The macOS job therefore signs only when run by hand on a release
branch, at the freeze, and keeps what it made long enough to be tested; the tag never re-signs, and
the files are attached to the draft by hand, as Windows' are. These tests fail if the job drifts
back to signing at the tag.
"""

from __future__ import annotations

from pathlib import Path

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


def test_the_draft_never_takes_macos_files_from_the_tag_run() -> None:
    draft = _jobs()["draft"]
    assert "macos" not in draft["needs"]
    assert "macos" not in draft["if"]
    assert "knaif-macos" not in yaml.safe_dump(draft["steps"])
