"""What a run looks like once it has finished.

The panel's job is to make a judgement possible, which mostly means refusing to flatten things
that are not comparable:

* **There is no single "time" row.** Native starts a process per run; Python keeps the model
  resident and reuses the KV cache. The comparable figure is `generate_plan TOTAL`; wall clock is
  shown separately and labelled cold or warm, with the reused-token count beside it so a fast
  repeat explains itself.
* **The side-by-side points at the first disagreement** rather than printing two plans and
  leaving the reader to diff them.
* **Statistics require more than one sample.** A single run is a number, not a measurement.

See docs/plans/2026-09-21-skill-prompt-workbench.md (D4c, T6).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .runners import RunResult


def _fmt_ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} ms"


def timing_rows(results: Sequence[RunResult]) -> list[list[str]]:
    """The timing table, as rows of ``[label, *per-result cells]``.

    Deliberately emits no row called "time". The one figure both runtimes may be held to is
    `generate_plan TOTAL`; everything else is context for why the wall figures differ.
    """
    rows: list[list[str]] = []

    def add(label: str, cell: Any) -> None:
        rows.append([label, *[cell(r) for r in results]])

    add(
        "model load", lambda r: "(resident)" if r.timings.warm else _fmt_ms(r.timings.model_load_ms)
    )
    add(
        "prompt decode",
        lambda r: (
            "—"
            if r.timings.prompt_tokens is None
            else f"{r.timings.prompt_tokens} tok / {_fmt_ms(r.timings.prompt_decode_ms)}"
        ),
    )
    add(
        "generation",
        lambda r: (
            "—"
            if r.timings.generation_tokens is None
            else f"{r.timings.generation_tokens} tok / {_fmt_ms(r.timings.generation_ms)}"
        ),
    )
    # The comparable row. Named after the native field so the two runtimes read as one table.
    add("generate_plan TOTAL", lambda r: _fmt_ms(r.timings.comparable_ms))
    add(
        "reused from cache",
        lambda r: "—" if r.timings.reused_tokens is None else f"{r.timings.reused_tokens} tok",
    )
    add("process overhead", lambda r: _fmt_ms(r.timings.process_overhead_ms))
    add(
        "wall (NOT comparable)",
        lambda r: f"{_fmt_ms(r.timings.wall_ms)} {'warm' if r.timings.warm else 'cold'}",
    )
    return rows


def first_divergence(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Where two plans stop agreeing — step number, field, and both values.

    Returns `None` only when the plans are identical. A run that produced no plan diverges from
    one that did: that is a real difference between the runtimes, not a reason to crash.
    """
    if left == right:
        return None
    if left is None or right is None:
        return {"step": 0, "field": "plan", "left": left, "right": right}

    steps_l = left.get("plan") or []
    steps_r = right.get("plan") or []
    for index in range(max(len(steps_l), len(steps_r))):
        step_l = steps_l[index] if index < len(steps_l) else None
        step_r = steps_r[index] if index < len(steps_r) else None
        if step_l == step_r:
            continue
        if step_l is None or step_r is None:
            return {"step": index + 1, "field": "step", "left": step_l, "right": step_r}
        if step_l.get("tool") != step_r.get("tool"):
            return {
                "step": index + 1,
                "field": "tool",
                "left": step_l.get("tool"),
                "right": step_r.get("tool"),
            }
        args_l = step_l.get("args") or {}
        args_r = step_r.get("args") or {}
        for key in sorted(set(args_l) | set(args_r)):
            if args_l.get(key) != args_r.get(key):
                return {
                    "step": index + 1,
                    "field": key,
                    "left": args_l.get(key),
                    "right": args_r.get(key),
                }
    return None


def percentiles(samples: Sequence[float]) -> dict[str, float]:
    """n, mean, p50 and p95 over repeated runs of one utterance.

    **Nearest-rank, not interpolated.** Over the handful of samples a bench produces, an
    interpolated p95 is a number no run actually took — for [100, 200, 300, 400] it reports
    385 ms, which never happened. Nearest-rank reports 400 ms, an observed value you can go and
    look at. With small n, p95 collapsing onto the slowest run is the honest answer: ten samples
    cannot resolve a 95th percentile more finely than that.

    Empty in, empty out — reporting zeros for an unmeasured population would be a claim.
    """
    values = sorted(v for v in samples if v is not None)
    if not values:
        return {}

    def at(fraction: float) -> float:
        import math

        rank = max(1, math.ceil(fraction * len(values)))
        return values[rank - 1]

    return {
        "n": float(len(values)),
        "mean": sum(values) / len(values),
        "p50": at(0.5),
        "p95": at(0.95),
    }


def describe_artifact(path: Path) -> str:
    """Size, plus an ffprobe one-liner for media — the point of a real run is the file."""
    if not path.is_file():
        return f"{path.name}  (gone)"
    size_mb = path.stat().st_size / 1_048_576
    line = f"{path.name}  {size_mb:.2f} MB"
    if path.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm", ".avi", ".mp3", ".wav", ".m4a"}:
        return line
    if not shutil.which("ffprobe"):
        return f"{line}  (ffprobe not installed)"
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,width,height,r_frame_rate:format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return line
    fields = dict(piece.split("=", 1) for piece in proc.stdout.splitlines() if "=" in piece)
    # A container with no frames (a trim past the end, say) probes as `duration=N/A` and nothing
    # else. That is a finding about the run, so say it — raising here would hide the plan too.
    if "codec_name" not in fields:
        return f"{line}  (no streams — empty output)"
    bits = [fields["codec_name"]]
    if fields.get("width"):
        bits.append(f"{fields['width']}x{fields.get('height', '?')}")
    try:
        bits.append(f"{float(fields['duration']):.1f}s")
    except (KeyError, ValueError):
        pass
    return f"{line}  {' '.join(bits)}"


def _both_ends(text: str, limit: int) -> str:
    """Keep the head and the tail when truncating, never just one.

    A llama.cpp load trace states the layer placement in its first lines and any failure in its
    last. Keeping only the tail — the obvious choice for a log — hid exactly what `verbose` was
    ticked to see.
    """
    text = text.rstrip()
    if len(text) <= limit:
        return text
    half = limit // 2
    elided = len(text) - (half * 2)
    marker = f"\n\n        … {elided:,} characters elided …\n\n"
    return text[:half] + marker + text[-half:]


def show(result: RunResult, *, verbose: bool = False, limit: int = 200_000) -> str:
    """One run, rendered.

    Compact by default — the plan, the command it renders to, where it ran and how long. That is
    what you come back to after each utterance, and burying it under a load trace is what the
    verbose switch exists to prevent.

    `verbose` appends the full plan as JSON and the raw stdout/stderr. It shows **all** of a
    normal load trace — a llama.cpp load is ~40,000 characters and the layer placement sits in
    the MIDDLE of it, so an earlier 4,000-character cap kept the head and tail and lost the one
    thing worth reading. `limit` survives only as a guard against something pathological, and
    when it does bite it keeps both ends and says how much it dropped.
    """
    lines = [
        f"{result.skill} · {result.utterance!r}",
        "",
        f"PLAN                    outcome: {result.outcome}",
    ]
    for step in (result.plan or {}).get("plan", []) or []:
        args = " ".join(f"{k}={v}" for k, v in (step.get("args") or {}).items())
        lines.append(f"  {step.get('tool'):<18} {args}")
    if result.executions:
        # A real run: what ran and how each one ended. A failure is followed by the tool's own
        # words, because "exit -22" alone sends you back to a terminal to find out why.
        lines += ["", "EXECUTED"]
        for run in result.executions:
            mark = "✓" if run.ok else "✗"
            lines.append(
                f"  {mark} {f'exit {run.returncode}':<9} {run.command or '(command not recorded)'}"
            )
            for reason in (run.reason or "").splitlines():
                lines.append(f"      │ {reason}")
    elif result.commands:
        lines += ["", "COMMAND"] + [f"  {c}" for c in result.commands]
    if result.artifacts:
        lines += ["", "ARTIFACTS"] + [f"  {describe_artifact(p)}" for p in result.artifacts]
    lines += ["", "WHERE IT RAN"]
    if result.placement:
        lines.append(f"  measured            {result.measured_backend}  {result.placement}")
    else:
        # Never guessed. The native runner reads this from the subprocess output; the Python
        # runner needs an fd-2 capture at model load, which is unavailable in some kernels.
        lines.append("  measured            unknown — tick verbose to read it from the load")
    if result.enumerated_device and result.enumerated_device != result.measured_backend:
        lines.append(f"  enumerated          {result.enumerated_device}  (not where it ran)")
    lines += ["", "TIME"]
    for row in timing_rows([result]):
        lines.append(f"  {row[0]:<22}{row[1]}")
    if result.error:
        lines += ["", f"ERROR  {result.error}"]

    if verbose:
        lines += ["", "─" * 72, "", "PLAN (json)"]
        lines.append(json.dumps(result.plan, indent=2)[:limit])
        if result.stdout.strip():
            lines += ["", "STDOUT", _both_ends(result.stdout, limit)]
        if result.stderr.strip():
            lines += ["", "STDERR", _both_ends(result.stderr, limit)]
    return "\n".join(lines)


def compare(left: RunResult, right: RunResult) -> str:
    """Two runtimes on the same utterance, with the first disagreement named."""
    lines = [f"{left.runtime} vs {right.runtime} · {left.utterance!r}", ""]
    divergence = first_divergence(left.plan, right.plan)
    if divergence is None:
        lines.append("plans identical")
    else:
        lines.append(f"first divergence  step {divergence['step']}, {divergence['field']}")
        lines.append(f"  {left.runtime:<8} {divergence['left']!r}")
        lines.append(f"  {right.runtime:<8} {divergence['right']!r}")
    if left.commands != right.commands:
        lines += ["", "commands differ"]
        lines += [f"  {left.runtime:<8} {c}" for c in left.commands]
        lines += [f"  {right.runtime:<8} {c}" for c in right.commands]
    lines += ["", "TIME"]
    header = f"  {'':<22}{left.runtime:<22}{right.runtime}"
    lines.append(header)
    for row in timing_rows([left, right]):
        lines.append(f"  {row[0]:<22}{row[1]:<22}{row[2]}")
    lines += [
        "",
        "wall clock is not comparable across runtimes — read generate_plan TOTAL.",
    ]
    return "\n".join(lines)


def stats(results: Sequence[RunResult]) -> str:
    """Repeated runs of one utterance: n, mean, p50, p95, and whether the plan held."""
    if not results:
        return "no runs"
    numbers = percentiles([r.timings.comparable_ms for r in results if r.timings.comparable_ms])
    plans = {json.dumps(r.plan, sort_keys=True) for r in results}
    lines = [
        f"n={len(results)} · {results[0].utterance!r} · {results[0].runtime}",
        "",
    ]
    if numbers:
        lines.append(
            f"  generate_plan TOTAL   mean {numbers['mean']:.0f} ms   "
            f"p50 {numbers['p50']:.0f} ms   p95 {numbers['p95']:.0f} ms"
        )
    else:
        lines.append("  generate_plan TOTAL   unmeasured")
    lines.append(
        f"  plans {'identical across all runs' if len(plans) == 1 else f'DIFFER ({len(plans)} distinct)'}"
    )
    return "\n".join(lines)
