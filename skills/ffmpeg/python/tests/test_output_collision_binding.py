"""T5b: what a downstream filename reference means after an output is renamed.

Collision handling rewrites a producer's requested output when it would overwrite
a file (``ffmpeg_175``: the plan asks to write ``clip.mp4`` from ``clip.mp4``, and
every rendered command carries ``-y``). That raises the binding question these
tests settle: when the original input and the producer's requested output share a
name, which file does a later ``"input": "clip.mp4"`` mean?

**It has one referent, not two.** By the time recipes are rendered the reference is
already bound: ``CommandAgent._forward_thread_reused_sources`` rewrites a later
reference to a single-source producer's input onto that producer's ``output``, so a
reference to the *original source* does not survive the optimizer at all (see
``test_binding_premise_*`` below, and ``test_forward_threads_to_explicit_producer_output``
in python/core/tests/test_chain_intermediate_linking.py). The rule:

    A name an earlier step declares it will write binds, for every later step, to
    what that step actually wrote. Collision handling SUBSTITUTES the old output
    name with the resolved one across steps strictly after the producer; it never
    re-infers which file was meant.

All of these pass. The three rule tests were written as ``xfail(strict=True)`` before the
implementation and the marks came off with it, which is the only reason they are known to
test the behaviour rather than to describe it.

See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T5b.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from knaif.agent import CommandAgent
from knaif.skill import Skill

FFMPEG_SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture()
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CommandAgent:
    """A wired ffmpeg agent over a tmp sandbox, with ffprobe/ffmpeg stubbed."""
    (tmp_path / "clip.mp4").write_bytes(b"")
    (tmp_path / "other.mp4").write_bytes(b"")
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(FFMPEG_SKILL_DIR)
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
        handlers_mod._deps,
        "run_ffmpeg",
        lambda c: {"returncode": 0, "stdout": "", "stderr": ""},
    )
    return CommandAgent.from_skill(FFMPEG_SKILL_DIR, sandbox=tmp_path, root=tmp_path)


def _link(agent: CommandAgent, plan: list[dict[str, Any]], utterance: str) -> list[dict[str, Any]]:
    """Apply the plan-level pass the real pipeline runs before ``execute_plan``."""
    CommandAgent._link_chain_intermediates(plan, utterance, agent._output_capable)
    return plan


def _rendered(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """One ``{input, output}`` pair per rendered command, in execution order."""
    pairs: list[dict[str, str]] = []
    for step in results:
        if step.get("tool") != "render_batch_commands":
            continue
        for entry in step["result"]["commands"]:
            pairs.append({"input": entry["input"], "output": entry["output"]})
    return pairs


# -- The premise: a downstream reference is bound before any recipe renders ----


def test_binding_premise_reference_to_original_source_does_not_survive(agent):
    """A later step naming the ORIGINAL source is rewritten to the producer's output.

    This is why the identity case is not ambiguous: if a reference to the original
    source survived anywhere, the same literal would have two possible meanings. It
    does not survive - so in the identity case the literal can only mean the
    producer's output. Implementing "leave a reference to the original source alone"
    would break this and reintroduce the documents bug the forward-threader fixes.
    """
    plan = _link(
        agent,
        [
            {
                "tool": "convert_video",
                "args": {"inputs": ["clip.mp4"], "output": "small.mp4", "container": "mp4"},
            },
            {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"]}},
        ],
        "convert clip.mp4 to small.mp4 then make a thumbnail of clip.mp4",
    )
    assert plan[1]["args"]["inputs"] == ["small.mp4"]


def test_binding_premise_readonly_producer_leaves_the_reference_alone(agent):
    """A step that does not write rebinds nothing - the later reference is the source."""
    plan = _link(
        agent,
        [
            {"tool": "inspect_media", "args": {"inputs": ["clip.mp4"]}},
            {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"]}},
        ],
        "inspect clip.mp4 then make a thumbnail of clip.mp4",
    )
    assert plan[1]["args"]["inputs"] == ["clip.mp4"]


# -- The rule: rename, then substitute across later steps ---------------------


def test_renamed_output_is_not_the_producers_own_input(agent):
    """``ffmpeg -y -i clip.mp4 ... clip.mp4`` truncates the source; rename the output."""
    plan = _link(
        agent,
        [
            {
                "tool": "convert_video",
                "args": {"inputs": ["clip.mp4"], "output": "clip.mp4", "container": "mp4"},
            },
        ],
        "convert clip.mp4 to mp4",
    )
    produced = _rendered(agent.execute_plan({"plan": plan}, dry_run=True, confirmed=True))
    assert produced[0]["output"] != produced[0]["input"]


def test_downstream_reference_follows_the_rename(agent):
    """The identity case: a later ``clip.mp4`` means what step 0 wrote, not the original."""
    plan = _link(
        agent,
        [
            {
                "tool": "convert_video",
                "args": {"inputs": ["clip.mp4"], "output": "clip.mp4", "container": "mp4"},
            },
            {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"]}},
        ],
        "convert clip.mp4 to mp4 and then make a thumbnail of clip.mp4",
    )
    produced = _rendered(agent.execute_plan({"plan": plan}, dry_run=True, confirmed=True))
    # Both conjuncts are needed: the first alone is satisfied vacuously by the
    # un-renamed behaviour, where every path is still `clip.mp4`.
    assert produced[1]["input"] == produced[0]["output"]
    assert Path(produced[1]["input"]).name != "clip.mp4"


def test_producers_own_input_is_not_rewritten(agent):
    """Substitution is scoped to steps AFTER the producer - it still reads the original.

    Holds today and must keep holding, so it carries no xfail: the rename must never
    walk backwards into the step that produces the file.
    """
    plan = _link(
        agent,
        [
            {
                "tool": "convert_video",
                "args": {"inputs": ["clip.mp4"], "output": "clip.mp4", "container": "mp4"},
            },
            {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"]}},
        ],
        "convert clip.mp4 to mp4 and then make a thumbnail of clip.mp4",
    )
    produced = _rendered(agent.execute_plan({"plan": plan}, dry_run=True, confirmed=True))
    assert Path(produced[0]["input"]).name == "clip.mp4"


def test_multi_input_producer_rename_still_rebinds_downstream(agent):
    """Forward-threading skips multi-source producers; the substitution must not rely on it.

    ``concat_video [clip.mp4, other.mp4] -> clip.mp4`` leaves the later ``clip.mp4``
    unnormalised. The same rule applies: step 0 declared it would write that name, so
    every later use means the join result.
    """
    plan = _link(
        agent,
        [
            {
                "tool": "concat_video",
                "args": {"inputs": ["clip.mp4", "other.mp4"], "output": "clip.mp4"},
            },
            {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"]}},
        ],
        "join clip.mp4 and other.mp4 into clip.mp4 then make a thumbnail of clip.mp4",
    )
    results = agent.execute_plan({"plan": plan}, dry_run=True, confirmed=True)
    concat = next(r for r in results if r["tool"] == "run_concat")
    joined = concat["result"]["command"][-1]
    assert Path(joined).name != "clip.mp4"
    assert _rendered(results)[0]["input"] == joined
