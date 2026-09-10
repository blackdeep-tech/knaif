"""L1: the example-selection parity contract, Python side.

Python sends the model a *filtered* examples block — one clarify example, one reject
example, and up to three domain examples ranked against the retrieved tool set. Native
shipped `prompt.yaml`'s whole block. For ffmpeg that is 5 examples against 28, in the same
prompt whose tool listing V1 just converged: the second half of the same divergence.

This side pins the reference to the contract, so that regenerating the contract to make the
*native* side pass would fail here first.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (V2, L1e).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from knaif.prompt import render_examples_block, select_examples
from knaif.registry import DEFAULT_TOP_K, load_registry, retrieve_tools

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "parity" / "example_cases.json"

DOC = json.loads(FIXTURES.read_text(encoding="utf-8"))
CASES = DOC["cases"]
SHIPPED = DOC["shipped"]


def _selected(case: dict) -> list[dict]:
    return select_examples(
        DOC["example_sets"][case["example_set"]],
        frozenset(case["retrieved"]),
        case["query"],
        DOC["max_tool_examples"],
    )


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_selection_matches_the_contract(case: dict) -> None:
    selected = _selected(case)
    assert [e["request"] for e in selected] == case["expected_requests"], case["why"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_rendered_block_matches_the_contract(case: dict) -> None:
    """Rule 2: the stage is selection *and* rendering — the block is what reaches the model."""
    selected = _selected(case)
    expected = case["expected_block"]
    assert (render_examples_block(selected) if selected else None) == expected


@pytest.mark.parametrize(
    "row", SHIPPED, ids=[f"{r['skill']}:{r['utterance'][:24]}" for r in SHIPPED]
)
def test_the_shipped_bundles_select_what_the_contract_says(row: dict) -> None:
    """The golden over the real bundles: real examples, real retrieval, real utterance."""
    bundle = REPO_ROOT / "skills" / row["skill"]
    raw = yaml.safe_load((bundle / "prompt.yaml").read_text(encoding="utf-8"))
    examples = [e for e in (raw.get("examples") or []) if isinstance(e, dict)]
    assert len(examples) == row["corpus_size"]

    registry = load_registry(bundle / "tools.yaml")
    registry.update(load_registry(REPO_ROOT / "contracts" / "runtime" / "core_tools.yaml"))
    retrieved = retrieve_tools(row["utterance"], registry, top_k=DEFAULT_TOP_K)
    assert sorted(n for n, td in retrieved.items() if not td.internal) == row["retrieved"]

    selected = select_examples(examples, frozenset(row["retrieved"]), row["utterance"])
    assert [e["request"] for e in selected] == row["expected_requests"]
    assert render_examples_block(selected) == row["expected_block"]


def test_filtering_is_not_a_no_op_on_the_real_corpus() -> None:
    """A contract that pins 28 examples to 28 would pass on both runtimes and prove nothing."""
    for row in SHIPPED:
        assert len(row["expected_requests"]) < row["corpus_size"], row["utterance"]


def test_the_contract_pins_sort_stability() -> None:
    """Python's `sorted(reverse=True)` keeps equal-scoring examples in corpus order. A Rust
    port that sorts ascending and reverses would keep the *last* three instead — so the tie
    case must exist and must not be satisfiable by either order."""
    case = next(c for c in CASES if c["name"] == "equal_scores_keep_corpus_order")
    requests = [e["request"] for e in DOC["example_sets"][case["example_set"]]]
    assert case["expected_requests"] == requests[:3]
    assert case["expected_requests"] != list(reversed(requests))[:3]
