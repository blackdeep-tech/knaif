"""L2: the Python reference meets contracts/parity/alert_cases.json.

The same file is the native mirror's contract, so both runtimes split the alert off a plan and
decide when a request brings the tool into the prompt in the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.core_tools import CORE_TOOL_DEFS
from knaif.planner import split_alert
from knaif.registry import triggered

DOC = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts" / "parity" / "alert_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", DOC["split"], ids=lambda c: c["name"])
def test_split(case: dict) -> None:
    plan, alert = split_alert({"plan": case["plan"]})
    assert plan["plan"] == case["expected_plan"]
    assert alert == case["expected_alert"]


@pytest.mark.parametrize("case", DOC["triggers"], ids=lambda c: c["utterance"][:40])
def test_triggers(case: dict) -> None:
    assert (
        triggered(case["utterance"], CORE_TOOL_DEFS["alert_when_done"].triggers) is case["expected"]
    )
