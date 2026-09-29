"""L2: the arg-shape-gate parity contract, Python side.

Two deterministic gates turn a would-be validation error into a clarify, so this layer is
either 100% or broken. Both were Python-only until 2026-09-15, and the L4 lane priced the
gap: ffmpeg's `extract_audio` slice measured 0.872 on the native binary against 0.949 in
Python, one row of which was exactly `Tool 'adjust_volume' has unsupported args:
[target_sample_rate]` erroring where Python clarified.

The two gates are checked separately on purpose — they are not composed, and they sit on
opposite sides of validation with different retry semantics. See the contract's `_stage`.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (L2a).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.nl_clarify_gate import required_args_clarify, unsupported_args_clarify
from knaif.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "parity" / "arg_gate_cases.json"

DOC = json.loads(FIXTURES.read_text(encoding="utf-8"))
CASES = DOC["cases"]

GATES = {"required": required_args_clarify, "unsupported": unsupported_args_clarify}


def _registry(tmp_path: Path, name: str):
    path = tmp_path / f"{name}.yaml"
    path.write_text(DOC["registries"][name], encoding="utf-8")
    return load_registry(path)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_arg_gate_contract(case: dict, tmp_path: Path) -> None:
    registry = _registry(tmp_path, case["registry"])
    gate = GATES[case["gate"]]
    steps = gate(case["plan"]["plan"], registry)
    actual = {"plan": steps} if steps is not None else None
    assert actual == case["expected"], (
        f"{case['name']}: Python produced {json.dumps(actual, ensure_ascii=False)}, "
        f"contract expects {json.dumps(case['expected'], ensure_ascii=False)}"
    )


def test_every_case_names_a_declared_gate_and_registry() -> None:
    """A typo in `gate` or `registry` would silently skip the case it was written for."""
    for case in CASES:
        assert case["gate"] in GATES, f"{case['name']}: unknown gate {case['gate']!r}"
        assert case["registry"] in DOC["registries"], f"{case['name']}: unknown registry"


def test_the_contract_covers_both_gates_both_ways() -> None:
    """A contract that only ever fires, or only ever passes, proves half the behaviour."""
    for gate in GATES:
        cases = [c for c in CASES if c["gate"] == gate]
        assert any(c["expected"] is not None for c in cases), f"{gate}: no firing case"
        assert any(c["expected"] is None for c in cases), f"{gate}: no pass-through case"
