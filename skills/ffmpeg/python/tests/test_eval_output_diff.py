"""Tests for the ffmpeg output_diff verifier (v2).

Unit tests mock ffprobe; integration test runs real ffprobe (skipped if not on PATH).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from knaif.evalsuite.protocols import Verifier
from skills.ffmpeg.eval.verifiers import VERIFIERS, output_diff

# ── helpers ───────────────────────────────────────────────────────────────────


def _probe(
    *,
    container: str = "mp4",
    video_codec: str = "h264",
    audio_codec: str = "aac",
    width: int = 1920,
    height: int = 1080,
    duration: str = "10.0",
    size: str = "500000",
    bit_rate: str = "400000",
    sample_rate: str = "44100",
    channels: int = 2,
    pix_fmt: str = "yuv420p",
    has_video: bool = True,
    has_audio: bool = True,
) -> dict:
    streams = []
    if has_video:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": video_codec,
                "width": width,
                "height": height,
                "pix_fmt": pix_fmt,
            }
        )
    if has_audio:
        streams.append(
            {
                "codec_type": "audio",
                "codec_name": audio_codec,
                "sample_rate": sample_rate,
                "channels": channels,
            }
        )
    return {
        "streams": streams,
        "format": {
            "format_name": container,
            "duration": duration,
            "size": size,
            "bit_rate": bit_rate,
        },
    }


def _mock_ffprobe(probe_data: dict):
    """Context manager that patches subprocess.run to return probe_data as JSON."""
    import subprocess as sp

    class _Result:
        stdout = json.dumps(probe_data)
        returncode = 0

    return patch.object(sp, "run", return_value=_Result())


def _make_files(
    tmp_path: Path, ref_name: str = "ref.mp4", art_name: str = "art.mp4"
) -> tuple[Path, Path]:
    """Create non-empty placeholder files so the existence check passes."""
    ref = tmp_path / ref_name
    art = tmp_path / art_name
    ref.write_bytes(b"x")
    art.write_bytes(b"x")
    return ref, art


# ── identical outputs score 1.0 ───────────────────────────────────────────────


def test_identical_outputs_score_one(tmp_path: Path):
    ref, art = _make_files(tmp_path)
    probe = _probe()

    def _fake_run(cmd, **kw):
        class R:
            stdout = json.dumps(probe)
            returncode = 0

        return R()

    with patch("subprocess.run", side_effect=_fake_run):
        result = output_diff(art, ref, {}, tmp_path)

    assert result.score == pytest.approx(1.0)
    assert result.failed == []


# ── file existence checks ─────────────────────────────────────────────────────


def test_missing_artifact_returns_zero(tmp_path: Path):
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"x")
    result = output_diff(tmp_path / "missing.mp4", ref, {}, tmp_path)
    assert result.score == pytest.approx(0.0)
    assert any("artifact" in f for f in result.failed)


def test_missing_reference_returns_zero(tmp_path: Path):
    art = tmp_path / "art.mp4"
    art.write_bytes(b"x")
    result = output_diff(art, tmp_path / "missing.mp4", {}, tmp_path)
    assert result.score == pytest.approx(0.0)
    assert any("reference" in f for f in result.failed)


def test_extension_mismatch_penalises_score(tmp_path: Path):
    ref = tmp_path / "ref.mp4"
    art = tmp_path / "art.mkv"
    ref.write_bytes(b"x")
    art.write_bytes(b"x")
    probe = _probe()

    def _fake_run(cmd, **kw):
        class R:
            stdout = json.dumps(probe)
            returncode = 0

        return R()

    with patch("subprocess.run", side_effect=_fake_run):
        result = output_diff(art, ref, {}, tmp_path)

    assert any("extension" in f for f in result.failed)
    assert result.score < 1.0


# ── codec mismatch scores < 1.0 ───────────────────────────────────────────────


def test_video_codec_mismatch_fails(tmp_path: Path):
    ref, art = _make_files(tmp_path)
    calls = [_probe(video_codec="h264"), _probe(video_codec="hevc")]

    def _fake_run(cmd, **kw):
        class R:
            stdout = json.dumps(calls.pop(0))
            returncode = 0

        return R()

    with patch("subprocess.run", side_effect=_fake_run):
        result = output_diff(art, ref, {}, tmp_path)

    assert result.score < 1.0
    assert any("video_codec" in f for f in result.failed)


def test_container_mismatch_fails(tmp_path: Path):
    ref, art = _make_files(tmp_path)
    calls = [_probe(container="mp4"), _probe(container="matroska")]

    def _fake_run(cmd, **kw):
        class R:
            stdout = json.dumps(calls.pop(0))
            returncode = 0

        return R()

    with patch("subprocess.run", side_effect=_fake_run):
        result = output_diff(art, ref, {}, tmp_path)

    assert result.score < 1.0
    assert any("container" in f for f in result.failed)


def test_audio_stream_presence_mismatch_fails(tmp_path: Path):
    ref, art = _make_files(tmp_path)
    calls = [_probe(has_audio=True), _probe(has_audio=False)]

    def _fake_run(cmd, **kw):
        class R:
            stdout = json.dumps(calls.pop(0))
            returncode = 0

        return R()

    with patch("subprocess.run", side_effect=_fake_run):
        result = output_diff(art, ref, {}, tmp_path)

    assert result.score < 1.0
    assert any("audio" in f for f in result.failed)


# ── tolerances ────────────────────────────────────────────────────────────────


def test_duration_within_tolerance_passes(tmp_path: Path):
    ref, art = _make_files(tmp_path)
    calls = [_probe(duration="10.0"), _probe(duration="10.3")]

    def _fake_run(cmd, **kw):
        class R:
            stdout = json.dumps(calls.pop(0))
            returncode = 0

        return R()

    with patch("subprocess.run", side_effect=_fake_run):
        result = output_diff(art, ref, {}, tmp_path)

    assert "duration" not in " ".join(result.failed)


def test_duration_outside_tolerance_fails(tmp_path: Path):
    ref, art = _make_files(tmp_path)
    calls = [_probe(duration="10.0"), _probe(duration="15.0")]

    def _fake_run(cmd, **kw):
        class R:
            stdout = json.dumps(calls.pop(0))
            returncode = 0

        return R()

    with patch("subprocess.run", side_effect=_fake_run):
        result = output_diff(art, ref, {}, tmp_path)

    assert any("duration" in f for f in result.failed)


def test_per_row_tolerance_override(tmp_path: Path):
    ref, art = _make_files(tmp_path)
    calls = [_probe(duration="10.0"), _probe(duration="12.0")]

    def _fake_run(cmd, **kw):
        class R:
            stdout = json.dumps(calls.pop(0))
            returncode = 0

        return R()

    with patch("subprocess.run", side_effect=_fake_run):
        result = output_diff(art, ref, {"duration_s": 3.0}, tmp_path)

    assert "duration" not in " ".join(result.failed)


# ── ffprobe error handling ────────────────────────────────────────────────────


def test_ffprobe_failure_returns_low_score(tmp_path: Path):
    ref, art = _make_files(tmp_path)

    class _FailResult:
        stdout = ""
        returncode = 1
        stderr = "no such file"

    with patch("subprocess.run", return_value=_FailResult()):
        result = output_diff(art, ref, {}, tmp_path)

    assert result.score < 1.0
    assert result.failed


def test_ffprobe_not_found_returns_low_score(tmp_path: Path):
    ref, art = _make_files(tmp_path)
    with patch("subprocess.run", side_effect=FileNotFoundError("ffprobe not found")):
        result = output_diff(art, ref, {}, tmp_path)

    assert result.score < 1.0
    assert any("ffprobe" in f for f in result.failed)


# ── VERIFIERS dict and Protocol conformance ───────────────────────────────────


def test_verifiers_has_output_diff():
    assert "output_diff" in VERIFIERS
    assert callable(VERIFIERS["output_diff"])


def test_output_diff_satisfies_verifier_protocol():
    assert isinstance(output_diff, Verifier)


# ── integration: real ffprobe (skipped if not on PATH) ────────────────────────


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not on PATH")
def test_integration_identical_real_files(tmp_path: Path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")

    from skills.ffmpeg.eval.fixtures import FIXTURES

    name = "clip_no_audio.mp4"
    from pathlib import Path as _P

    ext = _P(name).suffix
    out_a = tmp_path / ("a" + ext)
    out_b = tmp_path / ("b" + ext)

    cmd = FIXTURES[name]
    for out in (out_a, out_b):
        subprocess.run(
            cmd.replace("{output}", str(out)).split(),
            capture_output=True,
            timeout=60,
            check=True,
        )

    result = output_diff(out_a, out_b, {}, tmp_path)
    assert result.score == pytest.approx(1.0), f"failed fields: {result.failed}"
