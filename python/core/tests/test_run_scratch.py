"""The per-run scratch folder (`ctx.work_dir`) and handler reuse of tools (`ctx.call_tool`).

Intermediate files belong to knaif, not to the user: every run that executes gets a fresh
`<OS temp>/knaif/run-<id>/`, which is deleted when the run ends, on success and on failure. It
is kept only under `KNAIF_DEBUG`, and a run that crashed hard leaves a folder that the next
start sweeps away. A dry run writes nothing, so `work_dir` is a path that does not exist.
"""

from __future__ import annotations

import os
import textwrap
import time
from pathlib import Path

import pytest

from knaif.agent import CommandAgent

CODE = """
from pathlib import Path

from knaif.tool import Handler

SEEN = []


class Touch(Handler):
    name = "touch"

    def handle(self, args, ctx):
        wd = ctx.work_dir
        SEEN.append(
            {"work_dir": wd, "existed": wd is not None and Path(wd).is_dir(), "dry": ctx.dry_run}
        )
        if not (ctx.dry_run or ctx.skip_execution):
            (Path(wd) / "intermediate.bin").write_bytes(b"x")
        return {"ok": True}


class Boom(Handler):
    name = "boom"

    def handle(self, args, ctx):
        # Only when it runs: the rehearsal before a real run passes it, so the step fails
        # after the run began, the case these tests are about.
        if not ctx.dry_run:
            raise RuntimeError("handler failed")
        return {}


class Caller(Handler):
    name = "caller"

    def handle(self, args, ctx):
        return {"via": ctx.call_tool(args["target"], {"value": 7})}
"""

KIT_CODE = """
from knaif.tool import Handler


class Echo(Handler):
    name = "echo"

    def handle(self, args, ctx):
        return {"echo": args["value"], "same_run": ctx.work_dir}
"""

TOOLS = """
touch:
  effects: []
  description: "Write a file into the run scratch."
  safety_category: safe
boom:
  effects: []
  description: "Fail."
  safety_category: safe
caller:
  effects: []
  description: "Call another tool through the context."
  required_args: [target]
  safety_category: safe
"""

KIT_TOOLS = """
echo:
  effects: []
  description: "Echo a value."
  required_args: [value]
  safety_category: safe
  readonly: true
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def scratch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "temp" / "knaif"
    monkeypatch.setenv("KNAIF_SCRATCH_ROOT", str(root))
    monkeypatch.delenv("KNAIF_DEBUG", raising=False)
    monkeypatch.delenv("KNAIF_KEEP_SCRATCH", raising=False)
    return root


@pytest.fixture
def agent(tmp_path: Path) -> CommandAgent:
    skill_dir = tmp_path / "repo" / "skills" / "scratchy"
    _write(skill_dir / "skill.yaml", 'name: scratchy\ndescription: "Demo."\ntoolkits: [kit]\n')
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


def _seen(agent: CommandAgent) -> list[dict]:
    import sys

    return sys.modules[type(agent.tool_map["touch"]).__module__].SEEN


def _plan(*tools: str) -> dict:
    return {"plan": [{"tool": t, "args": {}} for t in tools]}


def test_a_run_gets_a_fresh_work_dir_that_is_deleted_after(agent, scratch_root) -> None:
    agent.execute_plan(_plan("touch", "touch"), dry_run=False, confirmed=True)
    seen = [s for s in _seen(agent) if not s["dry"]]  # not the rehearsal's
    assert len(seen) == 2
    work_dir = Path(seen[0]["work_dir"])
    assert seen[0]["existed"] and seen[1]["existed"]
    assert seen[1]["work_dir"] == seen[0]["work_dir"], "one folder per run, not per step"
    assert work_dir.parent == scratch_root and work_dir.name.startswith("run-")
    assert not work_dir.exists()


def test_two_runs_never_share_a_work_dir(agent, scratch_root) -> None:
    agent.execute_plan(_plan("touch"), dry_run=False, confirmed=True)
    agent.execute_plan(_plan("touch"), dry_run=False, confirmed=True)
    first, second = (s["work_dir"] for s in _seen(agent))
    assert first != second


def test_the_work_dir_is_deleted_when_a_step_fails(agent, scratch_root) -> None:
    with pytest.raises(RuntimeError, match="handler failed"):
        agent.execute_plan(_plan("touch", "boom"), dry_run=False, confirmed=True)
    work_dir = Path(_seen(agent)[0]["work_dir"])
    assert not work_dir.exists()
    assert not any(scratch_root.glob("run-*"))


def test_knaif_debug_keeps_the_work_dir(agent, scratch_root, monkeypatch) -> None:
    monkeypatch.setenv("KNAIF_DEBUG", "1")
    agent.execute_plan(_plan("touch"), dry_run=False, confirmed=True)
    work_dir = Path(_seen(agent)[0]["work_dir"])
    assert (work_dir / "intermediate.bin").is_file()


def test_a_dry_run_writes_nothing(agent, scratch_root) -> None:
    agent.execute_plan(_plan("touch"), dry_run=True)
    seen = _seen(agent)[0]
    assert seen["work_dir"] is not None, "a dry run still gets a (stub) path to render"
    assert not seen["existed"]
    assert not scratch_root.exists()


def test_stale_run_folders_are_swept_at_start(agent, scratch_root) -> None:
    stale = scratch_root / "run-crashed"
    fresh = scratch_root / "run-still-running"
    other = scratch_root / "not-a-run"
    for d in (stale, fresh, other):
        d.mkdir(parents=True)
        (d / "f.bin").write_bytes(b"x")
    old = time.time() - 3 * 24 * 3600
    os.utime(stale, (old, old))
    agent.execute_plan(_plan("touch"), dry_run=False, confirmed=True)
    assert not stale.exists()
    assert fresh.exists(), "a recent folder may belong to a run in another process"
    assert other.exists(), "only run-* folders are knaif's to sweep"


def test_a_handler_reuses_a_toolkit_tool_through_the_context(agent, scratch_root) -> None:
    results = agent.execute_plan(
        {"plan": [{"tool": "caller", "args": {"target": "echo"}}]}, dry_run=False, confirmed=True
    )
    via = results[0]["result"]["via"]
    assert via["echo"] == 7
    assert via["same_run"] is not None, "the called tool runs in the caller's run"


def test_call_tool_refuses_a_tool_the_skill_does_not_have(agent, scratch_root) -> None:
    with pytest.raises(ValueError, match="nope"):
        agent.execute_plan(
            {"plan": [{"tool": "caller", "args": {"target": "nope"}}]},
            dry_run=False,
            confirmed=True,
        )


def test_knaif_keep_scratch_keeps_it_without_debug(agent, scratch_root, monkeypatch) -> None:
    """The eval lane grades chain intermediates from the scratch, so it must survive the run
    without KNAIF_DEBUG's other effects. Same switch in both runtimes."""
    monkeypatch.setenv("KNAIF_KEEP_SCRATCH", "1")
    agent.execute_plan(_plan("touch"), dry_run=False, confirmed=True)
    assert (Path(_seen(agent)[0]["work_dir"]) / "intermediate.bin").is_file()


def test_display_shows_a_scratch_path_as_work(scratch_root) -> None:
    """What a person reads: the real scratch path names the temp folder (and the user), and the
    file is gone when the run ends. Native prints the same `<work>/…`."""
    from knaif.scratch import display

    run = scratch_root / "run-812-a1b2c3d4"
    text = f"ffmpeg -y -i clip.mp4 {run / 'videos' / 'silent.mp4'}"
    assert display(text) == "ffmpeg -y -i clip.mp4 <work>/videos/silent.mp4"
    assert display("ffmpeg -i clip.mp4 out.mp4") == "ffmpeg -i clip.mp4 out.mp4"


def test_a_relative_scratch_root_is_made_absolute(tmp_path, monkeypatch):
    """A relative `KNAIF_SCRATCH_ROOT` once lost a refined chain's results: `user_side_path`
    resolved the relative scratch path against the sandbox, never matched the run scratch, and
    the final file was written beside a phantom copy of it (2026-10-20)."""
    from knaif.scratch import scratch_root

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KNAIF_SCRATCH_ROOT", "rel/scratch")
    assert scratch_root() == tmp_path / "rel" / "scratch"
