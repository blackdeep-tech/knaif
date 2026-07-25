"""Tests for the ffmpeg Reviewer.render_row implementation."""

from __future__ import annotations

from pathlib import Path

from knaif.evalsuite.protocols import Reviewer
from skills.ffmpeg.eval.reviewer import FfmpegReviewer

# ── Protocol conformance ──────────────────────────────────────────────────────


def test_ffmpeg_reviewer_satisfies_protocol():
    assert isinstance(FfmpegReviewer(), Reviewer)


# ── render_row structure ──────────────────────────────────────────────────────


def test_render_row_returns_string():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert to mp4"], "tags": ["convert"]},
        arm_outputs={"knaif": None, "baseline": None},
        reference=None,
    )
    assert isinstance(html, str)
    assert len(html) > 0


def test_render_row_contains_row_id():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert to mp4"], "tags": []},
        arm_outputs={},
        reference=None,
    )
    assert "r001" in html


def test_render_row_contains_utterance():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["resize to 720p"], "tags": []},
        arm_outputs={},
        reference=None,
    )
    assert "resize to 720p" in html


def test_render_row_contains_video_tag_when_arm_output_exists(tmp_path: Path):
    art = tmp_path / "out.mp4"
    art.write_bytes(b"fake")
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": []},
        arm_outputs={"knaif": art},
        reference=None,
    )
    assert "<video" in html


def test_render_row_no_video_tag_for_none_output():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": []},
        arm_outputs={"knaif": None},
        reference=None,
    )
    assert "<video" not in html or "knaif" not in html


def test_render_row_shows_all_arm_names():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": []},
        arm_outputs={"knaif": None, "gemma": None, "baseline": None},
        reference=None,
    )
    assert "knaif" in html
    assert "gemma" in html
    assert "baseline" in html


def test_render_row_reference_path_shown_when_present(tmp_path: Path):
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"fake")
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": []},
        arm_outputs={},
        reference=ref,
    )
    assert "ref.mp4" in html or "reference" in html.lower()


def test_render_row_is_html_fragment():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": ["convert"]},
        arm_outputs={"knaif": None},
        reference=None,
    )
    # Should be a fragment — not a full document
    assert "<html" not in html.lower()
    assert "<div" in html or "<section" in html or "<article" in html


# ── T14 enhancements ──────────────────────────────────────────────────────────


def test_render_row_shows_all_utterances():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["first", "second", "third"], "tags": []},
        arm_outputs={},
        reference=None,
    )
    assert "first" in html
    assert "second" in html
    assert "third" in html


def test_render_row_audio_tag_for_mp3(tmp_path: Path):
    art = tmp_path / "out.mp3"
    art.write_bytes(b"fake")
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["extract audio"], "tags": []},
        arm_outputs={"knaif": art},
        reference=None,
    )
    assert "<audio" in html
    assert "<video" not in html


def test_render_row_img_tag_for_jpeg(tmp_path: Path):
    art = tmp_path / "out.jpeg"
    art.write_bytes(b"fake")
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["extract frame"], "tags": []},
        arm_outputs={"knaif": art},
        reference=None,
    )
    assert "<img" in html
    assert "<video" not in html
    assert "<audio" not in html


def test_render_row_video_tag_for_mp4(tmp_path: Path):
    art = tmp_path / "out.mp4"
    art.write_bytes(b"fake")
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": []},
        arm_outputs={"knaif": art},
        reference=None,
    )
    assert "<video" in html


def test_render_row_ffprobe_diff_table_rendered():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": []},
        arm_outputs={
            "_scores": {
                "knaif": (["extension=mp4", "video_codec=h264"], ["duration"]),
            }
        },
        reference=None,
    )
    assert "ffprobe-diff" in html
    assert "extension=mp4" in html
    assert "duration" in html


def test_render_row_fixture_shown(tmp_path: Path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"fake")
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": []},
        arm_outputs={"_fixture": fixture, "knaif": None},
        reference=None,
    )
    assert "fixture" in html


def test_render_row_skips_underscore_keys_as_arms():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": []},
        arm_outputs={"_scores": {}, "_review_cmd": "evalsuite review ...", "knaif": None},
        reference=None,
    )
    assert "_scores" not in html
    assert "_review_cmd" not in html


def test_render_row_review_command_shown():
    reviewer = FfmpegReviewer()
    html = reviewer.render_row(
        row={"id": "r001", "utterances": ["convert"], "tags": []},
        arm_outputs={"_review_cmd": "evalsuite review --row r001 --status reviewed"},
        reference=None,
    )
    assert "evalsuite review" in html


def test_reviewer_module_exports_reviewer_class():
    import importlib.util as _ilu
    from pathlib import Path as _Path

    spec = _ilu.spec_from_file_location("rev", _Path("skills/ffmpeg/eval/reviewer.py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "REVIEWER")
    assert mod.REVIEWER.__name__ == "FfmpegReviewer"
