"""Natural-language tester widget for the documents skill.

Mirrors ``tester_widget.FFmpegNLTester`` but renders documents' **structured**
results (output paths, page records, matches) rather than ffmpeg command lines.
Reuses ``model_selector.ModelSelector`` (model loading) and is inspected by
``debug_trace.show_debug`` (which reads ``tester._debug_state``).

Imported by the shared ``TesterWidget`` wrapper from the skill-local helpers directory.

The run engine (:meth:`DocumentsNLTester.run_query`) is deliberately separable
from the ipywidgets UI so it can be exercised headlessly in tests.
"""

from __future__ import annotations

import html as _html
import json as _json
import time
from pathlib import Path
from typing import Any

import ipywidgets as widgets
from IPython.display import HTML, clear_output, display
from model_selector import ModelSelector
from notebook_runner import NotebookRunEngine

from knaif import create_agent


class DocumentsNLTester:
    """Natural-language → documents-skill plan/execution tester."""

    def __init__(
        self,
        *,
        skill_dir: Path | str,
        fixtures_dir: Path | str,
        model_configs: dict,
        ollama_url: str,
        root: Path | str,
        default_backend: str = "llama_cpp",
        default_model: str = "",
        default_ollama_model: str = "",
        orchestrator: Any | None = None,
    ) -> None:
        self._skill_dir = Path(skill_dir)
        self._fixtures_dir = Path(fixtures_dir)
        self._root = Path(root)
        self._orchestrator = orchestrator
        self._ollama_model = default_ollama_model or "mistral"
        self._debug_state: dict[str, Any] = {}

        self._selector = ModelSelector(
            model_configs,
            ollama_url,
            root,
            default_backend=default_backend,
            default_model=default_model,
            default_ollama_model=default_ollama_model,
        )
        self._selector.on_loading_start = self._on_loading_start
        self._selector.on_ready = self._on_ready

        self._build_ui()

    # ── agent ────────────────────────────────────────────────────────────────
    def _make_agent(self):
        """Build the documents agent once and reuse it; only the orchestrator
        (loaded model) changes between Apply clicks. Avoids reloading the skill
        + registry on every Run."""
        if getattr(self, "_agent_cached", None) is None:
            self._agent_cached = create_agent(
                "documents",
                orchestrator=self._orchestrator,
                sandbox=self._fixtures_dir,
                root=self._fixtures_dir,
                show_plan=False,
                require_approval=False,
            )
        self._agent_cached.orchestrator = self._orchestrator
        return self._agent_cached

    # ── run engine (headless-testable) ─────────────────────────────────────────
    def run_query(self, prompt: str, *, dry_run: bool = True) -> dict[str, Any]:
        """Infer a plan and execute it. Populates ``self._debug_state`` and
        returns a small render-friendly summary dict. No ipywidgets required."""
        runner = NotebookRunEngine(
            make_agent=self._make_agent,
            debug_state=self._debug_state,
            ollama_model=self._ollama_model,
        )
        return runner.run_query(prompt, dry_run=dry_run)

    # ── UI ──────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        W = widgets.Layout
        fixtures = [
            f"<code>{f.name}</code>" for f in sorted(self._fixtures_dir.glob("*")) if f.is_file()
        ]
        self._hdr = widgets.HTML(
            "<b>documents skill</b> · sandbox: "
            f"<code>{self._fixtures_dir}</code><br>"
            f"<small>fixtures: {', '.join(fixtures) or '(none — run just eval-fixtures documents)'}</small>"
        )
        self._txt = widgets.Text(
            placeholder='e.g. "pull pages 1-2 out of sample.pdf"',
            layout=W(width="640px"),
        )
        self._chk_dry = widgets.Checkbox(value=True, description="dry-run", indent=False)
        self._btn_run = widgets.Button(
            description="Run", button_style="primary", layout=W(width="90px")
        )
        self._out = widgets.Output()
        self._btn_run.on_click(self._on_run)

    def show(self) -> None:
        display(
            widgets.VBox(
                [
                    self._selector.widget,
                    self._hdr,
                    widgets.HBox([self._txt, self._chk_dry, self._btn_run]),
                    self._out,
                ]
            )
        )

    # ── model selector callbacks ────────────────────────────────────────────────
    # Replace (not append) so repeated Apply clicks don't pile up status lines.
    def _on_loading_start(self) -> None:
        with self._out:
            clear_output(wait=True)
            print("⏳ loading model…")

    def _on_ready(self, orchestrator) -> None:
        self._orchestrator = orchestrator
        with self._out:
            clear_output(wait=True)
            print("✓ model ready")

    # ── run handler ──────────────────────────────────────────────────────────────
    def _on_run(self, _=None) -> None:
        # Guard against multiple executions for one logical click:
        #   * _busy   — re-entrant call while a run is in flight.
        #   * _last_done debounce — a duplicate-bound callback (e.g. %autoreload
        #     re-registered the handler) or a click queued during a slow
        #     inference, both of which fire right after the previous run ends.
        now = time.monotonic()
        if getattr(self, "_busy", False):
            return
        if now - getattr(self, "_last_done", 0.0) < 0.5:
            return
        self._busy = True
        try:
            prompt = self._txt.value.strip()
            with self._out:
                clear_output(wait=True)
                if not prompt:
                    return
                try:
                    summary = self.run_query(prompt, dry_run=self._chk_dry.value)
                except Exception as exc:  # noqa: BLE001
                    display(HTML(f"<pre style='color:#b91c1c'>{_html.escape(str(exc))}</pre>"))
                    return
                display(HTML(self._render(summary)))
        finally:
            self._busy = False
            self._last_done = time.monotonic()

    # ── rendering ────────────────────────────────────────────────────────────────
    def _render(self, summary: dict[str, Any]) -> str:
        first = summary["first_tool"]
        steps = summary["steps"]
        mode = "dry-run" if summary["dry_run"] else "executed"
        if not steps:
            return "<i>Empty plan.</i>"
        if first in ("clarify", "reject"):
            args = steps[0].get("args", {})
            msg = args.get("question") or args.get("reason") or _json.dumps(args)
            icon = "❓" if first == "clarify" else "🚫"
            color = "#92400e" if first == "clarify" else "#b91c1c"
            return (
                f"<div style='color:{color}'>{icon} <b>{first}</b>: {_html.escape(str(msg))}</div>"
            )

        rows = []
        for r in summary["results"]:
            tool = r.get("tool", "?")
            result = r.get("result", {})
            rows.append(
                f"<details><summary><b>{_html.escape(tool)}</b></summary>"
                f"<pre style='white-space:pre-wrap'>{_html.escape(_json.dumps(result, indent=2, default=str))}</pre>"
                f"</details>"
            )
        formatted = "".join(
            f"<div>• <b>{i.get('kind')}</b>: {_html.escape(str(i.get('message', '')))}</div>"
            for i in summary["formatted"]
        )
        return (
            f"<div style='font-family:system-ui'>"
            f"<div><b>plan</b> ({mode}): {' → '.join(s.get('tool', '?') for s in steps)}</div>"
            f"{formatted}{''.join(rows)}</div>"
        )
