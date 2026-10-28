"""An arg only the request can state is left out when it does not (2026-10-28).

`contracts/parity/stated_args_cases.json`; native runs the same cases
(native/crates/knaif-core/tests/stated_args_parity.rs).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from knaif.nl_clarify_gate import drop_unstated_args
from knaif.registry import load_registry

REPO = Path(__file__).resolve().parents[3]
DOC = json.loads((REPO / "contracts" / "parity" / "stated_args_cases.json").read_text("utf-8"))
REGISTRY = load_registry(REPO / "skills" / "ffmpeg" / "tools.yaml")


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_stated_args(case: dict[str, Any]) -> None:
    plan, notes = drop_unstated_args(case["plan"], case["utterance"], REGISTRY)
    assert plan == case["expect_plan"]
    assert notes == case["notes"]


def test_the_models_plan_is_not_mutated() -> None:
    case = DOC["cases"][0]
    before = json.dumps(case["plan"])
    drop_unstated_args(case["plan"], case["utterance"], REGISTRY)
    assert json.dumps(case["plan"]) == before


def test_compress_video_declares_its_size_stated() -> None:
    assert "target_size_mb" in REGISTRY["compress_video"].stated_args
