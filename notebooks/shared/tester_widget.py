"""FFmpeg skill notebook helpers — gate classes and the interactive tester widget.

Import in the notebook with:
    from tester_widget import FFmpegNLTester
"""

from __future__ import annotations

import html as _html
import importlib.util
import io as _io
import json
import threading
import time as _time
import traceback as _traceback
from pathlib import Path

import ipywidgets as widgets
from IPython.display import display
from model_selector import ModelSelector

from knaif.agent import CommandAgent


class TesterWidget:
    """Shared notebook tester wrapper.

    Skill-specific renderers stay outside this shared module; the wrapper delegates UI
    rendering while keeping one public notebook entry point.
    """

    def __init__(self, *, skill: str, **kwargs) -> None:
        self.skill = skill
        if skill == "ffmpeg":
            self._delegate = FFmpegNLTester(**kwargs)
        elif skill == "documents":
            self._delegate = self._make_documents_tester(kwargs)
        else:
            raise ValueError(f"Unsupported notebook tester skill: {skill!r}")

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    @property
    def _debug_state(self):
        return getattr(self._delegate, "_debug_state", {})

    def show(self) -> None:
        self._delegate.show()

    @staticmethod
    def _make_documents_tester(kwargs: dict):
        skill_dir = Path(kwargs["skill_dir"])
        helper_path = skill_dir / "notebooks" / "helpers" / "documents_tester_widget.py"
        spec = importlib.util.spec_from_file_location(
            "_documents_notebook_tester_widget", helper_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load documents tester helper at {helper_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.DocumentsNLTester(**kwargs)


# ── Approval gates ─────────────────────────────────────────────────────────────


class _ConfirmerGate:
    """Blocks the execution thread at wait_for_confirmation until the user clicks."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._result = False
        self.on_open: list = []
        self.on_close: list = []

    def __call__(self, prompt: str, preview) -> bool:
        self._result = False
        self._event.clear()
        for cb in self.on_open:
            cb(prompt, preview)
        self._event.wait(timeout=300)
        for cb in self.on_close:
            cb(self._result)
        return self._result

    def approve(self):
        self._result = True
        self._event.set()

    def decline(self):
        self._result = False
        self._event.set()


class _PlanGate:
    """Plan-level approval gate fired before execution with a summary string."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._result = False
        self.on_open: list = []
        self.on_close: list = []

    def __call__(self, summary: str) -> bool:
        self._result = False
        self._event.clear()
        for cb in self.on_open:
            cb(summary)
        self._event.wait(timeout=300)
        for cb in self.on_close:
            cb(self._result)
        return self._result

    def approve(self):
        self._result = True
        self._event.set()

    def decline(self):
        self._result = False
        self._event.set()


# ── Main tester widget ──────────────────────────────────────────────────────────


class FFmpegNLTester:
    """Natural-language → ffmpeg command tester.

    Pass ``model_configs``, ``ollama_url``, ``root``, and the ``default_*``
    parameters to embed a backend/model selector at the top of the widget.
    Without those, the widget uses whatever ``orchestrator`` is passed in.
    """

    def __init__(
        self,
        skill_dir,
        media_dir,
        orchestrator,
        model_configs=None,
        ollama_url: str = "http://localhost:11434",
        root=None,
        default_backend: str = "llama_cpp",
        default_model: str = "",
        default_ollama_model: str = "",
    ) -> None:
        self._skill_dir = Path(skill_dir)
        self._media_dir = Path(media_dir)
        self._orchestrator = orchestrator
        self._model_loading = False
        self._debug_state: dict = {}
        self._pending_intent_plan = None

        # Gates
        self._gate = _ConfirmerGate()
        self._gate.on_open.append(self._show_gate)
        self._gate.on_close.append(self._hide_gate)
        self._plan_gate = _PlanGate()
        self._plan_gate.on_open.append(self._show_plan_gate)
        self._plan_gate.on_close.append(self._hide_plan_gate)

        # Optional embedded model selector
        if model_configs:
            self._model_selector: ModelSelector | None = ModelSelector(
                model_configs=model_configs,
                ollama_url=ollama_url,
                root=root or self._skill_dir.parents[2],
                default_backend=default_backend,
                default_model=default_model,
                default_ollama_model=default_ollama_model,
            )
            self._model_selector.on_loading_start = self._on_model_loading_start
            self._model_selector.on_ready = self._on_model_ready
        else:
            self._model_selector = None

        self._build_ui()

    # ── model selector callbacks ───────────────────────────────────────────────

    def _on_model_loading_start(self) -> None:
        """Called by the selector when Apply is clicked (before thread starts)."""
        self._model_loading = True
        self.btn_run.disabled = True
        self.lbl_status.value = "<b style='color:#92400e'>⏳ Loading model…</b>"

    def _on_model_ready(self, orc) -> None:
        """Called from the selector's background thread once the orchestrator is ready."""
        self._orchestrator = orc
        self._model_loading = False
        self.btn_run.disabled = False
        if self._orchestrator is not None:
            self._orchestrator.thinking_enabled = bool(self._chk_thinking.value)
        if "Loading model" in self.lbl_status.value:
            self.lbl_status.value = ""

    # ── build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        W = widgets.Layout

        files_hint = ", ".join(
            f"<code>{f.name}</code>" for f in sorted(self._media_dir.glob("*")) if f.is_file()
        )
        self._hint = widgets.HTML(
            f"<small>Available files: {files_hint}<br>"
            "Reference by name in your prompt. Paths are resolved automatically.</small>"
        )

        self.txt_prompt = widgets.Textarea(
            placeholder=(
                "Examples:\n"
                '  "compress test1.mov for WhatsApp"\n'
                '  "combine test1.mov and test2.mov into result.mp4"\n'
                '  "extract audio from test1.mov as mp3"\n'
                '  "resize test2.mov to 720p"\n'
                '  "trim the first 30 seconds from test1.mov"'
            ),
            layout=W(width="100%", height="90px"),
        )

        self.btn_run = widgets.Button(
            description="▶  Run",
            button_style="primary",
            layout=W(width="120px"),
        )
        self.btn_clear = widgets.Button(
            description="Clear",
            layout=W(width="80px"),
        )
        self.lbl_status = widgets.HTML(value="")

        # ── Plan description (StepA) ───────────────────────────────────────
        self._sec_plan_desc = widgets.VBox(
            [
                widgets.HTML("<b>Plan</b> <small>(what the agent intends to do)</small>"),
                widgets.HTML(
                    layout=W(
                        width="100%",
                        border="1px solid #2563eb",
                        padding="8px",
                        background_color="#eff6ff",
                    ),
                ),
            ],
            layout=W(display="none"),
        )
        self._plan_desc_html = self._sec_plan_desc.children[1]

        # ── Plan approval gate (StepB) ─────────────────────────────────────
        self.btn_plan_proceed = widgets.Button(
            description="✓  Proceed",
            button_style="success",
            layout=W(width="49%"),
        )
        self.btn_plan_cancel = widgets.Button(
            description="✗  Cancel",
            button_style="danger",
            layout=W(width="49%"),
        )
        self._plan_approval_box = widgets.VBox(
            [
                widgets.HTML('<b style="color:#92400e">⏸  Approve this plan to continue?</b>'),
                widgets.HBox([self.btn_plan_proceed, self.btn_plan_cancel]),
            ],
            layout=W(
                display="none",
                border="2px solid #d97706",
                padding="10px",
                margin="8px 0",
            ),
        )

        # ── Intent plan ───────────────────────────────────────────────────
        self._sec_intent = widgets.VBox(
            [
                widgets.HTML("<b>Model intent plan</b> <small>(what the LLM decided)</small>"),
                widgets.Textarea(
                    disabled=True,
                    layout=W(width="100%", height="100px"),
                ),
            ],
            layout=W(display="none"),
        )
        self._ta_intent = self._sec_intent.children[1]

        # ── Thinking (chain-of-thought) ────────────────────────────────────
        self._sec_thinking = widgets.VBox(
            [
                widgets.HTML(
                    "<b>Model thinking</b> <small>(chain-of-thought, if available)</small>"
                ),
                widgets.Textarea(
                    disabled=True,
                    layout=W(width="100%", height="80px"),
                ),
            ],
            layout=W(display="none"),
        )
        self._ta_thinking = self._sec_thinking.children[1]

        # ── Workflow log ───────────────────────────────────────────────────
        self._sec_log = widgets.VBox(
            [
                widgets.HTML("<b>Workflow steps</b>"),
                widgets.HTML(
                    layout=W(
                        width="100%",
                        min_height="60px",
                        border="1px solid #ddd",
                        padding="8px",
                        overflow_y="auto",
                    ),
                ),
            ],
            layout=W(display="none"),
        )
        self._out_log = self._sec_log.children[1]

        # ── FFmpeg commands ────────────────────────────────────────────────
        self._sec_cmds = widgets.VBox(
            [
                widgets.HTML(
                    '<b style="font-size:1.05em">FFmpeg commands</b>'
                    ' <small style="color:#555">(generated plan)</small>'
                ),
                widgets.Textarea(
                    disabled=True,
                    layout=W(
                        width="100%",
                        height="120px",
                        font_family="monospace",
                    ),
                ),
            ],
            layout=W(
                display="none",
                border="2px solid #2563eb",
                padding="10px",
                margin="8px 0",
            ),
        )
        self._ta_cmds = self._sec_cmds.children[1]

        # ── Execute button ─────────────────────────────────────────────────
        self.btn_execute = widgets.Button(
            description="▶  Execute",
            button_style="success",
            layout=W(width="130px", display="none"),
        )
        self._lbl_execute_hint = widgets.HTML(
            "<small style='color:#555'>Click to run the commands above with real ffmpeg/ffprobe</small>",
            layout=W(display="none"),
        )
        self._execute_row = widgets.HBox(
            [self.btn_execute, self._lbl_execute_hint],
            layout=W(align_items="center", margin="4px 0"),
        )

        # ── Execution output ───────────────────────────────────────────────
        self._sec_exec_out = widgets.VBox(
            [
                widgets.HTML(
                    '<b style="font-size:1.05em">Execution output</b>'
                    ' <small style="color:#555">(real ffmpeg results)</small>'
                ),
                widgets.HTML(
                    layout=W(
                        width="100%",
                        min_height="60px",
                        border="1px solid #16a34a",
                        padding="8px",
                        overflow_y="auto",
                    ),
                ),
            ],
            layout=W(
                display="none",
                border="2px solid #16a34a",
                padding="10px",
                margin="8px 0",
            ),
        )
        self._exec_out_html = self._sec_exec_out.children[1]

        # ── Confirmation gate (mid-workflow preview gate) ──────────────────
        self.btn_approve = widgets.Button(
            description="✓  Approve — run the batch",
            button_style="success",
            layout=W(width="49%"),
        )
        self.btn_decline = widgets.Button(
            description="✗  Decline — stop here",
            button_style="danger",
            layout=W(width="49%"),
        )
        self._gate_preview = widgets.Textarea(
            disabled=True,
            layout=W(width="100%", height="80px"),
        )
        self._gate_box = widgets.VBox(
            [
                widgets.HTML(
                    '<hr><b style="color:#92400e">⏸  Preview ready — approve to run the batch, decline to stop</b>'
                ),
                self._gate_preview,
                widgets.HBox([self.btn_approve, self.btn_decline]),
            ],
            layout=W(
                display="none",
                border="2px solid #d97706",
                padding="10px",
                margin="8px 0",
            ),
        )

        # ── Thinking toggle ────────────────────────────────────────────────
        self._chk_thinking = widgets.Checkbox(
            value=False,
            description="Enable thinking (chain-of-thought)",
            style={"description_width": "initial"},
            layout=W(width="auto"),
        )
        self._thinking_hint = widgets.HTML(
            '<small style="color:#555">Uncheck for /no_think — ~3× faster on Qwen3</small>'
        )

        # ── Plan preview / approval toggles ───────────────────────────────
        self._chk_show_plan = widgets.Checkbox(
            value=True,
            description="Show plan description before running",
            style={"description_width": "initial"},
            layout=W(width="auto"),
        )
        self._chk_require_approval = widgets.Checkbox(
            value=True,
            description="Require approval before executing",
            style={"description_width": "initial"},
            layout=W(width="auto"),
        )

        # ── Dry-run toggle ─────────────────────────────────────────────────
        self._chk_dry_run = widgets.Checkbox(
            value=False,
            description="Dry run (no real execution, use placeholder data for missing files)",
            style={"description_width": "initial"},
            layout=W(width="auto"),
        )

        # ── Sync orchestrator with UI defaults ─────────────────────────────
        if self._orchestrator is not None:
            self._orchestrator.thinking_enabled = False

        # ── Wire events ────────────────────────────────────────────────────
        self.btn_run.on_click(self._on_run)
        self.btn_clear.on_click(self._on_clear)
        self.btn_approve.on_click(lambda _: self._gate.approve())
        self.btn_decline.on_click(lambda _: self._gate.decline())
        self.btn_plan_proceed.on_click(lambda _: self._plan_gate.approve())
        self.btn_plan_cancel.on_click(lambda _: self._plan_gate.decline())
        self.btn_execute.on_click(self._on_execute)
        self._chk_thinking.observe(self._on_thinking_change, names="value")

    # ── event handlers ─────────────────────────────────────────────────────────

    def _on_thinking_change(self, change) -> None:
        if self._orchestrator is not None:
            self._orchestrator.thinking_enabled = change["new"]
        if not change["new"]:
            self._sec_thinking.layout.display = "none"

    def _on_clear(self, _) -> None:
        self.txt_prompt.value = ""
        self._ta_intent.value = ""
        self._ta_thinking.value = ""
        self._ta_cmds.value = ""
        self._out_log.value = ""
        self._plan_desc_html.value = ""
        self._exec_out_html.value = ""
        self._sec_intent.layout.display = "none"
        self._sec_thinking.layout.display = "none"
        self._sec_log.layout.display = "none"
        self._sec_cmds.layout.display = "none"
        self._sec_plan_desc.layout.display = "none"
        self._sec_exec_out.layout.display = "none"
        self._plan_approval_box.layout.display = "none"
        self._gate_box.layout.display = "none"
        self.btn_execute.layout.display = "none"
        self._lbl_execute_hint.layout.display = "none"
        self.lbl_status.value = ""
        self._debug_state = {}
        self._pending_intent_plan = None

    def _on_run(self, _) -> None:
        if self._model_loading:
            self.lbl_status.value = (
                "<span style='color:orange'>⚠ Model is loading — please wait.</span>"
            )
            return

        prompt = self.txt_prompt.value.strip()
        if not prompt:
            self.lbl_status.value = "<span style='color:orange'>⚠ Enter a prompt first.</span>"
            return

        self.btn_run.disabled = True
        self.lbl_status.value = "<b>⏳ Running…</b>"

        self._ta_intent.value = ""
        self._ta_thinking.value = ""
        self._ta_cmds.value = ""
        self._out_log.value = ""
        self._plan_desc_html.value = ""
        self._exec_out_html.value = ""
        self._sec_thinking.layout.display = "none"
        self._sec_intent.layout.display = "none"
        self._sec_cmds.layout.display = "none"
        self._sec_exec_out.layout.display = "none"
        self._sec_plan_desc.layout.display = "none"
        self._plan_approval_box.layout.display = "none"
        self._gate_box.layout.display = "none"
        self.btn_execute.layout.display = "none"
        self._lbl_execute_hint.layout.display = "none"
        self._pending_intent_plan = None

        threading.Thread(target=self._run_thread, args=(prompt,), daemon=True).start()

    def _run_thread(self, prompt: str) -> None:
        try:
            self._execute(prompt)
        except Exception as exc:  # noqa: BLE001
            buf = _io.StringIO()
            _traceback.print_exc(file=buf)
            tb_text = buf.getvalue()
            self._debug_state["error"] = tb_text
            self._out_log.value = (
                f"<pre style='color:red'>❌  Error: {_html.escape(str(exc))}\n"
                f"{_html.escape(tb_text)}</pre>"
            )
            self.lbl_status.value = (
                f"<span style='color:red'>❌ Error: {_html.escape(str(exc))}</span>"
            )
        finally:
            self.btn_run.disabled = False

    def _execute(self, prompt: str) -> None:
        t_total = _time.perf_counter()

        self._debug_state = {
            "user_input": prompt,
            "system_msg": None,
            "user_msg": None,
            "thinking": "",
            "raw_llm_output": "",
            "parsed_plan": None,
            "llm_parse_error": None,
            "execution_results": [],
            "timing": {},
            "error": None,
        }

        dry_run = bool(self._chk_dry_run.value)
        show_plan = bool(self._chk_show_plan.value)
        require_approval = bool(self._chk_require_approval.value)

        agent = CommandAgent.from_skill(
            self._skill_dir,
            sandbox=self._media_dir,
            root=self._media_dir,
            orchestrator=self._orchestrator,
            confirmer=self._gate,
            plan_confirmer=self._plan_gate,
            plan_display=self._show_plan_description,
            show_plan=show_plan,
            require_approval=require_approval,
        )

        try:
            sys_msg, usr_msg = agent.build_prompt(prompt)
            self._debug_state["system_msg"] = sys_msg
            self._debug_state["user_msg"] = usr_msg
        except Exception:
            pass

        # ── use_mock logic ─────────────────────────────────────────────────
        # - orchestrator is None           → mock (no backend configured)
        # - llama_cpp, llm not loaded      → mock (model file not found)
        # - llama_cpp, llm loaded          → real inference
        # - ollama                         → real inference (llm attr stays None
        #                                    by design; readiness from _check_ollama)
        orc = self._orchestrator
        if orc is None:
            use_mock = True
        elif orc.backend == "llama_cpp":
            use_mock = not bool(getattr(orc, "llm", None))
        else:  # ollama
            use_mock = False

        # ── Step 1: LLM inference ──────────────────────────────────────────
        self.lbl_status.value = "<b>⏳ Asking the model…</b>"
        if use_mock:
            self.lbl_status.value = (
                "<span style='color:orange'>⚠ No model loaded — using mock inference. "
                "Use the model selector to load a model.</span>"
            )

        t_llm = _time.perf_counter()
        plan_acc = ""
        for kind, chunk in agent.infer_stream(prompt, use_mock=use_mock):
            if kind == "thinking":
                self._ta_thinking.value += chunk
                if self._chk_thinking.value:
                    if self._sec_thinking.layout.display == "none":
                        self._sec_thinking.layout.display = "block"
            else:
                plan_acc += chunk
        t_llm_ms = (_time.perf_counter() - t_llm) * 1000

        self._debug_state["thinking"] = self._ta_thinking.value
        self._debug_state["raw_llm_output"] = plan_acc
        self._debug_state["timing"]["llm_ms"] = t_llm_ms

        try:
            intent_plan = agent.parse_plan(agent._clean_json(plan_acc))
        except ValueError as _parse_err:
            self._debug_state["llm_parse_error"] = str(_parse_err)
            intent_plan = {
                "plan": [
                    {
                        "tool": "clarify",
                        "args": {"question": "Could not parse model output. Please rephrase."},
                    }
                ]
            }

        self._debug_state["parsed_plan"] = intent_plan

        self._ta_intent.value = json.dumps(intent_plan, indent=2)
        self._sec_intent.layout.display = "block"

        if not intent_plan.get("plan"):
            self.lbl_status.value = (
                "<span style='color:orange'>⚠ Model returned an empty plan. Try rephrasing.</span>"
            )
            return

        # ── Step 2: Plan expansion ─────────────────────────────────────────
        self.lbl_status.value = "<b>⏳ Expanding workflow…</b>"
        self._sec_log.layout.display = "block"

        t_exec = _time.perf_counter()
        results = agent.execute_plan(
            intent_plan,
            dry_run=dry_run,
            skip_execution=(not dry_run),
            confirmed=True,
        )
        t_exec_ms = (_time.perf_counter() - t_exec) * 1000

        t_total_ms = (_time.perf_counter() - t_total) * 1000
        self._debug_state["execution_results"] = results
        self._debug_state["timing"]["exec_ms"] = t_exec_ms
        self._debug_state["timing"]["total_ms"] = t_total_ms

        if not results and require_approval:
            self.lbl_status.value = (
                "<b style='color:#92400e'>✗ Cancelled — plan was not approved.</b>"
            )
            return

        # ── Step 3: Display workflow steps ─────────────────────────────────
        lines = [
            f"  🧠 llm_inference             → {t_llm_ms:6.0f} ms",
            "  ─────────────────────────────────────────",
        ]
        for r in results:
            line = self._print_step(r)
            if line:
                lines.append(_html.escape(line))
        lines += [
            "  ─────────────────────────────────────────",
            f"  ⏱  workflow total             → {t_exec_ms:6.0f} ms",
        ]
        self._out_log.value = "<pre style='margin:0;font-size:0.9em'>" + "\n".join(lines) + "</pre>"

        # ── Step 4: Extract and show FFmpeg commands ───────────────────────
        cmds = self._collect_commands(results)
        if cmds:
            self._ta_cmds.value = "\n\n".join(cmds)
            self._sec_cmds.layout.display = "block"

        last_tool = results[-1]["tool"] if results else "?"
        if last_tool == "wait_for_confirmation":
            status = results[-1]["result"].get("status", "?")
            if status == "declined":
                self.lbl_status.value = "<b>✗ Declined — workflow stopped at preview gate.</b>"
                return

        # ── Step 5: Show Execute button or report done ─────────────────────
        if not dry_run and cmds:
            self._pending_intent_plan = intent_plan
            self.btn_execute.layout.display = "inline-flex"
            self._lbl_execute_hint.layout.display = "inline"
            self.lbl_status.value = (
                f"<b style='color:#1d4ed8'>✓ Plan ready.</b>"
                f"  <small style='color:#555'>LLM: {t_llm_ms:.0f} ms"
                f" · plan: {t_exec_ms:.0f} ms"
                f" · Click <b>Execute</b> to run with real ffmpeg.</small>"
            )
        else:
            self.lbl_status.value = (
                f"<b style='color:green'>✓ Done.</b>"
                f"  <small style='color:#555'>LLM: {t_llm_ms:.0f} ms"
                f" · workflow: {t_exec_ms:.0f} ms"
                f" · total: {t_total_ms:.0f} ms</small>"
            )

    # ── Execute (real ffmpeg) ───────────────────────────────────────────────────

    def _on_execute(self, _) -> None:
        if not self._pending_intent_plan:
            self.lbl_status.value = (
                "<span style='color:orange'>⚠ No plan pending — run a query first.</span>"
            )
            return
        self.btn_execute.disabled = True
        self.btn_execute.layout.display = "none"
        self._lbl_execute_hint.layout.display = "none"
        self._exec_out_html.value = ""
        self._sec_exec_out.layout.display = "block"
        self.lbl_status.value = "<b>⏳ Running ffmpeg…</b>"
        threading.Thread(target=self._run_execute_thread, daemon=True).start()

    def _run_execute_thread(self) -> None:
        try:
            self._do_execute()
        except Exception as exc:  # noqa: BLE001
            buf = _io.StringIO()
            _traceback.print_exc(file=buf)
            tb = buf.getvalue()
            self._exec_out_html.value = (
                f"<pre style='color:red'>❌ Error: {_html.escape(str(exc))}\n"
                f"{_html.escape(tb)}</pre>"
            )
            self.lbl_status.value = (
                f"<span style='color:red'>❌ Execution error: {_html.escape(str(exc))}</span>"
            )
        finally:
            self.btn_execute.disabled = False

    def _do_execute(self) -> None:
        intent_plan = self._pending_intent_plan
        if not intent_plan:
            self._exec_out_html.value = (
                "<i style='color:orange'>No pending plan — run a query first.</i>"
            )
            return

        t0 = _time.perf_counter()

        agent = CommandAgent.from_skill(
            self._skill_dir,
            sandbox=self._media_dir,
            root=self._media_dir,
            orchestrator=self._orchestrator,
            confirmer=self._gate,
            plan_confirmer=self._plan_gate,
        )

        results = agent.execute_plan(
            intent_plan,
            dry_run=False,
            skip_execution=False,
            confirmed=True,
        )
        t_ms = (_time.perf_counter() - t0) * 1000

        lines = ["  ─────────────────────────────────────────"]
        for r in results:
            line = self._print_step(r)
            if line:
                lines.append(_html.escape(line))
        lines += [
            "  ─────────────────────────────────────────",
            f"  ⏱  execution total            → {t_ms:6.0f} ms",
        ]
        self._exec_out_html.value = (
            "<pre style='margin:0;font-size:0.9em'>" + "\n".join(lines) + "</pre>"
        )

        bad_rcs = [
            r["result"].get("returncode")
            for r in results
            if r["result"].get("returncode") not in (None, 0)
        ]
        if bad_rcs:
            self.lbl_status.value = (
                f"<span style='color:#b91c1c'>⚠ Execution finished with errors "
                f"(rc={bad_rcs[0]}).</span>"
            )
        else:
            self.lbl_status.value = (
                f"<b style='color:green'>✓ Executed.</b>"
                f"  <small style='color:#555'>Total: {t_ms:.0f} ms</small>"
            )

    # ── step printing ───────────────────────────────────────────────────────────

    def _print_step(self, r: dict) -> str:
        tool = r["tool"]
        result = r.get("result", {})
        ms = r.get("duration_ms")
        t_str = f"  [{ms:5.0f} ms]" if ms is not None else ""

        if tool == "resolve_inputs":
            files = result.get("files", [])
            names = [Path(f).name for f in files]
            return f"  ✓  resolve_inputs        → {names}{t_str}"

        elif tool == "inspect_media":
            lines = []
            for s in result.get("probes", []):
                lines.append(
                    f"  ✓  inspect_media         → {Path(s.get('file', '?')).name}"
                    f"  {s.get('video_codec', '–')}"
                    f"  {s.get('width', '?')}×{s.get('height', '?')}"
                    f"  {float(s.get('duration', 0) or 0):.1f}s"
                    f"  {int(s.get('size_bytes', 0) or 0) // 1_000_000} MB"
                )
            for e in result.get("errors", []):
                lines.append(
                    f"  ✗  inspect_media         → error: {e.get('file', '?')}: {e.get('error', '?')}"
                )
            if lines:
                lines[-1] += t_str
            return "\n".join(lines) if lines else f"  ✓  inspect_media         → (no probes){t_str}"

        elif tool == "load_platform_profile":
            return (
                f"  ✓  load_platform_profile → {result.get('platform', '?')}"
                f"  container={result.get('container', '?')}"
                f"  max_audio={result.get('max_audio_bitrate', '?')}"
                f"  max_res={result.get('max_width', '')}×{result.get('max_height', '')}"
                f"{t_str}"
            )

        elif tool == "load_quality_profile":
            return (
                f"  ✓  load_quality_profile  → {result.get('quality', '?')}"
                f"  crf={result.get('video_crf', '?')}"
                f"  preset={result.get('encoder_preset', '?')}"
                f"  audio_br={result.get('audio_bitrate', '?')}"
                f"{t_str}"
            )

        elif tool == "build_recipes":
            n = len(result.get("recipes", []))
            return f"  ✓  build_recipes          → {n} recipe(s) built{t_str}"

        elif tool == "render_preview_command":
            return f"  ✓  render_preview_command → (see FFmpeg commands box){t_str}"

        elif tool == "render_batch_commands":
            cmds = result.get("commands", [])
            return f"  ✓  render_batch_commands  → {len(cmds)} command(s) (see FFmpeg commands box){t_str}"

        elif tool == "run_concat":
            mode = result.get("mode", "?")
            outputs = result.get("outputs", [{}])
            out_file = Path(outputs[0].get("output", "")).name if outputs else "?"
            if mode == "dry_run":
                return f"  ✓  run_concat             → [plan]  → {out_file}  (see FFmpeg commands box){t_str}"
            else:
                rc = result.get("returncode", "?")
                return f"  ✓  run_concat             → returncode={rc}  → {out_file}{t_str}"

        elif tool == "run_preview":
            mode = result.get("mode", "?")
            return f"  ✓  run_preview            → [{mode}]{t_str}"

        elif tool == "run_batch":
            mode = result.get("mode", "?")
            n = len(result.get("outputs", []))
            if mode == "dry_run":
                return f"  ✓  run_batch              → [plan]  {n} command(s) ready{t_str}"
            else:
                outputs = result.get("outputs", [])
                rcs = [o.get("returncode", "?") for o in outputs]
                return f"  ✓  run_batch              → {n} command(s)  rc={rcs}{t_str}"

        elif tool == "verify_preview":
            return f"  ✓  verify_preview{t_str}"

        elif tool == "verify_outputs":
            return f"  ✓  verify_outputs{t_str}"

        elif tool == "generate_report":
            return (
                f"  ✓  generate_report        → {result.get('count', '?')} file(s) processed{t_str}"
            )

        elif tool == "wait_for_confirmation":
            status = result.get("status", "?")
            icon = "✓" if status == "confirmed" else "✗"
            return f"  {icon}  wait_for_confirmation → {status}{t_str}"

        elif tool == "clarify":
            return f"  ?  clarify                → {result.get('question', result)}{t_str}"

        elif tool == "reject":
            return f"  ✗  reject                 → {result.get('reason', result)}{t_str}"

        else:
            return f"  ✓  {tool:<26} → {json.dumps(result)[:100]}{t_str}"

    # ── command extraction ──────────────────────────────────────────────────────

    def _collect_commands(self, results: list[dict]) -> list[str]:
        out: list[str] = []

        for r in results:
            tool = r["tool"]
            result = r.get("result", {})

            if tool == "run_concat":
                cmd = result.get("command", [])
                if cmd and isinstance(cmd, list):
                    outputs = result.get("outputs", [{}])
                    out_file = Path(outputs[0].get("output", "")).name if outputs else ""
                    out.append(f"# Concat command: → {out_file}\n" + " ".join(str(x) for x in cmd))

            elif tool == "render_preview_command":
                cmd = result.get("command", [])
                if cmd:
                    out.append("# Preview command\n" + " ".join(str(x) for x in cmd))

            elif tool == "render_batch_commands":
                for i, c in enumerate(result.get("commands", []), 1):
                    cmd = c.get("command", [])
                    out_file = Path(c.get("output", "")).name
                    if cmd:
                        out.append(f"# Command {i}: → {out_file}\n" + " ".join(str(x) for x in cmd))

        return out

    # ── plan description / gate callbacks ──────────────────────────────────────

    def _show_plan_description(self, summary: str) -> None:
        self._plan_desc_html.value = f"<i>{_html.escape(summary)}</i>"
        self._sec_plan_desc.layout.display = "block"

    def _show_plan_gate(self, summary: str) -> None:
        self.lbl_status.value = "<b style='color:#92400e'>⏸  Waiting for plan approval…</b>"
        self._plan_approval_box.layout.display = "block"

    def _hide_plan_gate(self, approved: bool) -> None:
        self._plan_approval_box.layout.display = "none"

    def _show_gate(self, prompt: str, preview) -> None:
        self.lbl_status.value = "<b style='color:#92400e'>⏸  Waiting for your approval…</b>"
        self._gate_preview.value = json.dumps(preview, indent=2) if preview else "(no preview data)"
        self._gate_box.layout.display = "block"

    def _hide_gate(self, approved: bool) -> None:
        self._gate_box.layout.display = "none"

    # ── render ──────────────────────────────────────────────────────────────────

    def show(self) -> None:
        header = widgets.HTML(
            '<h3 style="margin-bottom:4px">FFmpeg Skill — Natural Language Tester</h3>'
            '<hr style="margin-top:4px">'
        )

        children: list = [header]

        if self._model_selector is not None:
            children += [
                self._model_selector.widget,
                widgets.HTML("<hr style='margin:8px 0'>"),
            ]

        children += [
            self._hint,
            widgets.HBox([self._chk_thinking, self._thinking_hint]),
            widgets.HBox([self._chk_show_plan, self._chk_require_approval]),
            self._chk_dry_run,
            widgets.HTML("<br><b>What do you want to do?</b>"),
            self.txt_prompt,
            widgets.HTML("<br>"),
            widgets.HBox([self.btn_run, self.btn_clear]),
            self.lbl_status,
            widgets.HTML("<br>"),
            self._sec_plan_desc,
            self._plan_approval_box,
            self._sec_thinking,
            self._sec_intent,
            self._sec_log,
            self._sec_cmds,
            self._execute_row,
            self._sec_exec_out,
            self._gate_box,
        ]

        display(widgets.VBox(children))
