"""L1b: the retrieval-parity contract, Python side.

Same utterance + registry -> the same tools **in the same order**. Order is the
load-bearing half: the model sees the listing in the order retrieval returns it, and the
scoring has tie-breaks, so a port can agree on the set and still show the model a
different prompt.

The Rust side (`retrieval_parity_cases` in `native/crates/knaif-core/tests/parity.rs`) is
`#[ignore]`d and cannot pass today: `knaif_core::retrieve_tools` returns a
`BTreeMap<String, &ToolDef>`, which is sorted by name and therefore **cannot represent a
relevance ranking at all**. V1 has to change that signature, not just wire the function up.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (L1b, V1).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from knaif.registry import load_registry, retrieve_tools

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "parity" / "retrieval_cases.json"

DOC = json.loads(FIXTURES.read_text(encoding="utf-8"))
CASES = DOC["cases"]


def _registry(tmp_path: Path, name: str):
    path = tmp_path / f"{name}.yaml"
    path.write_text(DOC["registries"][name], encoding="utf-8")
    return load_registry(path)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_retrieval_matches_the_contract(case: dict, tmp_path: Path) -> None:
    selected = retrieve_tools(
        case["query"],
        _registry(tmp_path, case["registry"]),
        top_k=case["top_k"],
        min_score=case["min_score"],
    )
    assert list(selected.keys()) == case["expected_order"]


def test_ties_break_on_name_descending() -> None:
    """The case that exists only to pin the tie-break.

    Every tool scores 0 on this query, so the result order is *entirely* the tie-break —
    a port that sorts ties the other way, or not at all, fails here and nowhere else.
    """
    case = next(c for c in CASES if c["name"] == "no_signal_keeps_order")
    ranked = [t for t in case["expected_order"] if t not in ("clarify", "reject", "done")]
    assert ranked == sorted(ranked, reverse=True)


def test_control_tools_are_always_included_and_last() -> None:
    for case in CASES:
        order = case["expected_order"]
        assert order[-3:] == ["clarify", "reject", "done"], case["name"]


def test_min_score_can_empty_the_ranked_selection() -> None:
    """Filtering must not take the control tools with it — the model always needs them."""
    case = next(c for c in CASES if c["name"] == "min_score_filters")
    assert case["expected_order"] == ["clarify", "reject", "done"]


def test_the_contract_covers_multilingual_retrieval() -> None:
    """Diacritics and CJK are where retrieval ports quietly diverge."""
    names = {c["name"] for c in CASES}
    assert {"diacritics_insensitive", "cjk_ngram_containment"} <= names


def test_top_k_truncates_the_ranked_selection() -> None:
    case = next(c for c in CASES if c["name"] == "top_k_truncates")
    ranked = [t for t in case["expected_order"] if t not in ("clarify", "reject", "done")]
    assert len(ranked) == case["top_k"]


def test_the_retrieved_order_is_deterministic_across_processes(tmp_path: Path) -> None:
    """Hash randomization must not reach the prompt.

    The control tools were appended by iterating a `frozenset`, whose iteration order
    depends on PYTHONHASHSEED — so the same query returned a different tool order in
    different processes. Nothing in Python noticed, because the prompt listing skips
    control tools; a port cannot be held to an order that is not stable in the reference.
    """
    reg = tmp_path / "demo.yaml"
    reg.write_text(DOC["registries"]["demo"], encoding="utf-8")
    snippet = (
        "import sys;"
        "sys.path.insert(0, r'" + str(REPO_ROOT / "python" / "core") + "');"
        "from knaif.registry import load_registry, retrieve_tools;"
        "import pathlib;"
        "r = load_registry(pathlib.Path(r'" + str(reg) + "'));"
        "print(list(retrieve_tools('hello there', r, top_k=5).keys()))"
    )
    seen = set()
    for seed in ("0", "1", "42"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", snippet], capture_output=True, text=True, env=env, check=True
        )
        seen.add(out.stdout.strip())
    assert len(seen) == 1, f"retrieval order varies with PYTHONHASHSEED: {seen}"


def test_always_included_tools_are_declared_in_a_fixed_order() -> None:
    """A set has no order to port. The contract needs a sequence."""
    from knaif.registry import _ALWAYS_INCLUDE

    assert isinstance(_ALWAYS_INCLUDE, (tuple, list))
    assert list(_ALWAYS_INCLUDE) == ["clarify", "reject", "done"]
