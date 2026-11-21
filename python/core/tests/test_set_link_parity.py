"""Golden parity: pointing readers at a one-file-into-many set, over
contracts/parity/set_link_cases.json.

The Rust side (native/crates/knaif-core/tests/set_link_parity.rs) runs the identical
fixtures. Which steps read the pieces of a split decides what runs once per piece, so the two
runtimes must agree on it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from knaif.batch_chain import PlannedSet, link_set_readers

DOC = json.loads(Path("contracts/parity/set_link_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_link_set_readers(case: dict) -> None:
    sets = {int(k): PlannedSet(v["name"], v["files"]) for k, v in case["sets"].items()}
    plan = copy.deepcopy(case["plan"])
    assert link_set_readers(plan, sets) == case["expected"]
    assert plan == case["plan"], "the plan given was changed"
