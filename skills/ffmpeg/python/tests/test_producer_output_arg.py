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


# ── a model-supplied output name must be a legal filename ────────────────────


@pytest.fixture(scope="module")
def engine():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + "._engine"]


def _out(engine, raw: str) -> str:
    return engine._resolve_output_target(
        raw, input_path=Path("/sb/clip.mp4"), mode="convert", options={"container": "mp4"}
    ).name


def test_a_timestamp_derived_output_name_is_a_legal_filename(engine):
    """`ffmpeg_268#4` — the row that costs ffmpeg its L4 `extract_audio` floor.

    The model named the output after the time range it was given:
    `clip_trimmed_00:00:00.mp4`. Colons are legal on Linux and illegal on Windows, so the
    plan renders a filename ffmpeg cannot open — *Error opening output files: Invalid
    argument* — and the chain dies at step 1 with nothing written.

    Both runtimes fail it, so this is not a native port defect; it is the skill trusting a
    model-supplied string as a filename. Sanitised unconditionally rather than per-platform:
    a plan that renders different names on Linux and Windows would break L3 parity across
    machines and make every eval result depend on where it ran.
    """
    assert _out(engine, "clip_trimmed_00:00:00.mp4") == "clip_trimmed_00-00-00.mp4"


def test_the_wildcard_output_vocabulary_survives_sanitising(engine):
    """`*` and `?` are illegal on Windows but they are this skill's own output grammar.

    `videos/*.mp4` means "same name, over there" and `_resolve_output_target` expands it.
    Stripping them here would break a documented feature to fix a different bug.
    """
    assert _out(engine, "*.mp4") == "clip.mp4"


@pytest.mark.parametrize(
    "raw,name",
    [
        ('clip"quoted".mp4', "clip-quoted-.mp4"),
        ("clip<1>.mp4", "clip-1-.mp4"),
        ("a|b.mp4", "a-b.mp4"),
    ],
)
def test_the_other_windows_illegal_characters_go_too(engine, raw, name):
    """Nothing observed these, but the same trust boundary produces them.

    The model writes filenames out of the utterance; a quoted title or a `<name>`
    placeholder is no less plausible than a timestamp, and each fails identically.
    """
    assert _out(engine, raw) == name


def test_a_directory_component_keeps_its_separator(engine):
    """Sanitising runs per path component — `/` is structure, not a stray character."""
    got = engine._resolve_output_target(
        "converted/clip_00:00:05.mp4",
        input_path=Path("/sb/clip.mp4"),
        mode="convert",
        options={"container": "mp4"},
    )
    assert got.name == "clip_00-00-05.mp4"
    assert got.parent.name == "converted"
