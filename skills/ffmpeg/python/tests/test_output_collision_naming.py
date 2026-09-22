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

    Asserted without naming a side: *that* the truncation is gone is this test's subject, and
    *which* of the two names moves is settled by the direction tests at the end of the file.
    """
    (tmp_path / "clip.mp4").write_bytes(b"")
    plan = [
        {"tool": "trim_video", "args": {"input": "clip.mp4", "output": "clip_trimmed.mp4"}},
        _convert("clip_trimmed.mp4", "clip_trimmed.mp4"),
    ]
    plan, subs = rebind_colliding_outputs(plan, tmp_path)
    assert plan[1]["args"]["output"] != plan[1]["args"]["inputs"][0], "still truncates its input"
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


# ── the base an output resolves against is per-tool, not universal ───────────


def _concat(sandbox: Path, **args):
    plan = [{"tool": "concat_video", "args": dict(args)}]
    out = rebind_colliding_outputs(plan, sandbox)
    steps = out[0] if isinstance(out, tuple) else out
    return steps[0]["args"].get("output")


def _two_inputs(sandbox: Path) -> None:
    sub = sandbox / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    for name in ("a.mp4", "b.mp4"):
        (sub / name).write_bytes(b"x")


def test_concat_output_colliding_with_an_input_is_still_renamed(tmp_path: Path) -> None:
    """`concat_video` resolves its output against the SANDBOX, not the first input's parent.

    Every other mode goes through `_build_one_recipe`, which resolves a relative output
    against `input_path.parent`. `RunConcatStep` resolves it against `ctx.sandbox`. Assuming
    the first rule for both put the comparison in a directory concat never writes to, so
    joining `sub/a.mp4` and `sub/b.mp4` into `sub/a.mp4` compared `<sandbox>/sub/sub/a.mp4`
    against the inputs, found no collision, and left the plan to truncate its own input under
    `-y` — the exact failure this module exists to prevent.
    """
    _two_inputs(tmp_path)
    got = _concat(tmp_path, inputs=["sub/a.mp4", "sub/b.mp4"], output="sub/a.mp4")
    assert got != "sub/a.mp4", "the output still names one of its own inputs"


def test_concat_output_that_collides_with_nothing_is_left_alone(tmp_path: Path) -> None:
    """The same wrong base in the other direction: a spurious rename.

    `a.mp4` at the sandbox root collides with nothing — the inputs are in `sub/`. Resolving
    it against the first input's parent made it `<sandbox>/sub/a.mp4`, which IS an input, so
    a perfectly good destination was renamed to `a_converted.mp4` and the user got a file
    they did not ask for.
    """
    _two_inputs(tmp_path)
    assert _concat(tmp_path, inputs=["sub/a.mp4", "sub/b.mp4"], output="a.mp4") == "a.mp4"


def test_a_per_file_mode_still_resolves_against_its_input(tmp_path: Path) -> None:
    """The control: convert_video's output IS relative to its input's directory."""
    _two_inputs(tmp_path)
    plan = [{"tool": "convert_video", "args": {"inputs": ["sub/a.mp4"], "output": "a.mp4"}}]
    out = rebind_colliding_outputs(plan, tmp_path)
    steps = out[0] if isinstance(out, tuple) else out
    assert (
        steps[0]["args"]["output"] != "a.mp4"
    ), "sub/a.mp4 -> a.mp4 resolves to the input itself for a per-file mode"


# -- which name moves: the intermediate, or the destination --------------------
#
# A self-overwrite has two shapes, and they do not want the same repair.
#
#   ffmpeg_175   `convert clip.mp4 -> clip.mp4`, where `clip.mp4` is a file on disk. Nothing
#                in the plan wrote it, so the output is the only name that CAN move.
#
#   a chain      `trim -> Test1.mov` feeding `convert Test1.mov -> Test1.mov`. Here the name
#                the user asked for belongs to the LAST step of the branch — that is the file
#                they described ("cut, then convert to mov lossless, name it Test1"). The
#                intermediate is the file nobody named. Moving the destination instead leaves
#                the user's name on the un-converted trim output, which is the wrong content
#                under the right name: a silent wrong answer in place of a loud failure.
#
# The first shape is settled above. These cover the second.


def _trim(src: str, out: str) -> dict:
    return {"tool": "trim_video", "args": {"input": src, "start": "1", "end": "3", "output": out}}


def test_a_chained_intermediate_moves_and_the_destination_keeps_its_name(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"")
    plan = [_trim("clip.mp4", "Test1.mov"), _convert("Test1.mov", "Test1.mov")]
    plan, subs = rebind_colliding_outputs(plan, tmp_path)

    assert plan[1]["args"]["output"] == "Test1.mov", "the user's name left the file they named"
    intermediate = plan[0]["args"]["output"]
    assert intermediate != "Test1.mov"
    assert plan[1]["args"]["inputs"] == [intermediate], "the chain came unlinked"
    assert subs == [
        {"step": "trim_video", "requested": "Test1.mov", "used": intermediate},
    ]


def test_a_step_after_the_destination_still_reads_the_destination(tmp_path: Path) -> None:
    """Rebinding is scoped to the steps BETWEEN producer and consumer, inclusive.

    After the consumer, `Test1.mov` means what the consumer wrote — it still exists, under
    the name it was always going to have. Rewriting those references onto the intermediate
    would feed the concat the un-converted file.
    """
    (tmp_path / "clip.mp4").write_bytes(b"")
    plan = [
        _trim("clip.mp4", "Test1.mov"),
        _convert("Test1.mov", "Test1.mov"),
        {"tool": "create_thumbnail", "args": {"inputs": ["Test1.mov"]}},
    ]
    plan, _ = rebind_colliding_outputs(plan, tmp_path)
    assert plan[2]["args"]["inputs"] == ["Test1.mov"]


def test_the_two_branch_chain_that_found_this(tmp_path: Path) -> None:
    """The whole utterance, as the model planned it.

    "take clip.mp4, remove its audio, cut from 1-3s then convert to mov with lossless quality
    and make a duplicate file with name Test1, cut from 3-6s then convert to mov with visually
    lossless quality and make duplicate with file name Test2, at the end combine Test1 and
    Test2 to file with name Test3"

    `trim_video` takes neither `container` nor `quality`, so the trim->convert chain is forced,
    not a model error. What the model got wrong is one thing only: it put the branch's final
    name on both of its steps. Test1, Test2 and Test3 must all end up holding what was asked
    for, and the concat must read the converted pair rather than the trims.
    """
    (tmp_path / "clip.mp4").write_bytes(b"")
    plan = [
        {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "clip_silent.mp4"}},
        _trim("clip_silent.mp4", "Test1.mov"),
        _convert("Test1.mov", "Test1.mov"),
        _trim("clip_silent.mp4", "Test2.mov"),
        _convert("Test2.mov", "Test2.mov"),
        {
            "tool": "concat_video",
            "args": {"base": "Test1.mov", "append": ["Test2.mov"], "output": "Test3.mov"},
        },
    ]
    plan, _ = rebind_colliding_outputs(plan, tmp_path)

    assert plan[2]["args"]["output"] == "Test1.mov"
    assert plan[4]["args"]["output"] == "Test2.mov"
    assert plan[5]["args"] == {
        "base": "Test1.mov",
        "append": ["Test2.mov"],
        "output": "Test3.mov",
    }, "the concat must join the converted files, under the names the user chose"
    # And no step writes what it reads — the condition ffmpeg refuses outright.
    for step in plan:
        args = step["args"]
        reads = {args.get("input"), *(args.get("inputs") or []), args.get("base")}
        assert args.get("output") not in reads, step
