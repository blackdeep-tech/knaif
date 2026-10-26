"""One file into many, inside the agent: a split's pieces as a set later steps run over.

The linking is pinned by contracts/parity/set_link_cases.json (test_set_link_parity.py) and the
lift by batch_chain_cases.json. These tests cover what the agent adds: it asks the skill
(`Skill.planned_outputs`) which files a step will write before anything runs, points the readers
at the set, routes it like any intermediate, and lifts the readers once per piece; previews,
failures and the result set speak of the pieces by their names in the user's folder.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from knaif import CommandAgent

_CODE = """
import os
from pathlib import Path

from knaif.batch_chain import PlannedSet
from knaif.skill_base import Skill
from knaif.tool import Handler

SEEN = []


class Piecey(Skill):
    def planned_outputs(self, tool, args, ctx):
        if tool != "split":
            return None
        out = args.get("output")
        if out and "%03d" in out:
            name = out
        else:
            stem, ext = os.path.splitext(out or args["input"])
            name = f"{stem}_%03d{ext}"
        return PlannedSet(name, [name % (i + 1) for i in range(int(args.get("parts", 2)))])


class Split(Handler):
    name = "split"

    def handle(self, args, ctx):
        SEEN.append(("split", dict(args, work_dir=ctx.work_dir)))
        return {"outputs": []}


class Shrink(Handler):
    name = "shrink"

    def handle(self, args, ctx):
        SEEN.append(("shrink", dict(args, work_dir=ctx.work_dir)))
        if any("002" in i for i in args["inputs"]):
            raise ValueError("piece two is broken")
        return {}


class Join(Handler):
    name = "join"

    def handle(self, args, ctx):
        SEEN.append(("join", dict(args, work_dir=ctx.work_dir)))
        return {}
"""

_TOOLS = """
split:
  effects: []
  description: "Split a video into pieces."
  required_args: [input]
  optional_args: [parts, output]
  derives: true
  safety_category: safe
shrink:
  effects: []
  description: "Make files smaller."
  optional_args: [inputs, output]
  safety_category: safe
join:
  effects: []
  description: "Join files into one."
  optional_args: [inputs, output]
  joins_inputs: true
  safety_category: safe
"""


def _agent(tmp_path: Path, *, scratch: bool = True) -> tuple[CommandAgent, list, list[str]]:
    skill_dir = tmp_path / "repo" / "skills" / "piecey"
    (skill_dir / "python").mkdir(parents=True)
    manifest = 'name: piecey\ndescription: "Demo."\n'
    if scratch:
        manifest += "scratch_intermediates: true\n"
    (skill_dir / "skill.yaml").write_text(manifest, encoding="utf-8")
    (skill_dir / "tools.yaml").write_text(_TOOLS, encoding="utf-8")
    (skill_dir / "python" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "python" / "handlers.py").write_text(textwrap.dedent(_CODE), encoding="utf-8")
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    (sandbox / "clip.mp4").write_bytes(b"x")
    agent = CommandAgent.from_skill(skill_dir, sandbox=sandbox)
    seen = sys.modules[type(agent.tool_map["split"]).__module__].SEEN
    seen.clear()
    shown: list[str] = []
    agent.show_plan = True
    agent.plan_display = shown.append
    agent.batch_failed = lambda line: seen.append(("failed", line))
    return agent, seen, shown


def _run(agent: CommandAgent, steps: list[dict], utterance: str) -> None:
    agent.execute_plan({"plan": steps}, utterance=utterance, dry_run=True)


_SPLIT_THEN_EACH = [
    {"tool": "split", "args": {"input": "clip.mp4", "parts": 3, "output": "part.mp4"}},
    {"tool": "shrink", "args": {"inputs": ["part.mp4"]}},
]


def test_a_step_over_the_pieces_runs_once_per_piece(tmp_path: Path) -> None:
    agent, seen, _ = _agent(tmp_path)
    _run(agent, _SPLIT_THEN_EACH, "split clip.mp4 into 3 parts and shrink each one")
    work = Path(seen[0][1]["work_dir"])
    assert seen[0][0] == "split"
    # The pieces are only refined, so the whole set goes into the scratch.
    assert seen[0][1]["output"] == str(work / "part_%03d.mp4")
    shrinks = [a["inputs"] for t, a in seen if t == "shrink"]
    assert shrinks == [[str(work / f"part_00{i}.mp4")] for i in (1, 2, 3)]


def test_a_glob_over_the_pieces_is_the_set(tmp_path: Path) -> None:
    agent, seen, _ = _agent(tmp_path, scratch=False)
    steps = [
        {"tool": "split", "args": {"input": "clip.mp4", "parts": 2}},
        {"tool": "shrink", "args": {"inputs": ["clip_*.mp4"], "output": "small.mp4"}},
    ]
    _run(agent, steps, "split clip.mp4 into 2 parts and shrink each one")
    assert seen[0][1]["output"] == "clip_%03d.mp4"
    assert [(a["inputs"], a["output"]) for t, a in seen if t == "shrink"] == [
        (["clip_001.mp4"], "clip_001_small.mp4"),
        (["clip_002.mp4"], "clip_002_small.mp4"),
    ]


def test_the_preview_and_failures_name_the_pieces_not_the_scratch(tmp_path: Path) -> None:
    agent, seen, shown = _agent(tmp_path)
    _run(agent, _SPLIT_THEN_EACH, "split clip.mp4 into 3 parts and shrink each one")
    assert "the same for 2 more files: part_002.mp4, part_003.mp4" in shown
    assert ("failed", "✗ part_002.mp4: piece two is broken") in seen
    assert (agent.last_batch_report.done, agent.last_batch_report.failed) == (2, 1)


def test_a_split_alone_leaves_its_pieces(tmp_path: Path) -> None:
    agent, seen, shown = _agent(tmp_path)
    steps = [{"tool": "split", "args": {"input": "clip.mp4", "parts": 3}}]
    _run(agent, steps, "split clip.mp4 into 3 parts")
    assert seen[0][1].get("output") is None
    assert shown[-1] == "leaves clip_001.mp4, clip_002.mp4 and clip_003.mp4"
    assert agent.last_batch_report is None


def test_a_join_over_the_pieces_reads_every_piece(tmp_path: Path) -> None:
    agent, seen, _ = _agent(tmp_path, scratch=False)
    steps = [
        {"tool": "split", "args": {"input": "clip.mp4", "parts": 2, "output": "part.mp4"}},
        {"tool": "join", "args": {"inputs": ["part.mp4"], "output": "all.mp4"}},
    ]
    _run(agent, steps, "split clip.mp4 into 2 parts and join them back")
    assert [a["inputs"] for t, a in seen if t == "join"] == [["part_001.mp4", "part_002.mp4"]]
