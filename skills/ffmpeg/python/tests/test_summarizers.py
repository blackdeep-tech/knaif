"""Tests for the ffmpeg skill SUMMARIZERS dict."""

from __future__ import annotations

from pathlib import Path

import pytest

# Import the skill module directly via Skill.load() to mirror runtime behavior.
from knaif.planner import summarize_plan
from knaif.skill import Skill

SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def skill() -> Skill:
    return Skill.load(SKILL_DIR)


def test_summarizers_cover_every_expander(skill):
    missing = set(skill.expanders) - set(skill.summarizers)
    assert not missing, f"missing SUMMARIZERS for {sorted(missing)}"


def test_prepare_for_platform_phrase(skill):
    fn = skill.summarizers["prepare_for_platform"]
    assert fn({"inputs": "clip1.mov", "platform": "instagram"}) == (
        "prepare clip1.mov for instagram"
    )


def test_convert_video_phrase_single_file(skill):
    fn = skill.summarizers["convert_video"]
    assert fn({"inputs": "clip1.mov", "container": "mp4"}) == "convert clip1.mov → MP4"


def test_convert_video_phrase_multi_files(skill):
    fn = skill.summarizers["convert_video"]
    assert fn({"inputs": ["a.mov", "b.mov"], "container": "mp4"}) == (
        "convert a.mov and b.mov → MP4"
    )


def test_convert_video_three_files_uses_oxford_comma(skill):
    fn = skill.summarizers["convert_video"]
    assert fn({"inputs": ["a.mov", "b.mov", "c.mov"], "container": "mp4"}) == (
        "convert a.mov, b.mov, and c.mov → MP4"
    )


def test_extract_audio_phrase(skill):
    fn = skill.summarizers["extract_audio"]
    assert fn({"inputs": "clip1.mov", "audio_format": "mp3"}) == (
        "extract audio from clip1.mov as MP3"
    )


def test_trim_video_phrase_with_start_and_end(skill):
    fn = skill.summarizers["trim_video"]
    assert fn({"input": "clip1.mov", "start": "00:00:10", "end": "00:00:40"}) == (
        "trim from 00:00:10 to 00:00:40 from clip1.mov"
    )


def test_trim_video_phrase_with_duration(skill):
    fn = skill.summarizers["trim_video"]
    assert fn({"input": "clip1.mov", "duration": 30}) == "trim the first 30s from clip1.mov"


def test_resize_video_phrase_with_height_only(skill):
    fn = skill.summarizers["resize_video"]
    assert fn({"inputs": "clip1.mov", "height": 720}) == "resize clip1.mov → 720p"


def test_resize_video_phrase_with_both_dims(skill):
    fn = skill.summarizers["resize_video"]
    assert fn({"inputs": "clip1.mov", "width": 1280, "height": 720}) == (
        "resize clip1.mov → 1280x720"
    )


def test_resize_video_crop_uses_crop_phrase(skill):
    fn = skill.summarizers["resize_video"]
    assert fn({"inputs": "clip1.mov", "width": 1080, "height": 1920, "fit": "crop"}) == (
        "crop clip1.mov → 1080x1920"
    )


def test_resize_video_aspect_uses_reframe_phrase(skill):
    fn = skill.summarizers["resize_video"]
    assert fn({"inputs": "clip1.mov", "aspect": "1:1"}) == "reframe clip1.mov → 1:1"


def test_compress_video_with_target(skill):
    fn = skill.summarizers["compress_video"]
    assert fn({"inputs": "clip1.mov", "target": "whatsapp"}) == "compress clip1.mov for whatsapp"


def test_create_thumbnail_phrase(skill):
    fn = skill.summarizers["create_thumbnail"]
    assert fn({"input": "clip1.mov", "at_time": "00:00:05"}) == (
        "create a thumbnail from clip1.mov at 00:00:05"
    )


def test_create_thumbnail_phrase_with_scale(skill):
    fn = skill.summarizers["create_thumbnail"]
    result = fn({"input": "clip.mp4", "at_time": "00:00:05", "scale": "4k"})
    assert "4k" in result


def test_create_thumbnail_phrase_without_scale_no_suffix(skill):
    fn = skill.summarizers["create_thumbnail"]
    result = fn({"input": "clip.mp4", "at_time": "00:00:05"})
    assert "scaled" not in result


def test_concat_video_phrase(skill):
    fn = skill.summarizers["concat_video"]
    assert fn({"inputs": ["a.mov", "b.mov"], "output": "out.mp4"}) == (
        "concatenate a.mov and b.mov into out.mp4"
    )


def test_strip_audio_phrase(skill):
    fn = skill.summarizers["strip_audio"]
    assert fn({"inputs": "clip1.mov"}) == "strip audio from clip1.mov"


def test_adjust_speed_phrase(skill):
    fn = skill.summarizers["adjust_speed"]
    assert fn({"inputs": "clip1.mov", "speed": 2.0}) == "adjust the speed of clip1.mov by 2.0x"


def test_adjust_volume_phrase_level(skill):
    fn = skill.summarizers["adjust_volume"]
    assert fn({"inputs": ["clip.mp4"], "level": "6dB"}) == "adjust volume of clip.mp4 by 6dB"


def test_adjust_volume_phrase_normalize(skill):
    fn = skill.summarizers["adjust_volume"]
    assert fn({"inputs": ["clip.mp4"], "normalize": True}) == "normalize audio in clip.mp4"


def test_reverse_video_phrase_default(skill):
    fn = skill.summarizers["reverse_video"]
    assert fn({"inputs": "clip1.mov"}) == "reverse clip1.mov"


def test_reverse_video_phrase_video_only(skill):
    fn = skill.summarizers["reverse_video"]
    assert fn({"inputs": "clip1.mov", "include_audio": False}) == "reverse clip1.mov (video only)"


def test_summarize_plan_end_to_end_with_ffmpeg(skill):
    plan = [
        {"tool": "convert_video", "args": {"inputs": "clip1.mov", "container": "mp4"}},
        {"tool": "extract_audio", "args": {"inputs": "clip1.mov", "audio_format": "mp3"}},
    ]
    expected = "Will convert clip1.mov → MP4, then extract audio from clip1.mov as MP3."
    assert summarize_plan(plan, skill.summarizers) == expected


def test_prepare_for_platform_with_skill_dir_loads_profile(skill):
    """When skill_dir is passed, the summarizer reads the platform profile."""
    fn = skill.summarizers["prepare_for_platform"]
    result = fn({"inputs": "video.mp4", "platform": "whatsapp"}, skill_dir=SKILL_DIR)
    # WhatsApp profile: max_height=720, container=mp4, video_codec=h264, audio_codec=aac
    assert "720p" in result
    assert "MP4" in result
    assert "H.264" in result
    assert "AAC" in result
    assert "whatsapp" in result


def test_prepare_for_platform_without_skill_dir_falls_back(skill):
    """Without skill_dir the summarizer produces the short fallback phrase."""
    fn = skill.summarizers["prepare_for_platform"]
    result = fn({"inputs": "video.mp4", "platform": "whatsapp"})
    assert result == "prepare video.mp4 for whatsapp"


def test_compress_video_with_quality_profile_hint(skill):
    """compress_video summarizer includes CRF hint when skill_dir is provided."""
    fn = skill.summarizers["compress_video"]
    result = fn({"inputs": "video.mp4", "quality": "small_file"}, skill_dir=SKILL_DIR)
    # small_file.yaml: video_crf=28, encoder_preset=slow
    assert "CRF 28" in result
    assert "slow" in result


def test_compress_video_without_skill_dir_plain(skill):
    fn = skill.summarizers["compress_video"]
    result = fn({"inputs": "video.mp4"})
    assert result == "compress video.mp4"


def test_convert_video_with_codec(skill):
    fn = skill.summarizers["convert_video"]
    result = fn({"inputs": "a.mov", "container": "mp4", "video_codec": "h265"})
    assert "H265" in result
    assert "MP4" in result
