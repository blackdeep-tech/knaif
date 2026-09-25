"""A codec written as the output extension (`*.hevc`, `clip.h265`) is not a container.

`ffmpeg_229#4` ("批量将所有视频转换为HEVC") planned `convert_video` with `output: "*.hevc"`.
ffmpeg picks its muxer from the extension, chose the raw `hevc` elementary-stream muxer, and
failed with "Invalid argument" (evals/runs/2026-09-24_config-parity-t6_success/report.md). The
container slot already moves a codec token into `video_codec`; this is the same repair for the
output name. The extension becomes the container's, and it supplies the codec only when the
plan names none. An explicit `video_codec` wins. Mirrored in skills/ffmpeg/native/src/run.rs and
pinned across runtimes by contracts/parity/expansion_cases.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from knaif import CommandAgent
from knaif.evalsuite.runner import _extract_artifacts
from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture()
def agent(tmp_path: Path) -> CommandAgent:
    return CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=tmp_path)


def _argv(agent: CommandAgent, args: dict) -> list[str]:
    results = agent.execute_plan(
        {"plan": [{"tool": "convert_video", "args": args}]}, dry_run=True, confirmed=False
    )
    (command,) = _extract_artifacts(results)
    return command.split()


@pytest.mark.parametrize("ext", ["hevc", "h265", "h264", "av1", "vp9", "HEVC"])
def test_a_codec_extension_becomes_the_container_extension(agent, ext):
    argv = _argv(agent, {"inputs": ["clip.mp4"], "output": f"clip_small.{ext}"})
    assert Path(argv[-1]).name == "clip_small.mp4"


def test_the_extension_supplies_the_codec_when_the_plan_has_none(agent):
    argv = _argv(agent, {"inputs": ["clip.mp4"], "output": "clip_small.hevc"})
    assert argv[argv.index("-c:v") + 1] == "libx265"
    assert "copy" not in argv[argv.index("-c:v") + 1]


def test_an_explicit_codec_wins_over_the_extension(agent):
    """`ffmpeg_229#4` as planned: the model's own h264 stands; only the file name is repaired."""
    argv = _argv(
        agent,
        {"inputs": ["clip.mp4"], "container": "mp4", "video_codec": "h264", "output": "*.hevc"},
    )
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert Path(argv[-1]).suffix == ".mp4"


def test_an_explicit_container_names_the_extension(agent):
    argv = _argv(agent, {"inputs": ["clip.mp4"], "container": "mkv", "output": "clip.hevc"})
    assert Path(argv[-1]).name == "clip.mkv"


def test_a_real_container_extension_is_untouched(agent):
    argv = _argv(agent, {"inputs": ["clip.mp4"], "output": "clip_small.webm"})
    assert Path(argv[-1]).name == "clip_small.webm"


@pytest.mark.parametrize("output", ["*.hevc", "*.mp4", "*"])
def test_a_same_folder_pattern_never_names_the_input_itself(agent, output):
    """`*.mp4` for `clip.mp4` resolved to `clip.mp4`, which ffmpeg refuses ("Invalid argument").

    ffmpeg_229#4's batch had `.mp4` inputs among others; after the codec-extension repair those
    still failed. A pattern that lands on its own input takes the derived name instead, which is
    also what the plan-level collision handling gives a literal `clip.mp4 -> clip.mp4`.
    """
    argv = _argv(agent, {"inputs": ["clip.mp4"], "container": "mp4", "output": output})
    assert argv[argv.index("-i") + 1] != argv[-1]
    assert Path(argv[-1]).name == "clip_converted.mp4"


@pytest.fixture(scope="module")
def steps_mod():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    return sys.modules[handlers.__package__ + ".steps"]


def _probe(name: str) -> dict:
    return {
        "file": f"/sb/{name}",
        "container": "mp4",
        "duration": 10.0,
        "size_bytes": 100000,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
    }


class _Ctx:
    sandbox = None
    dry_run = True


@pytest.mark.parametrize("names", [("clip.mp4", "clip.mov"), ("clip.mov", "clip.mp4")])
def test_a_batch_output_never_lands_on_another_input(steps_mod, names):
    """`clip.mov -> *.mp4` wrote `clip.mp4` over the batch's OTHER input, with `-y`.

    Found executing ffmpeg_229#4 for real: `clip.mp4` came back 8 s long (it is 10 s) because
    the `.mov` conversion replaced it. An input of the batch is a taken name, exactly as another
    output is, so the clash is resolved from the source name instead. Both orders, because the
    first recipe cannot yet know about a later input unless every input is reserved up front.
    """
    out = steps_mod.BuildRecipesStep().handle(
        {
            "probes": {"count": 2, "probes": [_probe(n) for n in names]},
            "options": {"mode": "convert", "container": "mp4", "output_path": "*.mp4"},
        },
        _Ctx(),
    )
    outputs = [Path(r["output"]).name for r in out["recipes"]]
    assert not set(names) & set(outputs), outputs
    assert len(set(outputs)) == len(outputs)
