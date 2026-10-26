"""Chain intermediates are routed into the run scratch, so only deliverables reach the folder.

An intermediate is a step's ``output`` that a later step reads. It moves into ``work_dir``
under the same name (so every argv still compares equal by basename), and every later reference
moves with it. Two things stay where the plan put them: an output nothing reads (a deliverable),
and a file the user named in the request (a file the user named is one they asked for).
"""

from __future__ import annotations

from pathlib import Path

from knaif.agent import CommandAgent

route = CommandAgent._route_intermediates_to_scratch


def _trim_then_resize(output: str = "clip_trimmed.mp4", ref: str = "clip_trimmed.mp4") -> list:
    return [
        {"tool": "trim_video", "args": {"input": "clip.mp4", "end": "00:00:02", "output": output}},
        {"tool": "resize_video", "args": {"inputs": [ref], "height": 240}},
    ]


def test_an_intermediate_moves_into_scratch_with_its_readers(tmp_path: Path) -> None:
    work, sb = tmp_path / "work", tmp_path / "sb"
    plan = route(_trim_then_resize(), work, "trim clip.mp4 to 2 s, then resize it", sb)
    moved = str(work / "clip_trimmed.mp4")
    assert plan[0]["args"]["output"] == moved
    assert plan[1]["args"]["inputs"] == [moved]
    assert "output" not in plan[1]["args"], "the deliverable is untouched"


def test_the_input_plan_is_not_mutated(tmp_path: Path) -> None:
    original = _trim_then_resize()
    route(original, tmp_path / "work", "trim clip.mp4, then resize it", tmp_path / "sb")
    assert original == _trim_then_resize()


def test_a_file_the_user_named_stays_put(tmp_path: Path) -> None:
    plan = _trim_then_resize("short.mp4", "short.mp4")
    got = route(
        plan, tmp_path / "w", "trim clip.mp4 into short.mp4, then resize short.mp4", tmp_path
    )
    assert got == plan


def test_an_output_nothing_reads_is_a_deliverable(tmp_path: Path) -> None:
    plan = [
        {"tool": "create_thumbnail", "args": {"inputs": ["clip.mp4"], "output": "thumb.jpg"}},
        {"tool": "compress_video", "args": {"inputs": ["clip.mp4"], "output": "small.mp4"}},
    ]
    assert route(plan, tmp_path / "w", "thumbnail and compress clip.mp4", tmp_path) == plan


def test_every_reader_of_a_fan_out_follows(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = [
        {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "output": "clip_c.mp4"}},
        {"tool": "create_thumbnail", "args": {"inputs": ["clip_c.mp4"]}},
        {"tool": "compress_video", "args": {"inputs": ["clip_c.mp4"]}},
    ]
    got = route(plan, work, "convert clip.mov to mp4, then thumbnail and compress it", tmp_path)
    moved = str(work / "clip_c.mp4")
    assert got[0]["args"]["output"] == moved
    assert got[1]["args"]["inputs"] == [moved] and got[2]["args"]["inputs"] == [moved]


def test_spellings_of_the_same_file_match(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = _trim_then_resize("clip_trimmed.mp4", "./clip_trimmed.mp4")
    got = route(plan, work, "trim clip.mp4, then resize it", tmp_path)
    assert got[1]["args"]["inputs"] == [str(work / "clip_trimmed.mp4")]


def test_a_single_input_arg_is_rewritten_too(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = [
        {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "output": "clip.mp4"}},
        {"tool": "trim_video", "args": {"input": "clip.mp4", "end": "00:00:03"}},
    ]
    got = route(plan, work, "convert clip.mov to mp4 and then trim it to 3 seconds", tmp_path)
    assert got[1]["args"]["input"] == str(work / "clip.mp4")


def test_two_intermediates_with_one_name_do_not_collide(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = [
        {"tool": "trim_video", "args": {"input": "a/clip.mp4", "output": "a/x.mp4"}},
        {"tool": "trim_video", "args": {"input": "b/clip.mp4", "output": "b/x.mp4"}},
        {"tool": "concat_video", "args": {"inputs": ["a/x.mp4", "b/x.mp4"], "output": "j.mp4"}},
    ]
    got = route(plan, work, "trim both clips and join them into j.mp4", tmp_path)
    first, second = got[0]["args"]["output"], got[1]["args"]["output"]
    assert first != second
    assert Path(first).name == Path(second).name == "x.mp4"
    assert got[2]["args"]["inputs"] == [first, second]


def test_without_an_utterance_every_name_is_the_callers(tmp_path: Path) -> None:
    plan = _trim_then_resize()
    assert route(plan, tmp_path / "w", None, tmp_path) == plan


def test_variable_references_and_terminals_are_ignored(tmp_path: Path) -> None:
    plan = [
        {"tool": "trim_video", "args": {"input": "clip.mp4", "output": "$out"}},
        {"tool": "resize_video", "args": {"inputs": ["$out"]}},
        {"tool": "done", "args": {}},
    ]
    assert route(plan, tmp_path / "w", "trim then resize clip.mp4", tmp_path) == plan


# ── the agent applies it only for a skill that opts in ─────────────────────────────────────

_CODE = """
from knaif.skill_base import Skill
from knaif.tool import Handler

SEEN = []


class Make(Handler):
    name = "make"

    def handle(self, args, ctx):
        SEEN.append(dict(args, work_dir=ctx.work_dir))
        return {"ok": True}
"""

_TOOLS = 'make:\n  effects: []\n  description: "Make a file."\n  optional_args: [src, output]\n  safety_category: safe\n'


def _agent(tmp_path: Path, opt_in: bool) -> tuple[CommandAgent, list]:
    import sys
    import textwrap

    skill_dir = tmp_path / "repo" / "skills" / "routed"
    (skill_dir / "python").mkdir(parents=True)
    # The opt-in is declarative, in skill.yaml, so both runtimes read the same contract.
    manifest = 'name: routed\ndescription: "Demo."\n'
    if opt_in:
        manifest += "scratch_intermediates: true\n"
    (skill_dir / "skill.yaml").write_text(manifest, encoding="utf-8")
    (skill_dir / "tools.yaml").write_text(_TOOLS, encoding="utf-8")
    (skill_dir / "python" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "python" / "handlers.py").write_text(textwrap.dedent(_CODE), encoding="utf-8")
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    agent = CommandAgent.from_skill(skill_dir, sandbox=sandbox)
    return agent, sys.modules[type(agent.tool_map["make"]).__module__].SEEN


_CHAIN = {
    "plan": [
        {"tool": "make", "args": {"src": "a.txt", "output": "mid.txt"}},
        {"tool": "make", "args": {"src": "mid.txt", "output": "final.txt"}},
    ]
}


def test_a_skill_that_opts_in_gets_its_intermediates_routed(tmp_path: Path) -> None:
    import copy

    agent, seen = _agent(tmp_path, opt_in=True)
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance="make a.txt twice", dry_run=True)
    work = Path(seen[0]["work_dir"])
    assert seen[0]["output"] == str(work / "mid.txt")
    assert seen[1]["src"] == str(work / "mid.txt")
    assert seen[1]["output"] == "final.txt"


def test_a_skill_that_does_not_opt_in_is_untouched(tmp_path: Path) -> None:
    import copy

    agent, seen = _agent(tmp_path, opt_in=False)
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance="make a.txt twice", dry_run=True)
    assert seen[0]["output"] == "mid.txt" and seen[1]["src"] == "mid.txt"


def test_the_model_cannot_name_the_scratch_folder(tmp_path: Path, monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("KNAIF_SCRATCH_ROOT", str(tmp_path / "scratch"))
    agent, _ = _agent(tmp_path, opt_in=True)
    sneaky = str(tmp_path / "scratch" / "run-1-abc" / "secret.txt")
    plan = {"plan": [{"tool": "make", "args": {"src": sneaky, "output": "x.txt"}}]}
    with pytest.raises(ValueError, match="outside sandbox"):
        agent.execute_plan(plan, utterance="make x.txt", dry_run=False, confirmed=True)


def test_an_intermediate_keeps_its_folder_and_spelling(tmp_path: Path) -> None:
    work = tmp_path / "work"
    plan = _trim_then_resize("videos/Clip_Trimmed.MP4", "videos/Clip_Trimmed.MP4")
    got = route(plan, work, "trim clip.mp4, then resize it", tmp_path)
    assert got[0]["args"]["output"] == str(work / "videos" / "Clip_Trimmed.MP4")


def test_the_class_attribute_opt_in_is_gone() -> None:
    """One contract both runtimes read: skill.yaml. No second, Python-only spelling."""
    from knaif.skill_base import Skill

    assert not hasattr(Skill, "scratch_intermediates")


def test_ffmpeg_opts_in_through_its_manifest() -> None:
    import yaml

    manifest = yaml.safe_load(Path("skills/ffmpeg/skill.yaml").read_text(encoding="utf-8"))
    assert manifest.get("scratch_intermediates") is True


def test_concat_declares_that_it_joins_its_inputs() -> None:
    """concat writes ONE file from several inputs, so its output is a routable intermediate
    (ffmpeg_243#0); a batch tool writes one file per input and is not."""
    from knaif.registry import load_registry

    registry = load_registry(Path("skills/ffmpeg/tools.yaml"))
    assert registry["concat_video"].joins_inputs is True
    assert registry["extract_audio"].joins_inputs is False
