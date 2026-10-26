"""A documents tool's dry run stops on the same bad input on both runtimes.

The rehearsal before a real run (owner, 2026-10-24) is a dry run, so a tool whose dry run reads
its file in one runtime and not in the other rehearses differently: in a manual check a
password-protected PDF in a batch was out before anything ran on Python and found only while
running on native. `contracts/parity/dry_run_depth_cases.json` pins, per tool, whether its dry run
passes on a protected and on a broken PDF; Python is the reference, native reads it in
`skills/documents/native/tests/dry_run_depth_parity.rs`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from knaif.agent import CommandAgent

REPO = Path(__file__).parents[3]
DOC = json.loads(
    (REPO / "contracts" / "parity" / "dry_run_depth_cases.json").read_text(encoding="utf-8")
)
PROTECTED = REPO / "sandbox" / "fixtures" / "documents" / "sample-protected.pdf"

pytest.importorskip("pypdf")


def _passes(case: dict[str, Any], folder: Path) -> bool:
    agent = CommandAgent.from_skill(REPO / "skills" / "documents", sandbox=folder, root=folder)
    plan = {"plan": [{"tool": case["tool"], "args": case["args"]}]}
    try:
        results = agent.execute_plan(plan, dry_run=True, confirmed=True)
    except Exception:  # noqa: BLE001 - any refusal is "stops"
        return False
    last = (results[-1].get("result") if results else None) or {}
    return not (isinstance(last, dict) and last.get("status") == "failed")


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_dry_run_depth(case: dict[str, Any], tmp_path: Path) -> None:
    if case["input"] == "protected":
        if not PROTECTED.exists():
            pytest.skip("documents fixtures not generated")
        shutil.copy(PROTECTED, tmp_path / "x.pdf")
    else:
        (tmp_path / "x.pdf").write_bytes(DOC["broken_bytes"].encode("ascii"))
    assert _passes(case, tmp_path) == case["dry_run_passes"]
