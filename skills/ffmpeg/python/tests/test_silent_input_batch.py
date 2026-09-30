"""A silent video must not fail the batch it happens to be in.

`extract_audio` over a folder builds one command per input and runs them in order. ffmpeg
cannot extract audio from a file that has none — it exits with *Output file does not contain
any stream* / *Error opening output files: Invalid argument* — so one silent video in a folder
of ten takes the whole request down and the user gets nothing.

Observed in `2026-09-15_l4-ffmpeg-t8`: `ffmpeg_134#2` and `ffmpeg_287#1` both fail with
`1 of 7 command(s) failed`, and the failing command is always `clip_no_audio.mp4`. Those two
rows expect `clarify` for unrelated reasons, so repairing this cannot move their score — which
is precisely why it went unfixed: no metric was pointing at it.

**The information needed to avoid it is already in hand.** `inspect_media` probes every input
and records `has_audio`; `_build_one_recipe` then builds a command it can see will fail. This
is a discarded fact, not a missing capability.

Scoped to `extract_audio` deliberately. Measured on a real silent file: `adjust_volume` and
`strip_audio` both succeed on it (ffmpeg treats them as no-ops), so widening the skip would
remove work that currently completes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def steps_mod():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + ".steps"]


def _probe(name: str, *, has_audio: bool) -> dict:
    return {
        "file": f"/sb/{name}",
        "container": "mp4",
        "duration": 10.0,
        "size_bytes": 100000,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac" if has_audio else None,
        "has_audio": has_audio,
    }


class _Ctx:
    sandbox = None
    dry_run = True


def _build(steps_mod, probes, mode="extract_audio"):
    step = steps_mod.BuildRecipesStep()
    return step.handle(
        {
            "probes": {"count": len(probes), "probes": probes},
            "options": {"mode": mode, "audio_format": "mp3"},
        },
        _Ctx(),
    )


def test_a_silent_input_is_skipped_not_built(steps_mod):
    """The batch keeps going; the silent file simply produces no command."""
    out = _build(
        steps_mod,
        [
            _probe("clip.mp4", has_audio=True),
            _probe("clip_no_audio.mp4", has_audio=False),
            _probe("clip2.mp4", has_audio=True),
        ],
    )
    built = [Path(r["input"]).name for r in out["recipes"]]
    assert built == ["clip.mp4", "clip2.mp4"], built


def test_the_skip_is_reported_not_silent(steps_mod):
    """Quietly returning fewer files than asked for is its own bug.

    The user asked for audio from three files and gets two; if nothing says why, the missing
    one reads as a tool that lost a file.
    """
    out = _build(
        steps_mod,
        [
            _probe("clip.mp4", has_audio=True),
            _probe("clip_no_audio.mp4", has_audio=False),
        ],
    )
    assert "skipped" in out, out.keys()
    assert [Path(p).name for p in out["skipped"]] == ["clip_no_audio.mp4"]


def test_a_batch_with_nothing_to_extract_is_an_error(steps_mod):
    """Skipping every input is not success with zero results.

    An empty batch would render no commands, execute cleanly and report done — telling the
    user the work happened. The request cannot be satisfied, so it says so.
    """
    with pytest.raises(ValueError) as e:
        _build(
            steps_mod,
            [
                _probe("a.mp4", has_audio=False),
                _probe("b.mp4", has_audio=False),
            ],
        )
    # Pinned byte-for-byte against `no_audio_error` in `native/src/run.rs`: this message reaches
    # the user on both runtimes, and two spellings of one refusal is how the two drift apart.
    assert str(e.value) == (
        "No audio to extract — none of these files has an audio track: a.mp4, b.mp4."
    )


def test_a_single_silent_input_still_errors_rather_than_vanishing(steps_mod):
    """The one-file case is the all-silent case, and must not degrade into a no-op."""
    with pytest.raises(ValueError) as e:
        _build(steps_mod, [_probe("clip_no_audio.mp4", has_audio=False)])
    assert str(e.value) == "No audio to extract: clip_no_audio.mp4 has no audio track."


def test_other_modes_do_not_skip_silent_inputs(steps_mod):
    """`adjust_volume` and `strip_audio` succeed on a silent file — measured, not assumed.

    Skipping there would remove work that currently completes.
    """
    for mode in ("adjust_volume", "strip_audio"):
        out = _build(steps_mod, [_probe("clip_no_audio.mp4", has_audio=False)], mode=mode)
        assert out["count"] == 1, f"{mode} dropped a silent input"
