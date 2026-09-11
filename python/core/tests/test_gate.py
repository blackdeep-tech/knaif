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
    for name in ("planner.py", "prompt.py", "registry.py"):
        (tmp_path / "python" / "core" / "knaif" / name).write_text("x = 1\n", encoding="utf-8")
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
