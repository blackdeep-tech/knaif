"""Tests for the success verifier and success_criteria-aware cheap verifier."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from skills.ffmpeg.eval.verifiers import VERIFIERS, success

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
    sample_rate: str = "44100",
    channels: int = 2,
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
        },
    }


def _mock_ffprobe(probe_data: dict):
    class _Result:
        stdout = json.dumps(probe_data)
        returncode = 0

    return patch.object(subprocess, "run", return_value=_Result())


def _mock_output(
    artifact: str | None = "ffmpeg -y -i in.mp4 -c:v libx264 -c:a aac out.mp4",
    artifact_path: Path | None = None,
) -> MagicMock:
    m = MagicMock()
    m.artifact = artifact
    m.artifact_path = artifact_path
    m.outcome = "plan"
    return m


# ── VERIFIERS dict ─────────────────────────────────────────────────────────────


def test_success_in_verifiers():
    assert "success" in VERIFIERS


# ── success: no criteria ───────────────────────────────────────────────────────


def test_success_empty_criteria_scores_one(tmp_path: Path):
    result = success(_mock_output(), {}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_success_empty_criteria_verifier_kind_is_output(tmp_path: Path):
    result = success(_mock_output(), {}, tmp_path)
    assert result.verifier_kind == "output"


# ── success: command-text checks (filters/flags) ───────────────────────────────


def test_success_filter_present_in_command_passes(tmp_path: Path):
    out = _mock_output("ffmpeg -y -i in.mp4 -vf scale=1280:720 out.mp4")
    result = success(out, {"filters": ["scale"]}, tmp_path)
    assert result.score == pytest.approx(1.0)
    assert any("filter:scale" in m for m in result.matched)


def test_success_filter_absent_in_command_fails(tmp_path: Path):
    out = _mock_output("ffmpeg -y -i in.mp4 out.mp4")
    result = success(out, {"filters": ["scale"]}, tmp_path)
    assert result.score == pytest.approx(0.0)
    assert any("scale" in f for f in result.failed)


def test_success_flag_present_in_command_passes(tmp_path: Path):
    out = _mock_output("ffmpeg -y -i in.mp4 -movflags +faststart out.mp4")
    result = success(out, {"flags": ["-movflags"]}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_success_flag_absent_in_command_fails(tmp_path: Path):
    out = _mock_output("ffmpeg -y -i in.mp4 out.mp4")
    result = success(out, {"flags": ["-movflags"]}, tmp_path)
    assert result.score == pytest.approx(0.0)


def test_success_multiple_flags_partial_score(tmp_path: Path):
    out = _mock_output("ffmpeg -y -i in.mp4 -movflags +faststart out.mp4")
    result = success(out, {"flags": ["-movflags", "-ss"]}, tmp_path)
    assert 0.0 < result.score < 1.0


# ── success: file-based checks — missing artifact ─────────────────────────────


def test_success_file_criteria_without_artifact_path_fails(tmp_path: Path):
    out = _mock_output(artifact_path=None)
    result = success(out, {"container": "mp4"}, tmp_path)
    assert result.score == pytest.approx(0.0)
    assert any("missing" in f or "artifact" in f for f in result.failed)


def test_success_file_criteria_nonexistent_path_fails(tmp_path: Path):
    out = _mock_output(artifact_path=tmp_path / "nonexistent.mp4")
    result = success(out, {"container": "mp4"}, tmp_path)
    assert result.score == pytest.approx(0.0)


# ── success: file-based checks — container ────────────────────────────────────


def test_success_container_match_passes(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(container="mp4")):
        result = success(out, {"container": "mp4"}, tmp_path)
    assert result.score == pytest.approx(1.0)
    assert any("container=mp4" in m for m in result.matched)


def test_success_container_mismatch_fails(tmp_path: Path):
    f = tmp_path / "out.mkv"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(container="matroska,webm")):
        result = success(out, {"container": "mp4"}, tmp_path)
    assert result.score == pytest.approx(0.0)
    assert any("container" in ff for ff in result.failed)


def test_success_container_single_value_subset_of_probe_passes(tmp_path: Path):
    """Expected 'webm' against a 'matroska,webm' probe must pass (token in list)."""
    f = tmp_path / "out.webm"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(container="matroska,webm")):
        result = success(out, {"container": "webm"}, tmp_path)
    assert result.score == pytest.approx(1.0)
    assert any("container" in m for m in result.matched)


def test_success_container_multivalue_expected_matches_probe(tmp_path: Path):
    """Expected multi-value 'matroska,webm' must pass against a 'matroska,webm' probe.

    Regression for the false failure: exact-token `exp in containers` never matches
    a comma-joined expected string — split + set-intersect like output_diff does.
    """
    f = tmp_path / "out.webm"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(container="matroska,webm")):
        result = success(out, {"container": "matroska,webm"}, tmp_path)
    assert result.score == pytest.approx(1.0)
    assert any("container" in m for m in result.matched)


def test_success_container_multivalue_genuine_mismatch_still_fails(tmp_path: Path):
    """A real mismatch (mp4 expected vs matroska,webm probe) must still fail."""
    f = tmp_path / "out.webm"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(container="matroska,webm")):
        result = success(out, {"container": "mp4,m4a"}, tmp_path)
    assert result.score == pytest.approx(0.0)
    assert any("container" in ff for ff in result.failed)


# ── success: file-based checks — video codec ──────────────────────────────────


def test_success_video_codec_match_passes(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(video_codec="h264")):
        result = success(out, {"video_codec": "h264"}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_success_video_codec_alias_passes(tmp_path: Path):
    """ffprobe may return 'avc' for h264 — aliases must be resolved."""
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(video_codec="avc")):
        result = success(out, {"video_codec": "h264"}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_success_video_codec_mismatch_fails(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(video_codec="hevc")):
        result = success(out, {"video_codec": "h264"}, tmp_path)
    assert result.score == pytest.approx(0.0)


# ── success: file-based checks — audio codec ──────────────────────────────────


def test_success_audio_codec_match_passes(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(audio_codec="aac")):
        result = success(out, {"audio_codec": "aac"}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_success_audio_codec_mismatch_fails(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(audio_codec="mp3")):
        result = success(out, {"audio_codec": "aac"}, tmp_path)
    assert result.score == pytest.approx(0.0)


# ── success: no_audio ─────────────────────────────────────────────────────────


def test_success_no_audio_true_passes_when_absent(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(has_audio=False)):
        result = success(out, {"no_audio": True}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_success_no_audio_true_fails_when_present(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(has_audio=True)):
        result = success(out, {"no_audio": True}, tmp_path)
    assert result.score == pytest.approx(0.0)


# ── success: max_width / max_height ───────────────────────────────────────────


def test_success_max_height_satisfied_passes(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(width=1280, height=720)):
        result = success(out, {"max_height": 720}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_success_max_height_exceeded_fails(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(width=1920, height=1080)):
        result = success(out, {"max_height": 720}, tmp_path)
    assert result.score == pytest.approx(0.0)


def test_success_max_width_satisfied_passes(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(width=1280, height=720)):
        result = success(out, {"max_width": 1280}, tmp_path)
    assert result.score == pytest.approx(1.0)


# ── success: combined criteria, partial score ─────────────────────────────────


def test_success_partial_criteria_partial_score(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(artifact_path=f)
    with _mock_ffprobe(_probe(container="mp4", video_codec="hevc")):
        # container passes, video_codec fails
        result = success(out, {"container": "mp4", "video_codec": "h264"}, tmp_path)
    assert 0.0 < result.score < 1.0
    assert any("container" in m for m in result.matched)
    assert any("video_codec" in ff for ff in result.failed)


def test_success_all_criteria_pass_score_one(tmp_path: Path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"fake")
    out = _mock_output(
        artifact="ffmpeg -y -i in.mp4 -c:v libx264 -c:a aac -movflags +faststart out.mp4",
        artifact_path=f,
    )
    with _mock_ffprobe(_probe(container="mp4", video_codec="h264", audio_codec="aac")):
        result = success(
            out,
            {
                "container": "mp4",
                "video_codec": "h264",
                "audio_codec": "aac",
                "flags": ["-movflags"],
            },
            tmp_path,
        )
    assert result.score == pytest.approx(1.0)
    assert result.failed == []


# ── success: routing-only grading (grade=routing) ─────────────────────────────


def test_success_routing_grade_skips_artifact_grading(tmp_path: Path):
    """grade=routing rows can't produce an artifact (glob/batch, fixture None).

    Reaching success() means the row routed to a plan, which is the only thing
    we grade — return full score and skip the artifact_file_missing penalty.
    """
    out = _mock_output(artifact_path=None)
    result = success(out, {"grade": "routing", "container": "mp4"}, tmp_path)
    assert result.score == pytest.approx(1.0)
    assert not any("missing" in f or "artifact" in f for f in result.failed)


def test_success_routing_grade_without_other_criteria_scores_one(tmp_path: Path):
    out = _mock_output(artifact_path=None)
    result = success(out, {"grade": "routing"}, tmp_path)
    assert result.score == pytest.approx(1.0)


# ── cheap with success_criteria (command-text) ────────────────────────────────


def test_cheap_with_criteria_codec_in_command_passes(tmp_path: Path):
    cheap = VERIFIERS["cheap"]
    out = _mock_output("ffmpeg -y -i in.mp4 -c:v libx264 -c:a aac out.mp4")
    result = cheap(out, {"flags": ["-c:v libx264"]}, tmp_path)
    assert result.score == pytest.approx(1.0)


def test_cheap_with_criteria_codec_absent_fails(tmp_path: Path):
    cheap = VERIFIERS["cheap"]
    out = _mock_output("ffmpeg -y -i in.mp4 out.mp4")
    result = cheap(out, {"flags": ["-c:v libvpx-vp9"]}, tmp_path)
    assert result.score < 1.0


def test_cheap_no_criteria_still_works(tmp_path: Path):
    cheap = VERIFIERS["cheap"]
    out = _mock_output("ffmpeg -y -i in.mp4 out.mp4")
    result = cheap(out, {}, tmp_path)
    assert result.score == pytest.approx(1.0)


# ── grade_outputs: multi-output grading ────────────────────────────────────────


def test_grade_outputs_in_verifiers():
    assert "grade_outputs" in VERIFIERS


def test_grade_outputs_all_pass_scores_one(tmp_path: Path):
    from skills.ffmpeg.eval.verifiers import grade_outputs

    video = tmp_path / "clip_trimmed.mp4"
    video.write_bytes(b"fake")
    audio = tmp_path / "clip_trimmed.mp3"
    audio.write_bytes(b"fake")
    spec = [
        {"command": "ffmpeg -i a.mp4 clip_trimmed.mp4", "criteria": {"video_codec": "h264"}},
        {
            "command": "ffmpeg -i clip_trimmed.mp4 clip_trimmed.mp3",
            "criteria": {"audio_codec": "mp3"},
        },
    ]
    # First output probes as h264 video, second as mp3 audio (no video).
    probes = iter([_probe(video_codec="h264"), _probe(audio_codec="mp3", has_video=False)])

    def _fake_run(*a, **k):
        class _R:
            stdout = json.dumps(next(probes))
            returncode = 0

        return _R()

    with patch.object(subprocess, "run", side_effect=_fake_run):
        result = grade_outputs([video, audio], spec, tmp_path)
    assert result.score == pytest.approx(1.0)
    assert result.failed == []


def test_grade_outputs_one_output_fails_lowers_score(tmp_path: Path):
    from skills.ffmpeg.eval.verifiers import grade_outputs

    video = tmp_path / "clip_trimmed.mp4"
    video.write_bytes(b"fake")
    audio = tmp_path / "clip_trimmed.mp3"
    audio.write_bytes(b"fake")
    spec = [
        {"command": "x", "criteria": {"video_codec": "h264"}},
        {"command": "y", "criteria": {"audio_codec": "mp3"}},
    ]
    # Second output is aac, not mp3 → fails.
    probes = iter([_probe(video_codec="h264"), _probe(audio_codec="aac", has_video=False)])

    def _fake_run(*a, **k):
        class _R:
            stdout = json.dumps(next(probes))
            returncode = 0

        return _R()

    with patch.object(subprocess, "run", side_effect=_fake_run):
        result = grade_outputs([video, audio], spec, tmp_path)
    assert 0.0 < result.score < 1.0


def test_grade_outputs_missing_produced_file_fails(tmp_path: Path):
    from skills.ffmpeg.eval.verifiers import grade_outputs

    video = tmp_path / "clip_trimmed.mp4"
    video.write_bytes(b"fake")
    spec = [
        {"command": "x", "criteria": {"video_codec": "h264"}},
        {"command": "y", "criteria": {"audio_codec": "mp3"}},
    ]
    # Only one path produced; the second deliverable is missing (None).
    with _mock_ffprobe(_probe(video_codec="h264")):
        result = grade_outputs([video, None], spec, tmp_path)
    assert result.score < 1.0
    assert any("out1" in f for f in result.failed)


def test_grade_outputs_prefixes_results_by_index(tmp_path: Path):
    from skills.ffmpeg.eval.verifiers import grade_outputs

    video = tmp_path / "clip_trimmed.mp4"
    video.write_bytes(b"fake")
    audio = tmp_path / "clip_trimmed.mp3"
    audio.write_bytes(b"fake")
    spec = [
        {"command": "x", "criteria": {"video_codec": "h264"}},
        {"command": "y", "criteria": {"audio_codec": "mp3"}},
    ]
    probes = iter([_probe(video_codec="h264"), _probe(audio_codec="mp3", has_video=False)])

    def _fake_run(*a, **k):
        class _R:
            stdout = json.dumps(next(probes))
            returncode = 0

        return _R()

    with patch.object(subprocess, "run", side_effect=_fake_run):
        result = grade_outputs([video, audio], spec, tmp_path)
    assert any(m.startswith("out0:") for m in result.matched)
    assert any(m.startswith("out1:") for m in result.matched)
