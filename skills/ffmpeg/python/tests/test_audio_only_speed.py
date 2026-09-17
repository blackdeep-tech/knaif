"""`adjust_speed` on an audio-only input must stay audio.

`ffmpeg_226` — "pull mp3 from clip.mp4 and apply 0.8x tempo" — planned correctly:
`extract_audio` to `clip.mp3`, then `adjust_speed` on it. The second step rendered a
**video** recipe against a file with no video stream:

    ffmpeg -y -i clip.mp3 -vf setpts=1.25*PTS -af atempo=0.8 \
           -c:v libx264 -crf 23 -preset medium -c:a aac -b:a 128k clip_speed.mp4

`-vf` filters a stream that isn't there, `-c:v libx264` encodes nothing, and the user's mp3
came back as an mp4 with an aac track. **ffmpeg exited 0 and produced a file**, so nothing
failed — the run scored it 0.5 on `audio_codec: expected 'mp3', got 'aac'` and only the
artifact-level criterion caught it at all.

The rule already existed for `adjust_volume`, with a comment saying "Only adjust_volume
currently routes audio-only inputs here". It is the same rule: an audio operation on an audio
file produces an audio file in that file's own format.
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


def _audio_probe(tmp: Path, name: str = "clip.mp3", codec: str = "mp3") -> dict:
    f = tmp / name
    f.write_bytes(b"")
    return {
        "file": str(f),
        "container": name.rsplit(".", 1)[1],
        "video_codec": None,
        "audio_codec": codec,
        "width": None,
        "height": None,
        "duration": 10.0,
    }


def _cmd(engine, probe: dict, tmp: Path, **options) -> list[str]:
    recipe = engine._build_one_recipe(probe, None, None, {"mode": "adjust_speed", **options}, tmp)
    return [str(x) for x in engine._render_command(recipe)]


def test_an_audio_input_keeps_its_container(engine, tmp_path: Path) -> None:
    cmd = _cmd(engine, _audio_probe(tmp_path), tmp_path, speed=0.8)
    assert cmd[-1].endswith(".mp3"), f"wrote {cmd[-1]!r} from an mp3 input"


def test_an_audio_input_is_not_re_encoded_to_aac(engine, tmp_path: Path) -> None:
    cmd = _cmd(engine, _audio_probe(tmp_path), tmp_path, speed=0.8)
    assert "aac" not in cmd, f"mp3 in, aac out: {' '.join(cmd)}"
    assert "libmp3lame" in cmd, f"expected an mp3 encoder, got {' '.join(cmd)}"


def test_an_audio_input_gets_no_video_filter_or_encoder(engine, tmp_path: Path) -> None:
    """`-vf setpts` and `-c:v` address a stream the file does not have."""
    cmd = _cmd(engine, _audio_probe(tmp_path), tmp_path, speed=0.8)
    assert "-vf" not in cmd, f"video filter on an audio file: {' '.join(cmd)}"
    assert "-c:v" not in cmd, f"video encoder on an audio file: {' '.join(cmd)}"


def test_the_tempo_filter_is_still_applied(engine, tmp_path: Path) -> None:
    """The operation itself must survive the fix — this is the whole request."""
    cmd = _cmd(engine, _audio_probe(tmp_path), tmp_path, speed=0.8)
    assert "-af" in cmd and "atempo=0.8" in cmd, " ".join(cmd)


def test_a_flac_input_keeps_flac_and_carries_no_bitrate(engine, tmp_path: Path) -> None:
    """A lossless container must not be handed a bitrate target."""
    cmd = _cmd(engine, _audio_probe(tmp_path, "clip.flac", "flac"), tmp_path, speed=1.5)
    assert cmd[-1].endswith(".flac"), " ".join(cmd)
    assert "-b:a" not in cmd, f"bitrate target on a lossless codec: {' '.join(cmd)}"


def test_a_video_input_is_untouched_by_this(engine, tmp_path: Path) -> None:
    """The control: a real video still gets the video recipe it always had."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"")
    probe = {
        "file": str(f),
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
        "width": 1920,
        "height": 1080,
        "duration": 10.0,
    }
    cmd = _cmd(engine, probe, tmp_path, speed=0.8)
    assert "-vf" in cmd and "setpts=1.25*PTS" in cmd, " ".join(cmd)
    assert cmd[-1].endswith(".mp4"), " ".join(cmd)


@pytest.mark.parametrize("speed", [0.0, -1.0, float("nan"), float("inf")])
def test_nonpositive_or_nonfinite_speed_is_rejected(engine, tmp_path: Path, speed) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        _cmd(engine, _audio_probe(tmp_path), tmp_path, speed=speed)
