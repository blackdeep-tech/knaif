"""Declared effects, Python side of contracts/parity/effects_cases.json.

A tool declares what it may do (`effects: [fs.read, fs.write, net, exec:<program>]`); a skill's or
toolkit's tool that declares nothing is refused when the bundle loads. The native runtime reads
the same YAML and must agree (native/crates/knaif-core/tests/effects_parity.rs).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.effects import check_declared
from knaif.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = json.loads((REPO_ROOT / "contracts" / "parity" / "effects_cases.json").read_text("utf-8"))


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_effects_match_the_contract(case: dict, tmp_path: Path) -> None:
    path = tmp_path / "tools.yaml"
    path.write_text(case["tools_yaml"], encoding="utf-8")
    if "error_contains" in case:
        with pytest.raises(ValueError, match=case["error_contains"]):
            check_declared(load_registry(path), source=str(path))
        return
    registry = load_registry(path)
    check_declared(registry, source=str(path))
    got = {name: sorted(td.effects or ()) for name, td in registry.items()}
    assert got == case["expected"]


def test_core_control_tools_need_no_declaration() -> None:
    """The core control tools are core's, loaded beside a skill's, and exempt."""
    core = load_registry(REPO_ROOT / "contracts" / "runtime" / "core_tools.yaml")
    assert all(td.effects is None for td in core.values())
