"""Tests for the workbench runner contract (docs/plans/2026-09-21-skill-prompt-workbench.md T3).

The native runner is exercised through a fake subprocess, so CI needs no GGUF and no GPU — the
parsing is what these tests are about, and the parsing is what breaks.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SHARED = Path("notebooks") / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from workbench.runners import (  # noqa: E402
    RunResult,
    Timings,
    parse_native_timings,
)

# Captured verbatim from `KNAIF_TIMING=1 target/release-cuda/knaif.exe run ffmpeg --dry-run`
# on 2026-09-22 (RTX 5080, knaif-qwen3-4b-v2-q4_k_m.gguf). Not invented: the whole point of the
# timing panel is that these are the only cross-runtime-comparable numbers, so the parser is
# pinned to output that actually occurred.
_REAL_TIMING_TRACE = """\
[knaif-timing] model load_from_file = 954 ms
[knaif-timing] new_context = 17 ms
[knaif-timing] prompt_decode (2442 tokens) = 234 ms
[knaif-timing] generation (33 tokens) = 163 ms
[knaif-timing] generate_plan TOTAL = 418 ms
"""


def test_native_timings_parse_from_a_real_trace() -> None:
    t = parse_native_timings(_REAL_TIMING_TRACE)
    assert t.model_load_ms == 954.0
    assert t.prompt_tokens == 2442
    assert t.prompt_decode_ms == 234.0
    assert t.generation_tokens == 33
    assert t.generation_ms == 163.0
    assert t.generate_plan_total_ms == 418.0


def test_native_timings_are_absent_not_zero_when_untimed() -> None:
    """A run without KNAIF_TIMING has no numbers. Zero would be a measurement; None is the truth."""
    t = parse_native_timings("load_tensors: layer 0 assigned to device CUDA0, is_swa = 0")
    assert t.model_load_ms is None
    assert t.generate_plan_total_ms is None
    assert t.prompt_tokens is None


def test_timings_refuse_a_single_cross_runtime_time() -> None:
    """D4c: wall clock is not comparable across runtimes, so nothing exposes one number.

    Native pays process start per run; Python keeps the orchestrator resident and reuses its
    cache. `comparable_ms` is the generation window both runtimes can be held to.
    """
    native = Timings(wall_ms=1710.0, generate_plan_total_ms=418.0, model_load_ms=954.0, warm=False)
    python = Timings(wall_ms=150.0, generate_plan_total_ms=397.0, warm=True)
    assert native.comparable_ms == 418.0
    assert python.comparable_ms == 397.0
    # The wall figures differ by 11x and mean nothing against each other.
    assert not hasattr(native, "time_ms")


def test_run_result_carries_what_the_panel_renders() -> None:
    r = RunResult(
        runtime="native",
        skill="ffmpeg",
        utterance="convert clip.mp4 to mkv",
        outcome="plan",
        plan={"plan": [{"tool": "convert_video", "args": {}}]},
        commands=["ffmpeg -i clip.mp4 out.mkv"],
        artifacts=[],
        timings=Timings(),
        placement={"CUDA0": 37},
        enumerated_device="CUDA0",
        stdout="",
        stderr="",
    )
    assert r.runtime == "native"
    assert r.measured_backend == "CUDA0"
    assert r.error is None


def test_run_result_reports_the_device_that_ran_not_the_one_enumerated() -> None:
    """The D2 defect, at the runner boundary: enumeration must never become the answer."""
    r = RunResult(
        runtime="native",
        skill="ffmpeg",
        utterance="convert clip.mp4 to mkv",
        outcome="plan",
        plan=None,
        commands=[],
        artifacts=[],
        timings=Timings(),
        placement={"CPU": 37},
        enumerated_device="CUDA0",
        stdout="",
        stderr="",
    )
    assert r.measured_backend == "CPU"


def test_dry_run_commands_come_from_stdout() -> None:
    """`--dry-run` prints the rendered command on stdout; only a real run echoes `running:`.

    Verified against target/release-cuda/knaif.exe on 2026-09-22: a dry run's entire stdout was
    `ffmpeg -y -i clip.mp4 -c copy clip_converted.mkv`. Without this the panel's COMMAND section
    is empty for exactly the mode the bench defaults to.
    """
    from workbench.runners import rendered_commands

    stdout = "ffmpeg -y -i clip.mp4 -c copy clip_converted.mkv\n"
    assert rendered_commands(stdout, echoed=[], outcome="plan") == [
        "ffmpeg -y -i clip.mp4 -c copy clip_converted.mkv"
    ]


def test_echoed_commands_win_over_stdout() -> None:
    """A real run echoes what it executed; that is the better source, so it is preferred."""
    from workbench.runners import rendered_commands

    assert rendered_commands("noise\n", echoed=["ffmpeg -i a.mp4 b.mkv"], outcome="plan") == [
        "ffmpeg -i a.mp4 b.mkv"
    ]


def test_a_refusal_renders_no_command() -> None:
    """reject/clarify print a message, not a command — treating it as one would be a lie."""
    from workbench.runners import rendered_commands

    assert rendered_commands("reject: unsafe request\n", echoed=[], outcome="reject") == []
    assert rendered_commands("clarify: which file?\n", echoed=[], outcome="clarify") == []


def test_warm_is_derived_from_cache_reuse_not_asserted() -> None:
    """A repeat call decodes one prompt token against a reused prefix. That is the signal."""
    from workbench.runners import _python_timings

    class _Agent:
        class orchestrator:  # noqa: N801
            last_timings = {
                "model_load_ms": None,
                "prompt_tokens": 1,
                "prompt_decode_ms": 0.0,
                "generation_tokens": 28,
                "generation_ms": 130.0,
                "reused_tokens": 28,
                "generate_plan_total_ms": 134.0,
            }

    t = _python_timings(_Agent(), 134.0)
    assert t.warm is True
    assert t.reused_tokens == 28
    assert t.comparable_ms == 134.0


def test_a_cold_call_is_not_reported_warm() -> None:
    from workbench.runners import _python_timings

    class _Agent:
        class orchestrator:  # noqa: N801
            last_timings = {
                "prompt_tokens": 28,
                "prompt_decode_ms": 167.0,
                "generation_tokens": 27,
                "generation_ms": 146.0,
                "reused_tokens": 26,
                "generate_plan_total_ms": 317.0,
            }

    assert _python_timings(_Agent(), 317.0).warm is False


def test_timings_fall_back_to_wall_clock_when_uninstrumented() -> None:
    """An ollama or mock agent has no counters; the panel still gets the one real number."""
    from workbench.runners import _python_timings

    class _Agent:
        orchestrator = None

    t = _python_timings(_Agent(), 500.0)
    assert t.generate_plan_total_ms == 500.0
    assert t.prompt_tokens is None


def test_fd2_capture_catches_c_level_output_and_always_restores() -> None:
    """llama.cpp writes past sys.stderr, so the descriptor itself has to be redirected."""
    import os

    from workbench.capture import capture_fd2

    with capture_fd2() as captured:
        os.write(2, b"load_tensors: layer   0 assigned to device CUDA0, is_swa = 0\n")
    assert "CUDA0" in captured[0]

    # The descriptor is usable again afterwards — losing a notebook's stderr would be worse
    # than missing a reading.
    os.write(2, b"")


def test_fd2_capture_restores_even_when_the_block_raises() -> None:
    import os

    import pytest
    from workbench.capture import capture_fd2

    with pytest.raises(ValueError):
        with capture_fd2():
            raise ValueError("boom")
    os.write(2, b"")


def test_the_agent_is_reused_until_the_selection_changes() -> None:
    """A model load is ~1-2s and 2.3 GB of VRAM. Reloading per utterance would make the bench
    slower than the eval suite it exists to shortcut.
    """
    from pathlib import Path as _Path

    import workbench.ui as ui_module
    from workbench.inventory import ModelEntry
    from workbench.selectors import Selection
    from workbench.ui import _AgentCache

    built: list[tuple[str, bool]] = []
    cache = _AgentCache()
    real = ui_module.python_agent

    def _fake(sel, root, sandbox, verbose=False):
        built.append((sel.model.path, verbose))
        return object()

    ui_module.python_agent = _fake
    try:
        a = ModelEntry(name="a", path="models/a.gguf", resolved=True)
        b = ModelEntry(name="b", path="models/b.gguf", resolved=True)
        here = _Path(".")

        cache.get(Selection(model=a), root=here, sandbox=here)
        cache.get(Selection(model=a), root=here, sandbox=here)
        assert built == [("models/a.gguf", False)], "same selection must not reload"

        cache.get(Selection(model=b), root=here, sandbox=here)
        assert len(built) == 2

        # force CPU changes where the weights go, so it is a different load.
        cache.get(Selection(model=b, force_cpu=True), root=here, sandbox=here)
        assert len(built) == 3
    finally:
        ui_module.python_agent = real


def test_verbosity_is_part_of_the_cache_key() -> None:
    """The load trace is the ONLY place the layer placement is stated, and it is emitted during
    the load. So ticking verbose reloads once — the honest cost of asking a load-time question.
    """
    from pathlib import Path as _Path

    import workbench.ui as ui_module
    from workbench.inventory import ModelEntry
    from workbench.selectors import Selection
    from workbench.ui import _AgentCache

    built: list[bool] = []
    cache = _AgentCache()
    real = ui_module.python_agent
    ui_module.python_agent = lambda sel, root, sandbox, verbose=False: (
        built.append(verbose) or object()
    )
    try:
        model = ModelEntry(name="a", path="models/a.gguf", resolved=True)
        here = _Path(".")
        cache.get(Selection(model=model), root=here, sandbox=here, verbose=False)
        cache.get(Selection(model=model), root=here, sandbox=here, verbose=True)
        cache.get(Selection(model=model), root=here, sandbox=here, verbose=True)
        assert built == [False, True], "verbose flips the load exactly once"
    finally:
        ui_module.python_agent = real


def test_changing_the_skill_rebuilds_the_agent() -> None:
    """The agent is built with `CommandAgent.from_skill`, so the skill is baked into its
    registry and prompt. Leaving it out of the cache key meant switching the Skill dropdown
    silently kept planning against the previous skill's tools.
    """
    from pathlib import Path as _Path

    import workbench.ui as ui_module
    from workbench.inventory import ModelEntry
    from workbench.selectors import Selection
    from workbench.ui import _AgentCache

    built: list[str] = []
    cache = _AgentCache()
    real = ui_module.python_agent
    ui_module.python_agent = lambda sel, root, sandbox, verbose=False: (
        built.append(sel.skill) or object()
    )
    try:
        model = ModelEntry(name="a", path="models/a.gguf", resolved=True)
        here = _Path(".")
        cache.get(Selection(model=model, skill="ffmpeg"), root=here, sandbox=here)
        cache.get(Selection(model=model, skill="ffmpeg"), root=here, sandbox=here)
        cache.get(Selection(model=model, skill="documents"), root=here, sandbox=here)
        assert built == ["ffmpeg", "documents"]
    finally:
        ui_module.python_agent = real


def _fake_proc(stdout: str, stderr: str, returncode: int):
    class _Proc:
        pass

    proc = _Proc()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


def test_a_failed_native_run_carries_the_reason(tmp_path, monkeypatch) -> None:
    """`outcome: error` without a message is a dead end — the panel prints the verdict and the
    reason is thrown away with the exit code. Observed on a 6-step ffmpeg chain that stopped
    after 3 commands with nothing on screen to say why.
    """
    import subprocess as _subprocess

    from workbench.runners import NativeRunner

    stderr = (
        "load_tensors: layer   0 assigned to device CUDA0, is_swa = 0\n"
        "running: ffmpeg -y -i clip.mp4 -an -c:v copy clip_silent.mp4\n"
        "Error: step 3 of 6 failed\n"
        "Caused by:\n"
        "    ffmpeg exited with status 234\n"
    )
    monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: _fake_proc("", stderr, 1))

    runner = NativeRunner("knaif.exe", "m.gguf", skill="ffmpeg", work_dir=tmp_path)
    result = runner.run("cut clip.mp4", dry_run=False)

    assert result.outcome == "error"
    assert result.error is not None
    assert "step 3 of 6 failed" in result.error
    # The reason, not the tail of a llama.cpp load trace.
    assert "load_tensors" not in result.error


def test_a_successful_native_run_reports_no_error(tmp_path, monkeypatch) -> None:
    import subprocess as _subprocess

    from workbench.runners import NativeRunner

    monkeypatch.setattr(
        _subprocess,
        "run",
        lambda *a, **kw: _fake_proc("ffmpeg -y -i clip.mp4 out.mkv\n", "", 0),
    )
    runner = NativeRunner("knaif.exe", "m.gguf", skill="ffmpeg", work_dir=tmp_path)
    result = runner.run("convert clip.mp4 to mkv")

    assert result.outcome == "plan"
    assert result.error is None


def test_a_run_reports_a_file_it_overwrote(tmp_path, monkeypatch) -> None:
    """An artifact list built from NEW NAMES ONLY goes empty on the second run of a plan.

    Observed: a re-run of the same six-step chain wrote `Test1.mov` exactly as the first run
    had, and the panel printed no ARTIFACTS section at all — reading as though the run had
    produced nothing, when it had produced the same file again.
    """
    import subprocess as _subprocess

    from workbench.runners import NativeRunner

    stale = tmp_path / "Test1.mov"
    stale.write_bytes(b"old")

    def _run(*_a, **_kw):
        stale.write_bytes(b"new, and a different size")
        return _fake_proc("", "running: ffmpeg -y -i clip.mp4 Test1.mov\n", 0)

    monkeypatch.setattr(_subprocess, "run", _run)
    runner = NativeRunner("knaif.exe", "m.gguf", skill="ffmpeg", work_dir=tmp_path)
    result = runner.run("trim clip.mp4", dry_run=False)

    assert [p.name for p in result.artifacts] == ["Test1.mov"]


def test_a_file_the_run_never_touched_is_not_an_artifact(tmp_path, monkeypatch) -> None:
    """The fixtures live in the same directory. Listing them would drown the real output."""
    import subprocess as _subprocess

    from workbench.runners import NativeRunner

    (tmp_path / "clip.mp4").write_bytes(b"fixture")

    monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: _fake_proc("", "", 0))
    runner = NativeRunner("knaif.exe", "m.gguf", skill="ffmpeg", work_dir=tmp_path)

    assert runner.run("convert clip.mp4 to mkv").artifacts == []


# ── Python runner: what actually ran ────────────────────────────────────────────────────────
#
# Shapes copied from a real `execute_plan` of the 6-step Test1/Test2/Test3 chain (2026-09-22).
# The argv sits on the render step, the exit code on the run step, and they meet on `output`.
# Reading only top-level `command` keys found neither, so the panel showed no command and no
# failure for a chain that stopped at step 5 with ffmpeg exit -22.


def _render(work: Path, src: str, dst: str) -> dict:
    cmd = ["ffmpeg", "-y", "-i", str(work / src), "-c:v", "libx264", str(work / dst)]
    return {
        "tool": "render_batch_commands",
        "result": {
            "count": 1,
            "commands": [{"input": str(work / src), "output": str(work / dst), "command": cmd}],
        },
    }


def _ran(work: Path, src: str, dst: str, returncode: int, stderr: str = "") -> dict:
    return {
        "tool": "run_batch",
        "result": {
            "mode": "execute",
            "count": 1,
            "outputs": [
                {
                    "mode": "execute",
                    "input": str(work / src),
                    "output": str(work / dst),
                    "returncode": returncode,
                    "stderr_tail": stderr,
                }
            ],
        },
    }


_EMPTY_INPUT_STDERR = (
    "  Duration: N/A, bitrate: N/A\n"
    "Output #0, mov, to 'Test2.mov':\n"
    "[out#0/mov @ 00000208ffa42600] Output file does not contain any stream\n"
    "Error opening output file Test2.mov.\n"
    "Error opening output files: Invalid argument\n"
)


def test_executions_pair_each_command_with_its_exit_code(tmp_path) -> None:
    from workbench.runners import executions_of

    results = [
        _render(tmp_path, "clip.mp4", "Test1.mov"),
        _ran(tmp_path, "clip.mp4", "Test1.mov", 0),
        _render(tmp_path, "Test1.mov", "Test2.mov"),
        _ran(tmp_path, "Test1.mov", "Test2.mov", 4294967274, _EMPTY_INPUT_STDERR),
    ]

    runs = executions_of(results, work_dir=tmp_path)

    assert [(r.command, r.returncode) for r in runs] == [
        ("ffmpeg -y -i clip.mp4 -c:v libx264 Test1.mov", 0),
        # Windows reports a negative exit unsigned; -22 is EINVAL, 4294967274 is noise.
        ("ffmpeg -y -i Test1.mov -c:v libx264 Test2.mov", -22),
    ]
    assert runs[0].reason is None
    assert "Output file does not contain any stream" in runs[1].reason


def test_a_dry_run_executes_nothing(tmp_path) -> None:
    from workbench.runners import executions_of

    dry = {
        "tool": "run_batch",
        "result": {
            "mode": "dry_run",
            "outputs": [{"mode": "dry_run", "output": "x", "command": ["ffmpeg", "-i", "a"]}],
        },
    }
    assert executions_of([_render(tmp_path, "a.mp4", "b.mkv"), dry], work_dir=tmp_path) == []


def test_python_commands_are_found_inside_step_results_once_each(tmp_path) -> None:
    from workbench.runners import commands_of

    dry = {
        "tool": "run_batch",
        "result": {
            "mode": "dry_run",
            "outputs": [
                {
                    "mode": "dry_run",
                    "output": str(tmp_path / "b.mkv"),
                    "command": [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(tmp_path / "a.mp4"),
                        "-c:v",
                        "libx264",
                        str(tmp_path / "b.mkv"),
                    ],
                }
            ],
        },
    }
    # The render step and the dry-run step both carry the argv; it is one command.
    assert commands_of([_render(tmp_path, "a.mp4", "b.mkv"), dry], work_dir=tmp_path) == [
        "ffmpeg -y -i a.mp4 -c:v libx264 b.mkv"
    ]


class _FakeAgent:
    registry: list = []
    orchestrator = None

    def __init__(self, results: list[dict]) -> None:
        self._results = results

    def infer(self, *_a, **_kw) -> dict:
        return {"plan": [{"tool": "trim_video", "args": {}}]}

    def execute_plan(self, *_a, **_kw) -> list[dict]:
        return self._results


def test_a_failed_ffmpeg_step_makes_the_python_run_an_error(tmp_path, monkeypatch) -> None:
    from workbench.runners import PythonRunner

    import knaif.registry

    monkeypatch.setattr(knaif.registry, "retrieve_tools", lambda *_a, **_kw: [])
    agent = _FakeAgent(
        [
            _render(tmp_path, "Test1.mov", "Test2.mov"),
            _ran(tmp_path, "Test1.mov", "Test2.mov", 4294967274, _EMPTY_INPUT_STDERR),
        ]
    )

    result = PythonRunner(agent, skill="ffmpeg", work_dir=tmp_path).run("x", dry_run=False)

    assert result.outcome == "error"
    assert "exit -22" in result.error
    assert "does not contain any stream" in result.error
    assert result.commands == ["ffmpeg -y -i Test1.mov -c:v libx264 Test2.mov"]


def test_a_concat_is_one_execution_not_two(tmp_path) -> None:
    """`run_concat` states its exit code at the top and again in `outputs[0]`."""
    from workbench.runners import executions_of

    concat = {
        "tool": "run_concat",
        "result": {
            "mode": "execute",
            "command": ["ffmpeg", "-y", "-f", "concat", "-i", "list.txt", "Test3.mov"],
            "returncode": 0,
            "outputs": [{"output": "Test3.mov", "returncode": 0, "stderr_tail": ""}],
        },
    }
    runs = executions_of([concat], work_dir=tmp_path)
    assert [(r.command, r.returncode) for r in runs] == [
        ("ffmpeg -y -f concat -i list.txt Test3.mov", 0)
    ]


def _console(monkeypatch, tmp_path, results: list[dict]):
    """The real console, with the model and the agent build stubbed out."""
    import types

    import pytest

    widgets = pytest.importorskip("ipywidgets")
    from workbench import ui

    import knaif.registry

    monkeypatch.setattr(knaif.registry, "retrieve_tools", lambda *_a, **_kw: [])
    monkeypatch.setattr(ui, "python_agent", lambda *_a, **_kw: _FakeAgent(results))
    selection = types.SimpleNamespace(
        model=types.SimpleNamespace(path="m.gguf"),
        skill="ffmpeg",
        force_cpu=False,
        dry_run=True,
        wants_python=True,
        wants_native=False,
        describe=lambda: "python",
    )
    handle = ui.console(selection, root=tmp_path, sandbox=tmp_path)
    out = next(w for w in handle.widget.children if isinstance(w, widgets.Output))
    return handle, out


def _text(out) -> str:
    return "".join(o.get("text", "") for o in out.outputs)


def test_one_run_is_printed_once(tmp_path, monkeypatch) -> None:
    """Observed in VS Code: every line of ONE run twice (same `_3` names in both copies).

    Capture (`with out: print(...)`) routes each write through the frontend, which doubled it.
    The console now sets the widget's content in one state write.
    """
    handle, out = _console(monkeypatch, tmp_path, [])
    handle.run("trim clip.mp4")

    assert len(out.outputs) == 1
    assert _text(out).count("PLAN ") == 1


def test_a_second_run_replaces_the_first(tmp_path, monkeypatch) -> None:
    handle, out = _console(monkeypatch, tmp_path, [])
    handle.run("first utterance")
    handle.run("second utterance")

    assert "second utterance" in _text(out)
    assert "first utterance" not in _text(out)
