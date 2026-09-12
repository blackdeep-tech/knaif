"""The replacement name must itself be free, or the fix creates the bug it removes.

Renaming ``clip.mp4`` to an unexamined ``clip_converted.mp4`` is not safer than not renaming
at all: every rendered command carries ``-y``, so the fallback can destroy a file that is
already on disk, another input of the same plan, or a name a later step has reserved for its
own output. These tests walk each of those three, plus the reporting — a rename the user is
not told about leaves them looking for a file that was never written.

Companion to ``test_output_collision_binding.py``, which covers the *binding* half (which
file a later reference means once a rename has happened).

See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T5b.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


def rebind_colliding_outputs(plan, sandbox):
    """Go through the shipped skill, so the test exercises the wiring as well as the rule."""
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    skill = sys.modules["_skill_oop_ffmpeg_handlers"].FFmpegSkill()
    rebound = skill.resolve_output_collisions(plan, sandbox=sandbox)
    return rebound, skill.output_substitutions


def _convert(src: str, out: str) -> dict:
    return {"tool": "convert_video", "args": {"inputs": [src], "output": out, "container": "mp4"}}


def _renamed(plan: list[dict], sandbox: Path) -> tuple[str, list[dict[str, str]]]:
    plan, subs = rebind_colliding_outputs(plan, sandbox)
    return plan[0]["args"]["output"], subs


def test_a_self_overwrite_is_renamed_at_all(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"")
    out, subs = _renamed([_convert("clip.mp4", "clip.mp4")], tmp_path)
    assert out == "clip_converted.mp4"
    assert subs == [
        {"step": "convert_video", "requested": "clip.mp4", "used": "clip_converted.mp4"}
    ]


def test_the_fallback_skips_a_file_that_already_exists(tmp_path: Path) -> None:
    """`-y` would have destroyed it silently, and the user never named it."""
    (tmp_path / "clip.mp4").write_bytes(b"")
    (tmp_path / "clip_converted.mp4").write_bytes(b"precious")
    out, _ = _renamed([_convert("clip.mp4", "clip.mp4")], tmp_path)
    assert out == "clip_converted_2.mp4"
    assert (tmp_path / "clip_converted.mp4").read_bytes() == b"precious"


def test_the_fallback_skips_another_input_of_the_same_plan(tmp_path: Path) -> None:
    """The file need not exist yet to be spoken for — a later step is going to read it."""
    (tmp_path / "clip.mp4").write_bytes(b"")
    plan = [
        _convert("clip.mp4", "clip.mp4"),
        {"tool": "create_thumbnail", "args": {"inputs": ["clip_converted.mp4"]}},
    ]
    plan, _ = rebind_colliding_outputs(plan, tmp_path)
    assert plan[0]["args"]["output"] == "clip_converted_2.mp4"
    # ...and the later step is left alone: it named a file the producer never wrote.
    assert plan[1]["args"]["inputs"] == ["clip_converted.mp4"]


def test_the_fallback_skips_an_output_a_later_step_reserved(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"")
    (tmp_path / "other.mp4").write_bytes(b"")
    plan = [
        _convert("clip.mp4", "clip.mp4"),
        _convert("other.mp4", "clip_converted.mp4"),
    ]
    plan, _ = rebind_colliding_outputs(plan, tmp_path)
    assert plan[0]["args"]["output"] == "clip_converted_2.mp4"
    assert plan[1]["args"]["output"] == "clip_converted.mp4"


def test_the_walk_is_deterministic(tmp_path: Path) -> None:
    """Two runs of the same plan must choose the same name, or a rerun writes a third file."""
    for _ in range(3):
        (tmp_path / "clip.mp4").write_bytes(b"")
        out, _ = _renamed([_convert("clip.mp4", "clip.mp4")], tmp_path)
        assert out == "clip_converted.mp4"


def test_an_output_naming_a_different_existing_file_is_left_alone(tmp_path: Path) -> None:
    """Renaming here would be second-guessing a clear instruction.

    "convert clip.mp4 to out.mp4" when out.mp4 exists is a destination the user chose, and
    `-y` overwriting it is the documented behaviour. Only a step's *own input* is protected,
    because that one destroys the thing the command is in the middle of reading.
    """
    (tmp_path / "clip.mp4").write_bytes(b"")
    (tmp_path / "out.mp4").write_bytes(b"old")
    out, subs = _renamed([_convert("clip.mp4", "out.mp4")], tmp_path)
    assert out == "out.mp4"
    assert subs == []


def test_a_plan_with_no_explicit_output_is_untouched(tmp_path: Path) -> None:
    """Batch and glob producers derive their own names and never collide with a consumer."""
    (tmp_path / "clip.mp4").write_bytes(b"")
    plan = [{"tool": "convert_video", "args": {"inputs": ["*.mp4"], "container": "webm"}}]
    before = repr(plan)
    plan, subs = rebind_colliding_outputs(plan, tmp_path)
    assert repr(plan) == before
    assert subs == []


@pytest.mark.parametrize("spelling", ["clip.mp4", "./clip.mp4"])
def test_the_collision_is_seen_through_path_spelling(tmp_path: Path, spelling: str) -> None:
    """`./clip.mp4` and `clip.mp4` are one file; comparing the literals would miss it."""
    (tmp_path / "clip.mp4").write_bytes(b"")
    out, subs = _renamed([_convert("clip.mp4", spelling)], tmp_path)
    assert subs, f"{spelling!r} was not recognised as the step's own input"
    assert Path(out).name == "clip_converted.mp4"


# -- the shapes the first implementation got wrong -----------------------------


def test_a_collision_is_fixed_even_when_the_target_is_not_on_disk_yet(tmp_path: Path) -> None:
    """A chained intermediate collides just as hard as a file that already exists.

    This is the shape `prompt.yaml` actively teaches — give step 0 an output filename and
    reuse it as step 1's input. The first implementation decided by `Path.exists()` alone,
    so it handed the colliding path straight back, left the `-y` truncation in place, AND
    told the user it had renamed the file to the same name it already had. It also made the
    outcome depend on whether the plan had been run before, since the first run created the
    file that the second run then noticed.
    """
    (tmp_path / "clip.mp4").write_bytes(b"")
    plan = [
        {"tool": "trim_video", "args": {"input": "clip.mp4", "output": "clip_trimmed.mp4"}},
        _convert("clip_trimmed.mp4", "clip_trimmed.mp4"),
    ]
    plan, subs = rebind_colliding_outputs(plan, tmp_path)
    assert plan[1]["args"]["output"] != "clip_trimmed.mp4"
    assert subs and subs[0]["requested"] != subs[0]["used"], "reported a rename that did not happen"


def test_a_consumer_in_a_different_directory_is_not_rebound(tmp_path: Path) -> None:
    """`a/clip.mp4` and `b/clip.mp4` are two files. Matching basenames merges them.

    The plan rules this out by name, and the prompt tells the model to preserve every
    directory component — so a rebind that compares basenames rewrites a consumer onto a
    file the producer never wrote, and drops the directory while doing it.
    """
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "clip.mp4").write_bytes(b"")
    (tmp_path / "b" / "clip.mp4").write_bytes(b"")
    plan = [
        _convert("a/clip.mp4", "clip.mp4"),
        {"tool": "create_thumbnail", "args": {"inputs": ["b/clip.mp4"]}},
    ]
    plan, subs = rebind_colliding_outputs(plan, tmp_path)
    assert subs, "a/clip.mp4 -> a/clip.mp4 is a self-overwrite and must be renamed"
    assert plan[1]["args"]["inputs"] == ["b/clip.mp4"], "b/ was rebound onto a file in a/"


def test_a_consumer_in_the_same_directory_is_rebound_and_keeps_it(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "clip.mp4").write_bytes(b"")
    plan = [
        _convert("a/clip.mp4", "clip.mp4"),
        {"tool": "create_thumbnail", "args": {"inputs": ["a/clip.mp4"]}},
    ]
    plan, _ = rebind_colliding_outputs(plan, tmp_path)
    assert plan[1]["args"]["inputs"] == ["a/clip_converted.mp4"], "the directory was dropped"


def test_the_disk_check_runs_in_the_directory_the_file_lands_in(tmp_path: Path) -> None:
    """Resolving against the wrong base checks a directory that does not exist.

    Then nothing looks taken, and the "free" name chosen is one already holding a file.
    """
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "clip.mp4").write_bytes(b"")
    (tmp_path / "a" / "clip_converted.mp4").write_bytes(b"precious")
    plan, _ = rebind_colliding_outputs([_convert("a/clip.mp4", "clip.mp4")], tmp_path)
    assert plan[0]["args"]["output"] == "clip_converted_2.mp4"
    assert (tmp_path / "a" / "clip_converted.mp4").read_bytes() == b"precious"


def test_an_output_that_lands_elsewhere_is_not_a_self_overwrite(tmp_path: Path) -> None:
    """An output resolves against the INPUT'S PARENT, not the sandbox — so it can escape it.

    `inputs=["a/clip.mp4"], output="a/clip.mp4"` renders to `<sandbox>/a/a/clip.mp4`, which
    is not the input at all. Treating the two as one base would invent a collision here.
    """
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "clip.mp4").write_bytes(b"")
    plan, subs = rebind_colliding_outputs([_convert("a/clip.mp4", "a/clip.mp4")], tmp_path)
    assert subs == []
    assert plan[0]["args"]["output"] == "a/clip.mp4"
