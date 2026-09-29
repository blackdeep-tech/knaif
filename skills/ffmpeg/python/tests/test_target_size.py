"""`target_size_mb` — declared, threaded through both runtimes, and until now never rendered.

`compress_video` accepts `target_size_mb`; `tools.yaml` declares it, `prompt.yaml` shows it in
a worked example, `intents.py` reads it, `_build_one_recipe` copies it into the recipe, and the
Rust `Options`/`Recipe` structs mirror it. It reached `_build_flags` and stopped: measured
2026-09-16, `target_size_mb=0.5` and `target_size_mb=20` rendered byte-identical flags. An arg
that is accepted and silently ignored is the worst of the available behaviours.

**A size target is a CEILING, not a target.** This is the decision the implementation turns on.
Encoding to a fixed `-b:v` derived from the target would *inflate* a clip that is already small:
`email.yaml` declares `default_target_size_mb: 20`, and a 10-second clip that compresses to
200 KB under CRF would become a 20 MB file — a regression dressed as a feature. So the target
becomes a **capped-CRF** bound: `-crf` still drives quality, and `-maxrate`/`-bufsize` stop the
result exceeding the size that was asked for. Small inputs stay small; large ones get capped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def engine():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + "._engine"]


def _probe(duration: float | None = 10.0) -> dict:
    return {
        "file": "clip_ctr.mp4",
        "container": "mp4",
        "duration": duration,
        "size_bytes": 1658334,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
    }


_QUALITY = {"video_crf": 28, "encoder_preset": "medium", "audio_bitrate": "96k"}


def _flags(engine, *, target=None, duration=10.0) -> list[str]:
    options = {"mode": "compress"}
    if target is not None:
        options["target_size_mb"] = target
    recipe = engine._build_one_recipe(_probe(duration), None, _QUALITY, options)
    return recipe["post_input_flags"]


def test_a_size_target_reaches_the_command(engine):
    """The defect itself: 0.5 and 20 used to render byte-identical flags."""
    assert _flags(engine, target=0.5) != _flags(engine, target=20.0)


def test_the_cap_is_a_ceiling_not_a_rewrite_of_quality(engine):
    """`-crf` survives. Replacing it with `-b:v` would inflate an already-small clip.

    `email.yaml` sets `default_target_size_mb: 20`; a clip that compresses to 200 KB must not
    become a 20 MB file because a cap was named.
    """
    flags = _flags(engine, target=0.5)
    assert "-crf" in flags, flags
    assert "-maxrate" in flags and "-bufsize" in flags, flags


def test_the_cap_leaves_room_for_the_audio_and_the_container(engine):
    """0.5 MiB over 10s, minus 96k of audio, minus overhead.

    4_194_304 bits / 10s = 419_430 bps total; 95% of that is 398_458; less 96_000 of audio
    leaves 302_458 bps, floored to whole kbit so the cap can only ever be conservative.
    """
    flags = _flags(engine, target=0.5)
    assert flags[flags.index("-maxrate") + 1] == "302k", flags


def test_bufsize_equals_maxrate_because_the_usual_2x_overshoots(engine):
    """Measured, not stylistic. At `bufsize = 2 x maxrate` a 1 MiB cap produced 1027 KB.

    A two-second rate-control window lets x264 exceed the average for long enough to break the
    ceiling. At 1x, all twelve fixture/target combinations in the 2026-09-16 sweep landed under,
    worst case 93.5% of the cap.
    """
    flags = _flags(engine, target=0.5)
    assert flags[flags.index("-bufsize") + 1] == "302k", flags


def test_no_target_renders_exactly_what_it_rendered_before(engine):
    plain = _flags(engine)
    assert "-maxrate" not in plain and "-bufsize" not in plain, plain


def test_an_unknown_duration_falls_back_rather_than_guessing(engine):
    """Duration is what converts a size into a bitrate. Without it there is no cap to compute.

    Falling back to plain CRF is the honest answer; inventing a duration would produce a cap
    that means nothing, and raising would fail a request that is otherwise perfectly valid.
    """
    flags = _flags(engine, target=0.5, duration=None)
    assert "-maxrate" not in flags, flags
    assert "-crf" in flags, flags


def test_a_target_too_small_for_its_own_audio_is_refused(engine):
    """10s of 96k audio is ~117 KB. A 0.05 MiB (51 KB) ceiling cannot hold it.

    Silently emitting a negative or floored-to-zero bitrate would produce a file several times
    the requested size while reporting success — the exact failure this whole change exists to
    remove. The request is impossible, so it says so.
    """
    with pytest.raises(ValueError, match="target_size_mb"):
        _flags(engine, target=0.05)


# ── the arithmetic is a cross-runtime contract ───────────────────────────────

#: Mirrored verbatim by `size_cap_table_matches_python` in `skills/ffmpeg/native/src/engine.rs`.
#: Float division and a floor are exactly where two runtimes drift apart without either being
#: obviously wrong, and the flags they produce are compared byte-for-byte at L3.
_CAP_TABLE = [
    (0.5, 10.0, "96k", 302),
    (20.0, 8.0, "128k", 19794),
    (1.0, 30.0, "96k", 169),
    (0.25, 5.0, "64k", 334),
    (2.0, 120.0, None, 132),
    (1.0, 7.3, "192k", 899),
    (100.0, 3600.0, "128k", 93),
]


@pytest.mark.parametrize("target,duration,audio,expected", _CAP_TABLE)
def test_the_cap_arithmetic_is_pinned(engine, target, duration, audio, expected):
    assert engine._size_cap_kbit(target, duration, audio) == expected


@pytest.mark.parametrize(
    "value,bps",
    [("96k", 96000), ("1.5M", 1500000), ("128000", 128000), (None, 0), ("garbage", 0)],
)
def test_bitrate_parsing_is_pinned(engine, value, bps):
    """An unreadable bitrate budgets nothing for audio, which makes the cap tighter, not looser."""
    assert engine._parse_bitrate_bps(value) == bps


# ── the profile default stays unwired, on purpose ────────────────────────────


def test_a_platform_profile_default_does_not_become_a_cap(engine):
    """`email.yaml` declares `default_target_size_mb: 20` and it must NOT reach the encode.

    Measured 2026-09-16 before deciding: a 20 MiB ceiling gives a 20-minute video 36 kbit/s and
    a 27-minute one 2 kbit/s, and past roughly 28 minutes it cannot hold 96k of audio at all —
    so `_size_cap_kbit` would refuse, turning a `plan` into an `error` for a size the user never
    asked for.

    The line is between a requirement and an aspiration. An explicit `target_size_mb` is the
    user saying "under 500 KB", and refusing an impossible one beats silently producing 3 MB. A
    profile default is a hint attached to a destination and must never refuse on its own
    authority.

    This guard exists because **no eval can catch it**: every corpus fixture is 5-10 seconds,
    the range where the cap is inert (a non-binding `-maxrate` leaves the decoded video
    bit-identical and the file 70 bytes larger). Wiring it would look free on the corpus and
    break real video. If you are here because you wired it, add a bitrate floor first.
    """
    import yaml

    profile = yaml.safe_load(
        (FFMPEG_SKILL_DIR / "profiles" / "platforms" / "email.yaml").read_text(encoding="utf-8")
    )
    assert profile["default_target_size_mb"] == 20, "the profile still declares a cap"

    recipe = engine._build_one_recipe(
        _probe(), profile, _QUALITY, {"mode": "platform", "platform": "email"}
    )
    flags = recipe["post_input_flags"]
    assert "-maxrate" not in flags, f"the profile default became a hard cap: {flags}"
    # A guard that cannot fail is the thing it is guarding against: the same profile WITH an
    # explicit target does cap, so this is testing the wiring and not just a quiet code path.
    with_explicit = engine._build_one_recipe(
        _probe(),
        profile,
        _QUALITY,
        {"mode": "compress", "target_size_mb": 20},
    )
    assert "-maxrate" in with_explicit["post_input_flags"]
    assert recipe.get("target_size_mb") is None, recipe.get("target_size_mb")
