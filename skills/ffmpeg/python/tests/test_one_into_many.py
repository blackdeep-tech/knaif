"""One file into many: split_video, extract_frames, create_thumbnail at several times.

Two new tools, two extended; the names are known before running (owner, 2026-10-20).
One planner (`_pieces.plan_pieces`) decides every piece's time and name, and both the
skill hook core asks before anything runs (`planned_outputs`) and `build_recipes` use it, so
what the preview promises is what runs. Each piece is an ordinary trim or thumbnail recipe:
video pieces are re-encoded, so they start and end exactly where asked.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill

SKILL_DIR = Path(__file__).parents[2]
FIXTURES = Path(__file__).parents[4] / "sandbox" / "fixtures" / "ffmpeg"
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="module")
def pieces():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + "._pieces"]


def _plan(pieces, tool, args, duration=10.0, fps=30.0):
    got = pieces.plan_pieces(tool, args, duration, fps)
    return got and (got.name, [(out, opts) for out, opts in got.items])


# ── the planner ─────────────────────────────────────────────────────────────────────────────


def test_pieces_by_length(pieces):
    name, items = _plan(pieces, "split_video", {"input": "clip.mp4", "every": "10s"}, 25.0)
    assert name == "clip_%03d.mp4"
    assert [o for o, _ in items] == ["clip_001.mp4", "clip_002.mp4", "clip_003.mp4"]
    assert [(x["mode"], x["start"], x["duration"]) for _, x in items] == [
        ("trim", "0", "10"),
        ("trim", "10", "10"),
        ("trim", "20", "5"),
    ]


def test_pieces_by_count(pieces):
    _, items = _plan(pieces, "split_video", {"input": "clip.mp4", "parts": 3}, 9.0)
    assert [(x["start"], x["duration"]) for _, x in items] == [("0", "3"), ("3", "3"), ("6", "3")]


def test_a_sliver_at_the_end_is_not_a_piece(pieces):
    _, items = _plan(pieces, "split_video", {"input": "clip.mp4", "every": 10}, 20.04)
    assert len(items) == 2


def test_piece_names_follow_the_output_or_the_input(pieces):
    assert _plan(pieces, "split_video", {"input": "clip.mp4", "parts": 2, "output": "part.mp4"})[
        0
    ] == ("part_%03d.mp4")
    assert _plan(
        pieces, "split_video", {"input": "clip.mp4", "parts": 2, "output": "out/p_%02d.mp4"}
    )[1][1][0] == ("out/p_02.mp4")
    assert _plan(pieces, "split_video", {"input": "videos/clip.mov", "parts": 2})[0] == (
        "videos/clip_%03d.mov"
    )


def test_a_split_needs_a_length_or_a_count(pieces):
    with pytest.raises(ValueError, match="how long"):
        pieces.plan_pieces("split_video", {"input": "clip.mp4"}, 10.0, 30.0)


def test_frames_by_interval(pieces):
    name, items = _plan(pieces, "extract_frames", {"input": "clip.mp4", "every": "5s"}, 12.0)
    assert name == "clip_frame_%03d.jpg"
    assert [(o, x["mode"], x["at_time"]) for o, x in items] == [
        ("clip_frame_001.jpg", "thumbnail", "0"),
        ("clip_frame_002.jpg", "thumbnail", "5"),
        ("clip_frame_003.jpg", "thumbnail", "10"),
    ]


def test_frames_spread_evenly(pieces):
    _, items = _plan(pieces, "extract_frames", {"input": "clip.mp4", "count": 4}, 8.0)
    assert [x["at_time"] for _, x in items] == ["1", "3", "5", "7"]


def test_every_nth_frame(pieces):
    _, items = _plan(pieces, "extract_frames", {"input": "clip.mp4", "every_nth": 10}, 2.0, 25.0)
    assert [x["at_time"] for _, x in items] == ["0", "0.4", "0.8", "1.2", "1.6"]


def test_frames_in_another_format_and_folder(pieces):
    name, items = _plan(
        pieces,
        "extract_frames",
        {"input": "clip.mp4", "count": 2, "output": "stills/frame.png"},
    )
    assert name == "stills/frame_%03d.png"
    assert items[0][1]["image_format"] == "png"


def test_thumbnails_at_several_times(pieces):
    name, items = _plan(
        pieces, "create_thumbnail", {"input": "clip.mp4", "at_time": ["0:05", "0:30", "1:10"]}
    )
    assert name == "clip_thumb_%03d.jpg"
    assert [(o, x["at_time"]) for o, x in items] == [
        ("clip_thumb_001.jpg", "0:05"),
        ("clip_thumb_002.jpg", "0:30"),
        ("clip_thumb_003.jpg", "1:10"),
    ]
    # Written as one string, the times still count as several.
    assert len(_plan(pieces, "create_thumbnail", {"input": "c.mp4", "at_time": "1, 2"})[1]) == 2


def test_one_thumbnail_is_no_set(pieces):
    assert (
        pieces.plan_pieces("create_thumbnail", {"input": "clip.mp4", "at_time": "0:05"}, 10.0, 30.0)
        is None
    )
    assert pieces.plan_pieces("compress_video", {"inputs": ["clip.mp4"]}, 10.0, 30.0) is None


# ── for real ────────────────────────────────────────────────────────────────────────────────


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def _agent(tmp_path: Path) -> CommandAgent:
    shutil.copy(FIXTURES / "clip.mp4", tmp_path / "clip.mp4")
    return CommandAgent.from_skill(SKILL_DIR, sandbox=tmp_path, root=tmp_path)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_a_real_split_cuts_where_asked(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.execute_plan(
        {"plan": [{"tool": "split_video", "args": {"input": "clip.mp4", "every": "4s"}}]},
        utterance="split clip.mp4 into 4-second parts",
        dry_run=False,
        confirmed=True,
    )
    parts = sorted(tmp_path.glob("clip_0*.mp4"))
    assert [p.name for p in parts] == ["clip_001.mp4", "clip_002.mp4", "clip_003.mp4"]
    assert [round(_duration(p)) for p in parts] == [4, 4, 2]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_real_frames_spread_evenly(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.execute_plan(
        {"plan": [{"tool": "extract_frames", "args": {"input": "clip.mp4", "count": 3}}]},
        utterance="give me 3 stills spread across clip.mp4",
        dry_run=False,
        confirmed=True,
    )
    assert sorted(p.name for p in tmp_path.glob("*.jpg")) == [
        "clip_frame_001.jpg",
        "clip_frame_002.jpg",
        "clip_frame_003.jpg",
    ]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_split_then_compress_each_piece_leaves_only_the_compressed(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.execute_plan(
        {
            "plan": [
                {"tool": "split_video", "args": {"input": "clip.mp4", "every": "4s"}},
                {"tool": "compress_video", "args": {"inputs": ["clip_*.mp4"]}},
            ]
        },
        utterance="split clip.mp4 into 4-second parts and compress each one",
        dry_run=False,
        confirmed=True,
    )
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == [
        "clip.mp4",
        "clip_001_compressed.mp4",
        "clip_002_compressed.mp4",
        "clip_003_compressed.mp4",
    ]
    assert (agent.last_batch_report.done, agent.last_batch_report.failed) == (3, 0)
