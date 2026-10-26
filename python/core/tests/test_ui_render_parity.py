"""One display for both CLIs (owner, 2026-10-23): the terminal view's renderers.

`contracts/parity/ui_render_cases.json`. pins them byte for byte; native `apps/cli/src/ui.rs` is
the reference (its `ui_render_cases` test records the cases), `knaif.ui` follows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from knaif import ui

CASES = json.loads(
    (Path(__file__).parents[3] / "contracts" / "parity" / "ui_render_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


def _secs(ms: int | None) -> float | None:
    return None if ms is None else ms / 1000


def _render(fn: str, style: ui.Style, a: dict[str, Any]) -> str:
    if fn == "fmt_secs":
        return ui.fmt_secs(_secs(a["ms"]))
    if fn == "leader":
        return ui.leader(style, a["label"], a["value"])
    if fn == "model_label":
        return ui.model_label(a["stem"])
    if fn == "header":
        return ui.render_header(style, a["skill"], a["model"])
    if fn == "planning":
        return ui.render_planning(style, _secs(a["load_ms"]), _secs(a["infer_ms"]))
    if fn == "plan":
        rows = [ui.PlanRow(tool, detail) for tool, detail in a["rows"]]
        return ui.render_plan(style, rows, a["notes"])
    if fn == "step_head":
        return ui.render_step_head(style, a["idx"], a["total"], a["tool"])
    if fn == "detail":
        return ui.render_detail(style, a["text"])
    if fn == "command":
        return ui.render_command(style, a["command"])
    if fn == "ok":
        return ui.render_ok(style, a["what"], _secs(a["ms"]))
    if fn == "fail":
        return ui.render_fail(style, a["what"], a["cause"])
    if fn == "closing":
        return ui.render_closing(style, a["kind"], a["message"], _secs(a["ms"]))
    if fn == "done":
        return ui.render_done(style, a["summary"], _secs(a["ms"]))
    if fn == "declined":
        return ui.render_declined(style)
    if fn == "declined_close":
        return ui.render_declined_close(style, a["step"], a["steps"], a["summary"], _secs(a["ms"]))
    if fn == "stopped":
        return ui.render_stopped(style, a["message"], a["detail"], _secs(a["ms"]))
    if fn == "box":
        tone = ui.Tone.WARN if a["tone"] == "warn" else ui.Tone.ACCENT
        return ui.render_box(style, tone, a["title"], a["text"])
    if fn == "fmt_clock":
        return ui.fmt_clock(a["seconds"])
    if fn == "progress":
        return ui.render_progress(
            style, a["frame"], _secs(a["elapsed_ms"]), a["done"], a["total"], a["speed"]
        )
    if fn == "fmt_bytes":
        return ui.fmt_bytes(a["n"])
    if fn == "files_written":
        return ui.files_written(a["n"])
    raise AssertionError(f"no renderer {fn}")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_ui_render(case: dict[str, Any]) -> None:
    style = ui.Style(color=case["color"])
    assert _render(case["fn"], style, case["args"]) == case["expected"]
