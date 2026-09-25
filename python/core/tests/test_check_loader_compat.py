"""scripts/check_loader_compat.py: the Python side must label `runtimes.native` as Rust does.

`knaif skills list` (apps/cli `cmd_skills_list`) prints `native:<crate>` only when the status
is `supported`, and `native:<status>` otherwise. The checker used to print the crate whenever
one was declared, so both skills failed `loader-check` from 2026-09-11, when their status was
lowered to `in-progress` and the crate stayed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_loader_compat.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_loader_compat", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clc = _load()


@pytest.mark.parametrize(
    ("native", "label"),
    [
        ({"status": "supported", "crate": "knaif-skill-ffmpeg"}, "knaif-skill-ffmpeg"),
        ({"status": "supported"}, "supported"),
        ({"status": "in-progress", "crate": "knaif-skill-ffmpeg"}, "in-progress"),
        ({"status": "parity", "crate": "knaif-skill-ffmpeg"}, "parity"),
        ({"crate": "knaif-skill-ffmpeg"}, "-"),
        ({}, "-"),
    ],
)
def test_native_label_mirrors_the_rust_skills_list(native: dict, label: str) -> None:
    assert clc.native_label(native) == label
