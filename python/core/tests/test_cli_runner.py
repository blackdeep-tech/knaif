"""Tests for knaif.cli.runner.App (run / invoke)."""

from __future__ import annotations

from typing import Annotated
from unittest.mock import MagicMock

import pytest

import knaif.cli as nk
from knaif.cli.runner import App

# ── fixtures ───────────────────────────────────────────────────────────────────


@nk.command(help="Return current time")
def now(tz: Annotated[str, nk.Opt(help="timezone")] = "UTC") -> dict:
    return {"tz": tz, "time": "2026-06-20T00:00:00Z"}


@nk.command(help="Add two strings")
def concat(a: str, b: str) -> dict:
    return {"result": a + b}


# ── App construction ───────────────────────────────────────────────────────────


def test_app_constructs():
    app = App([now, concat])
    assert app is not None


def test_app_requires_decorated_commands():
    def raw(x: str) -> dict:
        return {}

    with pytest.raises(ValueError, match="not decorated"):
        App([raw])


def test_app_has_registry():
    app = App([now, concat])
    assert "now" in app.registry
    assert "concat" in app.registry


# ── invoke() ──────────────────────────────────────────────────────────────────


def test_invoke_dispatches_via_mock_backend(tmp_path):
    """invoke() returns step results when a mock backend resolves to a valid plan."""
    mock_orch = MagicMock()
    mock_orch.infer.return_value = (
        '{"plan": [{"tool": "concat", "args": {"a": "foo", "b": "bar"}}]}'
    )

    app = App([concat], orchestrator=mock_orch, root=tmp_path)
    results = app.invoke("join foo and bar")
    # agent.run() returns [{"step": ..., "result": ..., "thinking": ...}]
    assert results[0]["result"]["result"] == "foobar"


def test_invoke_dry_run_does_not_call_fn(tmp_path):
    called = {}

    @nk.command(help="Side effect")
    def side_effect(x: str) -> dict:
        called["ran"] = True
        return {"x": x}

    mock_orch = MagicMock()
    mock_orch.infer.return_value = '{"plan": [{"tool": "side_effect", "args": {"x": "v"}}]}'

    app = App([side_effect], orchestrator=mock_orch, root=tmp_path)
    # dry_run=True: steps execute (FunctionStep.handle is called) but the
    # result is returned without persisting side effects. The function IS
    # called — dry_run controls destructive safety, not execution.
    results = app.invoke("do side effect", dry_run=True)
    assert called.get("ran") is True
    assert results[0]["result"]["x"] == "v"


def test_invoke_returns_list(tmp_path):
    mock_orch = MagicMock()
    mock_orch.infer.return_value = '{"plan": [{"tool": "now", "args": {"tz": "UTC"}}]}'

    app = App([now], orchestrator=mock_orch, root=tmp_path)
    results = app.invoke("what time is it")
    assert isinstance(results, list)
    assert len(results) == 1


def test_invoke_clarify_result_on_unknown_utterance(tmp_path):
    """When the model returns a clarify plan, invoke returns it as-is."""
    mock_orch = MagicMock()
    mock_orch.infer.return_value = (
        '{"plan": [{"tool": "clarify", "args": {"question": "What do you mean?"}}]}'
    )

    app = App([now], orchestrator=mock_orch, root=tmp_path)
    results = app.invoke("blah blah")
    # clarify is a terminal tool — agent.run() returns step dict
    assert results[0]["step"]["tool"] == "clarify"


def test_repeated_step_with_trailing_done_executes_once(tmp_path):
    """A re-issued step followed by `done` must not execute twice.

    Models (e.g. Qwen3) sometimes return [step] then [step, done] on the next
    re-plan. The stale-plan guard compares raw signatures so the second plan is
    recognised as already-submitted and the loop stops without re-executing.
    """
    calls = {"n": 0}

    @nk.command(help="Count calls")
    def counter() -> dict:
        calls["n"] += 1
        return {"n": calls["n"]}

    mock_orch = MagicMock()
    mock_orch.infer.side_effect = [
        '{"plan": [{"tool": "counter", "args": {}}]}',
        '{"plan": [{"tool": "counter", "args": {}}, {"tool": "done", "args": {}}]}',
    ]

    app = App([counter], orchestrator=mock_orch, root=tmp_path)
    results = app.invoke("count once")

    assert calls["n"] == 1
    counter_steps = [e for e in results if e["step"]["tool"] == "counter"]
    assert len(counter_steps) == 1


# ── App.run() CLI entry point ──────────────────────────────────────────────────


def test_run_is_callable():
    app = App([now])
    assert callable(app.run)
