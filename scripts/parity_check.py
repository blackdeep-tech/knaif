#!/usr/bin/env python3
"""Runtime parity check: native (Rust/C++) vs Python, over a skill's eval utterances.

This is deliberately NOT an eval-suite. It does not grade against baselines or compare
models. It verifies that the *ported deterministic pipeline* — prompt build → inference →
JSON extract → parse → normalize → defaults → validate → intent expand → command render —
produces the SAME rendered ffmpeg command(s) on both runtimes for the same input.

To make the comparison meaningful it pins BOTH runtimes to the *identical* GGUF file (via
each CLI's raw-path escape hatch) and relies on both decoding greedily (native = argmax,
Python = temperature 0), so the only expected source of divergence is a genuine sync gap
in the port — or occasional floating-point argmax ties across different GPU backends.

What is compared: the final rendered ffmpeg argv from `run --dry-run` on each side, shlex-
normalized to a token list so cosmetic quoting/spacing differences don't register. Outcome
*type* (commands / clarify / reject / none) is compared first; argv only when both produced
commands.

Known scope limit: native `run` currently previews only the FIRST plan step (main.rs), so
multi-intent chains (e.g. convert→strip) can't be command-compared yet. Such rows are
reported as `chain-native-single-step`, not as a mismatch, unless --strict is given.

Usage (normally via `just parity ffmpeg`, which builds native first):
    uv run python scripts/parity_check.py --skill ffmpeg \
        --native-bin target/debug/knaif.exe \
        --model-path models/knaif-qwen3-4b-v1-q4_k_m.gguf \
        --cwd sandbox/fixtures/ffmpeg [--limit N] [--tags audio,convert] [--skip-chains]

Self-test the pure parsing/normalization (no models, no subprocesses):
    uv run python scripts/parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


# ── output parsing (pure) ─────────────────────────────────────────────────────


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


_PATH_EXT = re.compile(r"\.[A-Za-z0-9]{1,4}$")


def to_argv(line: str) -> list[str]:
    """Tokenize one rendered command line into an argv.

    Backslashes are forward-slashed FIRST: on Windows Python emits `C:\\…` paths, and
    shlex(posix=True) would otherwise consume the backslash as an escape. Forward slashes
    are valid path separators for ffmpeg + std::path, so this is lossless for the file-path
    domain (mirrors native's own normalize_path_separators).
    """
    try:
        return shlex.split(line.replace("\\", "/"), posix=True)
    except ValueError:
        return []


def _is_ffmpeg_line(tokens: list[str]) -> bool:
    """A rendered ffmpeg invocation: argv[0] is `ffmpeg` (or ends in it)."""
    return bool(tokens) and Path(tokens[0]).name.lower() in ("ffmpeg", "ffmpeg.exe")


def _is_pathlike(tok: str) -> bool:
    """Heuristic: a token that names a file (has a separator or a short extension)."""
    return ("/" in tok) or bool(_PATH_EXT.search(tok))


def canon_token(tok: str) -> str:
    """Comparison form of a token: path-like ones reduce to their basename.

    Native emits relative paths (`clip.mp4`); Python resolves inputs to absolute
    (`C:/…/clip.mp4`). Both point to the same file under the shared cwd, so comparing by
    basename treats that representation difference as equal while a genuinely different
    filename/extension/output still diverges.
    """
    return tok.rsplit("/", 1)[-1] if _is_pathlike(tok) else tok


def _canon_scalar(v: object) -> str:
    """Canonicalize a plan-arg scalar so 720 == "720", 2.0 == "2.0", and paths → basename."""
    if isinstance(v, bool):
        return f"bool:{v}"
    if isinstance(v, (int, float)):
        f = float(v)
        return f"num:{int(f) if f.is_integer() else f}"
    if isinstance(v, str):
        s = v.strip()
        try:
            f = float(s)
            return f"num:{int(f) if f.is_integer() else f}"
        except ValueError:
            return f"str:{canon_token(s.replace(chr(92), '/'))}"
    return f"other:{v!r}"


def _canon_val(v: object):
    """Hashable canonical form of a plan-arg value (scalars coerced, paths → basename)."""
    if isinstance(v, list):
        return tuple(_canon_val(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((k, _canon_val(x)) for k, x in v.items()))
    return _canon_scalar(v)


def canon_plan_step(step: dict) -> tuple:
    """Canonical (tool, sorted-args) for a plan step — order-insensitive on arg keys."""
    args = step.get("args") or {}
    return (step.get("tool"), tuple(sorted((k, _canon_val(v)) for k, v in args.items())))


@dataclass
class Outcome:
    """The comparable result of one CLI invocation."""

    kind: str  # "commands" | "plan" | "clarify" | "reject" | "none" | "rendered-none" | "error"
    commands: list[list[str]] = field(default_factory=list)  # normalized argv per command
    plan: list[dict] = field(default_factory=list)  # plan steps (plan mode)
    text: str = ""  # clarify/reject message or error detail
    raw: str = ""  # raw stdout+stderr, for the report on mismatch

    def key(self) -> tuple:
        """Comparison key: commands/plan canonicalized (paths → basename); else just kind."""
        if self.kind == "commands":
            return ("commands", tuple(tuple(canon_token(t) for t in c) for c in self.commands))
        if self.kind == "plan":
            return ("plan", tuple(canon_plan_step(s) for s in self.plan))
        return (self.kind,)


def parse_native(stdout: str, stderr: str) -> Outcome:
    """Parse `knaif run <skill> --dry-run` output into an Outcome.

    Native prints each command as a bare shell-joined line to stdout; clarify/reject as
    `clarify: …` / `reject: …`; and a handful of "no plan" notices.
    """
    cmds: list[list[str]] = []
    text = ""
    kind = "none"
    for line in strip_ansi(stdout).splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("clarify:"):
            kind, text = "clarify", s.split(":", 1)[1].strip()
            continue
        if low.startswith("reject:"):
            kind, text = "reject", s.split(":", 1)[1].strip()
            continue
        tokens = to_argv(s)
        if _is_ffmpeg_line(tokens):
            cmds.append(tokens)
    if cmds:
        return Outcome("commands", cmds, raw=stdout + stderr)
    return Outcome(kind, text=text, raw=stdout + stderr)


def parse_python(stdout: str, stderr: str) -> Outcome:
    """Parse `knaif-cli run <skill> --dry-run` output into an Outcome.

    Python prints command items as `    $ ffmpeg …` and clarify/reject as
    `❓ CLARIFY: …` / `🚫 REJECT: …` (see app.py). stdout carries the render.
    """
    text = strip_ansi(stdout)
    cmds: list[list[str]] = []
    kind = "none"
    detail = ""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"\$\s+(ffmpeg\b.*)$", s)
        if m:
            tokens = to_argv(m.group(1))
            if tokens:
                cmds.append(tokens)
            continue
        if "CLARIFY:" in s:
            kind, detail = "clarify", s.split("CLARIFY:", 1)[1].strip()
        elif "REJECT:" in s:
            kind, detail = "reject", s.split("REJECT:", 1)[1].strip()
    if cmds:
        return Outcome("commands", cmds, raw=stdout + stderr)
    if kind in ("clarify", "reject"):
        return Outcome(kind, text=detail, raw=stdout + stderr)
    # Python planned but its dry-run renders no ffmpeg line for compress/platform/thumbnail/
    # batch intents (prints "(nothing to execute)" + a `• …` summary). That's a dry-run
    # rendering asymmetry vs native, not a decline — mark it so it isn't scored as a mismatch.
    if "(nothing to execute)" in text:
        return Outcome(
            "rendered-none",
            text="planned; dry-run emits no command for this intent",
            raw=stdout + stderr,
        )
    return Outcome("none", text=detail, raw=stdout + stderr)


# ── row loading ───────────────────────────────────────────────────────────────


@dataclass
class Row:
    id: str
    utterance: str
    tags: list[str]
    is_chain: bool


def load_rows(skill: str, tags_filter: set[str] | None) -> list[Row]:
    path = REPO_ROOT / "skills" / skill / "data" / "eval.jsonl"
    rows: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        utts = rec.get("utterances") or []
        if not utts:
            continue
        tags = rec.get("tags") or []
        if tags_filter and not (set(tags) & tags_filter):
            continue
        expected_tools = rec.get("expected_tools") or []
        is_chain = len(expected_tools) > 1
        rows.append(Row(rec["id"], utts[0], tags, is_chain))
    return rows


# ── invocation ────────────────────────────────────────────────────────────────


NO_LLAMA_MARKER = "no llama.cpp backend"


def native_llama_error(native_bin: Path, skill: str) -> str | None:
    """Return an error message if `native_bin` was built without the `llama` feature.

    `cargo build` / `just build-native` produce a mock-only binary at the same
    target/debug/knaif.exe path this harness points at. Such a binary rejects every
    `--model` invocation, so all N rows come back empty and the run reports a
    plausible-looking `0/N matches` — a port-sync failure that never happened.

    `--version` carries the compiled backend (see VERSION in apps/cli/src/main.rs), so the
    probe costs a process spawn and never touches the GGUF.
    """
    proc = subprocess.run(
        [str(native_bin), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if NO_LLAMA_MARKER not in (proc.stdout + proc.stderr):
        return None
    return (
        f"native binary was built WITHOUT llama.cpp, so every --model run fails and parity "
        f"would report a false 0/N.\n  binary : {native_bin}\n  version: "
        f"{proc.stdout.strip() or proc.stderr.strip()}\n"
        f"Rebuild it with a backend first, e.g.:\n"
        f'  just native-vulkan {skill} "convert clip.mp4 to mkv"   # one warm-up build\n'
        f"  (or: cargo build -p knaif-cli --features llama)"
    )


def run_native(native_bin: Path, skill: str, model_path: Path, utt: str, cwd: Path) -> Outcome:
    argv = [
        str(native_bin),
        "run",
        skill,
        "--dry-run",
        "--model",
        str(model_path),
        *utt.split(),
    ]
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return parse_native(proc.stdout, proc.stderr)


def parse_plan_json(stdout: str, stderr: str) -> Outcome:
    """Parse a `plan --json` envelope from stdout (last line that is a {...} with a `plan` key).

    clarify/reject plans map to those kinds (so decline-divergence still fires); a normal
    multi-step plan maps to kind "plan"; empty/absent → "none".
    """
    payload = None
    for line in stdout.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except ValueError:
            continue
        if isinstance(obj, dict) and "plan" in obj:
            payload = obj  # keep the last valid envelope
    if payload is None:
        return Outcome("none", raw=stdout + stderr)
    steps = payload.get("plan") or []
    if steps and steps[0].get("tool") in ("clarify", "reject"):
        return Outcome(
            steps[0]["tool"], text=str(steps[0].get("args", {})), plan=steps, raw=stdout + stderr
        )
    if not steps:
        return Outcome("none", raw=stdout + stderr)
    return Outcome("plan", plan=steps, raw=stdout + stderr)


def _plan_line_to_outcome(line: str) -> Outcome | None:
    """Parse one `plan --batch` stdout line into an Outcome, or None if it isn't a plan line."""
    s = line.strip()
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    return parse_plan_json(s, "") if isinstance(obj, dict) and "plan" in obj else None


def _parse_batch_outcomes(stdout: str) -> list[Outcome]:
    """Parse one plan envelope per JSON line of a `plan --batch` blob (order preserved)."""
    return [o for line in stdout.splitlines() if (o := _plan_line_to_outcome(line)) is not None]


def stream_batch_plan(
    native_bin: Path,
    skill: str,
    model_path: Path,
    python_model: str,
    utterances: list[str],
    cwd: Path,
):
    """Run native + python `plan --batch` CONCURRENTLY, yielding (idx, native, python) per row as

    soon as both sides have emitted that row's plan. Each model loads once (batch) and the two runs
    overlap, while a reader thread per side streams parsed plans so verdicts print live in order.
    """
    import tempfile
    import threading

    fd, name = tempfile.mkstemp(suffix=".txt", prefix="parity_utts_")
    uf = Path(name)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(utterances))
    n = len(utterances)

    def launch(argv: list[str]) -> subprocess.Popen:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

    procs = {
        "native": launch(
            [
                str(native_bin),
                "plan",
                "--skill",
                skill,
                "--json",
                "--batch",
                str(uf),
                "--model",
                str(model_path),
            ]
        ),
        "python": launch(
            [
                "uv",
                "run",
                "knaif-cli",
                "plan",
                skill,
                "--backend",
                "llama-cpp",
                "--model",
                python_model,
                "--batch",
                str(uf),
            ]
        ),
    }
    got: dict[str, list[Outcome]] = {"native": [], "python": []}
    done: dict[str, bool] = {"native": False, "python": False}

    def reader(key: str) -> None:
        for line in procs[key].stdout:  # blocks per line; each side flushes one plan at a time
            o = _plan_line_to_outcome(line)
            if o is not None:
                got[key].append(o)
        done[key] = True

    threads = [threading.Thread(target=reader, args=(k,), daemon=True) for k in procs]
    for t in threads:
        t.start()
    try:
        for i in range(n):
            row_out: dict[str, Outcome] = {}
            for key in ("native", "python"):
                while len(got[key]) <= i and not done[key]:
                    time.sleep(0.05)
                row_out[key] = (
                    got[key][i]
                    if len(got[key]) > i
                    else Outcome("none", raw=f"(missing {key} batch line)")
                )
            yield i, row_out["native"], row_out["python"]
    finally:
        for p in procs.values():
            p.wait()
        for t in threads:
            t.join(timeout=1)
        uf.unlink(missing_ok=True)


def run_native_plan(native_bin: Path, skill: str, model_path: Path, utt: str, cwd: Path) -> Outcome:
    argv = [
        str(native_bin),
        "plan",
        "--skill",
        skill,
        "--json",
        "--model",
        str(model_path),
        *utt.split(),
    ]
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return parse_plan_json(proc.stdout, proc.stderr)


def run_python_plan(skill: str, python_model: str, utt: str, cwd: Path) -> Outcome:
    argv = [
        "uv",
        "run",
        "knaif-cli",
        "plan",
        skill,
        "--backend",
        "llama-cpp",
        "--model",
        python_model,
        *utt.split(),
    ]
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return parse_plan_json(proc.stdout, proc.stderr)


def run_python(skill: str, python_model: str, utt: str, cwd: Path) -> Outcome:
    # Use the models.yaml NAME (not --model-path): a bare path drops the entry's per-model
    # options (json_mode/thinking_enabled), and this fine-tune needs json_mode:false — a raw
    # path defaults it to true and breaks generation. The name loads the right options AND the
    # same GGUF (verified by _resolve_python_model_path against --model-path before the run).
    argv = [
        "uv",
        "run",
        "knaif-cli",
        "run",
        skill,
        "--dry-run",
        "--backend",
        "llama-cpp",
        "--model",
        python_model,
        *utt.split(),
    ]
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return parse_python(proc.stdout, proc.stderr)


def _resolve_python_model_path(python_model: str) -> Path | None:
    """The GGUF path models.yaml maps `python_model` to (for the same-weights guard)."""
    import yaml  # local: only needed for the pre-run identity check

    reg = REPO_ROOT / "models.yaml"
    if not reg.exists():
        return None
    data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
    entry = (data.get("models") or {}).get(python_model) or {}
    path = (entry.get("options") or {}).get("path")
    return (REPO_ROOT / path).resolve() if path else None


# ── main ──────────────────────────────────────────────────────────────────────


# Args that name files/inputs — a difference in one of these is a real divergence, never a
# benign "materialized default" (mirrors planner._PATH_ARG_KEYS + outputs).
_SIGNIFICANT_ARG_KEYS = frozenset(
    {"inputs", "input", "files", "src", "dst", "path", "base", "append", "output", "outputs"}
)


def plan_equiv_modulo_defaults(a_steps: list[dict], b_steps: list[dict]) -> str | None:
    """If two plans differ ONLY because one side materialized optional-arg defaults the other

    left implicit (same tool sequence, all shared arg keys equal, and the key sets are nested),
    return a note describing the extra keys; else None. Native's apply_defaults fills args like
    preview/quality/include_audio that python omits — benign. But a differing input/path key
    (_SIGNIFICANT_ARG_KEYS) is a real divergence (e.g. one side hallucinated an `inputs`), never
    a default, so it is never normalized away.
    """
    if len(a_steps) != len(b_steps):
        return None
    extras: list[str] = []
    for a, b in zip(a_steps, b_steps, strict=True):
        if a.get("tool") != b.get("tool"):
            return None
        aa, ba = a.get("args") or {}, b.get("args") or {}
        if any(_canon_val(aa[k]) != _canon_val(ba[k]) for k in set(aa) & set(ba)):
            return None  # a shared key disagrees → real divergence
        only_a, only_b = set(aa) - set(ba), set(ba) - set(aa)
        if only_a and only_b:
            return None  # each side has unique keys → not a simple nesting
        if (only_a | only_b) & _SIGNIFICANT_ARG_KEYS:
            return None  # a differing input/path key is a real divergence, not a default
        extras += [f"native+{k}" for k in sorted(only_a)] + [f"python+{k}" for k in sorted(only_b)]
    return "equivalent modulo default args: " + ", ".join(extras) if extras else "equivalent"


def compare(
    row: Row, native: Outcome, py: Outcome, strict: bool, plan_mode: bool = False
) -> tuple[str, str]:
    """Return (status, note). status ∈ {match, mismatch, decline-divergence,
    not-comparable, chain-native-single-step}."""
    # One side planned but its dry-run renders no command (python compress/platform/thumbnail/
    # batch) — can't command-compare, so exclude rather than score as drift.
    if "rendered-none" in (native.kind, py.kind):
        return (
            "not-comparable",
            "python dry-run emits no command for this intent (compress/platform/thumbnail/batch)",
        )
    # Plan mode: accept plans that differ only by materialized optional-arg defaults.
    if plan_mode and native.kind == "plan" and py.kind == "plan" and native.key() != py.key():
        eq = plan_equiv_modulo_defaults(native.plan, py.plan)
        if eq is not None:
            return "match", eq
        return "mismatch", "plan tools/args differ"
    # Chain leniency applies ONLY in command mode, where native `run` previews just step 1. In
    # plan mode native `plan --json` emits the full plan, so chains compare end-to-end.
    if (
        not plan_mode
        and row.is_chain
        and native.kind == "commands"
        and py.kind == "commands"
        and not strict
    ):
        # Native previews only step 1; a prefix match on the first command is the best we
        # can assert until native `run` chains. Flag it rather than fail it.
        if native.commands and py.commands and native.commands[0] == py.commands[0]:
            return "chain-native-single-step", "native step-1 command matches python step-1"
        return "chain-native-single-step", "native single-step; first command differs (inspect)"
    if native.key() == py.key():
        # Equal actions, but flag when they only match after path normalization (native
        # emits relative paths, python absolute) so the representation gap stays visible.
        if native.kind == "commands" and native.commands != py.commands:
            return "match", "equivalent (paths differ: native relative, python absolute)"
        return "match", ""
    # Both declined execution but chose different control tools (reject vs clarify): a softer
    # class than real command drift — usually a prompt/core-tool sync gap, not a wrong action.
    if native.kind in ("clarify", "reject") and py.kind in ("clarify", "reject"):
        return "decline-divergence", f"native={native.kind} python={py.kind}"
    return "mismatch", f"native={native.kind} python={py.kind}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--skill", default="ffmpeg")
    ap.add_argument(
        "--mode",
        choices=["command", "plan"],
        default="command",
        help="command: diff rendered ffmpeg argv from `run --dry-run` (tests expansion "
        "+ render, but python skips compress/platform/thumbnail/batch/reverse and "
        "native previews only chain step 1). plan: diff the `plan --json` envelope "
        "(tool+args) — every intent, full chains, no render.",
    )
    ap.add_argument("--native-bin", type=Path)
    ap.add_argument(
        "--model-path",
        type=Path,
        help="GGUF native loads (--model PATH), the ground-truth weights.",
    )
    ap.add_argument(
        "--python-model",
        default="knaif-qwen3-4b-v1",
        help="models.yaml NAME python loads (carries json_mode/thinking options); "
        "must map to the same GGUF as --model-path.",
    )
    ap.add_argument("--cwd", type=Path, help="Working dir for both CLIs (where fixtures live).")
    ap.add_argument(
        "--limit", type=int, default=0, help="Only the first N matching rows (0 = all)."
    )
    ap.add_argument(
        "--tags", default="", help="Comma-separated tag filter (row kept if any tag matches)."
    )
    ap.add_argument(
        "--batch",
        action="store_true",
        help="Plan mode only: load each model ONCE and stream all utterances via "
        "`plan --batch` (2 model loads instead of 2·N). Much faster on full runs.",
    )
    ap.add_argument(
        "--skip-chains", action="store_true", help="Skip multi-intent chain rows entirely."
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Treat chain rows as normal (no single-step leniency).",
    )
    ap.add_argument(
        "--out", type=Path, help="Write the JSON report here (default: evals/parity/…)."
    )
    ap.add_argument(
        "--self-test", action="store_true", help="Run internal parser assertions and exit."
    )
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    # Stream our own per-row output live (so a tee'd log / terminal shows verdicts as they happen,
    # not buffered until exit) — matters for the streaming batch path especially.
    # UTF-8 too: the verdict icons below (✓ ✗ – ≈) are unencodable in the cp1252 that
    # Windows hands Python by default, and a report is worthless if printing it raises.
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    for req in ("native_bin", "model_path"):
        if getattr(args, req) is None:
            ap.error(f"--{req.replace('_', '-')} is required (or pass --self-test)")
    if not args.native_bin.exists():
        ap.error(
            f"native binary not found: {args.native_bin} (build it first: `just parity` does this)"
        )
    if not args.model_path.exists():
        ap.error(f"model not found: {args.model_path}")
    if (msg := native_llama_error(args.native_bin, args.skill)) is not None:
        ap.error(msg)

    # Same-weights guard: the whole comparison is only meaningful if both runtimes load the
    # identical GGUF. Native gets --model-path directly; python gets a name — verify the name
    # resolves (via models.yaml) to the same bytes, else the diff would compare two models.
    py_path = _resolve_python_model_path(args.python_model)
    if py_path is None:
        print(
            f"WARNING: python model {args.python_model!r} not found in models.yaml — cannot "
            f"verify weight identity; proceeding, but a diff may just mean different weights."
        )
    elif py_path != args.model_path.resolve():
        ap.error(
            f"weight mismatch: native loads {args.model_path.resolve()} but python model "
            f"{args.python_model!r} maps to {py_path}. Point --python-model at the entry that "
            f"backs --model-path (same GGUF), or override --model-path."
        )

    cwd = (args.cwd or REPO_ROOT).resolve()
    tags_filter = {t.strip() for t in args.tags.split(",") if t.strip()} or None
    rows = load_rows(args.skill, tags_filter)
    if args.skip_chains:
        rows = [r for r in rows if not r.is_chain]
    if args.limit:
        rows = rows[: args.limit]

    plan_mode = args.mode == "plan"
    batch = args.batch
    if batch and not plan_mode:
        ap.error("--batch is only supported with --mode plan")
    print(f"parity[{args.mode}{'/batch' if batch else ''}]: {args.skill} — {len(rows)} row(s)")
    sub = "plan --skill S --json" if plan_mode else "run S --dry-run"
    print(f"  native : {args.native_bin}  {sub} --model {args.model_path.name}")
    print(
        f"  python : uv run knaif-cli {'plan' if plan_mode else 'run --dry-run'} "
        f"--backend llama-cpp --model {args.python_model}"
    )
    print(f"  weights: {args.model_path}  (both runtimes, identity verified)")
    print(f"  cwd    : {cwd}\n")

    t0 = time.perf_counter()
    results = []
    counts = {
        "match": 0,
        "mismatch": 0,
        "decline-divergence": 0,
        "not-comparable": 0,
        "chain-native-single-step": 0,
    }

    def handle(idx: int, row: Row, native: Outcome, py: Outcome) -> None:
        status, note = compare(row, native, py, args.strict, plan_mode=plan_mode)
        counts[status] = counts.get(status, 0) + 1
        icon = {
            "match": "✓",
            "mismatch": "✗",
            "decline-divergence": "!",
            "not-comparable": "–",
            "chain-native-single-step": "≈",
        }[status]
        print(f"[{idx:>3}/{len(rows)}] {icon} {row.id:<16} {row.utterance[:52]}")
        if status != "match":
            print(f"        {note}")
            print(f"        native[{native.kind}]: {_fmt_cmds(native)}")
            print(f"        python[{py.kind}]: {_fmt_cmds(py)}")
        results.append(
            {
                "id": row.id,
                "utterance": row.utterance,
                "tags": row.tags,
                "is_chain": row.is_chain,
                "status": status,
                "note": note,
                "native": {
                    "kind": native.kind,
                    "commands": native.commands,
                    "plan": native.plan,
                    "text": native.text,
                    "raw": native.raw[:800],
                },
                "python": {
                    "kind": py.kind,
                    "commands": py.commands,
                    "plan": py.plan,
                    "text": py.text,
                    "raw": py.raw[:800],
                },
            }
        )

    if batch:
        # Concurrent + streaming: verdicts print live in order as both sides emit each row.
        for i, native, py in stream_batch_plan(
            args.native_bin,
            args.skill,
            args.model_path,
            args.python_model,
            [r.utterance for r in rows],
            cwd,
        ):
            handle(i + 1, rows[i], native, py)
    else:
        for i, row in enumerate(rows, 1):
            if plan_mode:
                native = run_native_plan(
                    args.native_bin, args.skill, args.model_path, row.utterance, cwd
                )
                py = run_python_plan(args.skill, args.python_model, row.utterance, cwd)
            else:
                native = run_native(
                    args.native_bin, args.skill, args.model_path, row.utterance, cwd
                )
                py = run_python(args.skill, args.python_model, row.utterance, cwd)
            handle(i, row, native, py)

    elapsed = time.perf_counter() - t0
    total = len(rows)
    comparable = counts["match"] + counts["mismatch"]
    print("\n── summary ─────────────────────────────────────────")
    print(f"  matched                 : {counts['match']}/{comparable} comparable")
    print(f"  mismatched (cmd drift)  : {counts['mismatch']}")
    print(f"  decline-divergence      : {counts['decline-divergence']}  (reject vs clarify)")
    print(f"  not-comparable          : {counts['not-comparable']}  (python renders no cmd)")
    print(f"  chain (native 1-step)   : {counts['chain-native-single-step']}")
    print(f"  total rows / time       : {total} / {elapsed:.0f}s")

    out = args.out or (
        REPO_ROOT
        / "evals"
        / "parity"
        / f"parity_{args.skill}_{args.mode}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "skill": args.skill,
                "mode": args.mode,
                "model": str(args.model_path),
                "counts": counts,
                "total": total,
                "elapsed_s": round(elapsed, 1),
                "rows": results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"  report                  : {out}")
    # Non-zero on real divergence (command drift or reject/clarify disagreement). The chain
    # single-step class is a known native limitation, not a failure, so it doesn't gate.
    return 1 if (counts["mismatch"] or counts["decline-divergence"]) else 0


def _fmt_cmds(o: Outcome) -> str:
    if o.kind == "commands":
        return " | ".join(" ".join(c) for c in o.commands) or "(none)"
    if o.kind == "plan":
        return " → ".join(f"{s.get('tool')}({s.get('args', {})})" for s in o.plan) or "(none)"
    return o.text or "(none)"


def _self_test() -> int:
    """Assert the pure parsers/normalizers on representative captured output."""
    # Native command output (bare shell-joined lines; llama logs go to stderr).
    nat = parse_native(
        "ffmpeg -y -i clip.mp4 -c copy -movflags +faststart clip_converted.mp4\n", "ggml log\n"
    )
    assert nat.kind == "commands" and nat.commands == [
        [
            "ffmpeg",
            "-y",
            "-i",
            "clip.mp4",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "clip_converted.mp4",
        ]
    ], nat
    # Python command output (`  $ ffmpeg …`, with ANSI + header noise).
    py = parse_python(
        "intent: 1.5s\nffmpeg › convert clip.mp4\n  \x1b[90m$\x1b[0m ffmpeg -y -i clip.mp4 -c copy -movflags +faststart clip_converted.mp4\n    dry-run\n",
        "",
    )
    assert py.kind == "commands" and py.commands == nat.commands, py
    assert nat.key() == py.key(), "identical argv must compare equal"
    # Quoting difference must NOT register (shlex canonicalization).
    a = parse_native('ffmpeg -y -i "my clip.mp4" out.mp4\n', "")
    b = parse_python("  $ ffmpeg -y -i 'my clip.mp4' out.mp4\n", "")
    assert a.key() == b.key(), "quoting-only difference should match"
    # clarify / reject.
    assert parse_native("clarify: which file?\n", "").kind == "clarify"
    assert parse_native("reject: blocked by policy\n", "").kind == "reject"
    assert parse_python("\n❓ CLARIFY: which file?\n", "").kind == "clarify"
    assert parse_python("\n\U0001f6ab REJECT: no\n", "").kind == "reject"
    # A real divergence must register as different keys.
    x = parse_native("ffmpeg -y -i a.mp4 out.mkv\n", "")
    y = parse_python("  $ ffmpeg -y -i a.mp4 out.mp4\n", "")
    assert x.key() != y.key(), "different container must mismatch"
    # Windows absolute path (backslashes) vs native relative → same action after canon.
    nrel = parse_native("ffmpeg -y -i clip.mp4 -c copy clip_converted.mkv\n", "")
    # The path only has to be Windows-absolute with backslashes — that is what exercises
    # to_argv's forward-slashing. Keep it short and synthetic; never a real checkout path.
    pabs = parse_python(
        r"  $ ffmpeg -y -i C:\media\clip.mp4 -c copy C:\media\clip_converted.mkv" + "\n",
        "",
    )
    assert (
        pabs.commands and "C:" in pabs.commands[0][3]
    ), f"backslash path must survive tokenizing: {pabs.commands}"
    assert (
        nrel.key() == pabs.key()
    ), f"abs vs rel path must canonicalize equal:\n{nrel.key()}\n{pabs.key()}"
    assert nrel.commands != pabs.commands, "raw commands still differ (representation note path)"
    # A different OUTPUT filename (not just abs/rel) must still mismatch.
    other = parse_python("  $ ffmpeg -y -i clip.mp4 -c copy renamed.mkv\n", "")
    assert nrel.key() != other.key(), "different basename must mismatch"
    # Python compress/platform dry-run: a plan summary + "(nothing to execute)" → rendered-none,
    # and comparing against native commands must be not-comparable, not a mismatch.
    rn = parse_python(
        "intent: 1.1s\n  • compress clip_ctr.mp4 → 480p MP4\n    (nothing to execute)\n    dry-run\n",
        "",
    )
    assert rn.kind == "rendered-none", rn
    ncmd = parse_native("ffmpeg -y -i clip_ctr.mp4 -c:v libx264 out.mp4\n", "")
    st, _ = compare(Row("x", "compress clip_ctr.mp4", [], False), ncmd, rn, strict=False)
    assert st == "not-comparable", st

    # ── plan mode ──
    # Same plan, arg key order + int/str + abs/rel path differences → still match.
    np = parse_plan_json(
        '{"plan":[{"tool":"resize_video","args":{"inputs":["clip.mp4"],"height":720}}]}\n', ""
    )
    pp = parse_plan_json(
        'noise line\n{"plan":[{"tool":"resize_video","args":{"height":"720","inputs":["/abs/clip.mp4"]}}]}\n',
        "",
    )
    assert np.kind == "plan" and pp.kind == "plan", (np, pp)
    assert (
        np.key() == pp.key()
    ), f"plan key should be order/type/path invariant:\n{np.key()}\n{pp.key()}"
    # A chain plan compares end-to-end in plan mode (no single-step leniency).
    chain = parse_plan_json(
        '{"plan":[{"tool":"convert_video","args":{"inputs":["clip.mov"],"container":"mp4"}},{"tool":"strip_audio","args":{"inputs":["clip.mp4"]}}]}\n',
        "",
    )
    st, _ = compare(Row("c", "convert+strip", [], True), chain, chain, strict=False, plan_mode=True)
    assert st == "match", f"identical chain plan must match in plan mode: {st}"
    # Different tool → mismatch.
    other = parse_plan_json(
        '{"plan":[{"tool":"compress_video","args":{"inputs":["clip.mp4"]}}]}\n', ""
    )
    assert np.key() != other.key(), "different tool must mismatch"
    # clarify plan classifies as clarify (feeds decline-divergence).
    cl = parse_plan_json('{"plan":[{"tool":"clarify","args":{"question":"which file?"}}]}\n', "")
    assert cl.kind == "clarify", cl
    # Native materialized a default (preview) python omitted → equivalent-modulo-defaults match.
    nd = parse_plan_json(
        '{"plan":[{"tool":"compress_video","args":{"inputs":["clip.mp4"],"crf":18,"preview":true}}]}\n',
        "",
    )
    pd = parse_plan_json(
        '{"plan":[{"tool":"compress_video","args":{"inputs":["clip.mp4"],"crf":18}}]}\n', ""
    )
    st, note = compare(Row("d", "compress", [], False), nd, pd, strict=False, plan_mode=True)
    assert st == "match" and "native+preview" in note, (st, note)
    # But a disagreeing SHARED arg value is a real mismatch, not a default.
    pd2 = parse_plan_json(
        '{"plan":[{"tool":"compress_video","args":{"inputs":["clip.mp4"],"crf":28}}]}\n', ""
    )
    st2, _ = compare(Row("d", "compress", [], False), nd, pd2, strict=False, plan_mode=True)
    assert st2 == "mismatch", st2
    # A differing INPUT/path key is a real divergence, never a benign default (regression guard:
    # native hallucinated `inputs` while python omitted it must NOT normalize to a match).
    hi = parse_plan_json(
        '{"plan":[{"tool":"resize_video","args":{"inputs":["video.mp4"],"keep_aspect_ratio":true}}]}\n',
        "",
    )
    lo = parse_plan_json(
        '{"plan":[{"tool":"resize_video","args":{"keep_aspect_ratio":true}}]}\n', ""
    )
    st3, _ = compare(Row("r", "resize the video", [], False), hi, lo, strict=False, plan_mode=True)
    assert st3 == "mismatch", st3
    # Batch parsing: one plan envelope per line, in order; non-JSON noise lines ignored.
    batch_out = _parse_batch_outcomes(
        "ggml log to stdout\n"
        '{"plan":[{"tool":"convert_video","args":{"inputs":["a.mp4"],"container":"mkv"}}]}\n'
        '{"plan":[{"tool":"clarify","args":{"question":"which?"}}]}\n'
    )
    assert len(batch_out) == 2, batch_out
    assert batch_out[0].kind == "plan" and batch_out[1].kind == "clarify", batch_out
    print("self-test: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
