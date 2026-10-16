"""Guard the evidence-cell workflow (`.github/workflows/evidence-cell.yml`).

It runs part of one L4 cell on GitHub's runners: the CPU cells and macOS Metal that the
faster-release-evidence plan moves off the maintainer's box (Part C). Evidence is only evidence if
it measures the files that were frozen, so the workflow may run only when a person starts it (no
fork or PR can; a push only during the T0 experiment, on its own branch, when asked to), may write
nothing back, and must check every file it downloads against a published sha256 before using it.
It must also work unchanged in a fork, so no repository or machine is named in it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "evidence-cell.yml"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return doc.get("on", doc.get(True))


def _run_text(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def test_only_a_person_can_start_it() -> None:
    triggers = _triggers(_doc())
    assert "workflow_dispatch" in triggers
    # No fork or PR can start it; the only other trigger is the T0 experiment's below.
    assert set(triggers) <= {"workflow_dispatch", "push"}


def test_the_t0_push_trigger_is_narrow_and_temporary() -> None:
    """While the method is an experiment it runs from its own branch (owner, 2026-10-17): a push
    starts it only on that one branch and only when the commit message says `[t0]`. The trigger
    is removed before the workflow is ever merged, which this test cannot see, so the plan's T0b
    says it."""
    doc = _doc()
    push = _triggers(doc).get("push")
    if push is None:
        return
    branches = push["branches"]
    assert len(branches) == 1 and branches[0].startswith("feat/")
    assert "[t0]" in doc["jobs"]["setup"]["if"]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "remove before merging" in text.lower()


def test_it_can_write_nothing_back() -> None:
    perms = _doc()["permissions"]
    assert perms == {"contents": "read"}


def test_the_inputs_name_what_is_measured() -> None:
    inputs = _triggers(_doc())["workflow_dispatch"]["inputs"]
    assert {"release", "os", "skill", "model", "rows", "shards"} <= set(inputs)
    assert set(inputs["os"]["options"]) == {"windows", "linux", "macos"}


def test_every_download_is_checked_against_a_published_sha256() -> None:
    cell = _doc()["jobs"]["cell"]
    run = _run_text(cell)
    assert "SHA256SUMS" in run, "the release file must be checked against the release's sums"
    assert "model-manifest.yaml" in run, "the model must be checked against the manifest's sha256"
    assert "sha256 mismatch" in run, "a mismatch must stop the job, with a reason"


def test_macos_runs_on_apple_silicon() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "macos-15" in text


def test_nothing_names_a_repository_branch_or_machine() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    for word in ("blackdeep", "knaif-lab", "refs/heads/main", "c:/users", "/home/", "/users/"):
        assert word not in text, f"{word!r} would tie the workflow to one checkout"


def test_each_shard_job_draws_its_own_shard() -> None:
    # The draw is deterministic, so every job redraws the cell's shards and runs its own; no
    # job depends on another's upload, and anyone can redraw them to check.
    assert "evalsuite shards" in _run_text(_doc()["jobs"]["cell"])


def test_the_results_come_back_as_artifacts() -> None:
    cell = _doc()["jobs"]["cell"]
    uploads = [s for s in cell["steps"] if "upload-artifact" in s.get("uses", "")]
    assert uploads, "the cell's boards and timings must be uploaded"
    assert all(s.get("if") == "always()" for s in uploads), "a failed shard still reports"
