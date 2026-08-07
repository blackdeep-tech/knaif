"""Guard the CI workflow's path filter.

`.github/workflows/ci.yml` decides which jobs run from which files a PR touches. That rule
has one failure mode worth a test: if a pattern stops matching, the job it gates silently
stops running and CI still reports green. Nothing else in the repo would notice — a job
that does not run looks exactly like a job with nothing to say.

The patterns are **extracted from the workflow**, never restated here. A copy would drift
from the file it is supposed to be checking, which is the same reason `test_site_data.py`
regenerates rather than asserting a snapshot of the generator's output.

Dialect note: the workflow evaluates these with `grep -E`, this test with Python `re`. The
constructs used — anchors, alternation, groups, escaped dots, negated classes — mean the
same thing in both.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

# echo "python=$(match "<pattern>")"
_MATCH_LINE = re.compile(r'echo\s+"(?P<name>\w+)=\$\(match\s+"(?P<pattern>.+?)"\)"')
_COMMON = re.compile(r"common='(?P<pattern>[^']+)'")


def _filters() -> dict[str, re.Pattern[str]]:
    """The four job filters, as the workflow actually spells them."""
    text = WORKFLOW.read_text(encoding="utf-8")

    common = _COMMON.search(text)
    assert common, "the `changes` job no longer defines a `common` pattern"

    found = {}
    for match in _MATCH_LINE.finditer(text):
        pattern = match.group("pattern").replace("$common", common.group("pattern"))
        found[match.group("name")] = re.compile(pattern)

    assert found, "no `match` expressions found in ci.yml - did the changes job move?"
    return found


def _decide(files: list[str]) -> dict[str, bool]:
    """Mirror the workflow: empty file list (a push to main) runs everything."""
    filters = _filters()
    if not files:
        return dict.fromkeys(filters, True)
    return {name: any(rx.search(f) for f in files) for name, rx in filters.items()}


JOBS = ("python", "native", "site", "packaging")


def test_workflow_declares_the_four_filters():
    assert set(_filters()) == set(JOBS)


def test_every_filtered_job_exists_in_the_workflow():
    """A filter naming a job that does not exist gates nothing."""
    text = WORKFLOW.read_text(encoding="utf-8")
    for job in JOBS:
        assert re.search(rf"^  {job}:$", text, re.MULTILINE), f"no `{job}:` job in ci.yml"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("python/core/knaif/planner.py", {"python"}),
        ("scripts/site_data.py", {"python"}),
        ("native/crates/knaif-core/src/lib.rs", {"native"}),
        ("apps/cli/src/main.rs", {"native"}),
        ("Cargo.toml", {"native"}),
        ("rust-toolchain.toml", {"native"}),
        ("site/org/src/pages/index.astro", {"site"}),
        ("installers/smoke.sh", {"packaging"}),
        # Docs are not gated by any job; only the aggregate check runs.
        ("docs/plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md", set()),
        ("README.md", set()),
    ],
)
def test_single_file_routes_to_the_right_jobs(path, expected):
    decided = {job for job, hit in _decide([path]).items() if hit}
    assert decided == expected


@pytest.mark.parametrize(
    "path",
    [
        "contracts/runtime/core_tools.yaml",
        "skills/ffmpeg/tools.yaml",
        "skills/ffmpeg/python/handlers.py",
        "skills/ffmpeg/native/src/lib.rs",
        "skills/documents/skill.yaml",
    ],
)
def test_dual_runtime_paths_run_both_runtimes(path):
    """`contracts/` and `skills/` are read by BOTH loaders.

    A skill bundle carries its YAML contract at the top and a Cargo workspace member under
    `native/`, so no change inside one may run only half the repo. This is the case the
    first draft got wrong: `skills/` routed to Python alone, which would have let a change
    to a skill's Rust crate merge without `cargo` ever building it.
    """
    decided = _decide([path])
    assert decided["python"] and decided["native"], f"{path} must run both runtimes"


@pytest.mark.parametrize("path", ["justfile", ".github/workflows/ci.yml", "mise.toml"])
def test_files_that_define_the_build_run_everything(path):
    """These decide what every other job does, so none of them may skip a job."""
    assert all(_decide([path]).values()), f"{path} must run every job"


def test_push_to_main_runs_everything():
    """The workflow passes an empty file list on push, meaning "no filtering".

    It is the backstop for a path rule that is subtly wrong: the mistake then surfaces on
    main rather than never surfacing at all.
    """
    assert all(_decide([]).values())


def test_a_nested_directory_does_not_match_a_top_level_prefix():
    """`docs/site/` is not the website; the patterns are anchored for this reason."""
    assert not any(_decide(["docs/site/notes.md"]).values())


def test_the_aggregate_gate_treats_skipped_as_success():
    """`ci` is the only check branch protection should require (C5).

    Every other job is conditional, and a required check that is skipped blocks a PR
    forever. This asserts the two halves that make the aggregate correct: it runs
    unconditionally, and it fails on cancellation rather than reading it as green.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    gate = text.split("\n  ci:\n", 1)
    assert len(gate) == 2, "no `ci:` aggregate job in ci.yml"
    body = gate[1]
    assert "if: always()" in body
    assert "failure" in body and "cancelled" in body
