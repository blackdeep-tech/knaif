"""A batch's lines as each file ends, not when the step ends (owner, 2026-10-25).

Native prints each command as it starts and each file's `✓` as soon as that file is done.
Python printed a step's lines from its results, so a person watching a long batch saw nothing
until the last file was done. `run_batch` now shows each line as it happens (`ctx.show`), and
the step's report leaves out the lines already shown.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from knaif.skill import Skill

SKILL_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def mods():
    sys.modules.pop("_skill_oop_ffmpeg_handlers", None)
    Skill.load(SKILL_DIR)
    handlers = sys.modules["_skill_oop_ffmpeg_handlers"]
    package = handlers.__package__
    return sys.modules[package + ".steps"], sys.modules[package + "._reporting"]


class _Ctx:
    sandbox = None
    work_dir = None
    skip_execution = False
    dry_run = False

    def __init__(self, events: list[tuple[str, str]]) -> None:
        self.events = events

    def file_finished(self, file: str, failure: str | None = None) -> None:
        pass

    def show(self, item: dict[str, Any]) -> bool:
        what = item.get("argv", [""])[-1] if item["kind"] == "command" else item["message"]
        self.events.append((item["kind"], what))
        return True


def _commands(*names: str) -> dict:
    return {
        "commands": [
            {"input": f"/sb/{n}", "output": f"/sb/{n}.mkv", "command": ["ffmpeg", n]} for n in names
        ]
    }


def test_each_file_s_lines_are_shown_as_it_runs(mods, monkeypatch):
    steps, _ = mods
    events: list[tuple[str, str]] = []

    def fake_run(command, output, ctx):
        events.append(("ran", command[-1]))
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(steps, "run_and_commit", fake_run)
    steps.RunBatchStep().handle(_commands("a.mov", "b.mov"), _Ctx(events))
    assert events == [
        ("command", "a.mov"),
        ("ran", "a.mov"),
        ("output", "/sb/a.mov.mkv"),
        ("command", "b.mov"),
        ("ran", "b.mov"),
        ("output", "/sb/b.mov.mkv"),
    ]


def test_the_report_leaves_out_what_was_shown(mods, monkeypatch):
    steps, reporting = mods
    monkeypatch.setattr(steps, "run_and_commit", lambda c, o, ctx: {"returncode": 0, "stderr": ""})
    result = steps.RunBatchStep().handle(_commands("a.mov", "b.mov"), _Ctx([]))
    items = reporting._format_results([{"tool": "run_batch", "result": result}], dry_run=False)
    assert [i for i in items if i["kind"] in ("command", "output")] == []


def test_without_a_live_view_the_report_has_every_line(mods, monkeypatch):
    """A caller that shows nothing live (an eval, the SDK) still gets each line in the report."""
    steps, reporting = mods
    monkeypatch.setattr(steps, "run_and_commit", lambda c, o, ctx: {"returncode": 0, "stderr": ""})

    class _Quiet(_Ctx):
        def show(self, item: dict[str, Any]) -> bool:
            return False

    result = steps.RunBatchStep().handle(_commands("a.mov", "b.mov"), _Quiet([]))
    items = reporting._format_results([{"tool": "run_batch", "result": result}], dry_run=False)
    assert [i["kind"] for i in items] == ["command", "output", "command", "output"]
