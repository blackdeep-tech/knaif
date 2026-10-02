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
    artifact_binary,
    bundle_change_allowed,
    contracts_change_allowed,
    evidence_tuple,
    run_preregistered,
    sample_run_problems,
    sampled_bundle_file_allowed,
    tree_at_commit,
)

from .test_gate_equivalence import _layers, tree  # noqa: F401 - pytest fixture
from .test_gate_equivalence import _write as _write_plain


def _git(root: Path, *args: str) -> str:
    env = ["-c", "user.email=a@b", "-c", "user.name=t", "-c", "core.autocrlf=false"]
    return subprocess.run(
        ["git", *env, *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root: Path, msg: str) -> str:
    if not (root / ".git").exists():
        _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", msg)
    return _git(root, "rev-parse", "HEAD")


def _write(root: Path, entries: list[dict]) -> None:
    """The gate re-derives a sampled entry from git, so each names the measured commit (the
    repo's first) and the current one (HEAD), as `evalsuite equivalence` records them."""
    first = _git(root, "rev-list", "--max-parents=0", "HEAD")
    for entry in entries:
        entry.setdefault("from_commit", first)
        entry.setdefault("to_commit", _git(root, "rev-parse", "HEAD"))
    _write_plain(root, entries)


SKILL_YAML = "name: demo\n"
WITH_DEPS = (
    "name: demo\n"
    "dependencies:\n"
    "  external_tools:\n"
    "    - name: tool\n"
    "      commands: [tool]\n"
    "      windows: {winget: Vendor.Tool, dirs: ['%ProgramFiles%\\\\Tool']}\n"
)


def _sample_run(root: Path, rel: str = "evals/runs/x", verdict: str = "equivalent on the sample"):
    """A sample run on disk for the one OS (windows-x64) and skill (demo) the fixture measures."""
    run = root / rel
    run.mkdir(parents=True, exist_ok=True)
    (run / "verdicts.txt").write_text(f"== win demo\nVERDICT: {verdict}\n", encoding="utf-8")
    (run / "COMPLETE").write_text("START win x\nDONE win x\n", encoding="utf-8")
    return run


@pytest.fixture
def rc3(tree):  # noqa: F811 - the imported fixture
    """The measured build, then an RC3-like change: native source AND the bundle's skill.yaml."""
    root, old_native, old, old_sha, new, new_sha = tree
    old_bundle = evidence_tuple("demo", root)["bundle"]
    _commit(root, "measured")
    (root / "skills" / "demo" / "skill.yaml").write_text(WITH_DEPS, encoding="utf-8")
    _sample_run(root)
    _commit(root, "rc3")
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


def test_the_gate_rechecks_the_sample_run_it_names(rc3) -> None:
    """A hand-edited entry naming a run that is missing or did not pass carries nothing (Codex)."""
    root, native, bundle, binary, new = rc3
    _write(root, [_sampled("rc3", native, bundle, binary, sample_run="evals/runs/missing")])
    assert _layers(root, [new])[0]["L4"].state == "stale"
    _sample_run(root, "evals/runs/bad", verdict="NOT equivalent")
    _write(root, [_sampled("rc3", native, bundle, binary, sample_run="evals/runs/bad")])
    assert _layers(root, [new])[0]["L4"].state == "stale"


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


def test_an_indented_failed_line_is_a_problem(tmp_path) -> None:
    run = _run(tmp_path, "   FAILED win: documents: not equivalent\n" + VERDICTS_OK)
    assert sample_run_problems(run, OSES, SKILLS)


def test_a_second_block_for_the_same_pair_is_a_problem(tmp_path) -> None:
    """A good verdict must not overwrite an earlier bad one for the same OS and skill (Codex)."""
    bad_first = "== win ffmpeg\nVERDICT: NOT equivalent\n" + VERDICTS_OK
    problems = sample_run_problems(_run(tmp_path, bad_first), OSES, SKILLS)
    assert any("windows-x64 ffmpeg" in p for p in problems)


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


# ── the binaries are the ones the run tested ──────────────────────────────────────────────────


def test_the_binary_inside_a_zip_and_a_tarball_is_found(tmp_path) -> None:
    import hashlib
    import io
    import tarfile
    import zipfile

    exe = (
        b"MZ"
        + b"\0" * 0x3A
        + (0x40).to_bytes(4, "little")
        + b"PE\0\0"
        + (0x8664).to_bytes(2, "little")
    )
    zpath = tmp_path / "knaif-9.9.9-windows-x64.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("knaif-9.9.9-windows-x64/bin/knaif.exe", exe)
        z.writestr("knaif-9.9.9-windows-x64/README.txt", "x")
    elf = b"\x7fELF" + bytes([2, 1]) + b"\0" * 12 + (0x3E).to_bytes(2, "little") + b"body"
    tpath = tmp_path / "knaif-9.9.9-linux-x64.tar.gz"
    with tarfile.open(tpath, "w:gz") as t:
        info = tarfile.TarInfo("knaif-9.9.9-linux-x64/bin/knaif")
        info.size = len(elf)
        t.addfile(info, io.BytesIO(elf))
    assert artifact_binary(zpath) == ("windows-x64", hashlib.sha256(exe).hexdigest())
    assert artifact_binary(tpath) == ("linux-x64", hashlib.sha256(elf).hexdigest())


# ── pre-registration ──────────────────────────────────────────────────────────────────────────


def _git_repo(path: Path):
    path.mkdir(parents=True)

    def git(*a: str) -> str:
        env = ["-c", "user.email=a@b", "-c", "user.name=t"]
        return subprocess.run(
            ["git", *env, *a], cwd=path, check=True, capture_output=True, text=True
        ).stdout

    git("init", "-q")
    return git


def test_rules_committed_before_the_results_are_pre_registered(tmp_path) -> None:
    repo = tmp_path / "repo"
    git = _git_repo(repo)
    run = repo / "evals" / "runs" / "r"
    run.mkdir(parents=True)
    (run / "run.sh").write_text("# rules\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "rules")
    (run / "verdicts.txt").write_text("VERDICT\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "results")
    assert run_preregistered(repo, "evals/runs/r")


def test_rules_committed_with_the_results_are_not_pre_registered(tmp_path) -> None:
    repo = tmp_path / "repo"
    git = _git_repo(repo)
    run = repo / "evals" / "runs" / "r"
    run.mkdir(parents=True)
    (run / "run.sh").write_text("# rules\n", encoding="utf-8")
    (run / "verdicts.txt").write_text("VERDICT\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "both at once")
    assert not run_preregistered(repo, "evals/runs/r")


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


# ── 1.2.1: a sample that also checks the Python side may carry python_core and contracts ────────
#
# 1.2.1 changed the Python core (a password keeps the user's spelling, `prompt.py`), a Python
# handler in the ffmpeg bundle, and the backend manifest under contracts/backends/ — which no skill
# reads. None of that could ride a sampled equivalence, whose run checked the native binary only.
# The owner chose (2026-10-02) to let it, when the run ALSO checks the Python runtime on the same
# requests (a `python` stage: both runtimes' plans and commands against the accepted L3 rows).


def _with_python_stage(root: Path, rel: str = "evals/runs/x", **tree_override) -> None:
    """A `python` stage, and its record of the tree it ran on (the current one by default)."""
    import json

    run = root / rel
    now = evidence_tuple("demo", root)
    record = {"python_core": now["python_core"], "contracts": now["contracts"]}
    record["bundle"] = {"demo": now["bundle"]}
    record.update(tree_override)
    (run / "python_tree.json").write_text(json.dumps(record), encoding="utf-8")
    with open(run / "verdicts.txt", "a", encoding="utf-8") as f:
        f.write("== python demo\nVERDICT: equivalent on the sample\n")
    with open(run / "COMPLETE", "a", encoding="utf-8") as f:
        f.write("START python x\nDONE python x\n")


@pytest.fixture
def patch121(rc3):
    """rc3, plus a Python-core change and a backend-manifest change."""
    root, native, bundle, binary, new = rc3
    from knaif.evalsuite.gate import evidence_tuple as et

    before = et("demo", root)
    (root / "python" / "core" / "knaif" / "prompt.py").write_text("x = 2\n", encoding="utf-8")
    (root / "contracts" / "backends").mkdir(parents=True, exist_ok=True)
    (root / "contracts" / "backends" / "backend-manifest.yaml").write_text(
        'knaif_version: "9.9.1"\n', encoding="utf-8"
    )
    _commit(root, "1.2.1")
    after = et("demo", root)
    core = (before["python_core"], after["python_core"])
    contracts = (before["contracts"], after["contracts"])
    return root, native, bundle, binary, new, core, contracts


def _sampled121(native, bundle, binary, core, contracts, **kw):
    entry = _sampled("p121", native, bundle, binary, **kw)
    if core:
        entry["fingerprints"]["python_core"] = {"from": core[0], "to": core[1]}
    if contracts:
        entry["fingerprints"]["contracts"] = {"from": contracts[0], "to": contracts[1]}
    return entry


def test_python_core_and_contracts_make_a_native_sample_stale(patch121) -> None:
    root, native, bundle, binary, new, _, _ = patch121
    _write(root, [_sampled("rc3", native, bundle, binary)])
    detail = _layers(root, [new])[0]["L4"].detail
    assert "python_core" in detail and "contracts" in detail


def test_a_sample_with_a_python_stage_carries_python_core_and_contracts(patch121) -> None:
    root, native, bundle, binary, new, core, contracts = patch121
    _with_python_stage(root)
    _write(root, [_sampled121(native, bundle, binary, core, contracts)])
    layers, gate = _layers(root, [new])
    assert layers["L4"].state == "valid" and layers["L3"].state == "valid", layers["L4"].detail
    assert gate.derived == "supported"


def test_python_core_is_not_carried_without_a_python_stage(patch121) -> None:
    root, native, bundle, binary, new, core, contracts = patch121
    _write(root, [_sampled121(native, bundle, binary, core, contracts)])
    assert _layers(root, [new])[0]["L4"].state == "stale"


def test_the_python_core_and_contracts_mappings_must_name_the_exact_values(patch121) -> None:
    root, native, bundle, binary, new, core, contracts = patch121
    _with_python_stage(root)
    _write(root, [_sampled121(native, bundle, binary, (core[0], "f" * 64), contracts)])
    assert _layers(root, [new])[0]["L4"].state == "stale"
    _write(root, [_sampled121(native, bundle, binary, core, (contracts[0], "f" * 64))])
    assert _layers(root, [new])[0]["L4"].state == "stale"


def test_a_text_equivalence_cannot_map_python_core(patch121) -> None:
    root, native, bundle, binary, new, core, contracts = patch121
    _with_python_stage(root)
    _write(
        root,
        [_sampled121(native, bundle, binary, core, contracts, kind=None, sample_run=None)],
    )
    assert _layers(root, [new])[0]["L4"].state == "stale"


def test_a_python_stage_must_cover_every_skill_and_finish(tmp_path) -> None:
    for sub in ("a", "b", "c"):
        (tmp_path / sub).mkdir()
    ok = VERDICTS_OK + (
        "== python ffmpeg\nVERDICT: equivalent on the sample\n"
        "== python documents\nVERDICT: equivalent on the sample\n"
    )
    done = "START win x\nDONE win x\nSTART linux x\nDONE linux x\nSTART python x\nDONE python x\n"
    assert sample_run_problems(_run(tmp_path / "a", ok, done), OSES, SKILLS, python=True) == []
    missing = VERDICTS_OK + "== python ffmpeg\nVERDICT: equivalent on the sample\n"
    problems = sample_run_problems(_run(tmp_path / "b", missing, done), OSES, SKILLS, python=True)
    assert any("python documents" in p for p in problems)
    unfinished = done.replace("DONE python x\n", "")
    assert sample_run_problems(_run(tmp_path / "c", ok, unfinished), OSES, SKILLS, python=True)


def test_only_backend_manifest_changes_may_ride_a_sample() -> None:
    assert contracts_change_allowed(["contracts/backends/backend-manifest.yaml"])
    assert not contracts_change_allowed(
        ["contracts/backends/backend-manifest.yaml", "contracts/runtime/core_tools.yaml"]
    )
    assert not contracts_change_allowed(["contracts/parity/planner_cases.json"])
    assert not contracts_change_allowed([])


@pytest.mark.parametrize(
    ("rel", "python_stage", "allowed"),
    [
        ("native/src/run.rs", False, True),
        ("python/_reporting.py", False, False),
        ("python/_reporting.py", True, True),
        ("python/tests/test_preflights.py", True, True),
        ("prompt.yaml", True, False),
        ("tools.yaml", True, False),
    ],
)
def test_which_bundle_files_a_sample_may_carry(rel, python_stage, allowed) -> None:
    assert sampled_bundle_file_allowed(rel, python_stage) is allowed


# ── Codex audit, 2026-10-02: everything is re-derived at load time ─────────────────────────────


def test_a_contracts_mapping_outside_backends_is_not_honoured(patch121) -> None:
    """A hand-edited entry naming the exact hashes still cannot carry a planning contract."""
    root, native, bundle, binary, new, core, _ = patch121
    from knaif.evalsuite.gate import tree_at_commit

    first = _git(root, "rev-list", "--max-parents=0", "HEAD")
    measured = tree_at_commit(root, first, ("**/*.yaml", "**/*.json"), base="contracts")
    (root / "contracts" / "parity" / "cases.json").write_text(
        '{"changed": true}\n', encoding="utf-8"
    )
    _commit(root, "planning contract")
    contracts = (measured, evidence_tuple("demo", root)["contracts"])
    _with_python_stage(root)
    _write(root, [_sampled121(native, bundle, binary, core, contracts)])
    assert _layers(root, [new])[0]["L4"].state == "stale"


def test_a_python_bundle_change_needs_a_python_stage(rc3) -> None:
    root, native, (old_bundle, _), binary, new = rc3
    (root / "skills" / "demo" / "python").mkdir(exist_ok=True)
    (root / "skills" / "demo" / "python" / "handler.py").write_text("x = 1\n", encoding="utf-8")
    _commit(root, "python handler")
    bundle = (old_bundle, evidence_tuple("demo", root)["bundle"])
    _write(root, [_sampled("rc3", native, bundle, binary)])
    assert _layers(root, [new])[0]["L4"].state == "stale"
    _with_python_stage(root)
    _write(root, [_sampled("rc3", native, bundle, binary)])
    assert _layers(root, [new])[0]["L4"].state == "valid"


def test_a_python_stage_from_another_tree_does_not_vouch(patch121) -> None:
    root, native, bundle, binary, new, core, contracts = patch121
    _with_python_stage(root, python_core="0" * 64)
    _write(root, [_sampled121(native, bundle, binary, core, contracts)])
    assert _layers(root, [new])[0]["L4"].state == "stale"


def test_an_entry_whose_commits_git_cannot_read_is_not_honoured(patch121) -> None:
    root, native, bundle, binary, new, core, contracts = patch121
    _with_python_stage(root)
    entry = _sampled121(native, bundle, binary, core, contracts)
    entry["from_commit"] = "f" * 40
    _write(root, [entry])
    assert _layers(root, [new])[0]["L4"].state == "stale"
