"""Cat B step 2: transform-in-place producers must accept and thread `output`.

resize_video / reverse_video / adjust_speed / adjust_volume / rotate_video lacked
an `output` arg, so a multi-step chain whose producer is one of them could not be
auto-linked (writing `output` to them failed schema validation). Giving them the
same `output` → `options["output_path"]` threading that trim/strip already have
both enables explicit output naming and lets the chain auto-link recover those
chains. See docs/plans/2026-06-18-ffmpeg-prompt-optimization.md (Cat B residue).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def handlers_mod():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    return sys.modules["_skill_oop_ffmpeg_handlers"]


def _recipe_options(plan: list[dict]) -> dict:
    for step in plan:
        if step.get("tool") == "build_recipes":
            return step["args"]["options"]
    return {}


_CASES = [
    ("ResizeVideoIntent", {"inputs": ["clip.mp4"], "height": 720}),
    ("ReverseVideoIntent", {"inputs": ["clip.mp4"]}),
    ("AdjustSpeedIntent", {"inputs": ["clip.mp4"], "speed": 2.0}),
    ("AdjustVolumeIntent", {"inputs": ["clip.mp4"], "level": "6dB"}),
    ("RotateVideoIntent", {"inputs": ["clip.mp4"], "angle": 90}),
]


@pytest.mark.parametrize("cls_name,base_args", _CASES)
def test_producer_threads_output_path(handlers_mod, cls_name, base_args):
    cls = getattr(handlers_mod, cls_name)
    plan = cls().expand({**base_args, "output": "intermediate.mp4"})
    assert _recipe_options(plan).get("output_path") == "intermediate.mp4"


@pytest.mark.parametrize("cls_name,base_args", _CASES)
def test_producer_output_omitted_when_absent(handlers_mod, cls_name, base_args):
    cls = getattr(handlers_mod, cls_name)
    plan = cls().expand(dict(base_args))
    assert "output_path" not in _recipe_options(plan)


def test_resize_video_accepts_output_in_schema():
    from knaif.registry import load_registry

    reg = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")
    for name in (
        "resize_video",
        "reverse_video",
        "adjust_speed",
        "adjust_volume",
        "rotate_video",
    ):
        td = reg[name]
        allowed = set(td.required_args) | set(td.optional_args)
        assert "output" in allowed, f"{name} must accept an output arg"


def test_agent_caches_output_capable_set(tmp_path):
    """The output-capable set is precomputed once (not rebuilt per inference)."""
    from knaif import create_agent

    agent = create_agent("ffmpeg", sandbox=tmp_path)
    assert isinstance(agent._output_capable, set)
    assert {"resize_video", "trim_video", "convert_video"} <= agent._output_capable
    assert "clarify" not in agent._output_capable
    assert "prepare_for_platform" not in agent._output_capable  # has no output arg
