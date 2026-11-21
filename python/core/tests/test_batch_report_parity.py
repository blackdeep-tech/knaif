"""Golden parity: the batch failure line and end summary over
contracts/parity/batch_report_cases.json.

The Rust side (native/crates/knaif-core/tests/batch_report_parity.rs) runs the identical
fixtures; both runtimes print the same text (owner, 2026-10-13).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.batch_chain import failure_line, summary_line

DOC = json.loads(Path("contracts/parity/batch_report_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["summary"], ids=[c["line"] for c in DOC["summary"]])
def test_summary_line(case: dict) -> None:
    assert (
        summary_line(case["done"], case["failed"], case["seconds"], case["bytes"]) == case["line"]
    )


@pytest.mark.parametrize("case", DOC["failure"], ids=[c["line"] for c in DOC["failure"]])
def test_failure_line(case: dict) -> None:
    assert failure_line(case["file"], case["reason"]) == case["line"]
