"""End-to-end test: validator-feedback retry over the real ffmpeg eval corpus.

Drives a real ffmpeg CommandAgent (real registry, real execute_plan) through the
eval ``run_corpus`` harness on an actual corpus row, with a scripted orchestrator
that first emits a validation-failing plan (the kind a small model produces) and
then a corrected one. Proves the retry converts a failing eval outcome into a
passing ``plan`` outcome end-to-end — the accuracy win the feature exists for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif import create_agent
from knaif.evalsuite.corpus import load_corpus
from knaif.evalsuite.runner import run_corpus

CORPUS = Path("skills") / "ffmpeg" / "data" / "eval.jsonl"


class _ScriptedOrchestrator:
    """Returns each queued response in turn; records how many calls were made."""

    backend = "ollama"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def infer(self, system_msg: str, user_msg: str, **kwargs) -> str:
        self.calls += 1
        # Repeat the last response once exhausted, mirroring a model that keeps
        # making the same mistake.
        idx = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[idx]


def _convert_row():
    """First convert-style row from the real corpus (expects a `plan` outcome)."""
    corpus = load_corpus(CORPUS)
    for row in corpus:
        if row.expected_outcome == "plan" and row.expected_tool == "convert_video":
            return row
    pytest.skip("no convert_video plan row in corpus")


def _agent_with(responses: list[str], sandbox: Path, *, repair: bool):
    orch = _ScriptedOrchestrator(responses)
    agent = create_agent("ffmpeg", sandbox=sandbox, orchestrator=orch)
    agent.repair_invalid_plans = repair
    return agent, orch


def test_retry_recovers_real_corpus_row_end_to_end(tmp_path):
    row = _convert_row()
    fname = row.fixture or "clip.mp4"

    # A realistic small-model failure: right tool, but an unsupported arg key
    # (here `codec`, which is not in convert_video's schema) — fails structural
    # validation without being a missing-required-arg clarify case.
    bad = json.dumps(
        {"plan": [{"tool": "convert_video", "args": {"inputs": [fname], "codec": "h264"}}]}
    )
    good = json.dumps(
        {"plan": [{"tool": "convert_video", "args": {"inputs": [fname], "container": "mp4"}}]}
    )

    # Baseline: retry disabled → the invalid plan reaches execute_plan and errors.
    agent_off, orch_off = _agent_with([bad], tmp_path / "off", repair=False)
    out_off = run_corpus(agent_off, [row], use_mock=False)[0]
    assert out_off.outcome != "plan"
    assert orch_off.calls == 1  # no retry

    # With the retry: one corrective re-prompt yields a valid plan → `plan`.
    agent_on, orch_on = _agent_with([bad, good], tmp_path / "on", repair=True)
    out_on = run_corpus(agent_on, [row], use_mock=False)[0]
    assert out_on.outcome == "plan"
    assert out_on.artifact is not None
    assert orch_on.calls == 2  # original + one retry
    assert out_on.outcome == row.expected_outcome  # the row now passes


def test_retry_does_not_regress_a_clean_corpus_row(tmp_path):
    """A first-try-valid plan must not trigger a wasted second call."""
    row = _convert_row()
    fname = row.fixture or "clip.mp4"
    good = json.dumps(
        {"plan": [{"tool": "convert_video", "args": {"inputs": [fname], "container": "mp4"}}]}
    )

    agent, orch = _agent_with([good], tmp_path / "clean", repair=True)
    out = run_corpus(agent, [row], use_mock=False)[0]

    assert out.outcome == "plan"
    assert orch.calls == 1  # no retry on a clean plan
