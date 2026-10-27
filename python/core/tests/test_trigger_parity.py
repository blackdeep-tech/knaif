"""L2: when a triggered tool joins the prompt, over contracts/parity/trigger_cases.json.

The Rust side (native/crates/knaif-core/tests/trigger_parity.rs) runs the identical cases: the
same request must bring the same tools into both runtimes' prompts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.registry import triggered

CASES = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts/parity/trigger_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["utterance"][:40] for c in CASES])
def test_triggered_matches_the_contract(case: dict) -> None:
    assert triggered(case["utterance"], tuple(case["triggers"])) is case["expected"]
