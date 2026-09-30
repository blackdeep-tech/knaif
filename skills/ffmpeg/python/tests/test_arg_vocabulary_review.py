"""Defects an external review found in the argument-vocabulary commit (b311af6).

Each was verified against the code before being written here; the batch collision and the
formatting divergence were reproduced directly, and the mkdir traversal is POSIX-only
(Windows `CreateDirectory` normalises `..` lexically, so it does not reproduce here - which
is precisely why it needs a test rather than a manual check).

Ordered by severity:

1. **Data loss.** `inputs=["clip.mp4","clip.mov"], output="converted"` rendered
   `converted/clip.mp4` for BOTH, and ffmpeg runs with `-y`, so the second conversion
   silently destroyed the first. The destination expansion dropped the one thing that
   distinguished the two inputs.
2. **Parity.** The media-extension filter was added to Python's 13 call sites and never
   ported to native, so a bare `*` still sweeps non-media there.
3. **Sandbox.** The output's parent was created from the UNRESOLVED path, so a destination
   spelled `../escaped/../sb/out.mp4` passes containment (it resolves inside) while its
   unnormalised parent can create a directory outside on POSIX.
4. **Parity.** `_format_seconds` used `:g` (6 significant digits) against Rust's plain
   float display, and emitted scientific notation (`-1e-06`) that ffmpeg cannot parse.
5. **Silent constraint loss.** The from-end branch discarded a supplied `end`/`duration`
   instead of refusing an unreadable one.
6. **Coverage.** The extension allowlist omitted ordinary media (mxf, ts, mpeg, mts, aif).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def engine():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + "._engine"]


@pytest.fixture(scope="module")
def vocab():
    return yaml.safe_load((FFMPEG_SKILL_DIR / "vocab.yaml").read_text(encoding="utf-8"))


# ── 1. a batch destination must not collapse two inputs onto one output ──────
def _recipe(inp: str, out: str) -> dict:
    return {"input": inp, "output": out}


def test_destination_keeps_inputs_apart_when_stems_collide(engine):
    """`clip.mp4` and `clip.mov` into one destination are two files, not one.

    Both rendered `converted/clip.mp4`; ffmpeg's `-y` then overwrote the first result with
    the second. Nothing upstream caught it: the collision handler excludes extensionless
    destinations and runs before these paths exist. Only the batch can see the clash, so
    that is where it is resolved.
    """
    got = engine.disambiguate_outputs(
        [
            _recipe("/sb/clip.mp4", "/sb/converted/clip.mp4"),
            _recipe("/sb/clip.mov", "/sb/converted/clip.mp4"),
        ]
    )
    outs = [r["output"] for r in got]
    assert outs[0] != outs[1], f"both inputs render {outs[0]}"
    assert "mov" in outs[1], outs[1]


def test_a_lone_input_keeps_its_plain_name(engine):
    """No clash, no suffix — `clip.mp4` alone stays `clip.mp4`."""
    got = engine.disambiguate_outputs([_recipe("/sb/clip.mp4", "/sb/converted/clip.mkv")])
    assert got[0]["output"].endswith("clip.mkv"), got


def test_three_way_collision_terminates(engine):
    got = engine.disambiguate_outputs(
        [
            _recipe("/sb/a/clip.mp4", "/sb/out/clip.mp4"),
            _recipe("/sb/b/clip.mp4", "/sb/out/clip.mp4"),
            _recipe("/sb/c/clip.mp4", "/sb/out/clip.mp4"),
        ]
    )
    outs = [r["output"] for r in got]
    assert len(set(outs)) == 3, outs


def test_a_batch_name_says_which_input_produced_it(engine):
    """`audio_mp4_5.mp3` — observed in `2026-09-15_l4-ffmpeg-t8`, from `clip_no_audio.mp4`.

    Six fixture videos extracted to one literal `audio.mp3` produced `audio.mp3`,
    `audio_mov.mp3`, `audio_mp4.mp3`, `audio_mp4_2.mp3` … The suffix was the *source
    extension*, which five of the six share — so it distinguished nothing and the counter did
    all the work. The user is left with six files and no way to tell which input made which.

    The source stem is what differs here, so it is what the name carries.
    """
    got = engine.disambiguate_outputs(
        [
            _recipe(f"/sb/{name}", "/sb/audio.mp3")
            for name in ("clip.mov", "clip2.mp4", "clip_4k.mp4", "clip_no_audio.mp4")
        ]
    )
    outs = [Path(r["output"]).name for r in got]
    assert len(set(outs)) == 4, outs
    assert outs[0] == "audio.mp3", outs
    for name, out in zip(("clip2", "clip_4k", "clip_no_audio"), outs[1:], strict=True):
        assert out == f"audio_{name}.mp3", outs
    assert not any(
        re.search(r"_\d+\.", o) for o in outs
    ), f"a counter carries no information: {outs}"


# ── 4. one seconds format, both runtimes ─────────────────────────────────────
@pytest.mark.parametrize(
    "value,rendered",
    [
        (5.0, "5"),
        (-2.0, "-2"),
        (11.9, "11.9"),
        (6.172839, "6.172839"),
        (-1.23456789, "-1.23456789"),
        (-0.000001, "-0.000001"),  # was "-1e-06"; ffmpeg cannot parse that
        (0.5, "0.5"),
    ],
)
def test_seconds_render_without_exponent_or_rounding(engine, value, rendered):
    assert engine._format_seconds(value) == rendered


# ── 5. an unreadable constraint is refused, not dropped ──────────────────────
def test_from_end_keeps_a_real_end_constraint(engine):
    """`start=-10, end=-5` is the ten-to-five-seconds-before-the-end window."""
    got = engine._normalize_trim(start="-10s", duration=None, end="-5s", frames=None)
    assert got.get("end") is not None or got.get("duration") is not None, got


def test_from_end_refuses_an_unreadable_end(engine):
    """`end="banana"` must not be read as "to the end of the clip"."""
    with pytest.raises(ValueError):
        engine._normalize_trim(start="-2s", duration=None, end="banana", frames=None)


def test_zero_duration_still_wins_over_from_end(engine):
    """`start=-2, duration=0` is an empty range; the guard must still see it."""
    got = engine._normalize_trim(start="-2", duration="0", end=None, frames=None)
    assert got["frames"] == 1, got


# ── 6. the allowlist covers ordinary media ───────────────────────────────────
@pytest.mark.parametrize("ext", ["mxf", "ts", "mpeg", "mpg", "mts", "m2ts", "aif", "3gp"])
def test_media_extensions_cover_ordinary_container_formats(vocab, ext):
    assert ext in vocab["media_extensions"]


# ── 7. the output heuristic does not mangle valid filenames ──────────────────
def test_a_dotfile_output_is_a_file_not_a_directory(engine):
    """`.mp4` is a concrete filename; pathlib reports no suffix for a dotfile."""
    got = engine._resolve_output_target(
        ".mp4", input_path=Path("/sb/clip.mp4"), mode="convert", options={"container": "mp4"}
    )
    assert got == Path(".mp4"), got


def test_a_wildcard_in_a_parent_segment_is_refused(engine):
    """`out*/clip.mp4` never entered the wildcard branch and would create a literal `out*`."""
    with pytest.raises(ValueError):
        engine._resolve_output_target(
            "out*/clip.mp4",
            input_path=Path("/sb/clip.mp4"),
            mode="convert",
            options={"container": "mp4"},
        )


def test_a_wildcard_pattern_keeps_the_text_around_it(engine):
    """`prefix_*.mp4` dropped `prefix_` entirely - the `*` stands for the stem, not the name."""
    got = engine._resolve_output_target(
        "prefix_*.mp4",
        input_path=Path("/sb/clip.mp4"),
        mode="convert",
        options={"container": "mp4"},
    )
    assert got.name == "prefix_clip.mp4", got
