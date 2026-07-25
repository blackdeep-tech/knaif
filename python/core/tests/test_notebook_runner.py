"""Tests for the shared notebook runner contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("ipywidgets")

_SHARED = Path("notebooks") / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


class _FakeAgent:
    last_thinking = ""

    def __init__(self) -> None:
        self.result_formatter = lambda results, dry_run: [
            {"kind": "info", "message": f"{len(results)} result(s), dry_run={dry_run}"}
        ]

    def build_prompt(self, prompt: str):
        return "system", f"user:{prompt}"

    def infer_stream(self, prompt: str, **kwargs):
        yield "plan", json.dumps(
            {"plan": [{"tool": "inspect_document", "args": {"input": "a.pdf"}}]}
        )

    def _clean_json(self, text: str) -> str:
        return text

    def parse_plan(self, text: str):
        return json.loads(text)

    def execute_plan(self, plan, **kwargs):
        return [
            {
                "tool": "inspect_document",
                "args": {"input": "a.pdf"},
                "result": {"status": "ok"},
            }
        ]


def test_notebook_runner_populates_debug_state_and_summary():
    from notebook_runner import NotebookRunEngine

    debug_state: dict = {}
    engine = NotebookRunEngine(
        make_agent=lambda: _FakeAgent(),
        debug_state=debug_state,
        ollama_model="mistral",
    )

    summary = engine.run_query("inspect a.pdf", dry_run=True)

    assert summary["first_tool"] == "inspect_document"
    assert summary["results"][0]["result"] == {"status": "ok"}
    assert summary["formatted"] == [{"kind": "info", "message": "1 result(s), dry_run=True"}]
    assert debug_state["system_msg"] == "system"
    assert debug_state["parsed_plan"]["plan"][0]["tool"] == "inspect_document"
    assert debug_state["execution_results"] == summary["results"]
