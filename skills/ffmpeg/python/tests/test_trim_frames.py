"""Frame-count trims, and what a zero-length time range should render.

``ffmpeg_161`` asks for a one-frame video and gets nothing: the plan emits
``-ss 00:00:00 -to 00:00:00``, which is an empty range, and ffmpeg exits 0 having written a
file with no frames in it. The corpus then scored it as a pass, because its
``success_criteria`` was ``{"container": "mp4"}`` and a full-length MP4 satisfies that too.

**These are two behaviours, and conflating them was an error in the plan's first draft.**

1. *A frame-count request.* ``trim_video`` accepted ``input, start, end, duration, output,
   preview`` and no frame count at all, so "give me 1 frame" had nothing to bind to. That is
   a **contract change** — a new ``frames`` arg, its rendering, a ``tools.yaml`` entry,
   prompt coverage so it is actually selected, and both runtimes.
2. *A plan that supplies ``start == end``.* Adding ``frames`` does not repair this: the model
   can still emit a zero-length range. Equal bounds render one frame, which is what makes
   ``ffmpeg_161`` pass **without depending on the model learning the new argument** — a
   product fix that only works after a fine-tune is not a product fix.

``frames`` is mutually exclusive with ``end`` and ``duration``: "3 frames *and* a 10-second
range" has no coherent reading, so supplying both is an error rather than a silent
precedence rule that would quietly drop half the request.

See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T5b.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from knaif import CommandAgent
from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def engine():
    """The engine as the loaded skill sees it (the loader names the package per bundle)."""
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + "._engine"]


@pytest.fixture(scope="module")
def skill():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    return sys.modules["_skill_oop_ffmpeg_handlers"].FFmpegSkill()


def _probe(tmp: Path) -> dict:
    f = tmp / "clip.mp4"
    f.write_bytes(b"")
    return {
        "file": str(f),
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
        "width": 1920,
        "height": 1080,
        "duration": 10.0,
    }


def _cmd(engine, tmp: Path, **options) -> list[str]:
    recipe = engine._build_one_recipe(_probe(tmp), None, None, {"mode": "trim", **options}, tmp)
    return [str(x) for x in engine._render_command(recipe)]


# -- 1. the frame-count contract ----------------------------------------------


def test_tools_yaml_declares_frames(engine) -> None:
    """The registry feeds the rendered prompt, so an arg the model may not name is unusable."""
    tools = yaml.safe_load((FFMPEG_SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))
    assert "frames" in tools["trim_video"]["optional_args"]
    assert tools["trim_video"]["arg_schemas"]["frames"]["type"] == "integer"


def test_a_frame_count_renders_frames_v(engine, tmp_path: Path) -> None:
    cmd = _cmd(engine, tmp_path, start="00:00:02", frames=3)
    assert "-vframes" in cmd
    assert cmd[cmd.index("-vframes") + 1] == "3"
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "00:00:02"


def test_a_frame_count_without_a_start_is_still_a_frame_count(engine, tmp_path: Path) -> None:
    cmd = _cmd(engine, tmp_path, frames=1)
    assert "-vframes" in cmd and cmd[cmd.index("-vframes") + 1] == "1"
    assert "-to" not in cmd and "-t" not in cmd


def test_a_frame_count_never_renders_a_duration(engine, tmp_path: Path) -> None:
    """`-frames:v` and `-t` together let ffmpeg stop on whichever comes first."""
    cmd = _cmd(engine, tmp_path, start="00:00:02", frames=5)
    assert "-t" not in cmd
    assert "-to" not in cmd


@pytest.mark.parametrize("conflicting", [{"end": "00:00:05"}, {"duration": 5}])
def test_frames_with_a_range_is_a_validation_error(skill, tmp_path: Path, conflicting) -> None:
    """No coherent reading, so no silent precedence rule."""
    errors = skill.preflight(
        "trim_video",
        {"input": "clip.mp4", "frames": 3, **conflicting},
        root=tmp_path,
        sandbox=tmp_path,
    )
    assert errors, f"frames + {conflicting} was accepted"
    assert any("frames" in e for e in errors)


@pytest.mark.parametrize("bad", [0, -1, "many"])
def test_frames_must_be_a_positive_integer(skill, tmp_path: Path, bad) -> None:
    errors = skill.preflight(
        "trim_video", {"input": "clip.mp4", "frames": bad}, root=tmp_path, sandbox=tmp_path
    )
    assert errors, f"frames={bad!r} was accepted"


def test_frames_with_a_start_is_not_an_error(skill, tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"")
    assert (
        skill.preflight(
            "trim_video",
            {"input": "clip.mp4", "frames": 3, "start": "00:00:02"},
            root=tmp_path,
            sandbox=tmp_path,
        )
        == []
    )


# -- 2. a zero-length range, which the model can still emit -------------------


def test_equal_bounds_render_exactly_one_frame(engine, tmp_path: Path) -> None:
    """`ffmpeg_161`: -ss 00:00:00 -to 00:00:00 produced a file with nothing in it."""
    cmd = _cmd(engine, tmp_path, start="00:00:00", end="00:00:00")
    assert "-vframes" in cmd and cmd[cmd.index("-vframes") + 1] == "1"
    assert "-to" not in cmd, "an empty range must not survive into the command"


def test_equal_bounds_away_from_zero_render_one_frame_there(engine, tmp_path: Path) -> None:
    """Pinned away from the degenerate 00:00:00 case, where a bug could look like a fix."""
    cmd = _cmd(engine, tmp_path, start="00:00:04", end="00:00:04")
    assert "-vframes" in cmd and cmd[cmd.index("-vframes") + 1] == "1"
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "00:00:04"
    assert "-to" not in cmd


def test_mixed_timestamp_spellings_are_still_the_same_instant(engine, tmp_path: Path) -> None:
    """Comparing the literals would miss this, and the model writes both spellings."""
    cmd = _cmd(engine, tmp_path, start="0", end="00:00:00")
    assert "-vframes" in cmd and cmd[cmd.index("-vframes") + 1] == "1"


def test_a_real_range_is_untouched(engine, tmp_path: Path) -> None:
    """Both bounds are INPUT options — `-to` after `-i` means a length, not an end.

    This test used to assert only that `-to` was present with the right value, so it stayed
    green while every range trim rendered the wrong duration. Position is the thing that was
    wrong, so position is what it checks.
    """
    cmd = _cmd(engine, tmp_path, start="00:00:02", end="00:00:07")
    assert "-vframes" not in cmd
    assert "-to" in cmd and cmd[cmd.index("-to") + 1] == "00:00:07"
    assert cmd.index("-to") < cmd.index("-i"), cmd
    assert cmd.index("-ss") < cmd.index("-i"), cmd


def test_a_zero_duration_is_also_one_frame(engine, tmp_path: Path) -> None:
    """`duration: 0` is the same request spelled the other way."""
    cmd = _cmd(engine, tmp_path, start="00:00:03", duration=0)
    assert "-vframes" in cmd and cmd[cmd.index("-vframes") + 1] == "1"
    assert "-t" not in cmd


# -- 4. the wiring: from the model's plan to the rendered command -------------
#
# Everything above tests `_build_one_recipe` and the preflight in isolation, which is how a
# frame count could be declared in tools.yaml, rendered correctly by the engine, validated
# correctly by the preflight — and still do nothing at all. `TrimVideoIntent.expand()` never
# forwarded it into the recipe options, so a three-frame request against a ten-frame clip
# produced all ten frames, and the conflict check never ran because `preflight` is handed the
# *expanded* step's args, where `frames` no longer exists.
#
# These tests go through the agent, which is the only level at which that gap is visible.

FIXTURES = Path("sandbox/fixtures/ffmpeg")

agent_test = pytest.mark.skipif(
    not (FIXTURES / "clip.mp4").exists(),
    reason="needs the generated ffmpeg fixtures (just eval-fixtures ffmpeg)",
)


def _render_through_agent(args: dict) -> list[str]:
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=FIXTURES.resolve())
    plan = {"plan": [{"tool": "trim_video", "args": args}]}
    results = agent.execute_plan(json.loads(json.dumps(plan)), dry_run=True, confirmed=True)
    for entry in results if isinstance(results, list) else [results]:
        result = entry.get("result") if isinstance(entry, dict) else None
        if isinstance(result, dict):
            for out in result.get("outputs") or []:
                if out.get("command"):
                    return [str(c) for c in out["command"]]
    raise AssertionError(f"no rendered command in {results!r}")


@agent_test
def test_a_frame_count_survives_expansion(tmp_path: Path) -> None:
    """The defect: `frames` was dropped between the model's plan and the recipe."""
    cmd = _render_through_agent({"input": "clip.mp4", "frames": 3, "output": "f3.mp4"})
    assert "-vframes" in cmd, f"frames never reached the command: {' '.join(cmd)}"
    assert cmd[cmd.index("-vframes") + 1] == "3", " ".join(cmd)


@agent_test
def test_a_frame_count_with_a_range_is_refused_through_the_agent() -> None:
    """A frame count and a time range are two requests; picking one silently is the bug.

    The preflight that says so never saw `frames` through this path, so the plan executed
    and quietly honoured the range.
    """
    with pytest.raises(ValueError, match="frames"):
        _render_through_agent(
            {"input": "clip.mp4", "frames": 3, "end": "00:00:05", "output": "c.mp4"}
        )


@agent_test
def test_a_frame_count_with_a_start_still_works_through_the_agent() -> None:
    """`start` is a seek, not a range — it composes with a frame count."""
    cmd = _render_through_agent(
        {"input": "clip.mp4", "frames": 2, "start": "00:00:04", "output": "f2.mp4"}
    )
    assert "-vframes" in cmd and cmd[cmd.index("-vframes") + 1] == "2", " ".join(cmd)
    assert "-ss" in cmd, " ".join(cmd)


# -- 3. a frame count alongside a zero-length range ---------------------------
# `ffmpeg_161#0` (release plan R2): the model sent `start: 00:00:00, end: 00:00:00, frames: 1`,
# and the conflict rule refused it. A zero-length range names an instant, not a length, so it
# does not compete with the frame count: `frames` decides how much, `start` decides where.


@pytest.mark.parametrize(
    "bounds",
    [
        {"start": "00:00:00", "end": "00:00:00"},
        {"start": "0", "end": "00:00:00"},
        {"end": "00:00:00"},
        {"start": "00:00:04", "end": "4"},
    ],
)
def test_frames_with_a_zero_length_range_is_accepted(skill, tmp_path: Path, bounds) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"")
    errors = skill.preflight(
        "trim_video", {"input": "clip.mp4", "frames": 1, **bounds}, root=tmp_path, sandbox=tmp_path
    )
    assert errors == []


def test_frames_with_a_real_range_is_still_refused(skill, tmp_path: Path) -> None:
    errors = skill.preflight(
        "trim_video",
        {"input": "clip.mp4", "frames": 3, "start": "00:00:02", "end": "00:00:05"},
        root=tmp_path,
        sandbox=tmp_path,
    )
    assert any("frames" in e for e in errors)


def test_frames_decide_the_length_of_a_zero_length_range(tmp_path: Path) -> None:
    """Through the whole pipeline: the count is rendered, the empty range is not."""
    from knaif.evalsuite.runner import _extract_artifacts

    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=tmp_path)
    results = agent.execute_plan(
        {
            "plan": [
                {
                    "tool": "trim_video",
                    "args": {
                        "input": "clip.mp4",
                        "start": "00:00:04",
                        "end": "00:00:04",
                        "frames": 3,
                    },
                }
            ]
        },
        dry_run=True,
        confirmed=False,
    )
    (command,) = _extract_artifacts(results)
    cmd = command.split()
    assert cmd[cmd.index("-vframes") + 1] == "3"
    assert cmd[cmd.index("-ss") + 1] == "00:00:04"
    assert "-to" not in cmd
