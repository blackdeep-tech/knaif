"""Argument values the model proposes that the skill cannot read — and should.

T6b's `codec` and `batch` regressions were traced to four deterministic gaps rather than
to the model. In each case the proposed value is a real-world-correct way to say the thing;
the skill simply has no vocabulary for it and passes the token to ffmpeg, which fails at
execution time. Two of them were additionally scored *correct* on the control arm, because
the rows carry no artifact criteria and a wrong artifact still counts as "a plan ran".

1. `video_codec: "hvc1"` — the ISO-BMFF/Apple sample-entry tag for HEVC. `vocab.yaml`
   already carries two spellings per codec (`h264`/`avc`, `hevc`/`h265`); the container-tag
   family was simply missing.
2. `inputs: ["*"]` — `ResolveInputs` already accepts an `extensions` filter and `vocab.yaml`
   already lists the media extensions; no ffmpeg call site connected them, so a bare glob
   swept in every non-media file in the sandbox.
3. `start: "-2s"` — ffmpeg itself accepts `5s`/`-2s`, and `_normalize_trim` already guards a
   reversed range. `_timestamp_seconds` could read neither the unit suffix nor a leading
   sign (`float("-00") * 60 == -0.0`, so `-00:00:02` parsed as **+2.0**), which left that
   guard reading the wrong number or nothing at all.
4. `at_time: "last_frame"` — the schema has no way to say "the last frame", and the model
   cannot compute it because it does not know the duration. The engine probes the duration,
   so this is the one layer that *can* resolve it.

See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T6b analysis.
"""

from __future__ import annotations

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


# ── 1. codec container tags ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "token,encoder",
    [
        ("hvc1", "libx265"),  # HEVC in MP4 (Apple); what T6b emitted
        ("hev1", "libx265"),  # HEVC in MP4 (the other sample-entry form)
        ("avc1", "libx264"),  # H.264 in MP4
        ("vp09", "libvpx-vp9"),
        ("av01", "libsvtav1"),
    ],
)
def test_iso_bmff_codec_tags_map_to_encoders(vocab, token, encoder):
    """A container sample-entry tag names the same codec as its short token."""
    assert vocab["video_encoder_map"].get(token) == encoder


def test_existing_codec_tokens_are_unchanged(vocab):
    """The tags are additions; nothing already mapped may move."""
    m = vocab["video_encoder_map"]
    assert m["h264"] == "libx264"
    assert m["avc"] == "libx264"
    assert m["hevc"] == "libx265"
    assert m["h265"] == "libx265"
    assert m["vp9"] == "libvpx-vp9"
    assert m["av1"] == "libsvtav1"


# ── 3. timestamps: unit suffixes and signs ───────────────────────────────────
@pytest.mark.parametrize(
    "value,seconds",
    [
        ("5s", 5.0),  # ffmpeg accepts this spelling
        ("-2s", -2.0),  # "the last 2 seconds"; what T6b emitted
        ("0s", 0.0),
        ("500ms", 0.5),
        ("2.5s", 2.5),
        ("-00:00:02", -2.0),  # sign was lost -> parsed as +2.0
        ("-00:00:00", 0.0),
        ("00:00:05", 5.0),  # unchanged
        ("5", 5.0),  # unchanged
        ("2.5", 2.5),  # unchanged
        ("01:30", 90.0),  # unchanged
    ],
)
def test_timestamp_seconds_reads_units_and_signs(engine, value, seconds):
    assert engine._timestamp_seconds(value) == pytest.approx(seconds)


@pytest.mark.parametrize("value", ["last_frame", "", "abc", None, "::"])
def test_timestamp_seconds_still_rejects_non_times(engine, value):
    assert engine._timestamp_seconds(value) is None


@pytest.mark.parametrize(
    "start,end",
    [("-2s", "0s"), ("-00:00:02", "-00:00:00"), ("-2s", None)],
)
def test_negative_start_is_a_from_end_offset_not_a_reversed_range(engine, start, end):
    """ "The last 2 seconds" is a 2-second request, not a degenerate one.

    Both models spelled it; neither spelling survived. `-2s`/`0s` was unreadable and reached
    ffmpeg verbatim, while `-00:00:02` lost its sign, read as +2.0, tripped the reversed-range
    guard and silently became a ONE-FRAME image that the corpus scored 1.0.
    """
    got = engine._normalize_trim(start=start, duration=None, end=end, frames=None)
    assert got["start_from_end"] == pytest.approx(-2.0)
    assert got["frames"] is None, "a 2-second request must not collapse to one frame"
    assert got["end"] is None


def test_genuinely_reversed_range_still_collapses_to_one_frame(engine):
    """The existing guard must survive the from-end change."""
    got = engine._normalize_trim(start=5, duration=None, end=2, frames=None)
    assert got["frames"] == 1


def test_ordinary_range_is_untouched(engine):
    got = engine._normalize_trim(start="00:00:01", duration=None, end="00:00:05", frames=None)
    assert got == {"start": "00:00:01", "duration": None, "end": "00:00:05", "frames": None}


# ── 4. symbolic times resolved against the probed duration ───────────────────
@pytest.mark.parametrize("token", ["last_frame", "last", "end"])
def test_symbolic_at_time_resolves_to_the_end_of_the_clip(engine, token):
    """ "The last frame" is only computable here — the model does not know the duration."""
    resolved = engine._resolve_at_time(token, duration=12.0)
    assert resolved is not None
    assert 11.0 <= float(resolved) < 12.0, f"{token} -> {resolved!r}"


@pytest.mark.parametrize("token", ["middle", "midpoint", "halfway"])
def test_symbolic_middle_resolves_to_half_the_duration(engine, token):
    """ "The middle" is as computable as "the end", and only here."""
    assert float(engine._resolve_at_time(token, duration=12.0)) == pytest.approx(6.0)


@pytest.mark.parametrize("token", ["first", "start", "beginning"])
def test_symbolic_start_tokens_resolve_to_zero(engine, token):
    assert float(engine._resolve_at_time(token, duration=12.0)) == 0.0


def test_numeric_at_time_is_left_alone(engine):
    assert engine._resolve_at_time("00:00:03", duration=12.0) == "00:00:03"
    assert engine._resolve_at_time(4, duration=12.0) == 4


def test_unknown_at_time_token_is_not_invented(engine):
    """An unreadable token must not silently become a number."""
    assert engine._resolve_at_time("banana", duration=12.0) is None


# ── 2. globs stay inside media ───────────────────────────────────────────────
def test_media_extensions_cover_video_and_audio(vocab):
    media = set(vocab["media_extensions"])
    assert set(vocab["video_containers"]) <= media
    assert set(vocab["audio_ext_codec"]) <= media
    assert "txt" not in media and "json" not in media and "py" not in media


# ── 5. a batch output that names a destination, not a file ───────────────────
def _thumb_probe(name: str = "clip.mp4") -> dict:
    return {
        "file": str(Path("/sb") / name),
        "container": "mp4",
        "duration": 10.0,
        "size_bytes": None,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
    }


@pytest.mark.parametrize("stem", ["clip", "holiday"])
def test_glob_output_binds_the_input_stem(engine, stem):
    """`output: "videos/*.mp4"` is per-file, not one literal filename.

    A batch writes one file per input, so a `*` in the output is the model saying "same name,
    over there". Passed through verbatim it reached ffmpeg as a literal `*`.
    """
    recipe = engine._build_one_recipe(
        _thumb_probe(f"{stem}.mp4"),
        None,
        None,
        {"mode": "convert", "container": "mp4", "output_path": "videos/*.mp4"},
    )
    out = Path(recipe["output"])
    assert out.name == f"{stem}.mp4", recipe["output"]
    assert out.parent.name == "videos"


def test_extensionless_output_is_a_directory(engine):
    """`output: "videos_hevc"` names a destination directory.

    ffmpeg cannot write an extensionless file without an explicit `-f`; left alone it failed
    with "Error initializing the muxer ... Invalid argument".
    """
    recipe = engine._build_one_recipe(
        _thumb_probe("clip.mp4"),
        None,
        None,
        {"mode": "convert", "container": "mkv", "output_path": "videos_hevc"},
    )
    out = Path(recipe["output"])
    assert out.name == "clip.mkv", recipe["output"]
    assert out.parent.name == "videos_hevc"


def test_a_real_output_filename_is_still_honoured(engine):
    """The single-file case must not move."""
    recipe = engine._build_one_recipe(
        _thumb_probe("clip.mp4"),
        None,
        None,
        {"mode": "convert", "container": "mp4", "output_path": "renamed.mp4"},
    )
    assert Path(recipe["output"]).name == "renamed.mp4"


def test_derived_output_is_still_used_when_none_given(engine):
    recipe = engine._build_one_recipe(
        _thumb_probe("clip.mp4"),
        None,
        None,
        {"mode": "convert", "container": "mp4"},
    )
    assert Path(recipe["output"]).name == "clip_converted.mp4"
