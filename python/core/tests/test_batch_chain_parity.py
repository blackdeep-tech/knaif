"""Golden parity: Python batch chain lifting over contracts/parity/batch_chain_cases.json.

The Rust side (native/crates/knaif-core/tests/batch_chain_parity.rs) runs the identical
fixtures; both must produce the identical lifted plan.

**Why this exists.** The per-file batch rule: the model writes the steps for one file with
the batch glob as the first step's input, and core repeats them for every file the glob matched.
The lift decides which file every repeated step reads and writes, so the two runtimes must agree
on it step for step, names included.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from knaif.batch_chain import lift_batch, lift_per_file_chain

FIXTURES = Path("contracts/parity/batch_chain_cases.json")


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_batch_chain_lifting_matches_the_contract(doc: dict) -> None:
    assert doc["cases"], "fixture file has no cases"
    for case in doc["cases"]:
        if "home" in case or "single" in case:
            continue  # lift_batch's options: the record test below
        plan = copy.deepcopy(case["plan"])
        got = lift_per_file_chain(
            plan, case["expand"], set(doc["joining_tools"]), doc["batch_outputs"]
        )
        assert got == case["expected"], f"{case['name']}: lifted plan changed"
        assert plan == case["plan"], f"{case['name']}: the lift changed its input plan"


def test_the_lift_records_which_file_each_step_works_on(doc: dict) -> None:
    """`file_of`, `files`, `shown` and `join_files`: what per-file failures and reports read."""
    for case in doc["cases"]:
        lift = lift_batch(
            case["plan"],
            case["expand"],
            set(doc["joining_tools"]),
            home=case.get("home"),
            single=case.get("single", False),
            batch_outputs=doc["batch_outputs"],
        )
        assert lift.plan == case["expected"], f"{case['name']}: lifted plan changed"
        got = {
            "files": lift.files,
            "shown": lift.shown,
            "file_of": lift.file_of,
            "join_files": {str(k): v for k, v in lift.join_files.items()},
        }
        assert got == case["lift"], f"{case['name']}: lift record changed"
