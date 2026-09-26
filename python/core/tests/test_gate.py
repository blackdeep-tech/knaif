"""G1/G2: the gate — a declared native status must be backed by current evidence.

The properties worth pinning are the ones where the obvious implementation is subtly wrong:
status is *derived* rather than decremented, a failed run is not evidence, and a change to a
shared file invalidates every layer that depends on it — not just the per-skill ones.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (G1, G2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from knaif.evalsuite.gate import (
    STATUS_ORDER,
    check_platform_coverage,
    evaluate_skill,
    evidence_tuple,
    load_status_contract,
    record_layers,
    recorded_platform_coverage,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A miniature repo with the two contracts and one skill."""
    return make_tree(tmp_path)


def make_tree(tmp_path: Path) -> Path:
    """Build the miniature repo. A plain function so other gate tests can share it."""
    (tmp_path / "contracts" / "release").mkdir(parents=True)
    (tmp_path / "contracts" / "runtime").mkdir(parents=True)
    (tmp_path / "contracts" / "parity").mkdir(parents=True)
    (tmp_path / "python" / "core" / "knaif").mkdir(parents=True)
    (tmp_path / "skills" / "demo" / "data").mkdir(parents=True)
    (tmp_path / "skills" / "demo" / "eval").mkdir(parents=True)

    # Reuse the real status contract so the test cannot drift from it.
    src = (REPO_ROOT / "contracts" / "release" / "native_status.yaml").read_text(encoding="utf-8")
    (tmp_path / "contracts" / "release" / "native_status.yaml").write_text(src, encoding="utf-8")
    (tmp_path / "contracts" / "release" / "platforms.yaml").write_text(
        yaml.safe_dump({"platforms": [{"id": "linux-x64", "status": "supported"}]}),
        encoding="utf-8",
    )
    (tmp_path / "contracts" / "parity" / "cases.json").write_text(
        json.dumps({"_coverage": {"ubuntu": "CI", "macos": "unexercised"}}), encoding="utf-8"
    )
    (tmp_path / "contracts" / "runtime" / "generation.yaml").write_text(
        "max_tokens: 512\n", "utf-8"
    )
    for name in ("planner.py", "prompt.py", "registry.py", "agent.py"):
        (tmp_path / "python" / "core" / "knaif" / name).write_text("x = 1\n", encoding="utf-8")
    # The miniature repo must carry the same *kinds* of file the real one does, or a dependency
    # the contract names records as None and every layer reads "does not pin".
    (tmp_path / "python" / "core" / "knaif" / "evalsuite").mkdir(parents=True)
    for name in ("scoring.py", "outcomes.py", "acceptance.py"):
        (tmp_path / "python" / "core" / "knaif" / "evalsuite" / name).write_text(
            "x = 1\n", encoding="utf-8"
        )
    (tmp_path / "native" / "crates" / "knaif-core" / "src").mkdir(parents=True)
    (tmp_path / "native" / "crates" / "knaif-core" / "src" / "planner.rs").write_text(
        "fn main() {}\n", encoding="utf-8"
    )
    (tmp_path / "apps" / "cli" / "src").mkdir(parents=True)
    (tmp_path / "apps" / "cli" / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (tmp_path / "skills" / "demo" / "skill.yaml").write_text("name: demo\n", encoding="utf-8")
    (tmp_path / "skills" / "demo" / "data" / "eval.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "skills" / "demo" / "eval" / "verifiers.py").write_text("V = 1\n", encoding="utf-8")
    return tmp_path


def _record_all(tree: Path, *, l3_passed: bool = True, l4: bool = True) -> None:
    layers = {
        "L1": {"summary": "green"},
        "L2": {"summary": "green"},
        "L3": {"summary": "run", "passed": l3_passed},
    }
    if l4:
        layers["L4"] = {"summary": "run", "passed": True}
    record_layers("demo", tree, layers)


# ── deriving status ───────────────────────────────────────────────────────────


def test_full_evidence_supports_supported(tree: Path) -> None:
    _record_all(tree)
    assert evaluate_skill("demo", tree, "supported").derived == "supported"


def test_missing_l4_caps_the_claim_at_parity(tree: Path) -> None:
    _record_all(tree, l4=False)
    gate = evaluate_skill("demo", tree, "supported")
    assert gate.derived == "parity"
    assert gate.problems, "claiming supported without L4 must fail"


def test_a_failed_run_is_not_evidence(tree: Path) -> None:
    """ "We ran it" is not "it passed". Recording a failing run as valid would let a status rest
    on a measurement that said no — the substitution this gate exists to prevent."""
    _record_all(tree, l3_passed=False, l4=False)
    gate = evaluate_skill("demo", tree, "parity")
    assert gate.derived == "in-progress"
    assert [s.state for s in gate.layers if s.layer == "L3"] == ["failing"]


def test_a_pending_layer_and_a_failing_one_are_distinguished(tree: Path) -> None:
    """Different facts: one is fixed by running something, the other by fixing something."""
    record_layers("demo", tree, {"L1": {"summary": "g"}, "L3": {"summary": "r", "passed": False}})
    states = {s.layer: s.state for s in evaluate_skill("demo", tree, "in-progress").layers}
    assert states["L2"] == "pending" and states["L3"] == "failing"


def test_a_lower_claim_than_the_evidence_is_fine(tree: Path) -> None:
    """The gate stops over-claiming, not under-claiming."""
    _record_all(tree)
    assert evaluate_skill("demo", tree, "in-progress").problems == []


def test_an_unknown_status_is_rejected(tree: Path) -> None:
    _record_all(tree)
    assert evaluate_skill("demo", tree, "shipped").problems


# ── staleness ─────────────────────────────────────────────────────────────────


def test_editing_the_bundle_invalidates_every_layer(tree: Path) -> None:
    _record_all(tree)
    (tree / "skills" / "demo" / "skill.yaml").write_text("name: demo\nx: 1\n", encoding="utf-8")
    gate = evaluate_skill("demo", tree, "supported")
    assert {s.state for s in gate.layers} == {"stale"}
    assert gate.derived == "in-progress"


def test_editing_the_shared_python_core_invalidates_every_layer(tree: Path) -> None:
    """The case the tuple exists for: `planner.py` changes plans for every skill while no skill
    bundle is touched, so nothing about editing it reminds you the evidence just expired."""
    _record_all(tree)
    (tree / "python" / "core" / "knaif" / "planner.py").write_text("x = 2\n", encoding="utf-8")
    gate = evaluate_skill("demo", tree, "supported")
    assert {s.state for s in gate.layers} == {"stale"}


def test_status_is_derived_not_decremented(tree: Path) -> None:
    """A stale `supported` becomes `in-progress`, not `parity`. Decrementing would assert a
    second claim whose evidence had also just expired."""
    _record_all(tree)
    (tree / "contracts" / "runtime" / "generation.yaml").write_text("max_tokens: 256\n", "utf-8")
    assert evaluate_skill("demo", tree, "supported").derived == "in-progress"


def test_the_verifier_implementation_is_hashed_not_its_name(tree: Path) -> None:
    """ "success" is a moving target: the same name can grade differently after an edit."""
    _record_all(tree)
    (tree / "skills" / "demo" / "eval" / "verifiers.py").write_text("V = 2\n", encoding="utf-8")
    states = {s.state for s in evaluate_skill("demo", tree, "supported").layers}
    assert "stale" in states


def test_the_evidence_tuple_covers_the_shared_members(tree: Path) -> None:
    keys = set(evidence_tuple("demo", tree))
    assert {"bundle", "contracts", "python_core", "corpus", "verifier", "settings"} <= keys


# ── platform coverage ─────────────────────────────────────────────────────────


def test_a_platform_without_recorded_coverage_cannot_be_supported(tree: Path) -> None:
    (tree / "contracts" / "parity" / "cases.json").write_text(
        json.dumps({"_coverage": {"macos": "unexercised"}}), encoding="utf-8"
    )
    assert check_platform_coverage(tree)


def test_an_alias_stops_the_guard_crying_wolf(tree: Path) -> None:
    """`linux-x64` is exercised by a runner the contracts call `ubuntu`. A guard that reports
    that as a gap gets deleted, which is worse than not having one."""
    assert check_platform_coverage(tree) == []


def test_unexercised_does_not_count_as_coverage(tree: Path) -> None:
    assert "macos" not in recorded_platform_coverage(tree)


# ── the real repo ─────────────────────────────────────────────────────────────


def test_the_repo_status_contract_defines_every_status_the_code_orders() -> None:
    contract = load_status_contract(REPO_ROOT)
    assert set(contract["statuses"]) == set(STATUS_ORDER)
    assert contract["statuses"]["supported"]["release_eligible"] is True
    assert contract["statuses"]["parity"]["release_eligible"] is False


def test_the_real_platform_matrix_passes_its_own_guard() -> None:
    assert check_platform_coverage(REPO_ROOT) == []


def test_every_skill_declares_a_status_the_contract_knows() -> None:
    contract = load_status_contract(REPO_ROOT)
    for path in sorted((REPO_ROOT / "skills").glob("*/skill.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        native = (manifest.get("runtimes") or {}).get("native") or {}
        if "status" in native:
            assert native["status"] in contract["statuses"], f"{path}: {native['status']}"


# ── what the fingerprint must cover, and when it must be taken ───────────────


def _write(tree: Path, rel: str, text: str) -> Path:
    path = tree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "rel",
    [
        # The execution pipeline itself: tool dispatch, expansion, the clarify gate. Changing
        # it changes what every run produces, for every skill, touching no bundle.
        "python/core/knaif/agent.py",
        # What turns a run into a number. A scoring change makes two scoreboards
        # incomparable even when nothing about the runtime moved.
        "python/core/knaif/evalsuite/scoring.py",
        "python/core/knaif/evalsuite/outcomes.py",
        # L3 and L4 are claims about the SHIPPED BINARY. Its sources were not fingerprinted at
        # all, so the native runtime could be rewritten under a "valid" native-parity record.
        "native/crates/knaif-core/src/planner.rs",
        "apps/cli/src/main.rs",
    ],
)
def test_editing_shared_execution_or_grading_code_invalidates_the_evidence(
    tree: Path, rel: str
) -> None:
    """Every one of these could change what a run produces, and none was fingerprinted.

    Found by review: changing all four of `agent.py`, the shared scoring code, the Rust core
    and the native CLI left the tuple identical and L4 still reading valid. A gate that cannot
    notice the runtime was rewritten is not a gate.
    """
    _write(tree, rel, "x = 1\n")
    _record_all(tree)
    assert evaluate_skill("demo", tree, "supported").derived == "supported"

    _write(tree, rel, "x = 2\n")
    states = {s.state for s in evaluate_skill("demo", tree, "supported").layers}
    assert "stale" in states, f"editing {rel} left the evidence looking current"


def test_recording_an_old_run_does_not_make_it_current(tree: Path) -> None:
    """The fingerprint belongs to the measurement, not to the moment it was written down.

    `record_layers` stamped `evidence_tuple(now)` onto whatever it was handed — including a
    saved run directory from before a planner change. So an expired record could be refreshed
    into validity by re-recording the same old result, which is the one thing the record is
    supposed to make impossible. A layer that carries its own captured fingerprint must keep
    it.
    """
    _record_all(tree)
    captured = evidence_tuple("demo", tree)

    # The tree moves on: this is what made the old measurement stale.
    _write(tree, "python/core/knaif/planner.py", "x = 99\n")
    assert {s.state for s in evaluate_skill("demo", tree, "supported").layers} == {"stale"}

    # Re-recording the SAME old measurement, carrying the fingerprint it was taken under.
    record_layers(
        "demo",
        tree,
        {"L3": {"summary": "the same old run", "passed": True, "evidence": captured}},
    )
    l3 = next(s for s in evaluate_skill("demo", tree, "supported").layers if s.layer == "L3")
    assert l3.state == "stale", "an old run was refreshed into validity by re-recording it"


#: Members of `invalidated_by` that cannot be computed from the working tree, and must instead
#: arrive in the fingerprint a measurement captures for itself. The GGUF and the built binary
#: are not in the tree at all, and which model a run used is a fact of that run.
_RUN_SCOPED_EVIDENCE = {"model", "native_binary", "policy"}


def test_every_tree_scoped_dependency_is_computed(tree: Path) -> None:
    """A key named in `invalidated_by` but absent from the tuple is silently ignored.

    `_layer_state` filters on `key in current`, so the contract can declare a dependency that
    is never checked and nothing anywhere says so — the layer simply reads valid forever. This
    is how `native` could be listed as the thing L3 and L4 are claims about while the Rust
    sources were not fingerprinted at all.
    """
    contract = load_status_contract(tree)
    computed = set(evidence_tuple("demo", tree))
    for layer, spec in contract["layers"].items():
        for key in spec.get("invalidated_by") or []:
            assert key in computed or key in _RUN_SCOPED_EVIDENCE, (
                f"{layer}.invalidated_by names {key!r}, which evidence_tuple never computes "
                f"and which is not declared run-scoped — it would be skipped in silence"
            )


# ── run-scoped evidence: the model, the binary and the grading policy ─────────────────────────
# `native_status.yaml` names all three under L3/L4 `invalidated_by`, and `_layer_state` skipped
# them in silence because `evidence_tuple` never computed them: swapping the GGUF, rebuilding the
# binary or moving POLICY_VERSION left an L4 record reading "valid" (release plan R2).

_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _with_manifest(tree: Path, sha: str) -> None:
    (tree / "contracts" / "models").mkdir(parents=True, exist_ok=True)
    (tree / "contracts" / "models" / "model-manifest.yaml").write_text(
        yaml.safe_dump({"models": {"knaif-demo-v1": {"file": "demo.gguf", "sha256": sha}}}),
        encoding="utf-8",
    )
    (tree / "skills" / "demo" / "skill.yaml").write_text(
        "name: demo\nrecommended_model: knaif-demo-v1\n", encoding="utf-8"
    )


def _layer(tree: Path, name: str, **kw):
    return next(
        s for s in evaluate_skill("demo", tree, "supported", **kw).layers if s.layer == name
    )


def test_the_policy_is_part_of_the_evidence(tree: Path) -> None:
    from knaif.evalsuite.outcomes import POLICY_VERSION

    assert evidence_tuple("demo", tree)["policy"] == str(POLICY_VERSION)


def test_the_recommended_model_is_part_of_the_evidence(tree: Path) -> None:
    _with_manifest(tree, _SHA_A)
    assert evidence_tuple("demo", tree)["model"] == _SHA_A


def test_a_model_with_no_published_hash_pins_nothing(tree: Path) -> None:
    """`sha256: TODO` (not uploaded yet) is not a hash; comparing against it would be noise."""
    _with_manifest(tree, "TODO")
    assert "model" not in evidence_tuple("demo", tree)


def test_an_l4_run_on_a_different_gguf_is_stale(tree: Path) -> None:
    """The plan's RED test: the same record with a different GGUF sha256 turns stale."""
    _with_manifest(tree, _SHA_A)
    _record_all(tree)
    run = {**evidence_tuple("demo", tree), "model": _SHA_A}
    record_layers("demo", tree, {"L4": {"summary": "run", "passed": True, "evidence": run}})
    assert _layer(tree, "L4").state == "valid"

    record_layers(
        "demo",
        tree,
        {"L4": {"summary": "run", "passed": True, "evidence": {**run, "model": _SHA_B}}},
    )
    l4 = _layer(tree, "L4")
    assert l4.state == "stale" and "model" in l4.detail


def test_an_l4_run_under_another_policy_is_stale(tree: Path) -> None:
    _record_all(tree)
    run = {**evidence_tuple("demo", tree), "policy": "0"}
    record_layers("demo", tree, {"L4": {"summary": "run", "passed": True, "evidence": run}})
    l4 = _layer(tree, "L4")
    assert l4.state == "stale" and "policy" in l4.detail


def test_a_run_on_another_binary_is_stale_when_the_binary_is_given(tree: Path) -> None:
    binary = tree / "knaif.exe"
    binary.write_bytes(b"the shipped binary")
    _record_all(tree)
    run = {**evidence_tuple("demo", tree), "native_binary": _SHA_B}
    record_layers("demo", tree, {"L4": {"summary": "run", "passed": True, "evidence": run}})

    l4 = _layer(tree, "L4", native_binary=binary)
    assert l4.state == "stale" and "native_binary" in l4.detail


def test_an_unchecked_binary_is_said_out_loud(tree: Path) -> None:
    """With no binary to compare against, the layer can stand, but not in silence."""
    _record_all(tree)
    run = {**evidence_tuple("demo", tree), "native_binary": _SHA_B}
    record_layers("demo", tree, {"L4": {"summary": "run", "passed": True, "evidence": run}})
    l4 = _layer(tree, "L4")
    assert l4.state == "valid"
    assert "native_binary" in l4.detail and "not checked" in l4.detail


def test_an_l3_record_pins_the_parity_run_s_model_and_binary(tree: Path) -> None:
    from knaif.evalsuite.gate import load_acceptance_record, record_from_parity_run

    run = tree / "evals" / "parity" / "run1"
    run.mkdir(parents=True)
    (run / "meta.json").write_text(
        json.dumps(
            {
                "model": {"sha256": _SHA_A},
                "binary": {"sha256": _SHA_B},
                "result": {"equivalence_rate": 0.9, "threshold": 0.8, "passed": True},
            }
        ),
        encoding="utf-8",
    )
    record_from_parity_run("demo", tree, run)
    cells = load_acceptance_record("demo", tree)["layers"]["L3"]["cells"]
    evidence = cells["unknown-model"]["evidence"]  # this meta names no GGUF path
    assert evidence["model"] == _SHA_A and evidence["native_binary"] == _SHA_B


def test_an_l3_record_is_keyed_by_the_model_it_measured(tree: Path) -> None:
    from knaif.evalsuite.gate import load_acceptance_record, record_from_parity_run

    _with_manifest(tree, _SHA_A)  # knaif-demo-v1 -> demo.gguf
    run = tree / "evals" / "parity" / "run2"
    run.mkdir(parents=True)
    (run / "meta.json").write_text(
        json.dumps(
            {
                "model": {"path": "models/demo.gguf", "sha256": _SHA_A},
                "binary": {"sha256": _SHA_B},
                "result": {"equivalence_rate": 0.9, "threshold": 0.8, "passed": True},
            }
        ),
        encoding="utf-8",
    )
    record_from_parity_run("demo", tree, run)
    l3 = load_acceptance_record("demo", tree)["layers"]["L3"]
    assert "knaif-demo-v1" in l3["cells"]


def test_a_parity_run_under_the_retired_rate_bar_is_not_passing_evidence(tree: Path) -> None:
    """L3's bar is zero port bugs + a pre-written disagreement bound (release plan R0).

    A run judged by the old equivalence-rate threshold says nothing about port bugs, so its
    `passed: true` answers a question the bar no longer asks.
    """
    from knaif.evalsuite.gate import load_acceptance_record, record_from_parity_run

    run = tree / "evals" / "parity" / "old"
    run.mkdir(parents=True)
    (run / "meta.json").write_text(
        json.dumps({"result": {"equivalence_rate": 1.0, "threshold": 1.0, "passed": True}}),
        encoding="utf-8",
    )
    record_from_parity_run("demo", tree, run)
    cell = load_acceptance_record("demo", tree)["layers"]["L3"]["cells"]["unknown-model"]
    assert cell["passed"] is False
    assert "retired" in cell["summary"]


def test_a_parity_run_under_the_new_bar_records_its_verdict(tree: Path) -> None:
    from knaif.evalsuite.gate import load_acceptance_record, record_from_parity_run

    run = tree / "evals" / "parity" / "new"
    run.mkdir(parents=True)
    verdict = {
        "port_bugs": 0,
        "native_not_implemented": 0,
        "plan_disagreement": 3,
        "plan_disagreement_rate": 0.02,
        "max_plan_disagreement": 0.05,
        "passed": True,
    }
    (run / "meta.json").write_text(
        json.dumps({"result": {"equivalence_rate": 0.97, **verdict}}), encoding="utf-8"
    )
    record_from_parity_run("demo", tree, run)
    cell = load_acceptance_record("demo", tree)["layers"]["L3"]["cells"]["unknown-model"]
    assert cell["passed"] is True
    assert cell["port_bugs"] == 0 and cell["max_plan_disagreement"] == 0.05
