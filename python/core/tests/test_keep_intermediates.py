"""`--keep-intermediates`: every step's output stays where the plan put it.

(owner, 2026-10-14): a flag on `knaif run` in both CLIs, and request words ("keep
the intermediate files", "keep everything") turn it on too. Without either, the keep rule
decides (`_asked_for`); with either, nothing is routed into the run scratch.

The request words are pinned by ``contracts/parity/keep_request_cases.json``, shared with
``native/crates/knaif-core/tests/keep_request_parity.rs``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from knaif.scratch import wants_everything_kept

CASES = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts/parity/keep_request_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["utterance"][:48] for c in CASES])
def test_request_words_match_the_contract(case: dict) -> None:
    assert wants_everything_kept(case["utterance"]) is case["keep"]


def test_no_request_keeps_the_default() -> None:
    assert wants_everything_kept(None) is False
    assert wants_everything_kept("") is False


# ── the agent: the flag and the words both skip routing ─────────────────────────────────────

_CHAIN = {
    "plan": [
        {"tool": "make", "args": {"src": "a.txt", "output": "mid.txt"}},
        {"tool": "make", "args": {"src": "mid.txt", "output": "final.txt"}},
    ]
}


def _agent(tmp_path: Path):
    """The opt-in test skill from test_scratch_routing, reused."""
    from .test_scratch_routing import _agent as make

    return make(tmp_path, opt_in=True)


def test_the_agent_routes_by_default(tmp_path: Path) -> None:
    agent, seen = _agent(tmp_path)
    assert agent.keep_intermediates is False
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance="make a.txt twice", dry_run=True)
    assert seen[0]["output"] != "mid.txt"


def test_the_flag_keeps_every_output(tmp_path: Path) -> None:
    agent, seen = _agent(tmp_path)
    agent.keep_intermediates = True
    agent.execute_plan(copy.deepcopy(_CHAIN), utterance="make a.txt twice", dry_run=True)
    assert seen[0]["output"] == "mid.txt" and seen[1]["src"] == "mid.txt"


def test_request_words_keep_every_output(tmp_path: Path) -> None:
    agent, seen = _agent(tmp_path)
    agent.execute_plan(
        copy.deepcopy(_CHAIN), utterance="make a.txt twice and keep everything", dry_run=True
    )
    assert seen[0]["output"] == "mid.txt" and seen[1]["src"] == "mid.txt"


# ── the Python CLI ───────────────────────────────────────────────────────────────────────────


def test_the_python_cli_has_the_flag() -> None:
    from knaif.app import run_cmd

    names = {o.name for o in run_cmd.params}
    assert "keep_intermediates" in names
    opt = next(o for o in run_cmd.params if o.name == "keep_intermediates")
    assert "--keep-intermediates" in opt.opts and opt.default is False


def test_the_python_cli_sets_the_agent(monkeypatch, tmp_path: Path) -> None:
    """`knaif-cli run … --keep-intermediates` reaches the agent before it runs."""
    from click.testing import CliRunner

    from knaif import app

    seen: dict = {}

    class Stop(Exception):
        pass

    def fake_execute(self, payload, **kw):
        seen["keep"] = self.keep_intermediates
        raise Stop

    monkeypatch.setattr("knaif.agent.CommandAgent.execute_plan", fake_execute)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app.cli,
        ["run", "ffmpeg", "--backend", "mock", "--keep-intermediates", "trim", "clip.mp4"],
    )
    # The CLI prints any error from the run instead of raising it, so `Stop` ends it there.
    assert result.exit_code == 1 and "Error: Stop" in result.output, result.output
    assert seen["keep"] is True
