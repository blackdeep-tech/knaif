"""`-to` must be an INPUT option, or every "from A to B" trim renders the wrong length.

ffmpeg's `-to` means different things either side of `-i`. As an **input** option it is
absolute in the source timeline. As an **output** option, when `-ss` has already seeked, it is
relative to the seek point — so `-ss 2 -i in.mp4 -to 5` is *five seconds starting at two*, not
the range 2->5.

Measured on a real 10-second file, both forms rendered side by side:

    ffmpeg -ss 00:00:02 -i clip.mp4 -to 00:00:05 ...   -> 5.000s   WRONG
    ffmpeg -ss 00:00:02 -to 00:00:05 -i clip.mp4 ...   -> 3.000s   right

The engine emitted the first form, so **every range trim it has ever produced was wrong** —
and nothing could see it, because no corpus row asserted a duration until `duration_s` was
added. It surfaced the moment that criterion landed: `ffmpeg_004`, `_091`, `_109`, `_119`,
`_140`, `_267` all returned the END TIMESTAMP as their length.

The plan was never at fault. The model emitted `{"start": "00:00:02", "end": "00:00:05"}` and
the corpus gold has always used the correct form; only the renderer disagreed.

Moving `-to` rather than converting it to `-t (end - start)` is deliberate: no arithmetic means
no float formatting to keep in sync across two runtimes, and it is the spelling the golds
already use.
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


def _probe(name="clip.mp4"):
    return {
        "file": name,
        "container": "mp4",
        "duration": 10.0,
        "size_bytes": 1000,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
    }


_Q = {"video_crf": 23, "encoder_preset": "medium", "audio_bitrate": "128k"}


def _flags(engine, options):
    r = engine._build_one_recipe(_probe(), None, _Q, options)
    return r["pre_input_flags"], r["post_input_flags"]


def test_a_range_trim_puts_to_before_the_input(engine):
    """`ffmpeg_004` — "cut clip.mp4 from 2 seconds to 5 seconds" rendered a 5-second clip."""
    pre, post = _flags(engine, {"mode": "trim", "start": "00:00:02", "end": "00:00:05"})
    assert pre == ["-ss", "00:00:02", "-to", "00:00:05"], pre
    assert "-to" not in post, post


def test_extract_audio_has_the_same_bug_and_the_same_fix(engine):
    """`ffmpeg_119` — "just the audio from 3 to 5 seconds" produced five seconds of audio.

    `extract_audio` builds its trim flags in a separate arm, so fixing only the `trim` arm
    would leave this one broken.
    """
    pre, post = _flags(
        engine,
        {"mode": "extract_audio", "start": "00:00:03", "end": "00:00:05", "audio_format": "mp3"},
    )
    assert pre == ["-ss", "00:00:03", "-to", "00:00:05"], pre
    assert "-to" not in post, post


def test_an_end_without_a_start_is_unchanged_in_meaning(engine):
    """With no seek there is nothing for `-to` to be relative to, so both forms agree.

    It still moves, because one spelling of one flag is easier to keep correct than two.
    """
    pre, post = _flags(engine, {"mode": "trim", "end": "00:00:07"})
    assert pre == ["-to", "00:00:07"], pre
    assert "-to" not in post, post


def test_a_duration_still_uses_t_after_the_input(engine):
    """`-t` is a LENGTH, already relative to the seek point, and was never wrong."""
    pre, post = _flags(engine, {"mode": "trim", "start": "00:00:02", "duration": "3"})
    assert pre == ["-ss", "00:00:02"], pre
    assert post[:2] == ["-t", "3"], post


def test_the_rendered_range_is_the_length_that_was_asked_for(engine):
    """The end-to-end claim, on a real file rather than a flag list."""
    pytest.importorskip("subprocess")
    import shutil
    import subprocess
    import tempfile

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    src = FFMPEG_SKILL_DIR.parents[1] / "sandbox" / "fixtures" / "ffmpeg" / "clip.mp4"
    if not src.exists():
        pytest.skip("fixture not generated")
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(src, tmp / "clip.mp4")
    r = engine._build_one_recipe(
        _probe(str(tmp / "clip.mp4")),
        None,
        _Q,
        {"mode": "trim", "start": "00:00:02", "end": "00:00:05"},
    )
    r["output"] = str(tmp / "out.mp4")
    subprocess.run(engine._render_command(r), capture_output=True)
    got = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(tmp / "out.mp4"),
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert abs(float(got) - 3.0) <= 0.5, f"2->5 should be 3s, got {got}s"
    shutil.rmtree(tmp, ignore_errors=True)
