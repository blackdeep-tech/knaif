"""Prompt rules that reach only the prompts they are for (`rules:` in prompt.yaml).

A rule names the tools it is about; it joins the header only when one of them is in the prompt.
A tool that joins prompts by its triggers (split_video, extract_frames) brings its
rule with it, and every other prompt stays byte for byte what it was.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from knaif import CommandAgent
from knaif.registry import retrieve_tools

_TOOLS = """
convert:
  effects: []
  description: "Convert a file."
  keywords: [convert]
  optional_args: [input, output]
  safety_category: safe
split:
  effects: []
  description: "Split a file."
  triggers: [split]
  optional_args: [input, output]
  safety_category: safe
"""

_PROMPT = """
system_header: |
  You plan.
  RULES:
  - be brief
rules:
  - tools: [split]
    text: |
      - SPLITTING: use split.
examples: []
"""

_CODE = """
from knaif.tool import Handler


class Convert(Handler):
    name = "convert"

    def handle(self, args, ctx):
        return {}


class Split(Handler):
    name = "split"

    def handle(self, args, ctx):
        return {}
"""


def _agent(tmp_path: Path) -> CommandAgent:
    skill = tmp_path / "skills" / "ruly"
    (skill / "python").mkdir(parents=True)
    (skill / "skill.yaml").write_text('name: ruly\ndescription: "Demo."\n', encoding="utf-8")
    (skill / "tools.yaml").write_text(_TOOLS, encoding="utf-8")
    (skill / "prompt.yaml").write_text(textwrap.dedent(_PROMPT), encoding="utf-8")
    (skill / "python" / "__init__.py").write_text("", encoding="utf-8")
    (skill / "python" / "handlers.py").write_text(textwrap.dedent(_CODE), encoding="utf-8")
    return CommandAgent.from_skill(skill)


def _system(agent: CommandAgent, utterance: str) -> str:
    system, _ = agent.build_prompt(
        utterance, registry_override=retrieve_tools(utterance, agent.registry)
    )
    return system


def test_a_rule_joins_the_prompt_with_its_tool(tmp_path: Path) -> None:
    system = _system(_agent(tmp_path), "split movie.mp4 in two")
    assert "- be brief\n- SPLITTING: use split.\n" in system


def test_without_its_tool_the_prompt_is_unchanged(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    system = _system(agent, "convert movie.mp4")
    assert "SPLITTING" not in system
    agent.prompt_rules = []
    assert _system(agent, "convert movie.mp4") == system
