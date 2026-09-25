"""Golden parity: Python chain linking over contracts/parity/chain_linking_cases.json.

The Rust side (native/crates/knaif-core/tests/chain_linking_parity.rs) runs the identical
fixtures; both must produce the identical linked plan.

**Why this exists.** Chain linking rewrites the model's plan *before* validation, so it decides
which file each step actually reads. Native shipped only its first pass until 2026-08-10, which
made "trim clip.mp4, compress it, and remove the audio" build the silent video from the
uncompressed trim — a wrong artefact that every plan-envelope comparison scored as a match,
because the envelopes were identical and only the *linked* plans differed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from knaif import CommandAgent

FIXTURES = Path("contracts/parity/chain_linking_cases.json")


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _agent(tmp_path: Path, registry_text: str, name: str) -> CommandAgent:
    reg_file = tmp_path / f"{name}.yaml"
    reg_file.write_text(registry_text, encoding="utf-8")
    return CommandAgent(reg_file)


def test_output_capable_derivation(doc: dict, tmp_path: Path) -> None:
    """A tool is an eligible producer only when its schema *accepts* an ``output`` arg.

    Pinned separately because it gates both passes: widening it writes ``output`` onto a tool the
    validator then rejects with "unsupported args", and narrowing it silently stops linking.
    """
    for reg_name, expected in doc["expected_output_capable"].items():
        agent = _agent(tmp_path, doc["registries"][reg_name], reg_name)
        assert sorted(agent._output_capable) == expected, f"{reg_name}: producer set changed"


def test_chain_linking_matches_the_contract(doc: dict, tmp_path: Path) -> None:
    assert doc["cases"], "fixture file has no cases"
    for case in doc["cases"]:
        agent = _agent(tmp_path, doc["registries"][case["registry"]], case["registry"])
        plan = copy.deepcopy(case["plan"]["plan"])
        CommandAgent._link_chain_intermediates(plan, case["utterance"], agent._output_capable)
        assert plan == case["expected"]["plan"], f"{case['name']}: linked plan changed"


def test_linked_plans_still_validate(doc: dict, tmp_path: Path) -> None:
    """Linking must never produce a plan the validator rejects.

    The whole point of the output-capable filter is that ``output`` is only written where the
    schema declares it; this is the assertion that catches it if that filter is ever widened.
    """
    from knaif.planner import validate_plan

    for case in doc["cases"]:
        agent = _agent(tmp_path, doc["registries"][case["registry"]], case["registry"])
        validate_plan(copy.deepcopy(case["expected"]), agent.registry, root=Path("."), sandbox=None)
