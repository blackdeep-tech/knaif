"""Backend and model selector widget for skill notebooks.

Can be used standalone (``selector.show()``) or embedded inside another
widget by including ``selector.widget`` in a VBox.

Import in the notebook with:
    from model_selector import ModelSelector
"""

from __future__ import annotations

import html as _html
import json as _json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

import ipywidgets as widgets
from IPython.display import display

from knaif.orchestrator import InferenceOrchestrator


class ModelSelector:
    """Dropdown UI for switching backend (llama_cpp / ollama) and model.

    Callbacks (set before ``show()`` / embedding):
        on_loading_start()          — called when Apply is clicked, before the
                                      background thread starts.
        on_ready(orchestrator)      — called from the background thread once the
                                      orchestrator is fully created. Keep it fast
                                      (variable assignment, attribute update).
    """

    def __init__(
        self,
        model_configs: dict,
        ollama_url: str,
        root: Path | str,
        default_backend: str = "llama_cpp",
        default_model: str = "",
        default_ollama_model: str = "",
    ) -> None:
        self._model_configs = model_configs
        self._ollama_url = ollama_url
        self._root = Path(root)
        self._default_backend = default_backend
        self._default_model = default_model
        self._default_ollama_model = default_ollama_model
        self._ollama_models: list[str] = []

        self.on_loading_start: Callable[[], None] | None = None
        self.on_ready: Callable[[InferenceOrchestrator], None] | None = None

        self._build_ui()
        threading.Thread(target=self._fetch_ollama_models, daemon=True).start()

    # ── background helpers ─────────────────────────────────────────────────────

    def _fetch_ollama_models(self) -> None:
        """Fetch pulled Ollama models from the local API (best-effort)."""
        try:
            url = f"{self._ollama_url}/api/tags"
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = _json.loads(resp.read())
            self._ollama_models = [m["name"] for m in data.get("models", [])]
            if self._dd_backend.value == "ollama":
                self._refresh_model_options()
        except (urllib.error.URLError, OSError):
            pass  # Ollama not running — silently skip

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        W = widgets.Layout

        self._dd_backend = widgets.Dropdown(
            options=["llama_cpp", "ollama"],
            value=self._default_backend,
            description="Backend:",
            style={"description_width": "70px"},
            layout=W(width="180px"),
        )

        self._dd_model = widgets.Dropdown(
            description="Model:",
            style={"description_width": "55px"},
            layout=W(width="380px"),
        )

        self._btn_apply = widgets.Button(
            description="▶  Apply",
            button_style="primary",
            layout=W(width="110px"),
        )

        self._lbl_status = widgets.HTML(value="")

        self._dd_backend.observe(self._on_backend_change, names="value")
        self._btn_apply.on_click(self._on_apply_click)

        self._refresh_model_options()

        # Pre-build the embeddable widget so it can be included in a parent VBox.
        self._widget = widgets.VBox(
            [
                widgets.HTML("<b>Backend &amp; model</b>"),
                widgets.HBox(
                    [self._dd_backend, self._dd_model, self._btn_apply],
                    layout=W(align_items="center", gap="8px"),
                ),
                self._lbl_status,
            ]
        )

    @property
    def widget(self) -> widgets.VBox:
        """Embeddable widget — include in a parent VBox instead of calling show()."""
        return self._widget

    def _refresh_model_options(self) -> None:
        backend = self._dd_backend.value

        if backend == "llama_cpp":
            options = [
                (f"{cfg.get('description', key)}  [{key}]", key)
                for key, cfg in self._model_configs.items()
            ]
            if not options:
                options = [("(no models configured)", "")]
            self._dd_model.options = options
            if self._default_model in self._model_configs:
                self._dd_model.value = self._default_model

        else:  # ollama
            if self._ollama_models:
                self._dd_model.options = self._ollama_models
                if self._default_ollama_model in self._ollama_models:
                    self._dd_model.value = self._default_ollama_model
                if "Fetching" in (self._lbl_status.value or ""):
                    self._lbl_status.value = ""
            else:
                fallback = self._default_ollama_model or "qwen:4b"
                self._dd_model.options = [fallback]
                self._dd_model.value = fallback
                self._lbl_status.value = (
                    "<small style='color:#6b7280'>Fetching Ollama model list… "
                    "If Ollama is not running the dropdown will stay fixed.</small>"
                )

    def _on_backend_change(self, _change) -> None:
        self._refresh_model_options()

    def _on_apply_click(self, _) -> None:
        # Idempotent: ignore duplicate/re-entrant fires for one click. The
        # handler can be bound more than once when %autoreload reloads this
        # module, and a disabled button does not stop already-registered
        # synchronous callbacks — so guard on an explicit flag instead.
        if getattr(self, "_loading", False):
            return
        self._loading = True
        self._btn_apply.disabled = True
        self._lbl_status.value = "<b>⏳ Loading…</b>"
        if self.on_loading_start is not None:
            self.on_loading_start()
        threading.Thread(target=self._load_orchestrator, daemon=True).start()

    def _load_orchestrator(self) -> None:
        backend = self._dd_backend.value
        model_key = self._dd_model.value

        try:
            if backend == "llama_cpp":
                model_cfg = self._model_configs.get(model_key)
                orc = InferenceOrchestrator(
                    backend="llama_cpp",
                    model_config=model_cfg,
                    root=self._root,
                    ollama_url=self._ollama_url,
                )
                model_loaded = bool(getattr(orc, "llm", None))
                label = (
                    self._model_configs.get(model_key, {}).get("description", model_key)
                    if model_cfg
                    else model_key
                )
                if model_loaded:
                    self._lbl_status.value = (
                        f"<b style='color:green'>✓ Loaded: {_html.escape(label)}</b>"
                    )
                else:
                    self._lbl_status.value = (
                        f"<span style='color:orange'>⚠ Model file not found — "
                        f"running as mock. ({_html.escape(label)})</span>"
                    )

            else:  # ollama
                orc = InferenceOrchestrator(
                    backend="ollama",
                    model_name=model_key,
                    ollama_url=self._ollama_url,
                    root=self._root,
                )
                self._lbl_status.value = (
                    f"<b style='color:green'>✓ Ollama: {_html.escape(model_key)}</b>"
                )

            if self.on_ready is not None:
                self.on_ready(orc)

        except Exception as exc:
            self._lbl_status.value = f"<span style='color:red'>❌ {_html.escape(str(exc))}</span>"
        finally:
            self._btn_apply.disabled = False
            self._loading = False

    # ── standalone display ─────────────────────────────────────────────────────

    def show(self) -> None:
        """Display as a standalone widget."""
        display(self._widget)
