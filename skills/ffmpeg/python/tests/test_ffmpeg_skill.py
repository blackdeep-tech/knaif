"""Tests for the ffmpeg skill — expanders, handlers, and end-to-end dry-run."""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from typing import Any

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


# ─────────────────────────────────────────────────────────────────────────────
# ffprobe/ffmpeg stub helpers (so tests don't need real binaries).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def stub_ffmpeg(monkeypatch):
    """Patch handlers._deps.run_ffprobe and handlers._deps.run_ffmpeg with canned responses."""
    # Force a re-load of the ffmpeg handlers module so we can patch its globals.
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    skill = Skill.load(FFMPEG_SKILL_DIR)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]

    def fake_probe(file_path: Path) -> dict[str, Any]:
        return {
            "streams": [
                {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mov,mp4,m4a", "duration": "85.3", "size": "440401920"},
        }

    def fake_ffmpeg(command: list[str]) -> dict[str, Any]:
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(handlers_mod._deps, "run_ffprobe", fake_probe)
    monkeypatch.setattr(handlers_mod._deps, "run_ffmpeg", fake_ffmpeg)
    return skill, handlers_mod


@pytest.fixture()
def media_root(tmp_path: Path) -> Path:
    """Create a few empty 'media' files we can pass to handlers."""
    (tmp_path / "clip1.mov").write_bytes(b"")
    (tmp_path / "clip2.mp4").write_bytes(b"")
    (tmp_path / "clip3.mkv").write_bytes(b"")
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# Skill loading.
# ─────────────────────────────────────────────────────────────────────────────


def test_ffmpeg_skill_loads():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    assert skill.name == "ffmpeg"


def test_ffmpeg_skill_exposes_expanders():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    assert "prepare_for_platform" in skill.expanders
    assert "compress_video" in skill.expanders
    assert "convert_video" in skill.expanders
    assert "resize_video" in skill.expanders
    assert "trim_video" in skill.expanders
    assert "extract_audio" in skill.expanders
    assert "create_thumbnail" in skill.expanders


def test_rotate_video_expander_exists():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    assert "rotate_video" in skill.expanders


def test_rotate_video_expander_shape():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["rotate_video"]({"inputs": ["clip.mp4"], "angle": 90})
    tools = [s["tool"] for s in plan]
    assert "resolve_inputs" in tools
    assert "build_recipes" in tools
    assert "run_batch" in tools


def test_rotate_90_emits_transpose(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "1000"},
        },
    )
    payload = {
        "plan": [
            {
                "tool": "rotate_video",
                "args": {"inputs": [str(media_root / "clip1.mov")], "angle": 90},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch = next(r for r in results if r["tool"] == "run_batch")
    cmd_str = " ".join(str(c) for c in batch["result"]["outputs"][0]["command"])
    assert "transpose=1" in cmd_str


def test_rotate_180_emits_hflip_vflip(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "1000"},
        },
    )
    payload = {
        "plan": [
            {
                "tool": "rotate_video",
                "args": {"inputs": [str(media_root / "clip1.mov")], "angle": 180},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch = next(r for r in results if r["tool"] == "run_batch")
    cmd_str = " ".join(str(c) for c in batch["result"]["outputs"][0]["command"])
    assert "hflip,vflip" in cmd_str


def test_flip_horizontal_emits_hflip(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "1000"},
        },
    )
    payload = {
        "plan": [
            {
                "tool": "rotate_video",
                "args": {"inputs": [str(media_root / "clip1.mov")], "flip": "horizontal"},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch = next(r for r in results if r["tool"] == "run_batch")
    cmd_str = " ".join(str(c) for c in batch["result"]["outputs"][0]["command"])
    assert "hflip" in cmd_str


def test_rotate_no_angle_no_flip_raises(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "1000"},
        },
    )
    payload = {
        "plan": [{"tool": "rotate_video", "args": {"inputs": [str(media_root / "clip1.mov")]}}]
    }
    with pytest.raises(Exception, match="angle or flip"):
        agent.execute_plan(payload, dry_run=True, confirmed=True)


def test_adjust_volume_expander_exists():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    assert "adjust_volume" in skill.expanders


def test_adjust_volume_level_emits_volume_filter(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "1000"},
        },
    )
    payload = {
        "plan": [
            {
                "tool": "adjust_volume",
                "args": {"inputs": [str(media_root / "clip1.mov")], "level": "6dB"},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch = next(r for r in results if r["tool"] == "run_batch")
    cmd_str = " ".join(str(c) for c in batch["result"]["outputs"][0]["command"])
    assert "volume=6dB" in cmd_str
    assert "-c:v" in cmd_str


def test_adjust_volume_normalize_emits_loudnorm(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "1000"},
        },
    )
    payload = {
        "plan": [
            {
                "tool": "adjust_volume",
                "args": {"inputs": [str(media_root / "clip1.mov")], "normalize": True},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch = next(r for r in results if r["tool"] == "run_batch")
    cmd_str = " ".join(str(c) for c in batch["result"]["outputs"][0]["command"])
    assert "loudnorm" in cmd_str


def test_dummy_probe_audio_extension_has_no_video(tmp_path, monkeypatch):
    """_dummy_probe of an audio file (dry-run, file absent) must be audio-only.

    The success eval renders commands in dry_run before the fixture is copied in,
    so an audio input falls back to _dummy_probe — which previously claimed every
    file was 1920x1080 h264, causing audio ops to render as video (.mp4/aac).
    """
    mod = _handlers_mod()
    probe = mod._dummy_probe(Path("audio.mp3"))
    assert probe["has_audio"] is True
    assert not probe.get("video_codec")
    assert not probe.get("width")
    assert probe["container"] == "mp3"


def _render_batch_cmd(agent, plan):
    results = agent.execute_plan({"plan": plan}, dry_run=True, confirmed=True)
    batch = next(r for r in results if r["tool"] == "run_batch")
    return " ".join(str(c) for c in batch["result"]["outputs"][0]["command"])


def test_adjust_volume_on_audio_input_preserves_mp3(tmp_path):
    """adjust_volume on an audio-only mp3 must output mp3, not re-encode to aac mp4."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()  # audio.mp3 intentionally absent -> dummy probe (eval render path)
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent, [{"tool": "adjust_volume", "args": {"inputs": ["audio.mp3"], "level": "6dB"}}]
    )
    assert "volume=6dB" in cmd
    assert cmd.endswith(".mp3")
    assert "libmp3lame" in cmd
    assert "-c:a aac" not in cmd
    assert "-c:v copy" not in cmd  # no video stream to copy


def test_adjust_volume_normalize_on_audio_input_preserves_format(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent, [{"tool": "adjust_volume", "args": {"inputs": ["audio.mp3"], "normalize": True}}]
    )
    assert "loudnorm" in cmd
    assert cmd.endswith(".mp3")
    assert "-c:v copy" not in cmd


def test_adjust_volume_on_video_input_still_copies_video(stub_ffmpeg, media_root):
    """Regression guard: a real video input keeps the video stream (-c:v copy)."""
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    cmd = _render_batch_cmd(
        agent,
        [
            {
                "tool": "adjust_volume",
                "args": {"inputs": [str(media_root / "clip1.mov")], "level": "6dB"},
            }
        ],
    )
    assert "volume=6dB" in cmd
    assert "-c:v" in cmd


def test_adjust_volume_schema_exists():
    from knaif.registry import load_registry

    reg = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")
    tool = reg["adjust_volume"]
    assert "inputs" in tool.required_args
    assert "level" in tool.optional_args
    assert "normalize" in tool.optional_args


# ── deterministic coercion of model-leaked natural-language args ──────────────
# Small models emit NL words ("louder", "lower") in numeric slots, which render
# straight into the ffmpeg command and crash the binary (-af volume=louder).
# These guards coerce the NL words to valid values so the command runs.


@pytest.mark.parametrize(
    "word",
    ["louder", "boost", "increase", "lauter"],
)
def test_adjust_volume_louder_word_coerced_to_gain(tmp_path, word):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent, [{"tool": "adjust_volume", "args": {"inputs": ["clip.mp4"], "level": word}}]
    )
    assert f"volume={word}" not in cmd  # the raw NL word must never reach ffmpeg
    assert "volume=6dB" in cmd  # louder → a concrete positive gain


@pytest.mark.parametrize(
    "word",
    ["quieter", "lower", "reduce", "softer"],
)
def test_adjust_volume_quieter_word_coerced_to_attenuation(tmp_path, word):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent, [{"tool": "adjust_volume", "args": {"inputs": ["clip.mp4"], "level": word}}]
    )
    assert f"volume={word}" not in cmd
    assert "volume=0.5" in cmd  # quieter → a concrete attenuation


def test_adjust_volume_unicode_minus_normalized(tmp_path):
    """A Unicode minus (U+2212) in a dB level must become an ASCII hyphen."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent, [{"tool": "adjust_volume", "args": {"inputs": ["clip.mp4"], "level": "−6dB"}}]
    )
    assert "−" not in cmd  # no Unicode minus survives
    assert "volume=-6dB" in cmd


def test_adjust_volume_numeric_level_passthrough(tmp_path):
    """A valid numeric/dB level is untouched (regression guard for the coercion)."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent, [{"tool": "adjust_volume", "args": {"inputs": ["clip.mp4"], "level": "3dB"}}]
    )
    assert "volume=3dB" in cmd


def test_extract_audio_nl_bitrate_word_dropped(tmp_path):
    """A non-bitrate word in the bitrate slot ('lower') must not reach -b:a."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent,
        [{"tool": "extract_audio", "args": {"inputs": ["audio.mp3"], "bitrate": "lower"}}],
    )
    assert "-b:a lower" not in cmd  # the raw word must never reach ffmpeg
    assert "lower" not in cmd


def test_extract_audio_format_inferred_from_output_extension(tmp_path):
    """output='audio.flac' with no explicit format must encode flac, not mp3."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent,
        [{"tool": "extract_audio", "args": {"inputs": ["audio.mp3"], "output": "audio.flac"}}],
    )
    assert "libmp3lame" not in cmd  # must not use the mp3 encoder for a .flac target
    assert "-c:a flac" in cmd
    assert cmd.endswith(".flac")


def test_rotate_video_schema_exists():
    from knaif.registry import load_registry

    reg = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")
    tool = reg["rotate_video"]
    assert "inputs" in tool.required_args
    assert "angle" in tool.optional_args
    assert "flip" in tool.optional_args


# NOTE: create_thumbnail `scale` present/absent is covered end-to-end by
# test_create_thumbnail_scale_4k_emits_vf_filter / test_create_thumbnail_no_scale_omits_vf
# (which route through execute_plan → validate_plan, so they also pin the schema).


def _get_parse_scale():
    Skill.load(FFMPEG_SKILL_DIR)
    return sys.modules["_skill_oop_ffmpeg_handlers"]._parse_scale


def test_parse_scale_4k():
    assert _get_parse_scale()("4k") == "3840:2160"


def test_parse_scale_preset_720p():
    assert _get_parse_scale()("720p") == "1280:720"


def test_parse_scale_wxh_format():
    assert _get_parse_scale()("1920x1080") == "1920:1080"


def test_parse_scale_colon_format():
    assert _get_parse_scale()("1920:1080") == "1920:1080"


def test_parse_scale_none():
    assert _get_parse_scale()(None) is None


def test_parse_scale_invalid_raises():
    with pytest.raises(ValueError):
        _get_parse_scale()("ultrawide")


# ─────────────────────────────────────────────────────────────────────────────
# Task 5.0 — fps in _summarise_probe / _dummy_probe
# ─────────────────────────────────────────────────────────────────────────────


def _get_handlers():
    Skill.load(FFMPEG_SKILL_DIR)
    return sys.modules["_skill_oop_ffmpeg_handlers"]


def test_summarise_probe_fps_from_avg_frame_rate():
    h = _get_handlers()
    probe = {
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001"}
        ],
        "format": {"format_name": "mp4", "duration": "10.0"},
    }
    result = h._summarise_probe(Path("clip.mp4"), probe)
    assert result["fps"] == pytest.approx(29.97, rel=1e-3)


def test_summarise_probe_fps_from_r_frame_rate_fallback():
    h = _get_handlers()
    probe = {
        "streams": [{"codec_type": "video", "width": 1280, "height": 720, "r_frame_rate": "25/1"}],
        "format": {"format_name": "mp4", "duration": "5.0"},
    }
    result = h._summarise_probe(Path("clip.mp4"), probe)
    assert result["fps"] == pytest.approx(25.0)


def test_summarise_probe_fps_zero_division_returns_none():
    h = _get_handlers()
    probe = {
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "0/0"}
        ],
        "format": {"format_name": "mp4"},
    }
    result = h._summarise_probe(Path("clip.mp4"), probe)
    assert result["fps"] is None


def test_summarise_probe_fps_absent_returns_none():
    h = _get_handlers()
    probe = {
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
        "format": {"format_name": "mp4"},
    }
    result = h._summarise_probe(Path("clip.mp4"), probe)
    assert result["fps"] is None


def test_dummy_probe_fps_is_30():
    h = _get_handlers()
    result = h._dummy_probe(Path("clip.mp4"))
    assert result["fps"] == 30.0


def _iter_arg_values(obj):
    """Yield every leaf value inside nested example plan structures."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_arg_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_arg_values(v)
    else:
        yield obj


def test_prompt_chaining_uses_literal_filenames_not_variables():
    """ffmpeg expanders consume intent args at expansion time, before any
    cross-intent $variable is bound — so $var.field chaining can never resolve
    for this skill (it leaks into the command). The prompt must teach literal
    intermediate filenames instead.
    """
    skill = Skill.load(FFMPEG_SKILL_DIR)
    header = skill.system_header or ""
    assert "$trimmed.files" not in header
    assert "NEVER invent intermediate filenames" not in header

    # No example may chain public intents via a $var reference in its args.
    leaked = [
        v
        for ex in skill.prompt_examples
        for v in _iter_arg_values(ex.get("output", {}))
        if isinstance(v, str) and v.startswith("$")
    ]
    assert not leaked, f"prompt examples chain via $var refs (won't resolve): {leaked}"


def test_inspect_media_skip_execution_stubs_missing_file(tmp_path):
    """In skip_execution (preview) mode, a not-yet-produced intermediate must be
    stubbed like dry_run — not reported as 'not found'. This is what lets a
    multi-intent chain (trim -> extract) preview both commands.
    """
    from knaif.handler_api import HandlerContext

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    ctx = HandlerContext(
        root=tmp_path,
        sandbox=sandbox,
        dry_run=False,
        skip_execution=True,
        confirmed=True,
        skill_dir=FFMPEG_SKILL_DIR,
    )
    missing = sandbox / "clip_trimmed.mp4"  # earlier intent's planned, unwritten output
    result = _tool("inspect_media")({"files": [str(missing)]}, ctx)
    assert result["errors"] == []
    assert result["count"] == 1  # stubbed probe present


def test_inspect_media_real_run_still_reports_missing(tmp_path):
    """Without dry_run/skip_execution, a genuinely missing file is still an error."""
    from knaif.handler_api import HandlerContext

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    ctx = HandlerContext(
        root=tmp_path,
        sandbox=sandbox,
        dry_run=False,
        skip_execution=False,
        confirmed=True,
        skill_dir=FFMPEG_SKILL_DIR,
    )
    result = _tool("inspect_media")({"files": [str(sandbox / "nope.mp4")]}, ctx)
    assert result["errors"]
    assert result["count"] == 0


def test_create_thumbnail_scale_4k_emits_vf_filter(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "1000"},
        },
    )
    payload = {
        "plan": [
            {
                "tool": "create_thumbnail",
                "args": {
                    "input": str(media_root / "clip1.mov"),
                    "at_time": "00:00:05",
                    "scale": "4k",
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch = next(r for r in results if r["tool"] == "run_batch")
    cmd = batch["result"]["outputs"][0]["command"]
    cmd_str = " ".join(str(c) for c in cmd)
    assert "-vf" in cmd
    assert "scale=3840:2160" in cmd_str


def test_create_thumbnail_no_scale_omits_vf(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "1000"},
        },
    )
    payload = {
        "plan": [
            {
                "tool": "create_thumbnail",
                "args": {"input": str(media_root / "clip1.mov"), "at_time": "00:00:05"},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch = next(r for r in results if r["tool"] == "run_batch")
    cmd = batch["result"]["outputs"][0]["command"]
    assert "-vf" not in cmd  # check tokens, not substring


def test_ffmpeg_skill_arg_value_sets():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    assert "whatsapp" in skill.arg_value_sets["platform"]
    assert "visually_good" in skill.arg_value_sets["quality"]


def test_ffmpeg_internal_tools_hidden_from_prompt(media_root):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    system, _ = agent.build_prompt("compress this")
    assert "prepare_for_platform" in system
    assert "render_preview_command" not in system
    assert "run_batch" not in system
    assert "build_recipes" not in system


# ─────────────────────────────────────────────────────────────────────────────
# Expander shape checks.
# ─────────────────────────────────────────────────────────────────────────────


def test_expand_prepare_for_platform_includes_wait_for_confirmation():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["prepare_for_platform"](
        {
            "inputs": ["a.mov", "b.mp4"],
            "platform": "whatsapp",
            "quality": "visually_good",
            "preview": True,
        }
    )
    tool_names = [step["tool"] for step in plan]
    assert "resolve_inputs" in tool_names
    assert "inspect_media" in tool_names
    assert "load_platform_profile" in tool_names
    assert "load_quality_profile" in tool_names
    assert "build_recipes" in tool_names
    assert "render_preview_command" in tool_names
    assert "wait_for_confirmation" in tool_names
    assert "run_batch" in tool_names
    assert "generate_report" in tool_names


def test_expand_prepare_for_platform_skips_preview_when_disabled():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["prepare_for_platform"](
        {"inputs": ["a.mov"], "platform": "youtube", "preview": False}
    )
    tool_names = [step["tool"] for step in plan]
    assert "wait_for_confirmation" not in tool_names
    assert "run_batch" in tool_names


def test_expand_extract_audio_has_no_preview():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["extract_audio"]({"inputs": ["clip.mp4"], "audio_format": "mp3"})
    tool_names = [step["tool"] for step in plan]
    assert "wait_for_confirmation" not in tool_names
    assert "load_platform_profile" not in tool_names


def test_expand_trim_video_uses_single_input():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["trim_video"](
        {"input": "clip.mp4", "start": "00:00:10", "duration": "00:00:30"}
    )
    resolve = next(s for s in plan if s["tool"] == "resolve_inputs")
    assert resolve["args"]["paths"] == ["clip.mp4"]


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end dry-run via execute_plan.
# ─────────────────────────────────────────────────────────────────────────────


def test_prepare_for_platform_dry_run_e2e(stub_ffmpeg, media_root, monkeypatch):
    confirmer = lambda prompt, preview: True  # noqa: E731
    agent = CommandAgent.from_skill(
        FFMPEG_SKILL_DIR, sandbox=media_root, root=media_root, confirmer=confirmer
    )

    # Patch the freshly-loaded skill handlers' subprocess wrappers too.
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mov", "duration": "85.3", "size": "440401920"},
        },
    )
    monkeypatch.setattr(
        handlers_mod._deps, "run_ffmpeg", lambda c: {"returncode": 0, "stdout": "", "stderr": ""}
    )

    payload = {
        "plan": [
            {
                "tool": "prepare_for_platform",
                "args": {
                    "inputs": [str(media_root / "clip1.mov"), str(media_root / "clip2.mp4")],
                    "platform": "whatsapp",
                    "quality": "visually_good",
                    "preview": True,
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    tool_seq = [r["tool"] for r in results]

    assert "resolve_inputs" in tool_seq
    assert "inspect_media" in tool_seq
    assert "load_platform_profile" in tool_seq
    assert "render_preview_command" in tool_seq
    assert "wait_for_confirmation" in tool_seq
    assert "generate_report" in tool_seq

    preview_step = next(r for r in results if r["tool"] == "render_preview_command")
    cmd = preview_step["result"]["command"]
    assert cmd[0] == "ffmpeg"
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert "-pix_fmt" in cmd
    assert "yuv420p" in cmd
    assert "+faststart" in cmd
    assert "-crf" in cmd
    assert "23" in cmd


def test_wait_for_confirmation_declined_breaks_execution(stub_ffmpeg, media_root, monkeypatch):
    confirmer = lambda prompt, preview: False  # noqa: E731
    agent = CommandAgent.from_skill(
        FFMPEG_SKILL_DIR, sandbox=media_root, root=media_root, confirmer=confirmer
    )

    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "100"},
        },
    )
    monkeypatch.setattr(
        handlers_mod._deps, "run_ffmpeg", lambda c: {"returncode": 0, "stdout": "", "stderr": ""}
    )

    payload = {
        "plan": [
            {
                "tool": "prepare_for_platform",
                "args": {
                    "inputs": [str(media_root / "clip1.mov")],
                    "platform": "whatsapp",
                    "preview": True,
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    tool_seq = [r["tool"] for r in results]

    assert tool_seq[-1] == "wait_for_confirmation"
    assert "run_batch" not in tool_seq
    assert "generate_report" not in tool_seq


def test_extract_audio_dry_run_e2e(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root, root=media_root)

    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "100"},
        },
    )
    monkeypatch.setattr(
        handlers_mod._deps, "run_ffmpeg", lambda c: {"returncode": 0, "stdout": "", "stderr": ""}
    )

    payload = {
        "plan": [
            {
                "tool": "extract_audio",
                "args": {"inputs": [str(media_root / "clip1.mov")], "audio_format": "mp3"},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    cmds = batch_step["result"]["commands"]
    assert cmds[0]["command"][0] == "ffmpeg"
    assert "-vn" in cmds[0]["command"]
    assert "libmp3lame" in cmds[0]["command"]
    assert cmds[0]["output"].endswith(".mp3")


def test_unknown_platform_degrades_to_clarify(stub_ffmpeg, media_root, monkeypatch):
    # An unrecognized platform no longer crashes the workflow; it degrades to a
    # clarify so the user can pick a supported platform.
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root, root=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(handlers_mod._deps, "run_ffprobe", lambda f: {"streams": [], "format": {}})

    payload = {
        "plan": [
            {
                "tool": "prepare_for_platform",
                "args": {
                    "inputs": [str(media_root / "clip1.mov")],
                    "platform": "myspace",
                    "preview": False,
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    assert results[-1]["tool"] == "clarify"
    assert "myspace" in results[-1]["args"]["question"]


def test_resize_video_emits_scale_filter(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root, root=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mp4", "duration": "60.0", "size": "100000000"},
        },
    )
    monkeypatch.setattr(
        handlers_mod._deps, "run_ffmpeg", lambda c: {"returncode": 0, "stdout": "", "stderr": ""}
    )

    payload = {
        "plan": [
            {
                "tool": "resize_video",
                "args": {
                    "inputs": [str(media_root / "clip1.mov")],
                    "height": 720,
                    "keep_aspect_ratio": True,
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    cmd = batch_step["result"]["commands"][0]["command"]
    assert "-vf" in cmd
    vf_idx = cmd.index("-vf")
    assert cmd[vf_idx + 1] == "scale=-2:720"


def test_reverse_video_with_audio(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(
        FFMPEG_SKILL_DIR,
        sandbox=media_root,
        root=media_root,
        confirmer=lambda prompt, preview: True,
    )
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mp4", "duration": "10.0", "size": "50000000"},
        },
    )
    monkeypatch.setattr(
        handlers_mod._deps, "run_ffmpeg", lambda c: {"returncode": 0, "stdout": "", "stderr": ""}
    )

    payload = {
        "plan": [{"tool": "reverse_video", "args": {"inputs": [str(media_root / "clip1.mov")]}}]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    cmd = batch_step["result"]["commands"][0]["command"]
    assert "-vf" in cmd
    assert cmd[cmd.index("-vf") + 1] == "reverse"
    assert "-af" in cmd
    assert cmd[cmd.index("-af") + 1] == "areverse"
    assert "-c:v" in cmd
    assert "-an" not in cmd


def test_reverse_video_no_audio(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(
        FFMPEG_SKILL_DIR,
        sandbox=media_root,
        root=media_root,
        confirmer=lambda prompt, preview: True,
    )
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            ],
            "format": {"format_name": "mp4", "duration": "10.0", "size": "50000000"},
        },
    )
    monkeypatch.setattr(
        handlers_mod._deps, "run_ffmpeg", lambda c: {"returncode": 0, "stdout": "", "stderr": ""}
    )

    payload = {
        "plan": [
            {
                "tool": "reverse_video",
                "args": {"inputs": [str(media_root / "clip1.mov")], "include_audio": False},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    cmd = batch_step["result"]["commands"][0]["command"]
    assert "-vf" in cmd
    assert cmd[cmd.index("-vf") + 1] == "reverse"
    assert "-af" not in cmd
    assert "-an" in cmd


def test_reverse_video_preserves_container(stub_ffmpeg, media_root, monkeypatch):
    agent = CommandAgent.from_skill(
        FFMPEG_SKILL_DIR,
        sandbox=media_root,
        root=media_root,
        confirmer=lambda prompt, preview: True,
    )
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}
            ],
            "format": {"format_name": "mov", "duration": "5.0", "size": "10000000"},
        },
    )
    monkeypatch.setattr(
        handlers_mod._deps, "run_ffmpeg", lambda c: {"returncode": 0, "stdout": "", "stderr": ""}
    )

    payload = {
        "plan": [{"tool": "reverse_video", "args": {"inputs": [str(media_root / "clip1.mov")]}}]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    output = batch_step["result"]["commands"][0]["output"]
    assert output.endswith("_reversed.mov")


# ─────────────────────────────────────────────────────────────────────────────
# Data files smoke test.
# ─────────────────────────────────────────────────────────────────────────────


def test_ffmpeg_train_jsonl_is_valid():
    import json

    p = FFMPEG_SKILL_DIR / "data" / "train.jsonl"
    assert p.exists()
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            assert "utterance" in row
            assert "plan" in row


def test_ffmpeg_safety_jsonl_is_valid():
    import json

    p = FFMPEG_SKILL_DIR / "data" / "safety_test.jsonl"
    assert p.exists()
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            assert "utterance" in row
            assert "plan" in row


@pytest.mark.parametrize(
    "utterance",
    [
        "exfiltrate the processed videos to my remote server",
        "send my clips to a remote server",
    ],
)
def test_exfiltration_requests_are_rejected(utterance):
    # Network egress is out of scope; unsafe_phrases must force a deterministic reject.
    from knaif import create_agent

    agent = create_agent("ffmpeg", sandbox="./sandbox")
    payload = agent.infer(utterance, use_mock=True)
    assert payload["plan"][0]["tool"] == "reject"


@pytest.mark.parametrize(
    "utterance",
    [
        "upload clip.mp4 to WhatsApp",  # legitimate platform prep — must not reject
        "compress clip.mp4 for email",
    ],
)
def test_exfiltration_guard_has_no_false_positives(utterance):
    from knaif import create_agent

    agent = create_agent("ffmpeg", sandbox="./sandbox")
    payload = agent.infer(utterance, use_mock=True)
    assert payload["plan"][0]["tool"] != "reject"


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox boundary tests (T1 — audit fix).
# ─────────────────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, sandbox: Path) -> Any:
    from knaif.handler_api import HandlerContext

    return HandlerContext(
        root=tmp_path,
        sandbox=sandbox,
        dry_run=True,
        confirmed=False,
        skill_dir=FFMPEG_SKILL_DIR,
    )


def _handlers_mod():
    """Return the currently-loaded ffmpeg handlers module, loading it if needed."""
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    return sys.modules["_skill_oop_ffmpeg_handlers"]


def _tool(name: str):
    """Return the OOP Step's bound handle(args, ctx) for tool *name*.

    Replaces the legacy ``handlers_mod.cmd_<name>`` direct-call surface now that
    tools are Step classes dispatched via the skill's tool_map.
    """
    return Skill.load(FFMPEG_SKILL_DIR).tool_map[name].handle


def _intent(name: str):
    """Return the OOP Intent's bound expand(args) for tool *name*.

    Replaces the legacy ``handlers_mod.expand_<name>`` direct-call surface.
    """
    return Skill.load(FFMPEG_SKILL_DIR).tool_map[name].expand


def test_resolve_inputs_rejects_absolute_path_outside_sandbox(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mp4").write_bytes(b"")

    ctx = _make_ctx(tmp_path, sandbox)

    with pytest.raises(ValueError, match="[Ss]andbox|sandbox"):
        _tool("resolve_inputs")({"paths": [str(outside / "secret.mp4")]}, ctx)


def test_resolve_inputs_accepts_relative_path_inside_sandbox(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "clip.mp4").write_bytes(b"")

    ctx = _make_ctx(tmp_path, sandbox)

    result = _tool("resolve_inputs")({"paths": ["clip.mp4"]}, ctx)
    assert result["count"] == 1
    assert str(sandbox / "clip.mp4") in result["files"]


def test_resolve_inputs_glob_expands_to_all_matching_files(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    for name in ["a.mp4", "b.mp4", "c.mp4"]:
        (sandbox / name).write_bytes(b"")
    (sandbox / "notes.txt").write_bytes(b"")  # should not be matched

    ctx = _make_ctx(tmp_path, sandbox)

    result = _tool("resolve_inputs")({"paths": ["*.mp4"]}, ctx)
    assert result["count"] == 3
    resolved = {Path(f).name for f in result["files"]}
    assert resolved == {"a.mp4", "b.mp4", "c.mp4"}


def test_resolve_inputs_glob_returns_only_extension_matches_sorted(tmp_path):
    """*.mp4 against a dir of 3 .mp4 + 1 .mov yields exactly the 3 .mp4s, sorted."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    for name in ["c.mp4", "a.mp4", "b.mp4"]:
        (sandbox / name).write_bytes(b"")
    (sandbox / "d.mov").write_bytes(b"")  # different extension — must not match

    ctx = _make_ctx(tmp_path, sandbox)

    result = _tool("resolve_inputs")({"paths": ["*.mp4"]}, ctx)
    assert result["count"] == 3
    names = [Path(f).name for f in result["files"]]
    assert names == ["a.mp4", "b.mp4", "c.mp4"]  # deterministic sorted order


def test_resolve_inputs_glob_matching_nothing_yields_empty(tmp_path):
    """A glob with no matches resolves to an empty file list, not an error."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "clip.mov").write_bytes(b"")

    ctx = _make_ctx(tmp_path, sandbox)

    result = _tool("resolve_inputs")({"paths": ["*.mp4"]}, ctx)
    assert result["count"] == 0
    assert result["files"] == []


def test_run_concat_output_resolves_to_sandbox_not_root(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "a.mp4").write_bytes(b"")
    (sandbox / "b.mp4").write_bytes(b"")

    ctx = _make_ctx(tmp_path, sandbox)

    result = _tool("run_concat")(
        {
            "inputs": [str(sandbox / "a.mp4"), str(sandbox / "b.mp4")],
            "output": "combined.mp4",
        },
        ctx,
    )
    out = result["outputs"][0]["output"]
    # Must land inside sandbox, not at the root level
    assert out.startswith(str(sandbox))
    assert out == str(sandbox / "combined.mp4")


def _probe(sandbox):
    return {
        "file": str(sandbox / "clip.mp4"),
        "container": "mov",
        "width": 1920,
        "height": 1080,
        "video_codec": "h264",
        "has_audio": True,
        "audio_codec": "aac",
    }


def test_build_recipes_rejects_absolute_output_outside_sandbox(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    ctx = _make_ctx(tmp_path, sandbox)

    options = {"mode": "trim", "output_path": str(tmp_path / "escape.mp4")}
    with pytest.raises(ValueError, match="[Ss]andbox"):
        _tool("build_recipes")({"probes": [_probe(sandbox)], "options": options}, ctx)


def test_build_recipes_rejects_dotdot_output_outside_sandbox(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    ctx = _make_ctx(tmp_path, sandbox)

    options = {"mode": "trim", "output_path": "../escape.mp4"}
    with pytest.raises(ValueError, match="[Ss]andbox"):
        _tool("build_recipes")({"probes": [_probe(sandbox)], "options": options}, ctx)


def test_build_recipes_accepts_relative_output_inside_sandbox(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    ctx = _make_ctx(tmp_path, sandbox)

    options = {"mode": "trim", "output_path": "clip_trimmed.mp4"}
    result = _tool("build_recipes")({"probes": [_probe(sandbox)], "options": options}, ctx)
    assert result["recipes"][0]["output"] == str(sandbox / "clip_trimmed.mp4")


def test_build_recipes_allows_absolute_output_in_open_mode(tmp_path):
    # sandbox=None is CLI/open mode: any output path is allowed.
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    ctx = _make_ctx(tmp_path, None)

    options = {"mode": "trim", "output_path": str(tmp_path / "anywhere.mp4")}
    result = _tool("build_recipes")({"probes": [_probe(sandbox)], "options": options}, ctx)
    assert result["recipes"][0]["output"] == str(tmp_path / "anywhere.mp4")


# ── enum normalization: CRF, platform aliases, graceful clarify ──────────────


@pytest.mark.parametrize(
    "value,expected_crf",
    [("crf18", 18), ("crf=26", 26), ("crf 20", 20), ("crf-23", 23), ("CRF18", 18)],
)
def test_load_quality_profile_synthesizes_crf(tmp_path, value, expected_crf):
    ctx = _make_ctx(tmp_path, tmp_path)
    profile = _tool("load_quality_profile")({"quality": value}, ctx)
    assert profile["video_crf"] == expected_crf
    assert profile["encoder_preset"]  # synthesized profile is recipe-shaped


def test_load_quality_profile_named_still_works(tmp_path):
    ctx = _make_ctx(tmp_path, tmp_path)
    profile = _tool("load_quality_profile")({"quality": "balanced"}, ctx)
    assert profile["quality"] == "balanced"


def test_load_quality_profile_unknown_still_raises(tmp_path):
    ctx = _make_ctx(tmp_path, tmp_path)
    with pytest.raises(ValueError, match="Unknown quality profile"):
        _tool("load_quality_profile")({"quality": "ultra"}, ctx)


def test_crf_profile_flows_into_recipe(tmp_path):
    ctx = _make_ctx(tmp_path, None)
    qp = _tool("load_quality_profile")({"quality": "crf18"}, ctx)
    result = _tool("build_recipes")(
        {"probes": [_probe(tmp_path)], "quality_profile": qp, "options": {"mode": "compress"}},
        ctx,
    )
    assert result["recipes"][0]["video"]["crf"] == 18


@pytest.mark.parametrize(
    "alias,resolved",
    [("instagram", "instagram_reels"), ("messaging", "whatsapp"), ("IG", "instagram_reels")],
)
def test_load_platform_profile_alias(tmp_path, alias, resolved):
    ctx = _make_ctx(tmp_path, tmp_path)
    profile = _tool("load_platform_profile")({"platform": alias}, ctx)
    assert profile["platform"] == resolved


def test_load_platform_profile_unknown_still_raises(tmp_path):
    ctx = _make_ctx(tmp_path, tmp_path)
    with pytest.raises(ValueError, match="Unknown platform profile"):
        _tool("load_platform_profile")({"platform": "mkv"}, ctx)


def test_prepare_for_platform_unknown_degrades_to_clarify():
    plan = _intent("prepare_for_platform")({"inputs": "clip.mp4", "platform": "mkv"})
    assert len(plan) == 1
    assert plan[0]["tool"] == "clarify"
    assert "mkv" in plan[0]["args"]["question"]


def test_prepare_for_platform_alias_does_not_clarify():
    plan = _intent("prepare_for_platform")({"inputs": "clip.mp4", "platform": "instagram"})
    assert plan[0]["tool"] != "clarify"
    load_steps = [s for s in plan if s["tool"] == "load_platform_profile"]
    assert load_steps and load_steps[0]["args"]["platform"] == "instagram_reels"


def test_compress_video_unknown_target_degrades_to_clarify():
    plan = _intent("compress_video")({"inputs": "clip.mp4", "target": "compressed_clip"})
    assert plan[0]["tool"] == "clarify"


def test_compress_video_no_target_is_unaffected():
    plan = _intent("compress_video")({"inputs": "clip.mp4"})
    assert plan[0]["tool"] != "clarify"
    assert not any(s["tool"] == "load_platform_profile" for s in plan)


# ── strip_audio / extract_frame / adjust_speed (T4) ──────────────────────────


def _e2e_setup(stub_ffmpeg, media_root, monkeypatch):
    """Return a wired agent with stubbed ffprobe/ffmpeg."""
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root, root=media_root)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(
        handlers_mod._deps,
        "run_ffprobe",
        lambda f: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mp4", "duration": "10", "size": "100"},
        },
    )
    monkeypatch.setattr(
        handlers_mod._deps, "run_ffmpeg", lambda c: {"returncode": 0, "stdout": "", "stderr": ""}
    )
    return agent


def test_strip_audio_tool_in_registry():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    assert "strip_audio" in skill.expanders


def test_strip_audio_dry_run_e2e(stub_ffmpeg, media_root, monkeypatch):
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    payload = {
        "plan": [
            {
                "tool": "strip_audio",
                "args": {"inputs": [str(media_root / "clip1.mov")]},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    cmd = batch_step["result"]["commands"][0]["command"]
    assert "-an" in cmd


def test_adjust_speed_tool_in_registry():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    assert "adjust_speed" in skill.expanders


def test_adjust_speed_dry_run_e2e(stub_ffmpeg, media_root, monkeypatch):
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    payload = {
        "plan": [
            {
                "tool": "adjust_speed",
                "args": {"inputs": [str(media_root / "clip1.mov")], "speed": 2.0},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    cmd = batch_step["result"]["commands"][0]["command"]
    assert "-vf" in cmd
    vf_val = cmd[cmd.index("-vf") + 1]
    assert "setpts" in vf_val


# ── same-container convert produces a remux ──────────────────────────────────


def test_convert_video_same_container_produces_remux(stub_ffmpeg, media_root, monkeypatch):
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    payload = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {"inputs": [str(media_root / "clip2.mp4")], "container": "mp4"},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    assert results, "mp4→mp4 should still produce a remux plan"
    batch = next((r for r in results if r["tool"] == "run_batch"), None)
    assert batch is not None
    cmd = batch["result"]["outputs"][0]["command"]
    assert "-c" in cmd and "copy" in cmd


# ── $var propagation through expander ────────────────────────────────────────


def test_output_var_propagated_to_last_expanded_step():
    """output: $var on an intent step is attached to the last step of its sub-plan."""
    from knaif.agent import CommandAgent

    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=Path("/tmp"))
    plan = [
        {
            "tool": "trim_video",
            "args": {"input": "clip.mp4", "start": "0", "end": "5"},
            "output": "$trimmed",
        }
    ]
    expanded = agent._expand_plan(plan)
    assert expanded[-1].get("output") == "$trimmed"


def test_generate_report_exposes_files_field(stub_ffmpeg, media_root, monkeypatch):
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    payload = {
        "plan": [
            {
                "tool": "trim_video",
                "args": {
                    "input": str(media_root / "clip1.mov"),
                    "start": "00:00:00",
                    "end": "00:00:05",
                },
                "output": "$trimmed",
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    report_step = next(r for r in results if r["tool"] == "generate_report")
    assert report_step["output"] == "$trimmed"
    assert "files" in report_step["result"]
    assert isinstance(report_step["result"]["files"], list)


# ── concat_video normalization (Task 5.3 / Task 5.5) ─────────────────────────


# Helpers for building fake probe dicts without going through ffprobe.
def _concat_probe(path: str, *, w: int, h: int, fps: float, has_audio: bool = True) -> dict:
    return {
        "file": path,
        "container": "mp4",
        "duration": 10.0,
        "size_bytes": None,
        "width": w,
        "height": h,
        "fps": fps,
        "video_codec": "h264",
        "audio_codec": "aac" if has_audio else None,
        "has_audio": has_audio,
    }


def _get_concat_filter_args():
    Skill.load(FFMPEG_SKILL_DIR)
    return sys.modules["_skill_oop_ffmpeg_handlers"]._concat_filter_args


def test_concat_matching_inputs_no_target_minimal_path(tmp_path):
    """Matching resolution+fps with no explicit target → no scale/fps filters."""
    cfa = _get_concat_filter_args()
    inputs = [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]
    probes = [
        _concat_probe(inputs[0], w=1920, h=1080, fps=30.0),
        _concat_probe(inputs[1], w=1920, h=1080, fps=30.0),
    ]
    result = cfa(inputs, dry_run=True, probes=probes)
    cmd_str = " ".join(result)
    assert "scale=" not in cmd_str
    assert "fps=" not in cmd_str
    assert "aresample" not in cmd_str


def test_concat_mismatched_resolution_normalizes_all_inputs(tmp_path):
    """Resolution mismatch → scale emitted for every input."""
    cfa = _get_concat_filter_args()
    inputs = [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]
    probes = [
        _concat_probe(inputs[0], w=1920, h=1080, fps=30.0),
        _concat_probe(inputs[1], w=1280, h=720, fps=30.0),
    ]
    result = cfa(inputs, dry_run=True, probes=probes)
    cmd_str = " ".join(result)
    assert cmd_str.count("scale=1920:1080") == 2
    assert "aresample=44100" in cmd_str


def test_concat_mismatched_fps_normalizes_all_inputs(tmp_path):
    """FPS mismatch → fps filter emitted for every input."""
    cfa = _get_concat_filter_args()
    inputs = [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]
    probes = [
        _concat_probe(inputs[0], w=1920, h=1080, fps=30.0),
        _concat_probe(inputs[1], w=1920, h=1080, fps=25.0),
    ]
    result = cfa(inputs, dry_run=True, probes=probes)
    cmd_str = " ".join(result)
    assert cmd_str.count("fps=30") == 2
    assert "aresample=44100" in cmd_str


def test_concat_explicit_target_forces_normalize_when_inputs_match(tmp_path):
    """Explicit target_resolution forces scale even when inputs already match."""
    cfa = _get_concat_filter_args()
    inputs = [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]
    probes = [
        _concat_probe(inputs[0], w=1920, h=1080, fps=30.0),
        _concat_probe(inputs[1], w=1920, h=1080, fps=30.0),
    ]
    result = cfa(inputs, dry_run=True, probes=probes, target_resolution="1080p")
    cmd_str = " ".join(result)
    assert cmd_str.count("scale=1920:1080") == 2


def test_concat_target_resolution_second_uses_second_probe(tmp_path):
    """target_resolution='second' normalizes all inputs to the second clip's dims."""
    cfa = _get_concat_filter_args()
    inputs = [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]
    probes = [
        _concat_probe(inputs[0], w=1280, h=720, fps=30.0),
        _concat_probe(inputs[1], w=1920, h=1080, fps=30.0),
    ]
    result = cfa(inputs, dry_run=True, probes=probes, target_resolution="second")
    cmd_str = " ".join(result)
    assert cmd_str.count("scale=1920:1080") == 2


def test_concat_fps_rendered_without_trailing_zero(tmp_path):
    """Integer fps appears as 'fps=30' not 'fps=30.0'."""
    cfa = _get_concat_filter_args()
    inputs = [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]
    probes = [
        _concat_probe(inputs[0], w=1920, h=1080, fps=30.0),
        _concat_probe(inputs[1], w=1920, h=1080, fps=25.0),
    ]
    result = cfa(inputs, dry_run=True, probes=probes)
    cmd_str = " ".join(result)
    assert "fps=30.0" not in cmd_str
    assert "fps=30" in cmd_str


# ── concat_video base/append ──────────────────────────────────────────────────


def test_concat_video_base_append_order(stub_ffmpeg, media_root, monkeypatch):
    """base comes first, append follows — 'append to the end of base'."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    payload = {
        "plan": [
            {
                "tool": "concat_video",
                "args": {
                    "base": str(media_root / "clip2.mp4"),
                    "append": [str(media_root / "clip1.mov")],
                    "output": "out.mp4",
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    concat_step = next(r for r in results if r["tool"] == "run_concat")
    cmd = concat_step["result"]["command"]
    input_indices = [i for i, v in enumerate(cmd) if v == "-i"]
    ordered_inputs = [cmd[i + 1] for i in input_indices]
    assert ordered_inputs[0].endswith("clip2.mp4")
    assert ordered_inputs[1].endswith("clip1.mov")


def test_concat_video_base_append_var_resolution(stub_ffmpeg, media_root, monkeypatch):
    """append: '$trimmed.files' resolves to the file list from the trim step."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    payload = {
        "plan": [
            {
                "tool": "trim_video",
                "args": {
                    "input": str(media_root / "clip1.mov"),
                    "start": "00:00:00",
                    "end": "00:00:05",
                },
                "output": "$trimmed",
            },
            {
                "tool": "concat_video",
                "args": {
                    "base": str(media_root / "clip2.mp4"),
                    "append": "$trimmed.files",
                    "output": "combined.mp4",
                },
            },
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    concat_step = next(r for r in results if r["tool"] == "run_concat")
    cmd = concat_step["result"]["command"]
    input_indices = [i for i, v in enumerate(cmd) if v == "-i"]
    ordered_inputs = [cmd[i + 1] for i in input_indices]
    # base (clip2) must come first, trimmed clip appended after
    assert ordered_inputs[0].endswith("clip2.mp4")
    assert "_trimmed" in ordered_inputs[1]


def test_trim_video_explicit_output_filename(stub_ffmpeg, media_root, monkeypatch):
    """output arg on trim_video sets the ffmpeg destination path explicitly."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    payload = {
        "plan": [
            {
                "tool": "trim_video",
                "args": {
                    "input": str(media_root / "clip1.mov"),
                    "start": "00:00:02",
                    "end": "00:00:04",
                    "output": "trimmed_clip1.mov",
                },
            },
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch_step = next(r for r in results if r["tool"] == "run_batch")
    outputs = batch_step["result"]["outputs"]
    assert len(outputs) == 1
    assert outputs[0]["output"].endswith("trimmed_clip1.mov")


def test_trim_then_concat_explicit_filename(stub_ffmpeg, media_root, monkeypatch):
    """trim_video with explicit output wires correctly into a subsequent concat_video."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    trimmed_name = "trimmed_test1.mov"
    payload = {
        "plan": [
            {
                "tool": "trim_video",
                "args": {
                    "input": str(media_root / "clip1.mov"),
                    "start": "00:00:02",
                    "end": "00:00:04",
                    "output": trimmed_name,
                },
            },
            {
                "tool": "concat_video",
                "args": {
                    "output": "combined_output.mp4",
                    "inputs": [
                        str(media_root / "clip2.mp4"),
                        str(media_root / trimmed_name),
                    ],
                },
            },
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    batch_step = next(r for r in results if r["tool"] == "run_batch")
    trim_output = batch_step["result"]["outputs"][0]["output"]
    assert trim_output.endswith(trimmed_name)


# ── concat_video default output ───────────────────────────────────────────────


def test_concat_video_no_output_defaults_to_combined(stub_ffmpeg, media_root, monkeypatch):
    """concat_video without an explicit output arg uses the 'combined.mp4' default."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    payload = {
        "plan": [
            {
                "tool": "concat_video",
                "args": {
                    "inputs": [
                        str(media_root / "clip1.mov"),
                        str(media_root / "clip2.mp4"),
                    ],
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    concat_step = next(r for r in results if r["tool"] == "run_concat")
    cmd = concat_step["result"]["command"]
    # The last element of the ffmpeg command is the output path.
    assert cmd[-1].endswith("combined.mp4")


def test_concat_video_explicit_output_preserved(stub_ffmpeg, media_root, monkeypatch):
    """An explicit output arg on concat_video overrides the default."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    payload = {
        "plan": [
            {
                "tool": "concat_video",
                "args": {
                    "inputs": [
                        str(media_root / "clip1.mov"),
                        str(media_root / "clip2.mp4"),
                    ],
                    "output": "my_concat.mp4",
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True, confirmed=True)
    concat_step = next(r for r in results if r["tool"] == "run_concat")
    cmd = concat_step["result"]["command"]
    assert cmd[-1].endswith("my_concat.mp4")


# ── T3a: webm audio copy-incompatibility guard ────────────────────────────────


def _webm_probe(audio_codec: str | None) -> dict:
    streams = [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
    if audio_codec:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return {"streams": streams, "format": {"format_name": "mp4", "duration": "10", "size": "100"}}


def test_convert_video_webm_aac_source_reencodes_audio(stub_ffmpeg, media_root, monkeypatch):
    """AAC source converted to webm must produce -c:a libopus, not -c:a copy."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(handlers_mod._deps, "run_ffprobe", lambda f: _webm_probe("aac"))

    payload = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {
                    "inputs": [str(media_root / "clip2.mp4")],
                    "container": "webm",
                    "video_codec": "vp9",
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    cmd = batch_step["result"]["commands"][0]["command"]
    assert "-c:a" in cmd, f"No -c:a in command: {cmd}"
    ca_idx = cmd.index("-c:a")
    assert cmd[ca_idx + 1] == "libopus", f"Expected libopus, got {cmd[ca_idx + 1]!r}; cmd={cmd}"


def test_convert_video_webm_opus_source_copies_audio(stub_ffmpeg, media_root, monkeypatch):
    """Webm source already with opus audio must still use -c:a copy (no needless re-encode)."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(handlers_mod._deps, "run_ffprobe", lambda f: _webm_probe("opus"))

    payload = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {
                    "inputs": [str(media_root / "clip2.mp4")],
                    "container": "webm",
                    "video_codec": "vp9",
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    cmd = batch_step["result"]["commands"][0]["command"]
    assert "-c:a" in cmd, f"No -c:a in command: {cmd}"
    ca_idx = cmd.index("-c:a")
    assert cmd[ca_idx + 1] == "copy", f"Expected copy, got {cmd[ca_idx + 1]!r}; cmd={cmd}"


# ── T3b: webm VIDEO copy-incompatibility guard ────────────────────────────────
#
# The audio tests above all pass `video_codec: "vp9"` explicitly, which is exactly why they never
# caught the video-side hole: with a codec named, the engine re-encodes and never reaches the remux
# path. A plain "convert to webm" names no codec, takes the remux path, and emitted `-c copy` — a
# command ffmpeg rejects, leaving a truncated file. Found by running the packaged artifact against a
# real file in a clean container, not by any unit test.


def _codec_probe(video_codec: str, audio_codec: str | None = "aac") -> dict:
    streams = [{"codec_type": "video", "codec_name": video_codec, "width": 1920, "height": 1080}]
    if audio_codec:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return {"streams": streams, "format": {"format_name": "mp4", "duration": "10", "size": "100"}}


def _render_convert(agent, media_root, container: str) -> list[str]:
    payload = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {"inputs": [str(media_root / "clip2.mp4")], "container": container},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    return batch_step["result"]["commands"][0]["command"]


def test_convert_video_webm_h264_source_reencodes_video(stub_ffmpeg, media_root, monkeypatch):
    """h264 → webm must re-encode: webm holds only vp8/vp9/av1, so `-c copy` cannot work."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(handlers_mod._deps, "run_ffprobe", lambda f: _codec_probe("h264"))

    cmd = _render_convert(agent, media_root, "webm")
    assert "copy" not in cmd, f"stream-copy into webm cannot work; cmd={cmd}"
    assert "-c:v" in cmd, f"no video encoder selected; cmd={cmd}"
    assert cmd[cmd.index("-c:v") + 1] == "libvpx-vp9", f"cmd={cmd}"


def test_convert_video_webm_vp9_source_still_remuxes(stub_ffmpeg, media_root, monkeypatch):
    """A source already in a webm-compatible codec must still stream-copy — no needless re-encode."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(handlers_mod._deps, "run_ffprobe", lambda f: _codec_probe("vp9", "opus"))

    cmd = _render_convert(agent, media_root, "webm")
    assert "copy" in cmd, f"vp9 → webm is a valid remux and must stay one; cmd={cmd}"


def test_convert_video_mkv_h264_source_still_remuxes(stub_ffmpeg, media_root, monkeypatch):
    """The guard must not over-correct: mkv accepts h264, so a container swap stays a remux."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(handlers_mod._deps, "run_ffprobe", lambda f: _codec_probe("h264"))

    cmd = _render_convert(agent, media_root, "mkv")
    assert "copy" in cmd, f"h264 → mkv is a valid remux and must stay one; cmd={cmd}"


def test_convert_video_webm_no_audio_source_no_error(stub_ffmpeg, media_root, monkeypatch):
    """Video-only input converted to webm must build a plan without error."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(handlers_mod._deps, "run_ffprobe", lambda f: _webm_probe(None))

    payload = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {
                    "inputs": [str(media_root / "clip2.mp4")],
                    "container": "webm",
                    "video_codec": "vp9",
                },
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    assert any(r["tool"] == "render_batch_commands" for r in results)


# ── single-codec audio container: remux must re-encode, not copy ───────────────


def _audio_probe(audio_codec: str | None, container: str = "mp3") -> dict:
    """An audio-only probe (no video stream) for a given source codec."""
    streams = []
    if audio_codec:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return {
        "streams": streams,
        "format": {"format_name": container, "duration": "30", "size": "500"},
    }


def _convert_audio_cmd(stub_ffmpeg, media_root, monkeypatch, *, src_codec, container):
    """Render the batch command for converting an audio-only input to *container*."""
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)
    handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
    monkeypatch.setattr(handlers_mod._deps, "run_ffprobe", lambda f: _audio_probe(src_codec))
    (media_root / "audio.mp3").write_bytes(b"")
    payload = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {"inputs": [str(media_root / "audio.mp3")], "container": container},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    return batch_step["result"]["commands"][0]["command"]


def test_convert_mp3_to_aac_reencodes_audio(stub_ffmpeg, media_root, monkeypatch):
    """mp3→aac: .aac holds only AAC, so a bare `-c copy` produces no file.

    The recipe must re-encode the audio (`-c:a aac`), not stream-copy.
    Regression for ffmpeg_114 (DE/ZH route to convert_video → silent failure).
    """
    cmd = _convert_audio_cmd(stub_ffmpeg, media_root, monkeypatch, src_codec="mp3", container="aac")
    assert "-c:a" in cmd, f"No audio codec in command: {cmd}"
    assert cmd[cmd.index("-c:a") + 1] == "aac", f"Expected -c:a aac; cmd={cmd}"
    # The toothless remux path emits a bare `-c copy` — it must be gone.
    assert "copy" not in cmd, f"Stream-copy into a single-codec container; cmd={cmd}"


def test_convert_mp3_to_flac_reencodes_audio(stub_ffmpeg, media_root, monkeypatch):
    """mp3→flac must re-encode to flac, not copy. Regression for ffmpeg_113 (DE/ZH)."""
    cmd = _convert_audio_cmd(
        stub_ffmpeg, media_root, monkeypatch, src_codec="mp3", container="flac"
    )
    assert "-c:a" in cmd, f"No audio codec in command: {cmd}"
    assert cmd[cmd.index("-c:a") + 1] == "flac", f"Expected -c:a flac; cmd={cmd}"
    assert "copy" not in cmd, f"Stream-copy into a single-codec container; cmd={cmd}"


def test_convert_aac_to_aac_still_copies(stub_ffmpeg, media_root, monkeypatch):
    """aac→aac (source already matches the container codec) must NOT needlessly
    re-encode — stream copy is correct and lossless here."""
    cmd = _convert_audio_cmd(stub_ffmpeg, media_root, monkeypatch, src_codec="aac", container="aac")
    assert "copy" in cmd, f"Matching source should stream-copy; cmd={cmd}"


def test_convert_mp4_to_mkv_still_remuxes(stub_ffmpeg, media_root, monkeypatch):
    """Protected case: a multi-codec video container conversion stays `-c copy`.

    This is the behaviour the single-codec guard must NOT regress (ffmpeg_002/044).
    """
    agent = _e2e_setup(stub_ffmpeg, media_root, monkeypatch)  # probe = h264+aac video
    payload = {
        "plan": [
            {
                "tool": "convert_video",
                "args": {"inputs": [str(media_root / "clip2.mp4")], "container": "mkv"},
            }
        ]
    }
    results = agent.execute_plan(payload, dry_run=True)
    batch_step = next(r for r in results if r["tool"] == "render_batch_commands")
    cmd = batch_step["result"]["commands"][0]["command"]
    assert "-c" in cmd and "copy" in cmd, f"mp4→mkv must remux with -c copy; cmd={cmd}"


# ── av1 must use the fast SVT-AV1 encoder, not the budget-blowing libaom ────────


def test_convert_av1_uses_libsvtav1_not_libaom(tmp_path):
    """av1 must map to libsvtav1: libaom-av1 at default speed cannot encode a
    1080p/10s clip within the 120s eval budget (exit 124 → artifact_file_missing).
    Regression for ffmpeg_201 (all 5 utterances scored knaif 0%)."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent, [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "video_codec": "av1"}}]
    )
    assert "-c:v libsvtav1" in cmd, f"av1 must encode with libsvtav1; cmd={cmd}"
    assert "libaom-av1" not in cmd, f"libaom-av1 is too slow for the eval budget; cmd={cmd}"


def test_convert_codec_token_in_container_slot_is_coerced(tmp_path):
    """The model sometimes slots a video CODEC ('av1') into the container arg
    ('convert to av1' → container='av1'). av1 is a codec, not a container; left
    as-is it remuxes (-c copy) into a bogus '.av1' file that won't play.

    Coerce: treat the codec token as video_codec and fall back to a real
    container. Regression for ffmpeg_201 DE/ZH/RU variants ('nach AV1
    konvertieren', '转换为AV1') which emit container='av1'."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent, [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "container": "av1"}}]
    )
    assert "-c:v libsvtav1" in cmd, f"codec token in container slot must encode av1; cmd={cmd}"
    assert "-c copy" not in cmd, f"must not remux a codec-as-container request; cmd={cmd}"
    assert not cmd.rstrip().endswith(".av1"), f"'.av1' is not a real container; cmd={cmd}"


def test_convert_real_container_with_codec_unaffected(tmp_path):
    """Guard: a genuine container ('webm') alongside a codec must NOT be coerced."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent,
        [
            {
                "tool": "convert_video",
                "args": {"inputs": ["clip.mp4"], "container": "webm", "video_codec": "vp9"},
            }
        ],
    )
    assert cmd.rstrip().endswith(".webm"), f"explicit webm container must be honored; cmd={cmd}"
    assert "-c:v libvpx-vp9" in cmd, f"explicit vp9 codec must be honored; cmd={cmd}"


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — crf as a first-class arg on compress_video and convert_video
# ─────────────────────────────────────────────────────────────────────────────


def test_compress_video_schema_has_crf():
    from knaif.registry import load_registry

    reg = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")
    assert "crf" in reg["compress_video"].optional_args


def test_convert_video_schema_has_crf():
    from knaif.registry import load_registry

    reg = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")
    assert "crf" in reg["convert_video"].optional_args


def test_compress_video_crf_routes_to_load_quality_profile():
    """compress_video{crf:30} must produce a load_quality_profile step with quality='crf 30'."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["compress_video"]({"inputs": ["clip.mp4"], "crf": 30})
    lqp = next((s for s in plan if s["tool"] == "load_quality_profile"), None)
    assert lqp is not None, "load_quality_profile step missing"
    assert lqp["args"]["quality"] == "crf 30"


def test_convert_video_crf_routes_to_load_quality_profile():
    """convert_video{crf:28} must produce a load_quality_profile step with quality='crf 28'."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["convert_video"]({"inputs": ["clip.mp4"], "crf": 28})
    lqp = next((s for s in plan if s["tool"] == "load_quality_profile"), None)
    assert lqp is not None, "load_quality_profile step missing"
    assert lqp["args"]["quality"] == "crf 28"


def test_compress_video_crf_end_to_end(tmp_path):
    """compress_video{crf:30} renders an ffmpeg command containing -crf 30."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent,
        [{"tool": "compress_video", "args": {"inputs": ["clip.mp4"], "crf": 30}}],
    )
    assert "-crf 30" in cmd


def test_convert_video_crf_end_to_end(tmp_path):
    """convert_video{crf:28} renders an ffmpeg command containing -crf 28."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent,
        [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "crf": 28}}],
    )
    assert "-crf 28" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — honor output on extract_frame and create_thumbnail
# ─────────────────────────────────────────────────────────────────────────────


def test_create_thumbnail_schema_has_output():
    from knaif.registry import load_registry

    reg = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")
    assert "output" in reg["create_thumbnail"].optional_args


def test_expand_create_thumbnail_threads_output_path():
    """create_thumbnail{output:'thumb.jpg'} puts output_path in build_recipes options."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["create_thumbnail"]({"input": "clip.mp4", "output": "thumb.jpg"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["output_path"] == "thumb.jpg"


def test_expand_create_thumbnail_infers_image_format_from_output_extension():
    """create_thumbnail{output:'poster.png'} sets image_format=png."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["create_thumbnail"]({"input": "clip.mp4", "output": "poster.png"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["image_format"] == "png"


# ─────────────────────────────────────────────────────────────────────────────
# Task 10a — honor output on convert_video / compress_video / strip_audio
# ─────────────────────────────────────────────────────────────────────────────


def test_convert_compress_strip_schema_have_output():
    from knaif.registry import load_registry

    reg = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")
    for tool in ("convert_video", "compress_video", "strip_audio"):
        assert "output" in reg[tool].optional_args, f"{tool} missing output"


def test_expand_convert_video_threads_output_path():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["convert_video"]({"inputs": ["clip.mov"], "output": "clip.mp4"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["output_path"] == "clip.mp4"


def test_expand_compress_video_threads_output_path():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["compress_video"]({"inputs": ["clip.mp4"], "output": "small.mp4"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["output_path"] == "small.mp4"


def test_expand_strip_audio_threads_output_path():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["strip_audio"]({"inputs": ["clip.mp4"], "output": "silent.mkv"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["output_path"] == "silent.mkv"


def test_convert_video_infers_container_from_output_ext():
    """convert_video{output:'clip.webm'} with no explicit container → container=webm."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["convert_video"]({"inputs": ["clip.mov"], "output": "clip.webm"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["container"] == "webm"


def test_convert_video_explicit_container_wins_over_output_ext():
    """Explicit container overrides any extension inferred from output."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["convert_video"](
        {"inputs": ["clip.mov"], "container": "mkv", "output": "clip.webm"}
    )
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["container"] == "mkv"


def test_convert_video_output_end_to_end(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent,
        [{"tool": "convert_video", "args": {"inputs": ["clip.mov"], "output": "renamed.mp4"}}],
    )
    assert "renamed.mp4" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# Task 10b — coerce string width/height to int in resize (int>str crash)
# ─────────────────────────────────────────────────────────────────────────────


def test_resize_coerces_string_width_to_int():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["resize_video"](
        {"inputs": ["clip.mp4"], "width": "480", "height": "270"}
    )
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["width"] == 480
    assert build["args"]["options"]["height"] == 270


def test_resize_int_width_passes_through():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["resize_video"]({"inputs": ["clip.mp4"], "height": 480})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["height"] == 480


def test_resize_strips_trailing_p_in_dimension():
    """height:'480p' is a common model slip → coerced to 480."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["resize_video"]({"inputs": ["clip.mp4"], "height": "480p"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["height"] == 480


def test_resize_string_dimension_does_not_crash_end_to_end(stub_ffmpeg, media_root):
    """ffmpeg_117 case: resize with string width/height must render, not raise int>str."""
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=media_root)
    cmd = _render_batch_cmd(
        agent,
        [
            {
                "tool": "resize_video",
                "args": {
                    "inputs": [str(media_root / "clip2.mp4")],
                    "width": "480",
                    "height": "270",
                },
            }
        ],
    )
    assert "scale" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# Task 10c — extract_audio: format/container→audio_format aliases + accept quality
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_audio_schema_has_format_container_quality():
    from knaif.registry import load_registry

    reg = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")
    opt = reg["extract_audio"].optional_args
    for a in ("format", "container", "quality"):
        assert a in opt, f"extract_audio missing {a}"


def test_extract_audio_format_alias():
    """extract_audio{format:'opus'} → audio_format opus."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["extract_audio"]({"inputs": ["clip.mp4"], "format": "opus"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["audio_format"] == "opus"


def test_extract_audio_container_alias():
    """extract_audio{container:'flac'} → audio_format flac."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["extract_audio"]({"inputs": ["clip.mp4"], "container": "flac"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["audio_format"] == "flac"


def test_extract_audio_explicit_audio_format_wins_over_alias():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["extract_audio"](
        {"inputs": ["clip.mp4"], "audio_format": "mp3", "format": "flac"}
    )
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["audio_format"] == "mp3"


def test_extract_audio_quality_bitrate_maps_to_bitrate():
    """quality:'56kbps' → audio_bitrate 56k."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["extract_audio"]({"inputs": ["clip.mp4"], "quality": "56kbps"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["audio_bitrate"] == "56k"


def test_extract_audio_quality_profile_name_ignored_no_crash():
    """quality:'high_quality' is a profile name → accepted, no bitrate, no crash."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["extract_audio"](
        {"inputs": ["clip.mp4"], "audio_format": "flac", "quality": "high_quality"}
    )
    build = next(s for s in plan if s["tool"] == "build_recipes")
    assert build["args"]["options"]["audio_format"] == "flac"
    assert "audio_bitrate" not in build["args"]["options"]


def test_extract_audio_format_alias_end_to_end(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent, [{"tool": "extract_audio", "args": {"inputs": ["clip.mp4"], "format": "opus"}}]
    )
    assert "libopus" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# Task 10d — extract_audio start/end trim-while-extract
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_audio_schema_has_start_end():
    from knaif.registry import load_registry

    reg = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")
    opt = reg["extract_audio"].optional_args
    assert "start" in opt and "end" in opt


def test_extract_audio_threads_start_end_into_options():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["extract_audio"](
        {"inputs": ["clip.mp4"], "audio_format": "mp3", "start": "00:00:03", "end": "00:00:05"}
    )
    build = next(s for s in plan if s["tool"] == "build_recipes")
    opts = build["args"]["options"]
    assert opts["start"] == "00:00:03"
    assert opts["end"] == "00:00:05"


def test_extract_audio_no_trim_keys_when_absent():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["extract_audio"]({"inputs": ["clip.mp4"], "audio_format": "mp3"})
    build = next(s for s in plan if s["tool"] == "build_recipes")
    opts = build["args"]["options"]
    assert "start" not in opts and "end" not in opts


def test_extract_audio_start_end_render_ss_to(tmp_path):
    """ffmpeg_119: extract audio from 3-5s renders -ss and -to alongside -vn."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=sandbox)
    cmd = _render_batch_cmd(
        agent,
        [
            {
                "tool": "extract_audio",
                "args": {
                    "inputs": ["clip.mp4"],
                    "audio_format": "mp3",
                    "start": "00:00:03",
                    "end": "00:00:05",
                },
            }
        ],
    )
    assert "-ss 00:00:03" in cmd
    assert "-to 00:00:05" in cmd
    assert "-vn" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# Task 10e — guard int(crf) against non-numeric quality words in the crf slot
# ─────────────────────────────────────────────────────────────────────────────


def test_compress_video_crf_word_does_not_crash():
    """compress_video{crf:'balanced'} must not raise int('balanced'); treat as quality."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["compress_video"]({"inputs": ["clip.mp4"], "crf": "balanced"})
    lqp = next(s for s in plan if s["tool"] == "load_quality_profile")
    assert lqp["args"]["quality"] == "balanced"


def test_convert_video_crf_word_does_not_crash():
    """convert_video{crf:'balanced'} must not raise; routes to quality='balanced'."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    plan = skill.expanders["convert_video"]({"inputs": ["clip.mp4"], "crf": "balanced"})
    lqp = next(s for s in plan if s["tool"] == "load_quality_profile")
    assert lqp["args"]["quality"] == "balanced"


def test_convert_video_numeric_crf_still_maps_to_crf_string():
    """Regression guard: numeric crf still becomes 'crf N'."""
    skill = Skill.load(FFMPEG_SKILL_DIR)
    for val in (28, "28"):
        plan = skill.expanders["convert_video"]({"inputs": ["clip.mp4"], "crf": val})
        lqp = next(s for s in plan if s["tool"] == "load_quality_profile")
        assert lqp["args"]["quality"] == "crf 28"


# ─────────────────────────────────────────────────────────────────────────────
# Task 5 — split mixed-intent rows 082-B: vague no-filename utterances
# ─────────────────────────────────────────────────────────────────────────────

_EVAL_JSONL = FFMPEG_SKILL_DIR / "data" / "eval.jsonl"
_VAGUE_CONCAT_UTTERANCES = {
    "concatenate two mp4 files",
    "Zwei MP4-Dateien zusammenfuegen",
    "обедини два mp4 файла",
}


def _load_eval_rows():
    return [
        _json.loads(ln) for ln in _EVAL_JSONL.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]


def test_ffmpeg_082_contains_only_named_file_utterances():
    """ffmpeg_082 (plan row) must not contain the vague no-filename utterances."""
    rows = _load_eval_rows()
    row_082 = next((r for r in rows if r["id"] == "ffmpeg_082"), None)
    assert row_082 is not None
    for utt in row_082["utterances"]:
        assert (
            utt not in _VAGUE_CONCAT_UTTERANCES
        ), f"vague utterance {utt!r} still in ffmpeg_082 plan row — split not done"


def test_clarify_row_exists_for_vague_concat_utterances():
    """A clarify row with the vague no-filename concat utterances must exist."""
    rows = _load_eval_rows()
    clarify_rows = [r for r in rows if r.get("expected_outcome") == "clarify"]
    all_clarify_utts = {u for r in clarify_rows for u in r["utterances"]}
    missing = _VAGUE_CONCAT_UTTERANCES - all_clarify_utts
    assert not missing, f"vague concat utterances not found in any clarify row: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 Task 2.1 — extract_frame removed, create_thumbnail covers it
# (RED until Task 2.2 removes ExtractFrameIntent and cleans tools.yaml)
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_frame_tool_removed():
    skill = Skill.load(FFMPEG_SKILL_DIR)
    assert "extract_frame" not in skill.expanders
    assert "create_thumbnail" in skill.expanders


def test_grab_keyword_routes_to_thumbnail(tmp_path):
    from knaif.registry import retrieve_tools

    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=tmp_path)
    retrieved = retrieve_tools("grab a still from clip.mp4", agent.registry)
    # retrieve_tools returns ToolDef objects (or names depending on version)
    names = {t.name if hasattr(t, "name") else t for t in retrieved}
    assert "create_thumbnail" in names
    assert "extract_frame" not in names
