"""Golden parity: the result set's "leaves" line over contracts/parity/result_set_cases.json.

The Rust side (native/crates/knaif-core/tests/result_set_parity.rs) runs the identical
fixtures. Every preview ends with this line (owner, 2026-10-20), so both runtimes must say the
same thing about what a plan leaves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.result_set import leaves_line, result_set, same_for_line

DOC = json.loads(Path("contracts/parity/result_set_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["leaves"], ids=[c["name"] for c in DOC["leaves"]])
def test_leaves_line(case: dict) -> None:
    t = DOC["tables"][case["tables"]]
    results = result_set(
        case["plan"],
        scratch="<work>",
        base=Path("."),
        expand=case["expand"],
        readonly=set(t["readonly"]),
        joining=set(t["joining"]),
        produces=t["produces"],
        file_kinds=t["file_kinds"],
    )
    assert leaves_line(results) == case["line"]


@pytest.mark.parametrize("case", DOC["same_for"], ids=[c["line"] for c in DOC["same_for"]])
def test_same_for_line(case: dict) -> None:
    assert same_for_line(case["files"]) == case["line"]
