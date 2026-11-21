"""Golden parity: a count of files must match the files the plan touches, over
contracts/parity/count_cases.json.

(owner, 2026-10-22): "конвертирай 2 от видеата в mp4" converted every video in the
folder; "convert three of the videos", "both mov files" (with three) and "the 3 biggest videos"
did the same in manual checks. The Rust side (native/crates/knaif-core/tests/count_parity.rs)
runs the same cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.nl_clarify_gate import count_question

DOC = json.loads(Path("contracts/parity/count_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_count_question(case: dict, tmp_path: Path) -> None:
    for f in case["files"]:
        (tmp_path / f).write_bytes(b"")
    got = count_question(case["utterance"], case["plan"], tmp_path, DOC["file_kinds"])
    assert got == case["expected"]
