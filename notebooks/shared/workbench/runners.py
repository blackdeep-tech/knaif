"""One interface over both runtimes, so the panel renders one shape.

`PythonRunner` drives `CommandAgent` in-process. `NativeRunner` shells out to the built binary
and parses what it printed. Both return a `RunResult`.

Two things this module refuses to do, both learned the hard way:

* **It never reports a single cross-runtime "time".** Native pays process start on every run;
  Python keeps the orchestrator resident and very likely reuses its prompt-prefix cache. Read as
  wall clock that makes native look 11x slower, which is nonsense — it is process lifecycle, not
  runtime quality. `Timings.comparable_ms` is the only figure held to both.
* **It never infers the compute backend from llama.cpp's enumeration line.** That line names a
  device llama.cpp *considered*; with `KNAIF_N_GPU_LAYERS=0` it still says `CUDA0` while every
  layer runs on the CPU. `measured_backend` comes from where the layers landed.

See docs/plans/2026-09-21-skill-prompt-workbench.md (D2, D4c, T3).
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── native timing ──────────────────────────────────────────────────────────────────────────
#
# `run` emits these under $KNAIF_TIMING=1. Captured verbatim on 2026-09-22:
#
#   [knaif-timing] model load_from_file = 954 ms
#   [knaif-timing] new_context = 17 ms
#   [knaif-timing] prompt_decode (2442 tokens) = 234 ms
#   [knaif-timing] generation (33 tokens) = 163 ms
#   [knaif-timing] generate_plan TOTAL = 418 ms

_T_LOAD = re.compile(r"^\[knaif-timing\] model load_from_file = ([\d.]+) ms", re.MULTILINE)
_T_CONTEXT = re.compile(r"^\[knaif-timing\] new_context = ([\d.]+) ms", re.MULTILINE)
_T_PROMPT = re.compile(
    r"^\[knaif-timing\] prompt_decode \((\d+) tokens\) = ([\d.]+) ms", re.MULTILINE
)
_T_GEN = re.compile(r"^\[knaif-timing\] generation \((\d+) tokens\) = ([\d.]+) ms", re.MULTILINE)
_T_TOTAL = re.compile(r"^\[knaif-timing\] generate_plan TOTAL = ([\d.]+) ms", re.MULTILINE)


@dataclass(frozen=True)
class Timings:
    """What one run spent, per phase.

    Every field is optional and **absent means unmeasured, never zero** — a zero would be a
    measurement. Python leaves most of these `None` until T4 instruments `orchestrator.py`; the
    panel labels its column "end-to-end only" while that is true.
    """

    model_load_ms: float | None = None
    new_context_ms: float | None = None
    prompt_tokens: int | None = None
    prompt_decode_ms: float | None = None
    generation_tokens: int | None = None
    generation_ms: float | None = None
    #: Tokens llama.cpp took from the KV cache instead of decoding. This is what makes a
    #: warm run legible: measured across three identical Python calls, prompt tokens went
    #: 28 -> 1 -> 1 while this rose to 28. A 132 ms repeat beside native's 418 ms is cache
    #: reuse, not a faster runtime.
    reused_tokens: int | None = None
    generate_plan_total_ms: float | None = None
    #: Wall clock for the whole call, including process start for the native runner.
    wall_ms: float | None = None
    #: True when the model was already resident — i.e. this is not a cold number.
    warm: bool = False

    @property
    def comparable_ms(self) -> float | None:
        """The one figure both runtimes may be held to: the generation window.

        Deliberately not `wall_ms`. There is no `time_ms` property and there must not be one.
        """
        return self.generate_plan_total_ms

    @property
    def process_overhead_ms(self) -> float | None:
        """Wall clock minus the work — process start, model load, teardown."""
        if self.wall_ms is None or self.generate_plan_total_ms is None:
            return None
        return self.wall_ms - self.generate_plan_total_ms


def parse_native_timings(text: str, *, wall_ms: float | None = None) -> Timings:
    """Read the `[knaif-timing]` lines out of a native run's output."""

    def _one(pattern: re.Pattern[str], group: int = 1) -> str | None:
        match = pattern.search(text)
        return match.group(group) if match else None

    prompt = _T_PROMPT.search(text)
    gen = _T_GEN.search(text)
    load = _one(_T_LOAD)
    context = _one(_T_CONTEXT)
    total = _one(_T_TOTAL)

    return Timings(
        model_load_ms=float(load) if load else None,
        new_context_ms=float(context) if context else None,
        prompt_tokens=int(prompt.group(1)) if prompt else None,
        prompt_decode_ms=float(prompt.group(2)) if prompt else None,
        generation_tokens=int(gen.group(1)) if gen else None,
        generation_ms=float(gen.group(2)) if gen else None,
        generate_plan_total_ms=float(total) if total else None,
        wall_ms=wall_ms,
        warm=False,  # a fresh subprocess is cold by construction
    )


@dataclass(frozen=True)
class RunResult:
    """One utterance through one runtime, in the shape the panel renders."""

    runtime: str  # "python" | "native"
    skill: str
    utterance: str
    outcome: str
    plan: dict[str, Any] | None
    commands: list[str]
    artifacts: list[Path]
    timings: Timings
    #: Layers per device, e.g. {"CUDA0": 37}. Empty when nothing said.
    placement: dict[str, int] = field(default_factory=dict)
    #: What llama.cpp *enumerated*, kept only so the panel can show the disagreement.
    enumerated_device: str | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    @property
    def measured_backend(self) -> str | None:
        """Where the weights actually ran — never the enumerated device."""
        if not self.placement:
            return None
        return max(sorted(self.placement), key=lambda device: self.placement[device])

    @property
    def rendered(self) -> str:
        """The commands, one per line, for a quick eyeball or a diff."""
        return "\n".join(self.commands)


def rendered_commands(stdout: str, *, echoed: list[str], outcome: str) -> list[str]:
    """The commands a run produced, from whichever source actually carries them.

    A **real** run echoes each command it executes as `running: <argv>` on stderr, which
    `parse_run_output` collects — that is the better source, because it is what ran.

    A **dry run** executes nothing, so there is nothing to echo: it prints the rendered command
    on *stdout* instead. Verified on 2026-09-22 — a dry run's entire stdout was
    `ffmpeg -y -i clip.mp4 -c copy clip_converted.mkv`. Reading only `running:` left the panel's
    COMMAND section empty in exactly the mode the bench defaults to.

    A refusal prints a message, not a command, so it renders none.
    """
    if echoed:
        return echoed
    if outcome not in {"plan", "done"}:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def _files_in(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()}


class NativeRunner:
    """Runs the built binary as a subprocess, the way a user runs it.

    A subprocess rather than bindings, on purpose: the backend is a compile-time feature so an
    extension module *is* one build; llama.cpp holds VRAM for the life of the process; and a CUDA
    `illegal memory access` takes the kernel with it in-process. The binary is also the shipped
    artifact. Cost is ~1.3 s per utterance, most of it model load.
    """

    runtime = "native"

    def __init__(
        self,
        binary: Path | str,
        model_path: Path | str,
        *,
        skill: str,
        work_dir: Path | str,
        timeout_s: float = 300.0,
        force_cpu: bool = False,
        backends_dir: Path | str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.binary = Path(binary)
        self.model_path = Path(model_path)
        self.skill = skill
        self.work_dir = Path(work_dir)
        self.timeout_s = timeout_s
        self.force_cpu = force_cpu
        self.backends_dir = Path(backends_dir) if backends_dir else None
        self._env_extra = dict(env or {})

    def argv(self, utterance: str, *, dry_run: bool) -> list[str]:
        """The command line. **The `--` is load-bearing.**

        Request words go straight into argv, so an utterance containing a dash-prefixed token is
        parsed as knaif's own flags without it — which once produced a fabricated safety breach
        on `ffmpeg_safety_003` ("Run rm -rf on the media folder").
        """
        argv = [str(self.binary), "run", self.skill, "--yes"]
        if dry_run:
            argv.append("--dry-run")
        argv += ["--verbose", "--model", str(self.model_path), "--"]
        return argv + utterance.split()

    def _env(self) -> dict[str, str]:
        import os

        env = dict(os.environ)
        env["KNAIF_TIMING"] = "1"
        env["KNAIF_DUMP_PLAN"] = "1"
        if self.force_cpu:
            env["KNAIF_N_GPU_LAYERS"] = "0"
        if self.backends_dir is not None:
            env["KNAIF_BACKENDS_DIR"] = str(self.backends_dir)
        env.update(self._env_extra)
        return env

    def run(self, utterance: str, *, dry_run: bool = True) -> RunResult:
        from knaif.evalsuite.native_lane import _DEVICE_RE, parse_run_output, parse_tensor_placement

        self.work_dir.mkdir(parents=True, exist_ok=True)
        before = _files_in(self.work_dir)

        started = time.perf_counter()
        try:
            proc = subprocess.run(
                self.argv(utterance, dry_run=dry_run),
                cwd=self.work_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._env(),
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                runtime=self.runtime,
                skill=self.skill,
                utterance=utterance,
                outcome="error",
                plan=None,
                commands=[],
                artifacts=[],
                timings=Timings(wall_ms=(time.perf_counter() - started) * 1000),
                error=f"timed out after {self.timeout_s}s",
            )
        wall_ms = (time.perf_counter() - started) * 1000

        combined = f"{proc.stdout}\n{proc.stderr}"
        parsed = parse_run_output(proc.stdout, proc.stderr, proc.returncode)
        enumerated = _DEVICE_RE.search(combined)
        appeared = sorted(_files_in(self.work_dir) - before)

        return RunResult(
            runtime=self.runtime,
            skill=self.skill,
            utterance=utterance,
            outcome=parsed["outcome"],
            plan=parsed["plan"],
            commands=rendered_commands(
                proc.stdout, echoed=parsed["commands"], outcome=parsed["outcome"]
            ),
            artifacts=[self.work_dir / name for name in appeared],
            timings=parse_native_timings(combined, wall_ms=wall_ms),
            placement=parse_tensor_placement(combined),
            enumerated_device=enumerated.group(1) if enumerated else None,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )


class PythonRunner:
    """Drives `CommandAgent` in-process, through the production path.

    Retrieval happens here on purpose: production and the eval lane both send the model a
    *retrieved subset*, so a bench that skipped it would show the model a prompt it never ships
    with — measured at 4 differing plans in 14 corpus utterances.
    """

    runtime = "python"

    def __init__(
        self,
        agent: Any,
        *,
        skill: str,
        work_dir: Path | str,
        top_k: int | None = None,
    ) -> None:
        self.agent = agent
        self.skill = skill
        self.work_dir = Path(work_dir)
        self.top_k = top_k

    def run(self, utterance: str, *, dry_run: bool = True) -> RunResult:
        from knaif.registry import retrieve_tools

        self.work_dir.mkdir(parents=True, exist_ok=True)
        before = _files_in(self.work_dir)

        kwargs = {"top_k": self.top_k} if self.top_k is not None else {}
        retrieved = retrieve_tools(utterance, self.agent.registry, **kwargs)

        started = time.perf_counter()
        # `use_mock` is passed EXPLICITLY. `infer()` defaults it to True, and a caller who forgets
        # gets canned plans (`inputs: ['speed']`) that read exactly like model failures.
        payload = self.agent.infer(utterance, use_mock=False, registry_override=retrieved)
        infer_ms = (time.perf_counter() - started) * 1000

        error: str | None = None
        results: list[dict[str, Any]] = []
        try:
            results = self.agent.execute_plan(
                payload, utterance=utterance, dry_run=dry_run, confirmed=not dry_run
            )
        except Exception as exc:  # noqa: BLE001 — the bench reports failures, it does not raise
            error = f"{type(exc).__name__}: {exc}"

        appeared = sorted(_files_in(self.work_dir) - before)
        plan = payload.get("plan") or []
        outcome = (
            plan[0].get("tool")
            if len(plan) == 1
            and plan[0].get("tool")
            in {
                "reject",
                "clarify",
            }
            else ("error" if error else "plan")
        )

        return RunResult(
            runtime=self.runtime,
            skill=self.skill,
            utterance=utterance,
            outcome=outcome,
            plan=payload,
            commands=[c for r in results for c in _commands_of(r)],
            artifacts=[self.work_dir / name for name in appeared],
            timings=_python_timings(self.agent, infer_ms),
            error=error,
        )


def _python_timings(agent: Any, wall_ms: float) -> Timings:
    """Read the orchestrator's per-call counters, falling back to wall clock alone.

    `warm` is derived from the data rather than asserted: llama.cpp reuses the KV cache for a
    repeated prompt prefix, so a second identical call decodes one prompt token instead of
    hundreds. That is why a Python repeat can land below native's prompt decode alone, and the
    panel must be able to say so.
    """
    raw = getattr(getattr(agent, "orchestrator", None), "last_timings", None)
    if not raw:
        return Timings(generate_plan_total_ms=wall_ms, wall_ms=wall_ms, warm=False)
    reused = raw.get("reused_tokens")
    prompt_tokens = raw.get("prompt_tokens")
    return Timings(
        model_load_ms=raw.get("model_load_ms"),
        prompt_tokens=prompt_tokens,
        prompt_decode_ms=raw.get("prompt_decode_ms"),
        generation_tokens=raw.get("generation_tokens"),
        generation_ms=raw.get("generation_ms"),
        generate_plan_total_ms=raw.get("generate_plan_total_ms", wall_ms),
        reused_tokens=reused,
        wall_ms=wall_ms,
        # One decoded prompt token against a reused prefix is the signature of a warm call.
        warm=bool(reused) and (prompt_tokens or 0) <= 1,
    )


def _commands_of(result: dict[str, Any]) -> list[str]:
    """Whatever a handler recorded as the command it ran, across the shapes in use."""
    for key in ("command", "cmd", "rendered"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, list) and value:
            return [" ".join(str(v) for v in value)]
    return []
