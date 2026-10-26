"""Golden parity: one file into many, rendered, over contracts/parity/pieces_expansion_cases.json.

split_video, extract_frames and create_thumbnail at several times render one
ffmpeg command per piece. The Rust side (skills/ffmpeg/native/tests/pieces_expansion_parity.rs)
renders the identical plans and must produce the same commands, in the same order.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "parity" / "pieces_expansion_cases.json"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

DOC = json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def agent():
    from knaif import CommandAgent

    return CommandAgent.from_skill(
        REPO_ROOT / "skills" / "ffmpeg", sandbox=REPO_ROOT / DOC["sandbox"]
    )


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_pieces_render_as_recorded(case: dict, agent) -> None:
    from parity_check import canon_token

    from knaif.evalsuite.runner import _replay_commands

    results = agent.execute_plan(case["plan"], dry_run=True, confirmed=False)
    got = [
        [canon_token(t.replace("\\", "/")) for t in c.split()] for c in _replay_commands(results)[0]
    ]
    assert got == case["expected_commands"]
