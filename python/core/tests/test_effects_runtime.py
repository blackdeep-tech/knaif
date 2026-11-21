"""Declared effects, enforced at run time.

A step may run an external program only through ``ctx.exec(program, argv)``, and only when its
tool declares ``exec:<program>``. It may reuse another tool through ``ctx.call_tool`` only when
it declares every effect of that tool (writing through the fs toolkit needs ``fs.write``). A
context built without declared effects (a bare ``HandlerContext``) is not restricted.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.effects import EffectNotDeclared
from knaif.handler_api import HandlerContext

CODE = """
import sys

from knaif.tool import Handler


class RunsPython(Handler):
    name = "runs_python"

    def handle(self, args, ctx):
        done = ctx.exec("python", [sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
        return {"out": done.stdout.strip()}


class RunsUndeclared(Handler):
    name = "runs_undeclared"

    def handle(self, args, ctx):
        ctx.exec("python", [sys.executable, "-c", "print('hi')"])
        return {}


class WritesThroughFs(Handler):
    name = "writes_through_fs"

    def handle(self, args, ctx):
        return ctx.call_tool("kit_write", {})


class ReadsThroughFs(Handler):
    name = "reads_through_fs"

    def handle(self, args, ctx):
        return ctx.call_tool("kit_write", {})
"""

KIT_CODE = """
from knaif.tool import Handler


class KitWrite(Handler):
    name = "kit_write"

    def handle(self, args, ctx):
        return {"wrote": True}
"""

TOOLS = """
runs_python:
  description: "Run python."
  effects: [exec:python]
runs_undeclared:
  description: "Run python without saying so."
  effects: []
writes_through_fs:
  description: "Write through the toolkit."
  effects: [fs.read, fs.write]
reads_through_fs:
  description: "Only reads, but calls a writer."
  effects: [fs.read]
"""

KIT_TOOLS = """
kit_write:
  description: "Write a file."
  effects: [fs.write]
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def agent(tmp_path: Path) -> CommandAgent:
    skill_dir = tmp_path / "repo" / "skills" / "effecty"
    _write(skill_dir / "skill.yaml", 'name: effecty\ndescription: "Demo."\ntoolkits: [kit]\n')
    _write(skill_dir / "tools.yaml", TOOLS)
    _write(skill_dir / "python" / "__init__.py", "")
    _write(skill_dir / "python" / "handlers.py", CODE)
    kit_dir = tmp_path / "repo" / "toolkits" / "kit"
    _write(kit_dir / "tools.yaml", KIT_TOOLS)
    _write(kit_dir / "python" / "__init__.py", "")
    _write(kit_dir / "python" / "tools.py", KIT_CODE)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    return CommandAgent.from_skill(skill_dir, sandbox=sandbox)


def _run(agent: CommandAgent, tool: str) -> dict:
    results = agent.execute_plan(
        {"plan": [{"tool": tool, "args": {}}]}, dry_run=False, confirmed=True
    )
    return results[0]["result"]


def test_a_declared_program_runs(agent) -> None:
    assert _run(agent, "runs_python") == {"out": "hi"}


def test_an_undeclared_program_is_refused(agent) -> None:
    with pytest.raises(EffectNotDeclared, match="exec:python"):
        _run(agent, "runs_undeclared")


def test_a_step_may_call_a_tool_whose_effects_it_declares(agent) -> None:
    assert _run(agent, "writes_through_fs") == {"wrote": True}


def test_a_step_without_fs_write_cannot_write_through_the_context(agent) -> None:
    with pytest.raises(EffectNotDeclared, match="fs.write"):
        _run(agent, "reads_through_fs")


def test_a_bare_context_is_not_restricted(tmp_path: Path) -> None:
    ctx = HandlerContext(root=tmp_path, dry_run=False, confirmed=True, skill_dir=tmp_path)
    done = ctx.exec("python", [sys.executable, "-c", "print(1)"], capture_output=True, text=True)
    assert done.stdout.strip() == "1"
