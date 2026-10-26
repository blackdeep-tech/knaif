"""Golden parity: how an ffmpeg step names what it writes, over contracts/parity/output_name_cases.json.

Owner, 2026-10-23: a check by hand found `beach.mov` written as `mov.mkv` (one
literal name over many files), `_converted` on a format change that cannot clash, and
`beach_converted_beach.mkv` for two sources sharing a name. The Rust side
(skills/ffmpeg/native/tests/output_name_parity.rs) renders the same plans.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif import CommandAgent

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILL = REPO_ROOT / "skills" / "ffmpeg"
DOC = json.loads(
    (REPO_ROOT / "contracts" / "parity" / "output_name_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_output_names(case: dict, tmp_path: Path) -> None:
    for f in case["files"]:
        (tmp_path / f).write_bytes(b"")
    agent = CommandAgent.from_skill(SKILL, sandbox=tmp_path)
    results = agent.execute_plan(case["plan"], dry_run=True, confirmed=False)
    outputs = [
        Path(o["output"]).resolve().relative_to(tmp_path.resolve()).as_posix()
        for r in results
        if r.get("tool") == "run_batch"
        for o in (r.get("result") or {}).get("outputs") or []
    ]
    assert outputs == case["expected_outputs"]
