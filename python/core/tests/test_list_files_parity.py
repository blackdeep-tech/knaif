"""Golden parity: the one folder listing, over contracts/parity/list_files_cases.json.

(owner, 2026-10-23): core's per-file batch and the fs toolkit's resolve_inputs listed
folders and globs with two copies of the same rules. Both now call `list_files`. The Rust side
(native/crates/knaif-core/tests/list_files_parity.rs) runs the same cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.batch_chain import list_files

DOC = json.loads(Path("contracts/parity/list_files_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_list_files(case: dict, tmp_path: Path) -> None:
    for f in case["files"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"")
    got = list_files(tmp_path / case["folder"], case["pattern"], recursive=case["recursive"])
    assert [rel for rel, _path in got] == case["expected"]
    assert all(path.is_file() for _rel, path in got)
