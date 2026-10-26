"""L2 parity: routing chain intermediates into the run scratch.

Golden fixtures in ``contracts/parity/scratch_routing_cases.json`.`, shared with
``native/crates/knaif-core/tests/scratch_routing_parity.rs``. Both runtimes must produce the
identical routed plan, or the same utterance leaves different files in the user's folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from knaif.agent import CommandAgent

DOC = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts/parity/scratch_routing_cases.json").read_text(
        encoding="utf-8"
    )
)
CASES = DOC["cases"]


def _placeholders(value: Any, work: Path) -> Any:
    """Absolute scratch paths back to the fixture's `<work>/…` spelling, `/`-separated."""
    if isinstance(value, str):
        prefix = str(work)
        if value.startswith(prefix):
            return "<work>" + value[len(prefix) :].replace("\\", "/")
        return value
    if isinstance(value, list):
        return [_placeholders(v, work) for v in value]
    if isinstance(value, dict):
        return {k: _placeholders(v, work) for k, v in value.items()}
    return value


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_routing_matches_the_contract(case: dict, tmp_path: Path) -> None:
    work, sandbox = tmp_path / "work", tmp_path / "sandbox"
    sandbox.mkdir()
    routed = CommandAgent._route_intermediates_to_scratch(
        case["plan"], work, case["utterance"], sandbox, set(DOC["joining_tools"])
    )
    assert _placeholders(routed, work) == case["expected"]
