""" "Every video" is every video (owner, 2026-10-21), through the real skill.

The rules are pinned by contracts/parity/kind_glob_cases.json and batch_chain_cases.json; these
tests check that the agent applies them: ffmpeg lists `video` under `batch_kinds:`, a lone
`*.mp4` for "every video here" covers the .mov and .mkv files too, and a one-path tool given
several globs runs once per file.
"""

from __future__ import annotations

from pathlib import Path

from knaif import create_agent
from knaif.skill import Skill

SKILL_DIR = Path(__file__).resolve().parents[2]


def _folder(tmp_path: Path) -> Path:
    for name in ("beach.mov", "concert.mp4", "garden.mkv", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def _inputs(results: list[dict]) -> list[str]:
    """The files each run of the step read, in order (its expansion's `resolve_inputs`)."""
    return [",".join(r["args"]["paths"]) for r in results if r["tool"] == "resolve_inputs"]


def test_ffmpeg_widens_video_globs_only() -> None:
    assert Skill.load(SKILL_DIR).batch_kinds == ["video"]


def test_a_lone_mp4_glob_for_every_video_covers_every_video(tmp_path: Path) -> None:
    agent = create_agent("ffmpeg", sandbox=_folder(tmp_path))
    plan = {"plan": [{"tool": "create_thumbnail", "args": {"input": "*.mp4"}}]}
    results = agent.execute_plan(
        plan, dry_run=True, utterance="make a thumbnail of every video here"
    )
    assert _inputs(results) == ["concert.mp4", "garden.mkv", "beach.mov"]


def test_several_globs_on_a_one_path_tool_run_once_per_file(tmp_path: Path) -> None:
    agent = create_agent("ffmpeg", sandbox=_folder(tmp_path))
    plan = {
        "plan": [
            {
                "tool": "trim_video",
                "args": {"inputs": ["*.mp4", "*.mov"], "start": "00:00:00", "end": "00:00:05"},
            }
        ]
    }
    results = agent.execute_plan(
        plan, dry_run=True, utterance="trim every clip to its first 5 seconds"
    )
    assert _inputs(results) == ["concert.mp4", "garden.mkv", "beach.mov"]


def test_a_named_type_stays_alone(tmp_path: Path) -> None:
    agent = create_agent("ffmpeg", sandbox=_folder(tmp_path))
    plan = {"plan": [{"tool": "create_thumbnail", "args": {"input": "*.mov"}}]}
    results = agent.execute_plan(
        plan, dry_run=True, utterance="make a thumbnail of every .mov file here"
    )
    assert _inputs(results) == ["beach.mov"]


def test_the_summary_counts_what_was_written_not_the_inputs(tmp_path: Path) -> None:
    """The batch report's size is what the run left: its outputs. `resolve_inputs` reports the
    step's input files under `files` too, which Python counted, twice the size native says
    (a single batch step with a broken file)."""
    import shutil
    import subprocess

    import pytest

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH")
    for name in ("beach.mov", "city.mov"):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=1:s=64x64:r=10", name],
            cwd=tmp_path,
            check=True,
        )
    agent = create_agent("ffmpeg", sandbox=tmp_path)
    plan = {"plan": [{"tool": "convert_video", "args": {"inputs": ["*.mov"], "container": "mp4"}}]}
    agent.execute_plan(
        plan, dry_run=False, confirmed=True, utterance="convert all the .mov files here to mp4"
    )
    outputs = sum(p.stat().st_size for p in tmp_path.glob("*.mp4"))
    assert agent.last_batch_report.size_bytes == outputs
