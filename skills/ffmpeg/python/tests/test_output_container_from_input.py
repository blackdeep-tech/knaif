"""An operation that keeps its input's container names the output after the input's extension.

`reverse_video` (and `adjust_volume`/`adjust_speed` on an audio-only file) write the same kind of
file they read. Both took that container from ffprobe's `format_name` — keeping the first entry
of a comma list — and used it as the output EXTENSION. ffprobe's names are demuxer names, not
extensions: `.mkv` and `.webm` both probe as `matroska,webm`, so a reverse of `clip.mkv` wrote
`clip_reversed.matroska` and ffmpeg failed with "Unable to choose an output format" (native,
workbench, 2026-09-23). `.mp4` and `.m4a` probe as `mov,mp4,m4a,…` and came back as `.mov`.

The input's own extension is the answer the user can see; the probe name is only a fallback for
an extensionless file, mapped to a real extension. Mirrored in skills/ffmpeg/native/src/engine.rs.
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


def _probe(name: str, format_name: str, *, video: bool = True) -> dict:
    # `container` is what `_summarise_probe` stores: the first entry of ffprobe's format_name.
    return {
        "file": name,
        "container": format_name.split(",")[0],
        "duration": 2.0,
        "width": 1280 if video else None,
        "height": 720 if video else None,
        "video_codec": "h264" if video else None,
        "audio_codec": "aac",
        "has_audio": True,
    }


@pytest.mark.parametrize(
    ("name", "format_name", "expected"),
    [
        ("clip.mkv", "matroska,webm", "clip_reversed.mkv"),
        ("clip.webm", "matroska,webm", "clip_reversed.webm"),
        ("clip.mp4", "mov,mp4,m4a,3gp,3g2,mj2", "clip_reversed.mp4"),
        ("clip.mov", "mov,mp4,m4a,3gp,3g2,mj2", "clip_reversed.mov"),
        ("clip.MKV", "matroska,webm", "clip_reversed.mkv"),
    ],
)
def test_reverse_keeps_the_input_extension(engine, name, format_name, expected):
    recipe = engine._build_one_recipe(_probe(name, format_name), None, None, {"mode": "reverse"})
    assert Path(recipe["output"]).name == expected


def test_an_extensionless_input_maps_the_probe_name_to_an_extension(engine):
    recipe = engine._build_one_recipe(
        _probe("clip", "matroska,webm"), None, None, {"mode": "reverse"}
    )
    assert Path(recipe["output"]).name == "clip_reversed.mkv"


def test_an_explicit_container_still_wins(engine):
    recipe = engine._build_one_recipe(
        _probe("clip.mkv", "matroska,webm"), None, None, {"mode": "reverse", "container": "mp4"}
    )
    assert Path(recipe["output"]).name == "clip_reversed.mp4"


def test_audio_only_volume_keeps_m4a(engine):
    recipe = engine._build_one_recipe(
        _probe("song.m4a", "mov,mp4,m4a,3gp,3g2,mj2", video=False),
        None,
        None,
        {"mode": "adjust_volume", "level": "2.0"},
    )
    assert Path(recipe["output"]).suffix == ".m4a"
    assert recipe["container"] == "m4a"


def test_reversing_a_webm_encodes_vp9_not_h264(engine):
    """Keeping `.webm` exposed the encoder: `-c:v libx264` into webm is refused by ffmpeg
    ("Conversion failed!", verified on a real VP9 clip 2026-09-23). The remux guard already
    swapped the encoder for webm; an encoding operation did not."""
    probe = _probe("clip.webm", "matroska,webm")
    probe["video_codec"] = "vp9"
    probe["audio_codec"] = "opus"

    recipe = engine._build_one_recipe(probe, None, None, {"mode": "reverse"})

    assert recipe["video"]["encoder"] == "libvpx-vp9"
    assert recipe["audio"]["codec"] == "libopus"


def test_an_explicit_video_encoder_is_left_alone(engine):
    probe = _probe("clip.webm", "matroska,webm")
    recipe = engine._build_one_recipe(
        probe, None, None, {"mode": "reverse", "video_encoder": "libaom-av1"}
    )
    assert recipe["video"]["encoder"] == "libaom-av1"


# ── editing operations keep the container; delivery operations do not ──────────────────────
#
# Owner decision 2026-09-23. "convert clip.mp4 to mkv then … crop it" came back as an .mp4:
# every operation with no `output` defaulted to mp4, undoing the conversion the user asked
# for. An edit (trim/resize/rotate/strip/speed/volume/reverse) now keeps what it was given;
# compress and prepare_for_platform are about delivery and stay mp4.

_EDITS = [
    {"mode": "trim", "start": "1", "end": "2"},
    {"mode": "resize", "width": 320, "height": 200},
    {"mode": "rotate", "angle": 90},
    {"mode": "strip_audio"},
    {"mode": "adjust_speed", "speed": 2.0},
    {"mode": "adjust_volume", "level": "2.0"},
]


@pytest.mark.parametrize("options", _EDITS, ids=lambda o: o["mode"])
@pytest.mark.parametrize("name", ["clip.mkv", "clip.mov", "clip.avi"])
def test_an_edit_keeps_the_input_container(engine, options, name):
    recipe = engine._build_one_recipe(_probe(name, "matroska,webm"), None, None, dict(options))
    assert Path(recipe["output"]).suffix == Path(name).suffix
    assert recipe["container"] == Path(name).suffix.lstrip(".")
    # mp4-only muxer flag must not follow the rename into another container.
    assert recipe["faststart"] is False


@pytest.mark.parametrize("options", _EDITS, ids=lambda o: o["mode"])
def test_an_edit_of_a_webm_stays_webm_with_legal_codecs(engine, options):
    recipe = engine._build_one_recipe(
        _probe("clip.webm", "matroska,webm"), None, None, dict(options)
    )
    assert Path(recipe["output"]).suffix == ".webm"
    assert recipe["video"]["encoder"] in ("libvpx-vp9", "copy")
    # strip_audio carries no audio section at all.
    assert (recipe.get("audio") or {}).get("codec") in ("libopus", "libvorbis", "copy", None)


def test_an_edit_of_an_ogg_falls_back_to_mp4(engine):
    """ogg video is theora-only and vocab has no theora encoder, so keeping it would fail."""
    recipe = engine._build_one_recipe(
        _probe("clip.ogg", "ogg"), None, None, {"mode": "resize", "width": 320}
    )
    assert recipe["container"] == "mp4"


def test_an_explicit_output_extension_decides_the_container(engine):
    """`trim … output=clip_trimmed.mkv` rendered `-movflags +faststart` into an mkv, because
    the recipe still believed it was writing mp4."""
    recipe = engine._build_one_recipe(
        _probe("clip.mp4", "mov,mp4,m4a,3gp,3g2,mj2"),
        None,
        None,
        {"mode": "trim", "start": "1", "end": "2", "output_path": "clip_trimmed.mkv"},
    )
    assert recipe["container"] == "mkv"
    assert recipe["faststart"] is False


@pytest.mark.parametrize("mode", ["compress"])
def test_a_delivery_operation_still_defaults_to_mp4(engine, mode):
    recipe = engine._build_one_recipe(
        _probe("clip.mkv", "matroska,webm"), None, None, {"mode": mode}
    )
    assert recipe["container"] == "mp4"
