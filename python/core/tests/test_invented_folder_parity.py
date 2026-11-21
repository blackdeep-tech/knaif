"""Golden parity: an invented batch folder reads as here, over
contracts/parity/invented_folder_cases.json.

(owner, 2026-10-21): a limited eval run lost 6 utterances to a `videos/` folder the
model made up for "here", which known-folder resolution then pointed at the user's Videos folder.
The Rust side (native/crates/knaif-core/tests/invented_folder_parity.rs) runs the same cases.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from knaif.batch_chain import drop_invented_folders

DOC = json.loads(Path("contracts/parity/invented_folder_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_drop_invented_folders(case: dict, tmp_path: Path) -> None:
    for d in case["dirs"]:
        (tmp_path / d).mkdir(parents=True)
    plan = copy.deepcopy(case["plan"])
    assert drop_invented_folders(plan, case["utterance"], tmp_path) == case["expected"]
    assert plan == case["plan"], "the plan given is left as it was"
