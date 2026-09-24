#!/usr/bin/env python3
"""How often does an eval answer change when nothing but the inference config changes?

docs/plans/2026-09-23-inference-config-parity.md, T2. Python and native feed the model identical
tokens and decode greedily, yet configure llama.cpp differently (flash attention, batch layout,
KV prefix reuse across rows), and on a borderline token that alone flipped a plan. This measures
how many corpus utterances are that fragile, so a difference between two eval runs can be judged
against the noise instead of read as a result.

Two subcommands:

    # native's plan for every corpus utterance, in eval order (one model load)
    uv run python scripts/flip_rate.py native --skill ffmpeg \\
        --bin target/release-cuda/knaif.exe --model models/knaif-qwen3-4b-v2-q4_k_m.gguf \\
        --out evals/runs/<dir>/ffmpeg_native.jsonl

    # compare any two runs: a saved `evalsuite run` JSON or a native .jsonl from above
    uv run python scripts/flip_rate.py compare A.json B.json [--label A/B] [--list]

**Three levels, because they answer different questions:**

* **decision** — tools and their non-file arguments. Core rewrites only *file* arguments after the
  model answers (chain linking, source threading), and the two runtimes rewrite differently, so
  this is the only level that compares what the model chose across lanes.
* **full** — the whole plan. Meaningful between two Python runs, which rewrite identically.
* **outcome** — graded correct vs not, where both runs were graded. What the decision rule reads.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Arguments that name files. Same set as scripts/parity_check.py — the keys core rewrites.
FILE_ARGS = frozenset(
    {"inputs", "input", "files", "src", "dst", "path", "base", "append", "output", "outputs"}
)
#: Control tools whose arguments are prose: two runs that both ask agree, whatever the wording.
PROSE_TOOLS = frozenset({"clarify", "reject", "done"})

Key = tuple[str, int]


@dataclass(frozen=True)
class Result:
    plan: list[dict[str, Any]]
    #: Graded outcome, or None when the run was not graded (a native plan batch).
    correct: bool | None = None


def _scalar(value: Any) -> str:
    """`720`, `720.0` and `"720"` are one value; anything else keeps its JSON spelling."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)) and float(value).is_integer():
        return str(int(value))
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return value
        return str(int(number)) if number.is_integer() else str(number)
    if isinstance(value, float):
        return repr(value)
    return json.dumps(value, sort_keys=True)


def _step_key(step: dict[str, Any], *, with_files: bool) -> tuple:
    tool = step.get("tool")
    if tool in PROSE_TOOLS:
        return (tool,)
    args = step.get("args") or {}
    kept = sorted((k, _scalar(v)) for k, v in args.items() if with_files or k not in FILE_ARGS)
    return (tool, tuple(kept))


def decision(plan: list[dict[str, Any]]) -> tuple:
    """What the model chose: tools and non-file arguments, order of steps kept."""
    return tuple(_step_key(s, with_files=False) for s in plan)


def full(plan: list[dict[str, Any]]) -> tuple:
    """The whole plan, file arguments included."""
    return tuple(_step_key(s, with_files=True) for s in plan)


def _steps(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("plan")
    return list(payload or [])


def load_run(path: Path) -> dict[Key, Result]:
    """A saved `evalsuite run` JSON (graded) or a native `.jsonl` from `native` (not graded)."""
    path = Path(path)
    if path.suffix == ".jsonl":
        out: dict[Key, Result] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[(rec["id"], int(rec["utterance_idx"]))] = Result(plan=_steps(rec.get("plan")))
        return out
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {
        (row["id"], int(row.get("utterance_idx") or 0)): Result(
            plan=_steps(row.get("plan")), correct=row.get("outcome_correct")
        )
        for row in doc.get("rows") or []
    }


def corpus_utterances(corpus: Path) -> list[tuple[str, int, str]]:
    """(row id, utterance index, text) for every utterance, in the order the eval suite runs."""
    out: list[tuple[str, int, str]] = []
    for line in Path(corpus).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            for idx, utt in enumerate(rec.get("utterances") or []):
                out.append((rec["id"], idx, utt))
    return out


@dataclass
class Report:
    shared: int
    missing: int
    decision_flips: list[Key] = field(default_factory=list)
    full_flips: list[Key] = field(default_factory=list)
    outcome_flips: list[Key] = field(default_factory=list)
    graded: int = 0

    @property
    def decision_rate(self) -> float | None:
        return len(self.decision_flips) / self.shared if self.shared else None

    @property
    def full_rate(self) -> float | None:
        return len(self.full_flips) / self.shared if self.shared else None

    @property
    def outcome_rate(self) -> float | None:
        return len(self.outcome_flips) / self.graded if self.graded else None


def compare(a: dict[Key, Result], b: dict[Key, Result]) -> Report:
    shared = sorted(set(a) & set(b))
    report = Report(shared=len(shared), missing=len(set(a) ^ set(b)))
    for key in shared:
        x, y = a[key], b[key]
        if decision(x.plan) != decision(y.plan):
            report.decision_flips.append(key)
        if full(x.plan) != full(y.plan):
            report.full_flips.append(key)
        if x.correct is not None and y.correct is not None:
            report.graded += 1
            if x.correct != y.correct:
                report.outcome_flips.append(key)
    return report


def _pct(rate: float | None) -> str:
    return "—" if rate is None else f"{rate * 100:.2f}%"


def _print_report(label: str, r: Report, a: dict[Key, Result], b: dict[Key, Result], show: bool):
    print(f"{label}")
    print(f"  shared utterances  {r.shared}   (in only one run: {r.missing})")
    print(f"  decision flips     {len(r.decision_flips):>4}   {_pct(r.decision_rate)}")
    print(f"  full-plan flips    {len(r.full_flips):>4}   {_pct(r.full_rate)}")
    print(
        f"  outcome flips      {len(r.outcome_flips):>4}   {_pct(r.outcome_rate)}  of {r.graded} graded"
    )
    if show:
        for key in r.decision_flips:
            tag = " (outcome flipped)" if key in r.outcome_flips else ""
            print(f"    {key[0]}#{key[1]}{tag}")
            print(f"      A: {[s.get('tool') for s in a[key].plan]}")
            print(f"      B: {[s.get('tool') for s in b[key].plan]}")


def progress_line(rid: str, idx: int, plan: Any, ms: float, utterance: str) -> str:
    """One finished utterance, in the row format `scripts/watch_run_progress.sh` counts.

    Field 3 is the first tool (the watcher tallies it), `empty` for `[]` and `none` when the
    envelope carried no plan.
    """
    steps = _steps(plan) if plan is not None else None
    tool = "none" if steps is None else (steps[0].get("tool") or "none") if steps else "empty"
    return f"  [{rid}#{idx}]  OK    {tool}   {ms:.0f}ms  {utterance}"


def _native(args: argparse.Namespace) -> int:
    corpus = REPO_ROOT / "skills" / args.skill / "data" / "eval.jsonl"
    utts = corpus_utterances(corpus)
    if args.limit:
        utts = utts[: args.limit]
    fd, name = tempfile.mkstemp(suffix=".txt", prefix="flip_utts_")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(u for _, _, u in utts))
    env = {**os.environ, "KNAIF_SKILLS_ROOT": str(REPO_ROOT / "skills")}
    cwd = REPO_ROOT / "sandbox" / "fixtures" / args.skill
    try:
        # Streamed, not captured: a CPU batch runs for an hour and must be watchable, so each
        # envelope becomes one progress line. stderr (the device trace) always goes to a file:
        # an unread PIPE deadlocks the stream once llama.cpp's trace fills it.
        stderr_path = Path(args.stderr_log) if args.stderr_log else Path(name + ".stderr")
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_sink = stderr_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [
                str(Path(args.bin).resolve()),
                "plan",
                "--skill",
                args.skill,
                "--json",
                "--model",
                str(Path(args.model).resolve()),
                "--batch",
                name,
            ],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=stderr_sink,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        envelopes = []
        last = time.monotonic()
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                envelope = json.loads(line)
            except ValueError:
                continue
            now = time.monotonic()
            if len(envelopes) < len(utts):
                rid, idx, utt = utts[len(envelopes)]
                print(
                    progress_line(rid, idx, envelope.get("plan"), (now - last) * 1000, utt),
                    flush=True,
                )
            last = now
            envelopes.append(envelope)
        proc.wait()
        stderr_sink.close()
        stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        if not args.stderr_log:
            stderr_path.unlink()
    finally:
        os.unlink(name)
    if len(envelopes) != len(utts):
        print(
            f"native returned {len(envelopes)} plans for {len(utts)} utterances "
            f"(exit {proc.returncode}); stderr tail:\n{stderr_tail}",
            file=sys.stderr,
        )
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for (rid, idx, _), env_ in zip(utts, envelopes, strict=True):
            fh.write(json.dumps({"id": rid, "utterance_idx": idx, "plan": env_.get("plan")}) + "\n")
    print(f"wrote {len(utts)} native plans to {out}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    a, b = load_run(args.a), load_run(args.b)
    report = compare(a, b)
    _print_report(
        args.label or f"{Path(args.a).name}  vs  {Path(args.b).name}", report, a, b, args.list
    )
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "a": str(args.a),
                    "b": str(args.b),
                    "shared": report.shared,
                    "missing": report.missing,
                    "decision_flips": [f"{k[0]}#{k[1]}" for k in report.decision_flips],
                    "full_flips": [f"{k[0]}#{k[1]}" for k in report.full_flips],
                    "outcome_flips": [f"{k[0]}#{k[1]}" for k in report.outcome_flips],
                    "graded": report.graded,
                    "decision_rate": report.decision_rate,
                    "full_rate": report.full_rate,
                    "outcome_rate": report.outcome_rate,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("native", help="native's plan for every corpus utterance")
    n.add_argument("--skill", required=True)
    n.add_argument("--bin", required=True)
    n.add_argument("--model", required=True)
    n.add_argument("--out", required=True)
    n.add_argument("--limit", type=int, help="first N utterances only (smoke test)")
    n.add_argument("--stderr-log", help="keep the binary's stderr (device trace) here")
    c = sub.add_parser("compare", help="flip counts between two runs")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    c.add_argument("--label")
    c.add_argument("--list", action="store_true", help="list every decision flip")
    c.add_argument("--json", help="also write the report here")
    args = ap.parse_args(argv)
    return _native(args) if args.cmd == "native" else _compare(args)


if __name__ == "__main__":
    sys.exit(main())
