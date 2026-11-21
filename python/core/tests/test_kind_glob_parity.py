"""Golden parity: a batch glob for "every video" covers every video, over
contracts/parity/kind_glob_cases.json.

(owner, 2026-10-21): a limited eval run lost 18 utterances to `*.mp4` written for "every
video here" in a folder of .mp4, .mov and .mkv files. The Rust side
(native/crates/knaif-core/tests/kind_glob_parity.rs) runs the same cases.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from knaif.batch_chain import widen_kind_globs

DOC = json.loads(Path("contracts/parity/kind_glob_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_widen_kind_globs(case: dict, tmp_path: Path) -> None:
    for f in case["files"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"")
    plan = copy.deepcopy(case["plan"])
    got = widen_kind_globs(
        plan,
        case["utterance"],
        tmp_path,
        DOC["file_kinds"],
        DOC["batch_kinds"],
        DOC["produces"],
    )
    assert got == case["expected"]
    assert plan == case["plan"], "the plan given is left as it was"
