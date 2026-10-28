"""A compress never makes a file bigger without saying so (manual test, 2026-10-28).

`clip.mov` (720p, 80 kbit/s video, 69 kbit/s AAC, 157,680 bytes) came out of `compress_video` at
173,676 bytes: the video shrank, but the audio was re-encoded at the profile's fixed 96k, above
what the source had. Two rules follow, one per half of the fix:

* **The audio never goes up.** A source track already at or under the profile's bitrate is
  copied when it is the codec the output wants, else re-encoded at its own bitrate.
  Measured on that clip: 173,606 bytes as shipped, 151,945 with the audio copied.
* **The result is checked.** A compress that is not smaller says so and leaves things as they
  were; one that is smaller says by how much (owner, 2026-10-28).
"""

from __future__ import annotations

import shutil
import subprocess
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


def _probe(audio_codec: str | None = "aac", audio_bitrate: int | None = 69339) -> dict:
    return {
        "file": "clip.mp4",
        "container": "mp4",
        "duration": 8.0,
        "size_bytes": 157680,
        "width": 1280,
        "height": 720,
        "fps": 25.0,
        "video_codec": "h264",
        "audio_codec": audio_codec,
        "audio_bitrate": audio_bitrate,
        "has_audio": audio_codec is not None,
    }


_QUALITY = {"video_crf": 28, "encoder_preset": "slow", "audio_bitrate": "96k"}


def _audio_flags(engine, probe: dict, mode: str = "compress", **options) -> list[str]:
    recipe = engine._build_one_recipe(probe, None, _QUALITY, {"mode": mode, **options})
    flags = recipe["post_input_flags"]
    return flags[flags.index("-c:a") :] if "-c:a" in flags else []


# ── the probe carries the audio bitrate ──────────────────────────────────────


def test_the_probe_reads_the_audio_streams_bitrate(engine):
    probe = engine._summarise_probe(
        Path("clip.mov"),
        {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
                {"codec_type": "audio", "codec_name": "aac", "bit_rate": "69339"},
            ],
            "format": {"format_name": "mov,mp4,m4a", "duration": "8.0", "size": "157680"},
        },
    )
    assert probe["audio_bitrate"] == 69339


def test_a_stream_without_a_bitrate_probes_as_unknown(engine):
    """mkv often leaves `bit_rate` off the stream. Unknown, not zero: zero would read as tiny."""
    probe = engine._summarise_probe(
        Path("clip.mkv"),
        {"streams": [{"codec_type": "audio", "codec_name": "opus"}], "format": {}},
    )
    assert probe["audio_bitrate"] is None


# ── the audio never goes up ──────────────────────────────────────────────────


def test_aac_under_the_profile_is_copied(engine):
    assert _audio_flags(engine, _probe()) == ["-c:a", "copy", "-movflags", "+faststart"]


def test_aac_over_the_profile_is_encoded_at_the_profile(engine):
    flags = _audio_flags(engine, _probe(audio_bitrate=192000))
    assert flags[:4] == ["-c:a", "aac", "-b:a", "96k"], flags


def test_another_codec_under_the_profile_keeps_its_own_bitrate(engine):
    """An mp3 track at 64k cannot be copied into the aac the output wants; at 96k it would grow."""
    flags = _audio_flags(engine, _probe(audio_codec="mp3", audio_bitrate=64000))
    assert flags[:4] == ["-c:a", "aac", "-b:a", "64k"], flags


def test_an_unknown_source_bitrate_renders_what_it_rendered_before(engine):
    """A dry run on a missing file (`_dummy_probe`) and an mkv without stream bitrates."""
    flags = _audio_flags(engine, _probe(audio_bitrate=None))
    assert flags[:4] == ["-c:a", "aac", "-b:a", "96k"], flags


def test_only_compress_holds_the_audio_down(engine):
    """A convert or a platform target has its own reasons for the profile's audio."""
    flags = _audio_flags(engine, _probe(), mode="convert", container="mkv")
    assert flags[:4] == ["-c:a", "aac", "-b:a", "96k"], flags


def test_a_copied_track_is_still_budgeted_in_a_size_cap(engine):
    """With the audio copied there is no `-b:a` to read; the cap budgets the profile's bitrate,
    which is at least the source's, so the ceiling stays a ceiling. Same 302k as an encode."""
    recipe = engine._build_one_recipe(
        {**_probe(), "duration": 10.0}, None, _QUALITY, {"mode": "compress", "target_size_mb": 0.5}
    )
    flags = recipe["post_input_flags"]
    assert flags[flags.index("-maxrate") + 1] == "302k", flags


# ── the result is checked (real ffmpeg) ──────────────────────────────────────

FIXTURES = FFMPEG_SKILL_DIR.parents[1] / "sandbox" / "fixtures" / "ffmpeg"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")


def _starved(path: Path) -> Path:
    """A clip already squeezed past what `small_file` encodes to: CRF 51 video, 32k audio.

    Re-encoding it at CRF 28 can only grow it, which is the case being tested.
    """
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
            "-c:v", "libx264", "-crf", "51", "-c:a", "aac", "-b:a", "32k", "-shortest",
            str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def _run(folder: Path, utterance: str, plan: list[dict]) -> list[dict]:
    from knaif.agent import CommandAgent

    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=folder)
    results = agent.execute_plan({"plan": plan}, utterance=utterance, dry_run=False, confirmed=True)
    return [
        out
        for r in results
        if r["tool"] == "run_batch"
        for out in (r.get("result") or {}).get("outputs") or []
    ]


@needs_ffmpeg
@pytest.mark.skipif(not (FIXTURES / "clip.mp4").exists(), reason="needs generated fixtures")
def test_a_compress_that_shrinks_says_by_how_much(tmp_path: Path) -> None:
    shutil.copy2(FIXTURES / "clip.mp4", tmp_path / "clip.mp4")
    (out,) = _run(
        tmp_path,
        "compress clip.mp4",
        [{"tool": "compress_video", "args": {"inputs": ["clip.mp4"]}}],
    )
    size = out["size"]
    assert size["kept"] is None
    assert size["before"] == (tmp_path / "clip.mp4").stat().st_size
    assert size["after"] == (tmp_path / "clip_compressed.mp4").stat().st_size < size["before"]


@needs_ffmpeg
def test_a_compress_that_cannot_shrink_leaves_things_as_they_were(tmp_path: Path) -> None:
    """The user's own file: nothing new is written, and the result says why."""
    _starved(tmp_path / "tiny.mp4")
    (out,) = _run(
        tmp_path,
        "make tiny.mp4 smaller",
        [{"tool": "compress_video", "args": {"inputs": ["tiny.mp4"]}}],
    )
    assert out["returncode"] == 0
    assert out["size"]["kept"] == "nothing"
    assert out["size"]["after"] >= out["size"]["before"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tiny.mp4"]


@needs_ffmpeg
def test_a_chain_intermediate_that_cannot_shrink_is_delivered_as_it_was(tmp_path: Path) -> None:
    """The manual test's shape: convert the .mov to mp4, then make it smaller. The mp4 is what
    was asked for; when it cannot be made smaller it is delivered under the compress's name."""
    _starved(tmp_path / "tiny.mov")
    plan = [
        {
            "tool": "convert_video",
            "args": {"inputs": ["tiny.mov"], "container": "mp4", "output": "tiny.mp4"},
        },
        {"tool": "compress_video", "args": {"inputs": ["tiny.mp4"]}},
    ]
    outs = _run(tmp_path, "convert tiny.mov to mp4 and make it smaller", plan)
    size = outs[-1]["size"]
    assert size["kept"] == "input"
    delivered = tmp_path / "tiny_compressed.mp4"
    assert delivered.stat().st_size == size["before"], "the intermediate's own bytes"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tiny.mov", "tiny_compressed.mp4"]
