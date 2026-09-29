"""Carrying accepted results over a CODE change, on the evidence of a pre-registered sample.

1.2.0 RC3 changed how the native binary finds its external tools (`skill.yaml` gained a `windows:`
block, the skills launch the path the lookup returns). A text-fix equivalence cannot describe that:
the native source changes in code, and each skill's `bundle` fingerprint moves too. The owner chose
(2026-09-29) to carry the L3/L4 results over on a pre-registered sample check instead of
re-measuring. A `sampled` equivalence maps the measured `native` source and the measured `bundle`
of each skill to the current ones, names the committed sample run, and the gate says "(sampled)".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from knaif.evalsuite.gate import (
    _sha256_tree,
    bundle_change_allowed,
    evidence_tuple,
    sample_run_problems,
    tree_at_commit,
)

from .test_gate_equivalence import _layers, _write, tree  # noqa: F401 - pytest fixture

SKILL_YAML = "name: demo\n"
WITH_DEPS = (
    "name: demo\n"
    "dependencies:\n"
    "  external_tools:\n"
    "    - name: tool\n"
    "      commands: [tool]\n"
    "      windows: {winget: Vendor.Tool, dirs: ['%ProgramFiles%\\\\Tool']}\n"
)


@pytest.fixture
def rc3(tree):  # noqa: F811 - the imported fixture
    """The measured build, then an RC3-like change: native source AND the bundle's skill.yaml."""
    root, old_native, old, old_sha, new, new_sha = tree
    old_bundle = evidence_tuple("demo", root)["bundle"]
    (root / "skills" / "demo" / "skill.yaml").write_text(WITH_DEPS, encoding="utf-8")
    now = evidence_tuple("demo", root)
    return root, (old_native, now["native"]), (old_bundle, now["bundle"]), (old_sha, new_sha), new


def _sampled(
    eid, native, bundle, binary, *, skill="demo", sample_run="evals/runs/x", kind="sampled"
):
    entry = {
        "id": eid,
        "fingerprints": {
            "native": {"from": native[0], "to": native[1]},
            "bundle": {skill: {"from": bundle[0], "to": bundle[1]}},
        },
        "binaries": {"windows-x64": {"from": binary[0], "to": binary[1]}},
    }
    if kind:
        entry["kind"] = kind
    if sample_run:
        entry["sample_run"] = sample_run
    return entry


def test_without_an_equivalence_both_fingerprints_are_stale(rc3) -> None:
    root, _, _, _, new = rc3
    layers, _ = _layers(root, [new])
    assert layers["L4"].state == "stale"
    assert "bundle" in layers["L4"].detail and "native" in layers["L4"].detail


def test_a_sampled_equivalence_carries_native_and_bundle_and_says_so(rc3) -> None:
    root, native, bundle, binary, new = rc3
    _write(root, [_sampled("rc3", native, bundle, binary)])
    layers, gate = _layers(root, [new])
    assert layers["L4"].state == "valid" and layers["L3"].state == "valid"
    assert gate.derived == "supported"
    assert "equivalent: rc3 (sampled)" in layers["L4"].detail


def test_a_text_equivalence_cannot_carry_a_bundle_change(rc3) -> None:
    root, native, bundle, binary, new = rc3
    _write(root, [_sampled("textfix", native, bundle, binary, kind=None, sample_run=None)])
    layers, _ = _layers(root, [new])
    assert layers["L4"].state == "stale"


def test_a_sampled_equivalence_must_name_its_sample_run(rc3) -> None:
    root, native, bundle, binary, new = rc3
    _write(root, [_sampled("rc3", native, bundle, binary, sample_run=None)])
    layers, _ = _layers(root, [new])
    assert layers["L4"].state == "stale"


def test_a_bundle_mapping_for_another_skill_does_not_carry(rc3) -> None:
    root, native, bundle, binary, new = rc3
    _write(root, [_sampled("rc3", native, bundle, binary, skill="other")])
    layers, _ = _layers(root, [new])
    assert layers["L4"].state == "stale" and "bundle" in layers["L4"].detail


def test_the_bundle_mapping_must_name_the_exact_values(rc3) -> None:
    root, native, bundle, binary, new = rc3
    _write(root, [_sampled("rc3", native, (bundle[0], "f" * 64), binary)])
    layers, _ = _layers(root, [new])
    assert layers["L4"].state == "stale"


# ── what the command accepts ──────────────────────────────────────────────────────────────────


def test_a_dependencies_only_change_to_skill_yaml_is_allowed() -> None:
    assert bundle_change_allowed(SKILL_YAML, WITH_DEPS)
    # The native status claim is not content either.
    assert bundle_change_allowed(
        "name: demo\nruntimes: {native: {status: parity}}\n",
        "name: demo\nruntimes: {native: {status: supported}}\n" + WITH_DEPS.split("\n", 1)[1],
    )


@pytest.mark.parametrize(
    "new",
    [
        "name: demo\nprompt: other.yaml\n",
        "name: renamed\n",
        "name: demo\nrecommended_model: knaif-qwen3-4b-v9\n",
        "::: not yaml :::\n",
    ],
)
def test_any_other_skill_yaml_change_is_refused(new) -> None:
    assert not bundle_change_allowed(SKILL_YAML, new)


VERDICTS_OK = """== win ffmpeg
sample 20: 20 identical, 0 different
VERDICT: equivalent on the sample
== win documents
sample 40: 40 identical, 0 different
VERDICT: equivalent on the sample
== linux ffmpeg
sample 20: 20 identical, 0 different
VERDICT: equivalent on the sample
== linux documents
sample 40: 40 identical, 0 different
VERDICT: equivalent on the sample
"""


def _run(tmp_path: Path, verdicts: str, complete: str | None = None) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "verdicts.txt").write_text(verdicts, encoding="utf-8")
    (run / "COMPLETE").write_text(
        complete or "START win x\nDONE win x\nSTART linux x\nDONE linux x\n", encoding="utf-8"
    )
    return run


OSES = {"windows-x64", "linux-x64"}
SKILLS = {"ffmpeg", "documents"}


def test_a_clean_sample_run_has_no_problems(tmp_path) -> None:
    assert sample_run_problems(_run(tmp_path, VERDICTS_OK), OSES, SKILLS) == []


def test_every_os_and_skill_must_be_equivalent(tmp_path) -> None:
    run = _run(tmp_path, VERDICTS_OK.split("== linux documents")[0])
    problems = sample_run_problems(run, OSES, SKILLS)
    assert any("linux-x64 documents" in p for p in problems)


def test_a_not_equivalent_verdict_is_a_problem(tmp_path) -> None:
    bad = VERDICTS_OK.replace(
        "== win documents\nsample 40: 40 identical, 0 different\nVERDICT: equivalent on the sample",
        "== win documents\nsample 40: 39 identical, 1 different\nVERDICT: NOT equivalent",
    )
    assert any(
        "windows-x64 documents" in p for p in sample_run_problems(_run(tmp_path, bad), OSES, SKILLS)
    )


def test_a_failed_line_is_a_problem(tmp_path) -> None:
    run = _run(tmp_path, "FAILED win: documents: not equivalent\n" + VERDICTS_OK)
    assert sample_run_problems(run, OSES, SKILLS)


def test_a_stage_that_finished_with_failures_is_a_problem(tmp_path) -> None:
    complete = "START win x\nFINISHED WITH FAILURES win x\nSTART linux x\nDONE linux x\n"
    assert sample_run_problems(_run(tmp_path, VERDICTS_OK, complete), OSES, SKILLS)


def test_each_stage_must_start_once_and_end_done(tmp_path) -> None:
    """Two runs overlapping in one folder (one START, two endings) is not a clean run."""
    run = _run(
        tmp_path,
        VERDICTS_OK,
        complete="START win x\nDONE win x\nDONE win y\nSTART linux x\nDONE linux x\n",
    )
    assert sample_run_problems(run, OSES, SKILLS)


# ── fingerprints at a commit ──────────────────────────────────────────────────────────────────


def test_tree_at_commit_matches_the_checkout_for_a_bundle(tmp_path) -> None:
    """The bundle fingerprint at a commit must equal the one computed from the checkout, including
    `skill.yaml`'s masked native status claim and paths relative to the skill folder."""
    repo = tmp_path / "repo"
    skill = repo / "skills" / "demo"
    (skill / "native" / "src").mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: demo\nruntimes: {native: {status: supported}}\n", encoding="utf-8"
    )
    (skill / "native" / "src" / "lib.rs").write_text("fn f() {}\n", encoding="utf-8")

    def git(*a: str) -> None:
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("-c", "user.email=a@b", "-c", "user.name=t", "add", ".")
    git("-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "x")
    patterns = ("*.yaml", "python/**/*.py", "native/src/**/*.rs")
    assert tree_at_commit(repo, "HEAD", patterns, base="skills/demo") == _sha256_tree(
        skill, patterns
    )
