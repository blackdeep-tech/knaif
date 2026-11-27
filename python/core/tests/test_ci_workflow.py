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
    """The job filters, as the workflow actually spells them."""
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


JOBS = ("python", "native", "site", "packaging", "macos")


def test_workflow_declares_one_filter_per_job():
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
        # The macOS job takes only the scripts it uses (see the tests below), not scripts/ whole.
        ("scripts/site_data.py", {"python"}),
        ("scripts/build_native_kind.sh", {"python", "macos"}),
        ("native/crates/knaif-core/src/lib.rs", {"native", "macos"}),
        ("apps/cli/src/main.rs", {"native", "macos"}),
        ("Cargo.toml", {"native", "macos"}),
        ("rust-toolchain.toml", {"native", "macos"}),
        ("site/org/src/pages/index.astro", {"site"}),
        ("installers/smoke.sh", {"packaging", "macos"}),
        ("installers/macos/pkg/Distribution.xml.in", {"packaging", "macos"}),
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
        # The L1/L2 parity contracts (plan 2026-09-10, L1d) and the generation settings
        # (V4). Each is consumed by a Python test and a Rust test, so a change to one that
        # reached only half the repo would let the two runtimes drift through the very file
        # written to stop that.
        "contracts/parity/prompt_cases.json",
        "contracts/parity/retrieval_cases.json",
        "contracts/parity/planner_cases.json",
        "contracts/parity/clarify_gate_cases.json",
        "contracts/parity/example_cases.json",
        "contracts/runtime/generation.yaml",
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


@pytest.mark.parametrize(
    "path",
    ["python/core/knaif/agent.py", "skills/ffmpeg/python/handlers.py", "docs/RELEASE.md"],
)
def test_python_and_docs_changes_never_queue_for_a_macos_runner(path):
    """D11: a Python-only or docs PR must not wait on the macOS job."""
    assert not _decide([path])["macos"]


@pytest.mark.parametrize(
    "path", ["contracts/parity/prompt_cases.json", "contracts/runtime/generation.yaml"]
)
def test_a_parity_contract_reaches_the_macos_job(path):
    """macOS is a supported platform from 1.3.0, which the gate allows only while the parity
    contracts record coverage there (`_coverage.macos: CI`). The macOS job runs
    `just check-contracts`, so a change to a contract must reach it."""
    assert _decide([path])["macos"]


#: What the macOS job runs that can name a script: the build and packaging scripts (directly or
#: through `just package-native` / `just package-pkg`), the contract check, and the installer
#: tests it runs under macOS's bash 3.2. Scanned, not listed, so a script one of them starts
#: using later reaches the macOS job without anyone remembering to add it.
_MACOS_USERS = (
    "scripts/build_native_kind.sh",
    "scripts/parity_check.py",
    "installers/package.sh",
    "installers/smoke.sh",
    *sorted(
        str(p.relative_to(REPO)).replace("\\", "/")
        for p in (REPO / "installers" / "macos").glob("*.sh")
    ),
)
_MACOS_TESTS = (
    "python/core/tests/test_installer_pkg.py",
    "python/core/tests/test_macos_release.py",
    "python/core/tests/test_macos_signing.py",
    "python/core/tests/test_macho_deps.py",
    "python/core/tests/test_l4_rows.py",
)
_SCRIPT_REF = re.compile(r"scripts/[A-Za-z0-9_.-]+\.(?:sh|py|ps1)")


def _scripts_the_macos_job_uses() -> set[str]:
    found = set()
    for rel in (*_MACOS_USERS, *_MACOS_TESTS):
        text = (REPO / rel).read_text(encoding="utf-8")
        found |= {m for m in _SCRIPT_REF.findall(text) if (REPO / m).exists()}
    # The tests also import scripts by bare name (`check_macho_deps.py`).
    for rel in _MACOS_TESTS:
        text = (REPO / rel).read_text(encoding="utf-8")
        for name in re.findall(r"[\"']([a-z0-9_]+\.(?:py|sh))[\"']", text):
            if (REPO / "scripts" / name).exists():
                found.add(f"scripts/{name}")
    return found


def test_the_macos_job_uses_scripts_the_scan_finds():
    """The scan is not vacuous: it finds the build script and the Mach-O check at least."""
    found = _scripts_the_macos_job_uses()
    assert {"scripts/build_native_kind.sh", "scripts/check_macho_deps.py"} <= found, found


def test_every_script_the_macos_job_uses_reaches_it():
    """Narrowing `scripts/` must never leave out a script the macOS build or its tests use."""
    missing = [s for s in sorted(_scripts_the_macos_job_uses()) if not _decide([s])["macos"]]
    assert not missing, f"the macOS filter misses scripts its job uses: {missing}"


@pytest.mark.parametrize("path", _MACOS_TESTS)
def test_the_installer_tests_the_macos_job_runs_reach_it(path):
    """The job runs these under macOS's bash 3.2, so a change to one must queue it."""
    assert _decide([path])["macos"], path


@pytest.mark.parametrize(
    "path",
    [
        "scripts/agent_vs_knaif/local_compare.py",
        "scripts/site_data.py",
        "scripts/check_site_a11y.py",
        "scripts/release_data.py",
    ],
)
def test_eval_and_site_scripts_never_queue_for_a_macos_runner(path):
    """An eval harness or a site script cannot change the macOS build."""
    assert not _decide([path])["macos"], path


def test_a_skill_contract_or_crate_reaches_the_macos_job():
    """The .pkg mirrors skill.yaml's macos blocks, and each skill crate is built there."""
    for path in ("skills/documents/skill.yaml", "skills/ffmpeg/native/src/lib.rs"):
        assert _decide([path])["macos"], path


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
