"""L2: the clarify-gate parity contract, Python side.

The gate is deterministic — no model, so this layer is either 100% or broken. It also
matters more than its size suggests: it fires on roughly 17% of the ffmpeg corpus in the
native runtime, and nothing previously proved Python's equivalent fires on the same rows.
A gate that fires on different inputs in the two runtimes is a silent behavioral fork that
no aggregate score would reveal.

The stage under test is `link_chain_intermediates -> hallucinated-filename guard`, in that
order. Comparing either half alone would be the Rule 2 mistake.

See `docs/plans/2026-09-10-skill-quality-lifecycle.md` (L2a).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "parity" / "clarify_gate_cases.json"

DOC = json.loads(FIXTURES.read_text(encoding="utf-8"))
CASES = DOC["cases"]


def _output_capable(tmp_path: Path, name: str) -> set[str]:
    path = tmp_path / f"{name}.yaml"
    path.write_text(DOC["registries"][name], encoding="utf-8")
    registry = load_registry(path)
    return {
        tool
        for tool, td in registry.items()
        if "output" in set(td.required_args) | set(td.optional_args)
    }


def _apply_gate(payload: dict, utterance: str, output_capable: set[str]) -> dict:
    """The composed stage, exactly as `CommandAgent.infer` composes it."""
    payload = copy.deepcopy(payload)
    steps = payload.get("plan") or []
    CommandAgent._link_chain_intermediates(steps, utterance, output_capable)
    hallucinated = CommandAgent._hallucinated_filename(steps, utterance)
    if hallucinated:
        return {
            "plan": [
                {
                    "tool": "clarify",
                    "args": {
                        "question": (
                            f"You didn't mention {hallucinated!r} in your request — "
                            "which file should I work on?"
                        )
                    },
                }
            ]
        }
    return payload


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_clarify_gate_matches_the_contract(case: dict, tmp_path: Path) -> None:
    got = _apply_gate(case["plan"], case["utterance"], _output_capable(tmp_path, case["registry"]))
    assert got == case["expected_payload"]


def test_an_intermediate_is_bound_to_its_producer(tmp_path: Path) -> None:
    """The case the whole mechanism exists for: a chain the model under-declared."""
    case = next(c for c in CASES if c["name"] == "chain_intermediate_is_linked_to_its_producer")
    steps = case["expected_payload"]["plan"]
    assert steps[0]["args"]["output"] == steps[1]["args"]["inputs"][0]


def test_an_invented_output_name_is_never_flagged(tmp_path: Path) -> None:
    """Output filenames are the model's to invent; inputs are not."""
    case = next(c for c in CASES if c["name"] == "invented_output_name_is_allowed")
    assert case["expected_payload"]["plan"][0]["tool"] == "resize_video"


def test_an_invented_input_name_always_is() -> None:
    case = next(c for c in CASES if c["name"] == "hallucinated_input_downgrades_to_clarify")
    assert case["expected_payload"]["plan"][0]["tool"] == "clarify"


def test_the_contract_probes_the_output_capable_definition() -> None:
    """Python derives output-capability from required/optional args only; native also
    accepts a tool that declares `output` in `arg_schemas`. A tool defined that way is
    therefore a producer on one side and not the other — so the contract carries a case
    for it rather than leaving the difference to be discovered in a corpus run."""
    assert any(c["name"] == "output_capable_only_via_arg_schemas" for c in CASES)
