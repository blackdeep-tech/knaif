"""The three dropdowns, plus the levers that do not fit in one.

"Backend" means three unrelated things in this repo, so the word never appears alone here:

    Runtime     who runs it            Python / Native / Both
    Inference   what makes the tokens  llama.cpp / ollama        (Python only)
    Compute     which chip             a build picker            (native only)

Plus **force CPU**, which is the one control that deliberately crosses both runtimes, because a
CPU-versus-GPU comparison where only one side moves is worthless.

Widgets are used on purpose (decided 2026-09-22). The cost, accepted: they do not render on
GitHub, so the notebook is meant to be run rather than read.

See docs/plans/2026-09-21-skill-prompt-workbench.md (D1, D1b-D1e).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .inventory import BuildEntry, ModelEntry


@dataclass
class Selection:
    """What the dropdowns currently say. Plain data, so a script can build one directly."""

    runtime: str = "python"  # "python" | "native" | "both"
    inference: str = "llama.cpp"
    build: BuildEntry | None = None
    model: ModelEntry | None = None
    skill: str = "ffmpeg"
    mode: str = "dry-run"  # "dry-run" | "execute"
    force_cpu: bool = False
    backends_dir: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def dry_run(self) -> bool:
        return self.mode != "execute"

    @property
    def wants_python(self) -> bool:
        return self.runtime in {"python", "both"}

    @property
    def wants_native(self) -> bool:
        return self.runtime in {"native", "both"}

    def describe(self) -> str:
        """A one-line record of what produced a result — worth pasting beside a number."""
        bits = [f"runtime={self.runtime}", f"skill={self.skill}", f"mode={self.mode}"]
        if self.model:
            bits.append(f"model={self.model.name}")
        if self.wants_native and self.build:
            bits.append(f"build={self.build.path.parent.name}")
        if self.force_cpu:
            bits.append("force_cpu")
        return " · ".join(bits)


def build_widgets(inventory: dict[str, Any], *, selection: Selection | None = None) -> Any:
    """Render the selector rows and return a handle whose `.selection` stays current.

    Importing ipywidgets here rather than at module import keeps `Selection` usable from a
    script, and keeps the unit tests free of a GUI dependency.
    """
    import ipywidgets as widgets
    from IPython.display import display

    current = selection or Selection()

    def _label(text: str) -> Any:
        return widgets.HTML(f"<code style='color:#64748b'>{text:<11}</code>")

    runtime = widgets.ToggleButtons(
        options=[("Python", "python"), ("Native", "native"), ("Both", "both")],
        value=current.runtime,
    )

    inference = widgets.Dropdown(
        options=["llama.cpp"] + [a for a in inventory.get("inference", []) if "ollama" in a],
        value="llama.cpp",
        layout=widgets.Layout(width="360px"),
    )

    builds: list[BuildEntry] = inventory.get("builds", [])
    build = widgets.Dropdown(
        options=[(b.label, b) for b in builds] or [("(no native build found)", None)],
        layout=widgets.Layout(width="520px"),
    )

    models = inventory.get("models", {})
    options: list[tuple[str, ModelEntry | None]] = []
    for group in ("published", "curated", "experimental", "missing"):
        for entry in models.get(group, []):
            options.append((f"{group:<13} {entry.label}", None if group == "missing" else entry))
    model = widgets.Dropdown(
        options=options or [("(no models resolve)", None)],
        layout=widgets.Layout(width="620px"),
    )

    skill = widgets.Dropdown(options=sorted(inventory.get("skills", {})), value=current.skill)
    mode = widgets.ToggleButtons(
        options=[("dry-run", "dry-run"), ("execute for real", "execute")], value=current.mode
    )
    force_cpu = widgets.Checkbox(value=False, description="force CPU (BOTH runtimes)", indent=False)
    backends_dir = widgets.Text(placeholder="backends dir", layout=widgets.Layout(width="420px"))

    venv_note = widgets.HTML(
        "<span style='color:#64748b'>python's chip is fixed by its venv — shown, not chosen</span>"
    )

    def _sync(_change: Any = None) -> None:
        current.runtime = runtime.value
        current.inference = inference.value
        current.build = build.value
        current.model = model.value
        current.skill = skill.value
        current.mode = mode.value
        current.force_cpu = force_cpu.value
        current.backends_dir = backends_dir.value or None
        # The backends dir is meaningful only for a dynamic-backends build, so it appears only
        # when one is selected — D1's rule that an inert control is worse than none.
        chosen = build.value
        backends_dir.layout.display = (
            "" if isinstance(chosen, BuildEntry) and chosen.dynamic_backends else "none"
        )
        build.disabled = current.runtime == "python"
        inference.disabled = current.runtime == "native"

    for control in (runtime, inference, build, model, skill, mode, force_cpu, backends_dir):
        control.observe(_sync, names="value")
    _sync()

    rows = widgets.VBox(
        [
            widgets.HBox([_label("Runtime"), runtime]),
            widgets.HBox([_label("Inference"), inference]),
            widgets.HBox([_label("Compute"), build]),
            widgets.HBox([_label(""), force_cpu, backends_dir]),
            widgets.HBox([_label(""), venv_note]),
            widgets.HBox([_label("Model"), model]),
            widgets.HBox([_label("Skill"), skill, mode]),
        ]
    )
    display(rows)

    class _Handle:
        selection = current
        controls = rows

    return _Handle()


def python_agent(selection: Selection, *, root: Path | str = ".", sandbox: Path | str) -> Any:
    """A `CommandAgent` wired to the selected model, through the production path."""
    from knaif import CommandAgent
    from knaif.orchestrator import InferenceOrchestrator

    if selection.model is None:
        raise ValueError("no model selected — the Python runner needs one")

    from knaif.evalsuite.native_lane import parse_tensor_placement

    from .capture import capture_fd2

    # `verbose=True` is forced so llama.cpp prints its load trace, and fd 2 is captured because
    # that trace never passes through sys.stderr. This is the ONLY way to learn where Python's
    # layers actually landed — the same question, and the same parser, as the native side.
    with capture_fd2() as trace:
        orchestrator = InferenceOrchestrator(
            backend="llama_cpp",
            model_config={
                "path": selection.model.path,
                "n_ctx": 8192,
                "n_gpu_layers": 0 if selection.force_cpu else 99,
                "max_tokens": 2048,
                "json_mode": False,
                "thinking_enabled": False,
                "verbose": True,
            },
            root=root,
        )
    orchestrator.placement = parse_tensor_placement(trace[0] if trace else "")

    return CommandAgent.from_skill(
        Path(root) / "skills" / selection.skill, sandbox=sandbox, orchestrator=orchestrator
    )
