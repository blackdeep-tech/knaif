"""Golden parity: Python tool retrieval over contracts/parity/retrieval_cases.json.

The Rust side (native/crates/knaif-core/tests/retrieval_parity.rs) runs the identical fixtures.
Both must select the same tools **in the same order**, because `build_prompt` lists retrieved
tools in the order retrieval returned them — so a set-equal but differently ordered result is a
different prompt, and the fine-tuned model sees a different distribution.

This is R2 of docs/plans/2026-08-08-native-python-planning-parity.md. Python is the reference
implementation here: these expectations were generated from `retrieve_tools`, so this test's job
is to catch a *change* to the reference, while the Rust test's job is to catch divergence from it.
"""

from __future__ import annotations

import json
from pathlib import Path

from knaif.registry import _ALWAYS_INCLUDE, load_registry, retrieve_tools

FIXTURES = Path("contracts/parity/retrieval_cases.json")


def _load_cases(tmp_path):
    doc = json.loads(FIXTURES.read_text(encoding="utf-8"))
    registries = {}
    for name, text in doc["registries"].items():
        path = tmp_path / f"{name}.yaml"
        path.write_text(text, encoding="utf-8")
        registries[name] = load_registry(path)
    return doc["cases"], registries


def test_retrieval_parity_ranked_order(tmp_path) -> None:
    cases, registries = _load_cases(tmp_path)
    assert cases, "fixture file has no cases"

    for case in cases:
        selected = retrieve_tools(
            case["query"],
            registries[case["registry"]],
            top_k=case["top_k"],
            min_score=case["min_score"],
        )
        ranked = [n for n in selected if n not in _ALWAYS_INCLUDE]
        assert ranked == case["expected_ranked"], (
            f"{case['name']}: ranked order changed\n"
            f"  expected {case['expected_ranked']}\n"
            f"  got      {ranked}"
        )


def test_retrieval_parity_always_include_is_a_set(tmp_path) -> None:
    """The always-include tools are asserted as a SET, deliberately.

    Python appends them by iterating a frozenset, whose order is not a language guarantee, and
    `build_prompt` filters them out before the model sees anything. Pinning their order would
    encode an implementation accident as a contract.
    """
    cases, registries = _load_cases(tmp_path)
    for case in cases:
        selected = retrieve_tools(
            case["query"],
            registries[case["registry"]],
            top_k=case["top_k"],
            min_score=case["min_score"],
        )
        always = sorted(n for n in selected if n in _ALWAYS_INCLUDE)
        assert always == case["expected_always_include"], case["name"]


def test_internal_tools_are_never_retrieved(tmp_path) -> None:
    """`internal_helper` carries matching keywords precisely so this cannot pass by accident."""
    cases, registries = _load_cases(tmp_path)
    registry = registries["retrieval_demo"]
    assert "internal_helper" in registry, "fixture no longer has an internal tool to exclude"
    assert registry["internal_helper"].internal

    for case in cases:
        selected = retrieve_tools(
            case["query"],
            registries[case["registry"]],
            top_k=case["top_k"],
            min_score=case["min_score"],
        )
        assert "internal_helper" not in selected, case["name"]
