"""Golden parity: prompt rules joining the system header, over
contracts/parity/prompt_rule_cases.json.

The one-file-into-many rule joins a prompt that lists its tools, the per-file batch rule one whose
request asks for many files. The Rust side (native/crates/knaif-core/tests/prompt_rule_parity.rs)
runs the same cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.prompt import apply_prompt_rules

DOC = json.loads(Path("contracts/parity/prompt_rule_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_apply_prompt_rules(case: dict) -> None:
    got = apply_prompt_rules(case["header"], DOC["rules"], set(case["present"]), case["utterance"])
    assert got == case["expected"]
