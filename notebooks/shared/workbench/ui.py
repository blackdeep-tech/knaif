"""The thing you actually use: type an utterance, press Run, read the plan.

Everything else in this package exists to make this cell honest. The console keeps the model
resident between runs — reloading a 2.3 GB GGUF per utterance would make the bench unusable for
the one job it has, which is trying phrasings quickly.

See docs/plans/2026-09-21-skill-prompt-workbench.md (T6, T7).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import fixtures, panel
from .runners import NativeRunner, PythonRunner
from .selectors import Selection, python_agent


class _AgentCache:
    """One loaded model per (path, force_cpu), reused until the selection changes.

    A model load is ~1-2 s and 2.3 GB of VRAM. Rebuilding per run would make an interactive
    bench slower than the eval suite it exists to shortcut.
    """

    def __init__(self) -> None:
        self._key: tuple[str, str, bool, bool] | None = None
        self._agent: Any = None

    def get(self, selection: Selection, *, root: Path, sandbox: Path, verbose: bool = False) -> Any:
        if selection.model is None:
            raise ValueError("no model selected")
        # Everything baked in AT LOAD belongs in this key. The skill is: the agent is built
        # with `CommandAgent.from_skill`, so its registry and prompt are that skill's. `verbose`
        # is too — the load trace is the only place the layer placement is stated. Changing
        # either reloads once, which is the honest cost of a load-time question.
        key = (selection.model.path, selection.skill, selection.force_cpu, verbose)
        if key != self._key:
            self._agent = python_agent(selection, root=root, sandbox=sandbox, verbose=verbose)
            self._key = key
        return self._agent


def console(
    selection: Selection,
    *,
    root: Path | str,
    sandbox: Path | str,
    utterance: str = "convert clip.mp4 to mkv",
    repeat: int = 1,
) -> Any:
    """A prompt box, a Run button, and an output area. Returns the widget box to display.

    The last results stay on the returned handle as `.results`, so a later cell can dig into a
    plan without re-running inference.
    """
    import ipywidgets as widgets
    from IPython.display import display

    root, sandbox = Path(root), Path(sandbox)
    cache = _AgentCache()

    box = widgets.Textarea(
        value=utterance,
        placeholder="what should knaif do?",
        layout=widgets.Layout(width="100%", height="64px"),
    )
    run = widgets.Button(description="Run", button_style="primary", icon="play")
    times = widgets.BoundedIntText(
        value=repeat, min=1, max=50, description="runs", layout=widgets.Layout(width="140px")
    )
    verbose = widgets.Checkbox(
        value=False, description="verbose", indent=False, layout=widgets.Layout(width="110px")
    )
    status = widgets.HTML("")
    out = widgets.Output()

    handle_results: list[Any] = []

    def _go(_button: Any = None) -> None:
        text = box.value.strip()
        out.clear_output(wait=True)
        if not text:
            with out:
                print("type something first")
            return

        status.value = "<span style='color:#a16207'>running…</span>"
        run.disabled = True
        # A real run writes files, so it needs its own copies to write over. Dry-run needs
        # none — and a skill whose fixtures were never generated stays usable in dry-run.
        note = ""
        if not selection.dry_run:
            _, note = fixtures.ensure(root, selection.skill, sandbox)
        try:
            results = []
            with out:
                if note:
                    print(note)
                    print()
                try:
                    if selection.wants_python:
                        agent = cache.get(
                            selection, root=root, sandbox=sandbox, verbose=verbose.value
                        )
                        runner = PythonRunner(agent, skill=selection.skill, work_dir=sandbox)
                        runs = [
                            runner.run(text, dry_run=selection.dry_run) for _ in range(times.value)
                        ]
                        results.append(runs[0])
                        print(panel.show(runs[0], verbose=verbose.value))
                        if len(runs) > 1:
                            print()
                            print(panel.stats(runs))

                    if selection.wants_native:
                        if selection.build is None or selection.model is None:
                            print("\nnative: pick a build and a model")
                        else:
                            native = NativeRunner(
                                binary=selection.build.path,
                                model_path=root / selection.model.path,
                                skill=selection.skill,
                                work_dir=sandbox,
                                force_cpu=selection.force_cpu,
                                backends_dir=selection.backends_dir,
                            ).run(text, dry_run=selection.dry_run)
                            results.append(native)
                            print()
                            print(panel.show(native, verbose=verbose.value))

                    if len(results) == 2:
                        print()
                        print(panel.compare(*results))
                except (
                    Exception
                ) as exc:  # noqa: BLE001 — a bench reports failures, it does not raise
                    print(f"\n{type(exc).__name__}: {exc}")
            handle_results[:] = results
            status.value = f"<span style='color:#64748b'>{selection.describe()}</span>"
        finally:
            run.disabled = False

    def _redraw(_change: Any = None) -> None:
        """Flip verbosity on what already ran, rather than paying for inference again."""
        if not handle_results:
            return
        out.clear_output(wait=True)
        with out:
            for index, result in enumerate(handle_results):
                if index:
                    print()
                print(panel.show(result, verbose=verbose.value))
            if len(handle_results) == 2:
                print()
                print(panel.compare(*handle_results))

    verbose.observe(_redraw, names="value")
    run.on_click(_go)
    box.continuous_update = False

    rows = widgets.VBox(
        [
            widgets.HTML("<b>Utterance</b>"),
            box,
            widgets.HBox([run, times, verbose, status]),
            out,
        ]
    )
    display(rows)

    class _Handle:
        widget = rows
        results = handle_results

        def run(self, text: str | None = None) -> None:
            """Run from code, for a scripted sweep."""
            if text is not None:
                box.value = text
            _go()

    return _Handle()
