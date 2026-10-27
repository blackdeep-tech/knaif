"""Golden parity: a glob or a folder expands in natural order, over
contracts/parity/batch_cases.json.

(owner, 2026-10-19): "merge all the clips in this folder into one" takes
`clip1, clip2, clip10`, and a batch runs its files in the same order. The Rust side
(toolkits/fs/native/tests/batch_cases_parity.rs, `knaif_toolkit_fs::resolve_inputs`) runs
the identical cases.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from toolkits.fs.python.resolve_inputs import ResolveInputs

DOC = json.loads(Path("contracts/parity/batch_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DOC["expand"], ids=[c["name"] for c in DOC["expand"]])
def test_expand_in_natural_order(case: dict, tmp_path: Path) -> None:
    for name in case["files"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    ctx = MagicMock()
    ctx.sandbox = tmp_path
    ctx.root = tmp_path
    ctx.work_dir = None
    args = {"paths": case["paths"]}
    for key in ("extensions", "exclude"):
        if key in case:
            args[key] = case[key]

    files = ResolveInputs().handle(args, ctx)["files"]

    root = tmp_path.resolve()
    assert [Path(f).resolve().relative_to(root).as_posix() for f in files] == case["expected"]
