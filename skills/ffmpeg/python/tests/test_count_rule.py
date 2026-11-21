"""A count of files must match what the plan touches, through the real agent.

Found by the owner: "конвертирай 2 от видеата в mp4" converted every video in the
folder. The rule itself is pinned by contracts/parity/count_cases.json; this checks that a run
asks before any step, and that a matching count runs.
"""

from __future__ import annotations

from pathlib import Path

from knaif import create_agent

PLAN = {"plan": [{"tool": "convert_video", "args": {"inputs": ["*.mov"], "container": "mp4"}}]}


def _folder(tmp_path: Path, names: list[str]) -> Path:
    for name in names:
        (tmp_path / name).write_bytes(b"")
    return tmp_path


def test_a_count_that_does_not_match_asks_before_any_step(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["beach.mov", "city.mov", "silent.mov"])
    agent = create_agent("ffmpeg", root=folder)
    results = agent.execute_plan(
        PLAN, utterance="конвертирай 2 от видеата в mp4", dry_run=True, confirmed=True
    )
    assert [r["tool"] for r in results] == ["clarify"]
    assert results[0]["args"]["question"] == "Which 2 of the 3 videos? Name them."


def test_a_count_that_matches_runs(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["beach.mov", "city.mov"])
    agent = create_agent("ffmpeg", root=folder)
    results = agent.execute_plan(
        PLAN, utterance="convert both mov files to mp4", dry_run=True, confirmed=True
    )
    assert results and results[0]["tool"] != "clarify"
