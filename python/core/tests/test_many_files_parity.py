"""Golden parity: whether a request asks for many files, over
contracts/parity/many_files_cases.json.

Decides whether the per-file batch rule and its examples join a prompt (owner,
2026-10-20). The Rust side (native/crates/knaif-core/tests/many_files_parity.rs) runs the same
cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.nl_clarify_gate import asks_for_many_files

DOC = json.loads(Path("contracts/parity/many_files_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["utterance"][:40] for c in DOC["cases"]])
def test_asks_for_many_files(case: dict) -> None:
    assert asks_for_many_files(case["utterance"]) is case["many"]
