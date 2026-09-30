"""`concat_video` via `base`/`append` must build the same command as via `inputs`.

`ffmpeg_244` failed on three of its four utterances with

    Nothing was written into output file, because at least one of its streams received
    no packets.  (exit -22)

and every one of the three used the `base`/`append` form. The two forms render very
differently for the same operation:

    inputs:      -i <sandbox>/clip.mov -i <sandbox>/clip.mp4
                 -filter_complex [0:v]scale=1280:720,fps=25[v0];[0:a]aresample=44100[a0];
                                 [1:v]scale=1280:720,fps=25[v1];...concat=...

    base/append: -i clip.mp4 -i clip.mov
                 -filter_complex [0:v][0:a][1:v][1:a]concat=...

Two faults, one cause. The `inputs` path is expanded with `resolve_inputs` + `inspect_media`;
the `base`/`append` path is expanded with neither, because those args may be `$var`
references that only exist at runtime. `RunConcatStep` was left to self-probe — but it never
resolved the filenames first, so the probe looked for `clip.mp4` relative to the process cwd,
found nothing, and emitted no normalization at all. `concat` requires identical width,
height and sample rate across its inputs, so the moment two clips differ ffmpeg writes
nothing and exits -22.

`output` in the same handler was already being resolved against the sandbox. The inputs
simply were not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif import CommandAgent

FFMPEG_SKILL_DIR = Path("skills/ffmpeg")
FIXTURES = Path("sandbox/fixtures/ffmpeg")

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "clip.mov").exists() or not (FIXTURES / "clip.mp4").exists(),
    reason="needs the generated ffmpeg fixtures (just eval-fixtures ffmpeg)",
)


def _concat_command(**args) -> list[str]:
    agent = CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=FIXTURES.resolve())
    plan = {"plan": [{"tool": "concat_video", "args": args}]}
    results = agent.execute_plan(json.loads(json.dumps(plan)), dry_run=True, confirmed=True)
    for entry in results if isinstance(results, list) else [results]:
        result = entry.get("result") if isinstance(entry, dict) else None
        if isinstance(result, dict) and result.get("command"):
            return [str(c) for c in result["command"]]
    raise AssertionError(f"no concat command in {results!r}")


def test_base_append_resolves_its_inputs_into_the_sandbox() -> None:
    cmd = _concat_command(base="clip.mp4", append=["clip.mov"], output="b.mp4")
    given = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "-i"]
    assert given, cmd
    for path in given:
        assert Path(path).is_absolute(), f"unresolved input {path!r} in {' '.join(cmd)}"
        assert Path(path).exists(), f"input {path!r} does not exist"


def test_base_append_normalizes_mismatched_clips() -> None:
    """The fault that made ffmpeg write nothing: concat needs identical stream parameters."""
    cmd = _concat_command(base="clip.mp4", append=["clip.mov"], output="b.mp4")
    filters = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=" in filters, f"no scaling for clips of different sizes: {filters}"
    assert "aresample=" in filters, f"no audio resampling: {filters}"


def test_both_forms_agree_on_the_filter_graph() -> None:
    """Same two clips, same order, same operation — the spelling of the args must not matter."""
    via_inputs = _concat_command(inputs=["clip.mp4", "clip.mov"], output="a.mp4")
    via_semantic = _concat_command(base="clip.mp4", append=["clip.mov"], output="b.mp4")
    assert (
        via_inputs[via_inputs.index("-filter_complex") + 1]
        == via_semantic[via_semantic.index("-filter_complex") + 1]
    )


def test_target_resolution_second_is_honoured_through_base_append() -> None:
    """`ffmpeg_244` asks for the second clip's resolution; clip.mov is 1280x720."""
    cmd = _concat_command(
        base="clip.mp4", append=["clip.mov"], target_resolution="second", output="b.mp4"
    )
    filters = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=1280:720" in filters, filters
