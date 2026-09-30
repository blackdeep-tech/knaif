"""L2: the NL-clarify-gate parity contract, Python side.

`nl_clarify_gate` runs after stem resolution and asks when the user never named an input the
plan uses. Native had no port of it until 2026-09-28: the R5c L3 run showed Python asking
"Which mov did you mean?" where native ran and failed with "input not found: mov". Both
runtimes are held to `contracts/parity/nl_clarify_gate_cases.json`; the native half is
`nl_clarify_gate_parity_cases` in native/crates/knaif-core/tests/parity.rs.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from knaif import nl_clarify_gate as gate_mod
from knaif.nl_clarify_gate import nl_clarify_gate
from knaif.planner import _PATH_ARG_KEYS
from knaif.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = json.loads(
    (REPO_ROOT / "contracts" / "parity" / "nl_clarify_gate_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_nl_clarify_gate_contract(case: dict, tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.yaml"
    reg_path.write_text(DOC["registries"][case["registry"]], encoding="utf-8")
    registry = load_registry(reg_path)
    plan = copy.deepcopy(case["plan"]["plan"])

    out = nl_clarify_gate(case["utterance"], plan, injected_files=None, registry=registry)

    assert {"plan": out} == case["expected_payload"]


def test_input_keys_are_walked_in_one_fixed_order() -> None:
    """The keys are a frozenset in the planner, whose iteration order changes per process:
    which unnamed input the question names must not."""
    assert set(gate_mod._INPUT_ARG_ORDER) == set(_PATH_ARG_KEYS)
    assert gate_mod._INPUT_ARG_ORDER == (
        "inputs",
        "input",
        "files",
        "src",
        "dst",
        "path",
        "base",
        "append",
    )
