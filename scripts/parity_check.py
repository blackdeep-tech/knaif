#!/usr/bin/env python3
"""Runtime parity check: native (Rust/C++) vs Python, over a skill's eval utterances.

This is deliberately NOT an eval-suite. It does not grade against baselines or compare
models. It verifies that the *ported deterministic pipeline* — prompt build → inference →
JSON extract → parse → normalize → defaults → validate → intent expand → command render —
produces the SAME rendered ffmpeg command(s) on both runtimes for the same input.

To make the comparison meaningful it pins BOTH runtimes to the *identical* GGUF, but they
are selected differently and that difference is deliberate: native takes the raw path
(`--model <path>`, the ground-truth weights), while Python takes a **models.yaml entry
name** (`--python-model`), because a bare path would drop that entry's per-model options
(`json_mode`, `thinking_enabled`, `n_ctx`, `max_tokens`) and silently compare two different
configurations of the same weights. A pre-run identity guard resolves the name through
models.yaml and errors if it does not point at the same GGUF as `--model-path` (warns, and
proceeds, only when the name is absent from models.yaml). Both decode greedily (native =
argmax, Python = temperature 0), so the only expected source of divergence is a genuine
sync gap in the port — or occasional floating-point argmax ties across different GPU
backends.

What is compared: the final rendered ffmpeg argv from `run --dry-run` on each side, shlex-
normalized to a token list so cosmetic quoting/spacing differences don't register. Outcome
*type* (commands / clarify / reject / none) is compared first; argv only when both produced
commands.

Chain rows compare end to end (since 2026-09-10, Workstream E). Native `run` used to execute
exactly one intent per invocation: first it silently previewed step 1 and dropped the rest
(docs/audits/2026-09-07-core-principles-and-rtx5080.md, F5), then it refused multi-step plans
outright with `not_implemented: this request needs N steps, ...`. It now runs them in order, so
the `chain-native-single-step` bucket — which could only prefix-match the first command — is
gone rather than left to pass every chain row on step 1 alone.

`not_implemented:` is still parsed and still counted apart from `reject:`, as
`native-not-implemented` rather than `mismatch`: a capability the port has not built and a
request the runtime deliberately declined are opposite facts about the product — a coverage gap
versus the safety model working. Both gate. The marker now fires for an unimplemented skill
tool rather than for chains.

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

# The marker native prints for a capability it has not built, as distinct from a `reject:`
# — a request it understood and declined. Kept in sync with `NOT_IMPLEMENTED_PREFIX` in
# `apps/cli/src/main.rs` and `knaif.evalsuite.outcomes`; a test asserts all three agree.
NOT_IMPLEMENTED_PREFIX = "not_implemented:"

#: The line both CLIs prefix their post-gate plan with under `$KNAIF_DUMP_PLAN` (stderr). The same
#: string in apps/cli/src/main.rs, knaif/app.py and evalsuite/native_lane.py; a test holds them
#: together. It is what lets a command-mode row tell "different plans" from "same plan, different
#: commands" — the second is a port bug, the first is not (release plan R0, L3's bar).
PLAN_DUMP_MARKER = "===KNAIF-PLAN==="


def _dumped_plan(stderr: str) -> list[dict]:
    """The plan steps a CLI dumped under `$KNAIF_DUMP_PLAN`, or [] when it dumped none."""
    for line in strip_ansi(stderr).splitlines():
        s = line.strip()
        if s.startswith(PLAN_DUMP_MARKER):
            try:
                payload = json.loads(s[len(PLAN_DUMP_MARKER) :])
            except json.JSONDecodeError:
                return []
            steps = payload.get("plan") if isinstance(payload, dict) else None
            return steps if isinstance(steps, list) else []
    return []


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
    """Comparison form of a *value already known to be a path*: reduce to its basename.

    Native emits relative paths (`clip.mp4`); Python resolves inputs to absolute
    (`C:/…/clip.mp4`). Both point to the same file under the shared cwd, so comparing by
    basename treats that representation difference as equal while a genuinely different
    filename/extension/output still diverges.

    Used for plan-mode arg values (``_canon_scalar``, always typed as a path/string arg)
    and as the ``cwd=None`` fallback for raw argv positions below. NOT used to decide
    whether an arbitrary argv *token* is a path in the first place — ``_canon_argv``
    does that positionally; see its docstring for why (audit F7).
    """
    return tok.rsplit("/", 1)[-1] if _is_pathlike(tok) else tok


_DRIVE_ABS = re.compile(r"^[A-Za-z]:/")


def _is_absolute_posixish(tok: str) -> bool:
    """True for a forward-slashed POSIX (`/a/b`) or Windows-drive (`C:/a/b`) absolute path."""
    return tok.startswith("/") or bool(_DRIVE_ABS.match(tok))


def _resolve_against(cwd: str, tok: str) -> str:
    """Lexically resolve *tok* against *cwd* (both forward-slashed) into a normalized
    absolute form — pure text, no filesystem access. This compares two claimed argv
    paths for equality under the shared working directory both runtimes ran under, not
    their real targets, so it must not stat/resolve symlinks."""
    joined = tok if _is_absolute_posixish(tok) else f"{cwd.rstrip('/')}/{tok}"
    parts: list[str] = []
    for part in joined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append(part)
        else:
            parts.append(part)
    prefix = "/" if joined.startswith("/") else ""
    return prefix + "/".join(parts)


def _canon_argv(argv: list[str], cwd: str | None) -> tuple[str, ...]:
    """Canonicalize one ffmpeg argv *positionally* for parity comparison.

    Only path-bearing argv positions are normalized: the value immediately after each
    `-i` (repeatable, for concat), and the trailing output token. Every other token —
    flags, codec settings, filter-graph expressions — is compared verbatim.

    The old heuristic (``canon_token``: any token containing `/`) is right for a value
    *already known* to be a path but wrong for a raw argv list: an ffmpeg filter
    expression can contain `/` as arithmetic (e.g. `pad=1280:720:(ow-iw)/2:(oh-ih)/2`),
    and blindly reducing that collapsed two different filters to the literal token `'2'`.
    It also compared two path-position tokens by basename alone, so `a/clip.mp4` and
    `b/clip.mp4` — genuinely different files in different directories — registered as
    equal. Resolving against the shared *cwd* fixes both: a relative token (native) and
    an absolute token (python) naming the same file resolve to the same absolute path,
    while two different directories do not. See docs/audits/2026-09-07-core-principles-
    and-rtx5080.md, F7.
    """
    out: list[str] = []
    last = len(argv) - 1
    for i, tok in enumerate(argv):
        prev = argv[i - 1] if i > 0 else None
        path_position = prev == "-i" or (i == last and i > 0 and not tok.startswith("-"))
        if not path_position:
            out.append(tok)
        elif cwd is not None:
            out.append(_resolve_against(cwd, tok))
        else:
            out.append(canon_token(tok))
    return tuple(out)


# Arg keys whose values are paths — mirrors `knaif.planner._PATH_ARG_KEYS` plus the output side.
# ONLY these get path canonicalization in plan mode. Every other string compares verbatim, so a
# non-path value that merely contains `/` (an aspect ratio like `4/3`) is never mangled — the
# plan-mode twin of the argv-position rule in `_canon_argv` (audit F7; review R4).
_PLAN_PATH_ARG_KEYS = frozenset(
    {"inputs", "input", "files", "src", "dst", "path", "base", "append", "output", "outputs"}
)


def _canon_scalar(v: object, *, is_path: bool = False, cwd: str | None = None) -> str:
    """Canonicalize a plan-arg scalar so 720 == "720" and 2.0 == "2.0".

    A value under a path-contract arg key (*is_path*) is resolved against the shared *cwd* so a
    relative token (native) and an absolute one (python) naming the same file compare equal while
    two different directories do not; with no *cwd* it falls back to basename canonicalization.
    Every other string is compared verbatim.
    """
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
            if not is_path:
                return f"str:{s}"
            p = s.replace(chr(92), "/")
            return f"str:{_resolve_against(cwd, p) if cwd is not None else canon_token(p)}"
    return f"other:{v!r}"


def _canon_val(v: object, *, is_path: bool = False, cwd: str | None = None):
    """Hashable canonical form of a plan-arg value (scalars coerced; path args normalized)."""
    if isinstance(v, list):
        return tuple(_canon_val(x, is_path=is_path, cwd=cwd) for x in v)
    if isinstance(v, dict):
        return tuple(
            sorted(
                (k, _canon_val(x, is_path=k in _PLAN_PATH_ARG_KEYS, cwd=cwd)) for k, x in v.items()
            )
        )
    return _canon_scalar(v, is_path=is_path, cwd=cwd)


def canon_plan_step(step: dict, cwd: str | None = None) -> tuple:
    """Canonical (tool, sorted-args) for a plan step — order-insensitive on arg keys."""
    args = step.get("args") or {}
    return (
        step.get("tool"),
        tuple(
            sorted(
                (k, _canon_val(v, is_path=k in _PLAN_PATH_ARG_KEYS, cwd=cwd))
                for k, v in args.items()
            )
        ),
    )


@dataclass
class Outcome:
    """The comparable result of one CLI invocation."""

    kind: str  # "commands" | "plan" | "clarify" | "reject" | "none" | "rendered-none" | "error"
    commands: list[list[str]] = field(default_factory=list)  # normalized argv per command
    plan: list[dict] = field(default_factory=list)  # plan steps (plan mode)
    dumped_plan: list[dict] = field(default_factory=list)  # command mode: the plan that ran
    text: str = ""  # clarify/reject message or error detail
    raw: str = ""  # raw stdout+stderr, for the report on mismatch

    def key(self, cwd: str | None = None) -> tuple:
        """Comparison key: commands/plan canonicalized; else just kind.

        *cwd* (forward-slashed, from the shared ``--cwd`` both runtimes ran under)
        resolves argv path-positions to absolute so a relative token (native) and an
        absolute token (python) compare equal only when they name the SAME file — see
        ``_canon_argv``. Omitting it falls back to basename-only canonicalization,
        which conflates same-named files in different directories; every real caller
        should pass it.
        """
        if self.kind == "commands":
            return ("commands", tuple(_canon_argv(c, cwd) for c in self.commands))
        if self.kind == "plan":
            return ("plan", tuple(canon_plan_step(s, cwd) for s in self.plan))
        return (self.kind,)


def _parse_native_body(stdout: str, stderr: str) -> Outcome:
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
        # Checked before `reject:` — a capability the runtime has not built is a coverage
        # gap, not the safety model working, and the two must never share a bucket.
        if low.startswith(NOT_IMPLEMENTED_PREFIX):
            kind, text = "not_implemented", s.split(":", 1)[1].strip()
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


def _parse_python_body(stdout: str, stderr: str) -> Outcome:
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


def parse_native(stdout: str, stderr: str) -> Outcome:
    """Parse `knaif run <skill> --dry-run`, plus the plan it dumped under `$KNAIF_DUMP_PLAN`."""
    out = _parse_native_body(stdout, stderr)
    out.dumped_plan = _dumped_plan(stderr)
    return out


def parse_python(stdout: str, stderr: str) -> Outcome:
    """Parse `knaif-cli run <skill> --dry-run`, plus the plan it dumped under `$KNAIF_DUMP_PLAN`."""
    out = _parse_python_body(stdout, stderr)
    out.dumped_plan = _dumped_plan(stderr)
    return out


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


def _native_env() -> dict[str, str]:
    """Environment for a native invocation.

    `$KNAIF_N_GPU_LAYERS` is passed through from the harness's own environment (see
    `--native-ngl`) because the compute backend is not a performance detail here: it moves the
    greedy argmax. Measured 2026-09-10 on `encode clip.mp4 at crf 22`, with the prompt verified
    byte-identical on both sides — native on CUDA renders `-crf 23`, native on CPU renders
    `-crf 22`, and Python renders `-crf 22`. Same weights, same prompt, greedy on both sides;
    only the accumulation differs.
    """
    # `$KNAIF_DUMP_PLAN` makes `run` print the plan it executed, which is what separates a port
    # bug (same plan, different commands) from plan disagreement (L3's bar, release plan R0).
    return {**os.environ, "KNAIF_DUMP_PLAN": "1"}


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
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_native_env(),
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
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "KNAIF_DUMP_PLAN": "1"},  # the plan it ran; see `_native_env`
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
# benign "materialized default". Same contract as the path-canonicalization set above; aliased
# rather than restated so the two can't drift.
_SIGNIFICANT_ARG_KEYS = _PLAN_PATH_ARG_KEYS


def load_tool_defaults(skill: str) -> dict[str, dict]:
    """`{tool: declared defaults}` from the skill's `tools.yaml` plus the core control tools.

    The **declared** defaults, from the same contract both runtimes' `apply_defaults` reads.
    This is what makes the plan relation sound: without it there is no way to tell an argument
    one side filled from the contract from one it simply chose differently.
    """
    from knaif.registry import load_registry

    registry = load_registry(REPO_ROOT / "skills" / skill / "tools.yaml")
    registry.update(load_registry(REPO_ROOT / "contracts" / "runtime" / "core_tools.yaml"))
    return {name: dict(td.defaults) for name, td in registry.items()}


def _with_defaults(args: dict, defaults: dict) -> dict:
    """`args` with every absent declared default filled in — what `apply_defaults` does."""
    filled = dict(args)
    for key, value in defaults.items():
        filled.setdefault(key, value)
    return filled


def plan_equiv_modulo_defaults(
    a_steps: list[dict],
    b_steps: list[dict],
    tool_defaults: dict[str, dict],
    cwd: str | None = None,
) -> str | None:
    """If two plans agree once each side's **declared** defaults are filled in, return a note;
    else None.

    **This function used to be unsound, and the name was the trap** (plan 2026-09-10, L3a).
    Despite "modulo defaults" it never consulted a default: it accepted any nesting of arg-key
    sets where the shared keys agreed and the extra keys were not paths. So native emitting
    `quality: "low"` where python omitted `quality` scored *equivalent* — although the two
    render different commands. An omitted argument and an explicitly different setting are not
    the same thing, and an acceptance relation that conflates them cannot gate anything.

    The relation now is: fill both sides from `tool_defaults` (the registry's declared
    defaults, the same map `apply_defaults` uses in both runtimes), then compare the full arg
    maps. An extra key survives only when its value **is** the declared default.

    Worth knowing before reading a result: ffmpeg declares defaults on exactly **one** tool
    (`concat_video.output`). Every other extra key it produces is now a divergence — which is
    the correction, not a side effect. The old docstring's examples (preview / quality /
    include_audio "filled by apply_defaults") describe defaults that **do not exist** in
    `tools.yaml`; whatever was producing those keys, it was not the contract.
    """
    if len(a_steps) != len(b_steps):
        return None
    notes: list[str] = []
    for a, b in zip(a_steps, b_steps, strict=True):
        tool = a.get("tool")
        if tool != b.get("tool"):
            return None
        defaults = tool_defaults.get(tool or "", {})
        aa = _with_defaults(a.get("args") or {}, defaults)
        ba = _with_defaults(b.get("args") or {}, defaults)
        if set(aa) != set(ba):
            return None  # a key one side has and the contract does not explain
        if any(
            _canon_val(aa[k], is_path=k in _PLAN_PATH_ARG_KEYS, cwd=cwd)
            != _canon_val(ba[k], is_path=k in _PLAN_PATH_ARG_KEYS, cwd=cwd)
            for k in aa
        ):
            return None  # a value disagrees → real divergence
        filled = sorted(set(defaults) - (set(a.get("args") or {}) & set(b.get("args") or {})))
        notes += [f"{tool}+{k}" for k in filled]
    return (
        "equivalent (declared defaults filled: " + ", ".join(notes) + ")" if notes else "equivalent"
    )


#: Buckets that count against the equivalence rate. `not-comparable` is excluded from the
#: DENOMINATOR — python renders no command for those intents, so there is nothing to compare —
#: and reported separately, because a rate whose excluded rows are invisible is the shape of
#: every misleading eval number this plan exists to prevent.
_GATED_BUCKETS = ("match", "mismatch", "decline-divergence", "native-not-implemented", "port-bug")


def _plans_equivalent(
    a: list[dict], b: list[dict], cwd: str | None, tool_defaults: dict[str, dict] | None
) -> bool:
    """Same plan: identical after canonicalization, or differing only by declared defaults."""
    if tuple(canon_plan_step(x, cwd) for x in a) == tuple(canon_plan_step(x, cwd) for x in b):
        return True
    if tool_defaults is None:
        return False
    return plan_equiv_modulo_defaults(a, b, tool_defaults, cwd) is not None


def l3_verdict(counts: dict[str, int], max_plan_disagreement: float) -> dict:
    """L3 at the owner's bar (release plan R0): zero port bugs, bounded plan disagreement.

    * `port-bug` (same plan, different commands) must be 0: a porting defect;
    * `native-not-implemented` must be 0: a capability the port lacks is a port defect too;
    * plan disagreement — `mismatch` (different plans) plus `decline-divergence` (clarify vs
      reject) — over the gated rows must not exceed *max_plan_disagreement*, a bound written
      before the run. The runtimes link different llama.cpp builds, so this is never zero.
    A run with nothing to compare does not pass.
    """
    gated = sum(counts.get(k, 0) for k in _GATED_BUCKETS)
    port_bugs = counts.get("port-bug", 0)
    not_impl = counts.get("native-not-implemented", 0)
    disagreement = counts.get("mismatch", 0) + counts.get("decline-divergence", 0)
    rate = disagreement / gated if gated else 0.0
    return {
        "gated": gated,
        "port_bugs": port_bugs,
        "native_not_implemented": not_impl,
        "plan_disagreement": disagreement,
        "plan_disagreement_rate": round(rate, 6),
        "max_plan_disagreement": max_plan_disagreement,
        "passed": bool(gated)
        and port_bugs == 0
        and not_impl == 0
        and rate <= max_plan_disagreement,
    }


def equivalence_rate(counts: dict[str, int]) -> tuple[int, float]:
    """`(gated rows, equivalence rate)`. A run with nothing to compare scores 0.0, not 1.0."""
    gated = sum(counts.get(k, 0) for k in _GATED_BUCKETS)
    return gated, (counts.get("match", 0) / gated if gated else 0.0)


def _sha256(path: Path) -> str | None:
    """Content hash of a file, or None if it isn't there."""
    import hashlib

    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*cmd: str) -> str:
    try:
        return subprocess.run(
            ["git", *cmd], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def build_meta(
    args, rows: list[Row], entry_points: dict[str, str], counts, rate, verdict: dict
) -> dict:
    """The provenance record for a saved run (L3b).

    Everything here answers "could this run be told apart from a different one?". The
    **backend** field is not bookkeeping: greedy argmax over different FP accumulation can flip
    a near-tie, so a CPU→CUDA change between two runs is indistinguishable from the change
    being measured unless both runs say which backend produced them. Nothing in the harness can
    detect that from the outside, so it is recorded from `$KNAIF_PARITY_BACKEND` and left
    explicitly `null` when the runner did not say — an unanswered question, not a guess.

    `entry_points` satisfies rule 2 (L3d): the record states which command was run on each
    side, so a later reader can check the two halves were the same stage.
    """
    corpus = REPO_ROOT / "skills" / args.skill / "data" / "eval.jsonl"
    return {
        "purpose": args.purpose or f"L3 behavioral parity — {args.skill} ({args.mode} mode)",
        "captured": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "git_sha": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "corpus": {
            "path": str(corpus.relative_to(REPO_ROOT)),
            "rows_compared": len(rows),
            "sha256": _sha256(corpus),
        },
        "model": {
            "path": str(args.model_path),
            "sha256": _sha256(args.model_path),
            "bytes": args.model_path.stat().st_size if args.model_path.is_file() else None,
        },
        "binary": {
            "path": str(args.native_bin),
            "sha256": _sha256(args.native_bin) if args.native_bin else None,
        },
        # See the docstring: an honest null beats a plausible default.
        "backend": os.environ.get("KNAIF_PARITY_BACKEND") or None,
        "native_n_gpu_layers": os.environ.get("KNAIF_N_GPU_LAYERS"),
        "backend_note": (
            "Set $KNAIF_PARITY_BACKEND (cpu|vulkan|cuda) so two runs can be compared. A backend "
            "change can flip a greedy-argmax near-tie, which would otherwise be indistinguishable "
            "from the change under measurement. MEASURED, not theoretical: on 2026-09-10, with "
            "the prompt verified byte-identical on both sides, `encode clip.mp4 at crf 22` "
            "rendered -crf 23 from native on CUDA and -crf 22 from native on CPU and from "
            "Python. The two runtimes link DIFFERENT llama.cpp builds (llama-cpp-2 vs "
            "llama-cpp-python), so this is a standing property of the comparison, not a "
            "misconfiguration — see `backend_attribution.json` next to this file."
        ),
        "entry_points": entry_points,
        # L3's bar (release plan R0): the verdict is `l3_verdict`, written into the record so
        # `gate --record-parity` reads the run's own answer. `equivalence_rate` stays as a
        # descriptive number; it no longer passes or fails the run.
        "result": {
            "counts": dict(counts),
            "equivalence_rate": round(rate, 6),
            **verdict,
        },
    }


def compare(
    row: Row,
    native: Outcome,
    py: Outcome,
    strict: bool,
    plan_mode: bool = False,
    cwd: str | None = None,
    tool_defaults: dict[str, dict] | None = None,
) -> tuple[str, str]:
    """Return (status, note). status ∈ {match, mismatch, decline-divergence,
    not-comparable, native-not-implemented}.

    *cwd*: forward-slashed shared working directory both runtimes ran under — passed
    through to ``Outcome.key()`` for command-mode argv path canonicalization (F7). The
    real caller (``main``) always has one; self-test's synthetic assertions that don't
    need it (plan mode, rendered-none) may omit it.
    """
    # One side planned but its dry-run renders no command (python compress/platform/thumbnail/
    # batch) — can't command-compare, so exclude rather than score as drift.
    # A capability native has not built. Still a divergence and still gates (below) — but
    # counted apart from command drift, because "the port is missing a feature" and "the two
    # planners disagree" call for different work, and an aggregate that merges them tells you
    # neither. This is what makes L4's coverage number computable at all.
    if native.kind == "not_implemented":
        return "native-not-implemented", f"native capability gap: {native.text}"
    if "rendered-none" in (native.kind, py.kind):
        return (
            "not-comparable",
            "python dry-run emits no command for this intent (compress/platform/thumbnail/batch)",
        )
    # Plan mode: accept plans that differ only by *declared* defaults one side materialized.
    # `tool_defaults` is required here rather than optional-with-a-fallback: without the
    # contract the relation cannot be evaluated, and quietly answering "mismatch" (or worse,
    # "match") would be the comparator deciding an acceptance question by accident.
    if plan_mode and native.kind == "plan" and py.kind == "plan" and native.key(cwd) != py.key(cwd):
        if tool_defaults is None:
            raise ValueError("plan-mode comparison needs tool_defaults (see load_tool_defaults)")
        eq = plan_equiv_modulo_defaults(native.plan, py.plan, tool_defaults, cwd)
        if eq is not None:
            return "match", eq
        return "mismatch", "plan tools/args differ"
    # NOTE: the `chain-native-single-step` bucket that used to sit here is **gone** (2026-09-10,
    # Workstream E). It existed because native could only ever render step 1 of a chain, so the
    # most that could be asserted was a prefix match on the first command. Native now executes
    # chains in order, and leaving a lenient branch in place would have been worse than the
    # limitation it was written for: every chain row would pass on its first command alone, and a
    # divergence in steps 2..n — precisely what the executor newly makes possible — would be
    # invisible. Chains now fall through to the same comparison as everything else.
    if native.key(cwd) == py.key(cwd):
        # Equal actions, but flag when they only match after path normalization (native
        # emits relative paths, python absolute) so the representation gap stays visible.
        if native.kind == "commands" and native.commands != py.commands:
            return "match", "equivalent (paths differ: native relative, python absolute)"
        return "match", ""
    # Same plan, different commands: the two runtimes agreed on WHAT to do and rendered it
    # differently. That is a porting defect, never model noise, and L3 requires zero of them.
    # Only decidable when both sides dumped the plan they ran; without it the row stays an
    # ordinary mismatch rather than being called a port bug on a guess.
    if (
        native.kind == "commands"
        and py.kind == "commands"
        and native.dumped_plan
        and py.dumped_plan
        and _plans_equivalent(native.dumped_plan, py.dumped_plan, cwd, tool_defaults)
    ):
        return "port-bug", "same plan, different commands"
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
        help="Accepted and ignored. The chain leniency it disabled is gone (2026-09-10, "
        "Workstream E) — chain rows are always compared in full now, so this is what the "
        "harness always does. Kept so existing invocations and scripts do not break.",
    )
    ap.add_argument(
        "--native-ngl",
        default=None,
        dest="native_ngl",
        metavar="N",
        help="Force native's GPU layer count ($KNAIF_N_GPU_LAYERS) for this run. Use 0 to put "
        "native on the CPU. THIS CHANGES THE RESULT: the compute backend moves the greedy "
        "argmax, so a native-CUDA vs Python-CPU comparison measures the two llama.cpp builds "
        "as much as it measures the port. Recorded in meta.json.",
    )
    ap.add_argument(
        "--max-plan-disagreement",
        type=float,
        default=None,
        dest="max_plan_disagreement",
        metavar="RATE",
        help="L3's bound on plan-level disagreement (different plans, or clarify vs reject) as a "
        "fraction of gated rows. REQUIRED with --label and written BEFORE the run: a bound "
        "chosen after seeing the result is not a bound. Port bugs (same plan, different "
        "commands) and capabilities native lacks must be zero regardless (release plan R0). "
        "Unlabelled dev runs default to 0.0.",
    )
    ap.add_argument(
        "--out", type=Path, help="Write the JSON report here (default: evals/parity/…)."
    )
    ap.add_argument(
        "--label",
        default="",
        help="Short name for this run. With it the report is written to a run DIRECTORY "
        "(evals/parity/<date>_<label>/) carrying report.json + meta.json, which is the form "
        "L3b requires for anything quoted as evidence.",
    )
    ap.add_argument(
        "--purpose",
        default="",
        help="One line recorded in meta.json saying what this run was for.",
    )
    ap.add_argument(
        "--self-test", action="store_true", help="Run internal parser assertions and exit."
    )
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if args.label and args.max_plan_disagreement is None:
        ap.error(
            "--label makes this run evidence, so state --max-plan-disagreement RATE before it "
            "runs (L3's bar: zero port bugs, plan disagreement within a pre-written bound)."
        )
    if args.max_plan_disagreement is None:
        args.max_plan_disagreement = 0.0

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
    # Absolutize BEFORE handing either path to a subprocess. Both CLIs run with `cwd` set to the
    # fixture directory, so a relative `--model-path` (valid from the repo root, where the check
    # above passed) resolves to nothing there — and native answers a missing model by printing
    # first-run guidance and exiting 0, which the harness classifies as `none` and scores as a
    # mismatch. The result is a run that reports 0% parity and looks like catastrophic drift when
    # nothing was ever compared. `just parity` passes absolute paths and never hit this; a
    # hand-written invocation does.
    args.native_bin = args.native_bin.resolve()
    args.model_path = args.model_path.resolve()
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
    cwd_posix = cwd.as_posix()  # for Outcome.key()'s argv path-position resolution (F7)
    tags_filter = {t.strip() for t in args.tags.split(",") if t.strip()} or None
    rows = load_rows(args.skill, tags_filter)
    if args.skip_chains:
        rows = [r for r in rows if not r.is_chain]
    if args.limit:
        rows = rows[: args.limit]

    plan_mode = args.mode == "plan"
    # Applied to this process's environment so every native subprocess inherits it (see
    # `_native_env`). Set before the header prints so the run states what it actually used.
    if args.native_ngl is not None:
        os.environ["KNAIF_N_GPU_LAYERS"] = str(args.native_ngl)
    # The declared defaults both runtimes' `apply_defaults` reads. Loaded once, up front, so a
    # broken bundle fails before any inference is spent.
    tool_defaults = load_tool_defaults(args.skill)
    batch = args.batch
    if batch and not plan_mode:
        ap.error("--batch is only supported with --mode plan")
    print(f"parity[{args.mode}{'/batch' if batch else ''}]: {args.skill} — {len(rows)} row(s)")
    # L3d / rule 2: name the entry point on each side, in the output *and* in the saved record.
    # "Compare the same stage on both sides" is unverifiable if the record does not say which
    # stage each side ran.
    entry_points = {
        "native": (
            f"{args.native_bin} "
            f"{'plan --skill S --json' if plan_mode else 'run S --dry-run'} "
            f"--model {args.model_path.name}"
        ),
        "python": (
            f"uv run knaif-cli {'plan' if plan_mode else 'run --dry-run'} "
            f"--backend llama-cpp --model {args.python_model}"
        ),
        "stage": (
            "validated plan envelope (planner output)"
            if plan_mode
            else "rendered command argv (shipped dry-run path)"
        ),
    }
    print(f"  native : {entry_points['native']}")
    print(f"  python : {entry_points['python']}")
    print(f"  stage  : {entry_points['stage']}")
    print(f"  weights: {args.model_path}  (both runtimes, identity verified)")
    print(
        f"  backend: {os.environ.get('KNAIF_PARITY_BACKEND') or 'UNRECORDED ($KNAIF_PARITY_BACKEND)'}"
    )
    print(f"  cwd    : {cwd}\n")

    t0 = time.perf_counter()
    results = []
    counts = {
        "match": 0,
        "mismatch": 0,
        "decline-divergence": 0,
        "not-comparable": 0,
        "native-not-implemented": 0,
        "port-bug": 0,
    }

    def handle(idx: int, row: Row, native: Outcome, py: Outcome) -> None:
        status, note = compare(
            row,
            native,
            py,
            args.strict,
            plan_mode=plan_mode,
            cwd=cwd_posix,
            tool_defaults=tool_defaults,
        )
        counts[status] = counts.get(status, 0) + 1
        icon = {
            "match": "✓",
            "mismatch": "✗",
            "decline-divergence": "!",
            "not-comparable": "–",
            "native-not-implemented": "∅",
            "port-bug": "✗✗",
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
    gated, rate = equivalence_rate(counts)
    verdict = l3_verdict(counts, args.max_plan_disagreement)
    print("\n── summary ─────────────────────────────────────────")
    print(f"  equivalent              : {counts['match']}/{gated} gated rows = {rate:.4f}")
    print(f"  port bugs               : {counts['port-bug']}  (same plan, different commands)")
    print(f"  plans differ            : {counts['mismatch']}")
    print(f"  decline-divergence      : {counts['decline-divergence']}  (reject vs clarify)")
    print(f"  not-comparable          : {counts['not-comparable']}  (python renders no cmd)")
    print(
        f"  native not-implemented  : {counts['native-not-implemented']}"
        "  (capability gap, not drift)"
    )
    print(f"  total rows / time       : {total} / {elapsed:.0f}s")

    # Enumerate, don't just count. An aggregate can hide offsetting changes in both
    # directions, and a rate with no list of what failed is not something anyone can act on.
    divergent = [r for r in results if r["status"] not in ("match", "not-comparable")]
    if divergent:
        print(f"\n── {len(divergent)} non-equivalent row(s) ──────────────────────")
        for r in divergent:
            print(f"  {r['status']:<24} {r['id']:<18} {r['utterance'][:44]}")
            print(f"    {r['note']}")

    # `--label` means "this is evidence", so it ALWAYS gets meta.json — the provenance
    # (backend, git sha, binary/model/corpus sha256s, entry points) without which two runs
    # cannot be compared. It used to be written only when `--label` came WITHOUT `--out`,
    # so passing both silently produced a bare report.json that this file's own comment
    # calls "not enough to quote as evidence". That is exactly how the 2026-09-14 L3 record
    # lost its backend, which then made the 2026-09-15 comparison unattributable: the run
    # before it was `cuda`, the one after was `vulkan`, and nothing recorded the middle.
    # `--out` now chooses only WHERE the report goes; meta.json lands beside it.
    run_dir: Path | None = None
    if args.label:
        if args.out:
            out = args.out
            run_dir = out.parent
        else:
            run_dir = (
                REPO_ROOT
                / "evals"
                / "parity"
                / f"{datetime.now(timezone.utc):%Y-%m-%d}_{args.label}"
            )
            out = run_dir / "report.json"
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
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
    if run_dir is not None:
        meta = build_meta(args, rows, entry_points, counts, rate, verdict)
        (run_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"  meta                    : {run_dir / 'meta.json'}")
        if meta["backend"] is None:
            print(
                "  WARNING: backend unrecorded — set $KNAIF_PARITY_BACKEND before quoting this run"
            )
        if meta["git_dirty"]:
            print("  WARNING: working tree dirty — the git SHA does not describe what ran")
        print(f"  NEXT                    : add a row to evals/INDEX.md for {run_dir.name}")

    # L3's bar (owner, 2026-09-25; release plan R0). The old 1.0 equivalence rate was
    # unreachable by construction: the runtimes link different llama.cpp builds, and even three
    # native backends do not agree 100%. So the gate separates what the port owns from what the
    # model owns: port bugs and missing capabilities must be zero; plan disagreement must stay
    # within a bound the runner wrote down before the run (required with --label).
    ok = verdict["passed"]
    print(
        f"  gate                    : {'PASS' if ok else 'FAIL'} "
        f"(port bugs {verdict['port_bugs']}, not implemented "
        f"{verdict['native_not_implemented']}, plan disagreement "
        f"{verdict['plan_disagreement_rate']:.4f} <= {verdict['max_plan_disagreement']:.4f} "
        "required)"
    )
    return 0 if ok else 1


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
    # A capability gap is not a reject: the two are opposite facts about the product, and
    # merging them makes coverage uncomputable.
    ni = parse_native("not_implemented: this request needs 2 steps, but native ...\n", "")
    assert ni.kind == "not_implemented", ni
    assert ni.text.startswith("this request needs 2 steps"), ni
    chain_row = Row(id="r1", utterance="u", tags=["chain2"], is_chain=True)
    st, note = compare(
        chain_row,
        ni,
        parse_native("ffmpeg -y -i a.mp4 out.mkv\n", ""),
        strict=False,
        plan_mode=False,
        cwd=None,
    )
    assert st == "native-not-implemented", (st, note)
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
    # F7: only `-i`'s value and the trailing output token are path positions — a filter
    # expression containing '/' as arithmetic (pad's centering) must never be touched, so
    # two DIFFERENT filters must still mismatch instead of both collapsing to the same key.
    f720 = parse_native(
        "ffmpeg -y -i clip.mp4 -vf "
        "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2 "
        "out.mp4\n",
        "",
    )
    f360 = parse_native(
        "ffmpeg -y -i clip.mp4 -vf "
        "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2 "
        "out.mp4\n",
        "",
    )
    assert f720.key() != f360.key(), (
        f"different filter expressions must not collapse to the same key:\n"
        f"{f720.key()}\n{f360.key()}"
    )
    # F7: two DIFFERENT source directories that happen to share a basename must not be
    # conflated when resolved against the shared cwd both runtimes ran under — only a
    # native-relative token and a python-absolute token naming the SAME file should match.
    a_dir = parse_native("ffmpeg -y -i a/clip.mp4 out.mp4\n", "")
    b_dir = parse_native("ffmpeg -y -i b/clip.mp4 out.mp4\n", "")
    assert a_dir.key(cwd="/work/fixtures") != b_dir.key(
        cwd="/work/fixtures"
    ), "different source directories sharing a basename must not canonicalize equal"
    py_abs_same = parse_python("  $ ffmpeg -y -i /work/fixtures/clip.mp4 out.mp4\n", "")
    nat_rel_same = parse_native("ffmpeg -y -i clip.mp4 out.mp4\n", "")
    assert nat_rel_same.key(cwd="/work/fixtures") == py_abs_same.key(
        cwd="/work/fixtures"
    ), "native-relative vs python-absolute of the SAME file under the shared cwd must match"
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
    # R4: plan mode must honor the shared cwd and argument contracts too, not just command mode.
    # Two DIFFERENT source directories sharing a basename must not compare equal...
    pa = parse_plan_json(
        '{"plan":[{"tool":"inspect_document","args":{"input":"a/report.pdf"}}]}\n', ""
    )
    pb = parse_plan_json(
        '{"plan":[{"tool":"inspect_document","args":{"input":"b/report.pdf"}}]}\n', ""
    )
    assert pa.key(cwd="/work") != pb.key(
        cwd="/work"
    ), "plan mode: different source directories sharing a basename must not compare equal"
    # ...while native-relative vs python-absolute of the SAME file under that cwd still must.
    prel = parse_plan_json(
        '{"plan":[{"tool":"inspect_document","args":{"input":"report.pdf"}}]}\n', ""
    )
    pabs2 = parse_plan_json(
        '{"plan":[{"tool":"inspect_document","args":{"input":"/work/report.pdf"}}]}\n', ""
    )
    assert prel.key(cwd="/work") == pabs2.key(
        cwd="/work"
    ), "plan mode: relative vs absolute of the SAME file under the shared cwd must match"
    # A non-path arg that merely contains '/' (an aspect ratio) must survive verbatim — only
    # path-contract args are path-normalized.
    ar43 = parse_plan_json(
        '{"plan":[{"tool":"resize_video","args":{"inputs":["clip.mp4"],"aspect":"4/3"}}]}\n', ""
    )
    ar163 = parse_plan_json(
        '{"plan":[{"tool":"resize_video","args":{"inputs":["clip.mp4"],"aspect":"16/3"}}]}\n', ""
    )
    assert ar43.key(cwd="/work") != ar163.key(
        cwd="/work"
    ), "plan mode: different aspect values must not both collapse to their last '/' segment"
    # A chain plan compares end-to-end in plan mode (no single-step leniency).
    chain = parse_plan_json(
        '{"plan":[{"tool":"convert_video","args":{"inputs":["clip.mov"],"container":"mp4"}},{"tool":"strip_audio","args":{"inputs":["clip.mp4"]}}]}\n',
        "",
    )
    # The real contract, loaded from the real bundle: the plan relation is only meaningful
    # against declared defaults, so the self-test uses them rather than a convenient fiction.
    ffmpeg_defaults = load_tool_defaults("ffmpeg")
    assert ffmpeg_defaults["concat_video"] == {"output": "combined.mp4"}
    st, _ = compare(
        Row("c", "convert+strip", [], True),
        chain,
        chain,
        strict=False,
        plan_mode=True,
        tool_defaults=ffmpeg_defaults,
    )
    assert st == "match", f"identical chain plan must match in plan mode: {st}"
    # Different tool → mismatch.
    other = parse_plan_json(
        '{"plan":[{"tool":"compress_video","args":{"inputs":["clip.mp4"]}}]}\n', ""
    )
    assert np.key() != other.key(), "different tool must mismatch"
    # clarify plan classifies as clarify (feeds decline-divergence).
    cl = parse_plan_json('{"plan":[{"tool":"clarify","args":{"question":"which file?"}}]}\n', "")
    assert cl.kind == "clarify", cl

    def plan_cmp(a: str, b: str) -> tuple[str, str]:
        return compare(
            Row("d", "u", [], False),
            parse_plan_json(a + "\n", ""),
            parse_plan_json(b + "\n", ""),
            strict=False,
            plan_mode=True,
            tool_defaults=ffmpeg_defaults,
        )

    # L3a — THE REGRESSION THIS RELATION WAS REWRITTEN FOR. `preview` has no declared default
    # in ffmpeg's tools.yaml, so one side emitting it and the other omitting it is a real
    # divergence: the two render different commands. The old relation scored this "equivalent
    # modulo default args" purely because `preview` is not a path key.
    st, note = plan_cmp(
        '{"plan":[{"tool":"compress_video","args":{"inputs":["a.mp4"],"crf":18,"preview":true}}]}',
        '{"plan":[{"tool":"compress_video","args":{"inputs":["a.mp4"],"crf":18}}]}',
    )
    assert st == "mismatch", (st, note)
    # The same shape with a value that IS the declared default is benign — and it is the only
    # thing that may be. `concat_video.output` defaults to `combined.mp4`, so python omitting it
    # and native materializing it are the same plan.
    st, note = plan_cmp(
        '{"plan":[{"tool":"concat_video","args":{"inputs":["a.mp4"],"output":"combined.mp4"}}]}',
        '{"plan":[{"tool":"concat_video","args":{"inputs":["a.mp4"]}}]}',
    )
    assert st == "match" and "concat_video+output" in note, (st, note)
    # ...and the same key carrying a value that is NOT the default is a divergence, which is
    # exactly the distinction the old relation could not make.
    st, note = plan_cmp(
        '{"plan":[{"tool":"concat_video","args":{"inputs":["a.mp4"],"output":"other.mp4"}}]}',
        '{"plan":[{"tool":"concat_video","args":{"inputs":["a.mp4"]}}]}',
    )
    assert st == "mismatch", (st, note)
    # A disagreeing SHARED arg value is a real mismatch.
    st, _ = plan_cmp(
        '{"plan":[{"tool":"compress_video","args":{"inputs":["a.mp4"],"crf":18}}]}',
        '{"plan":[{"tool":"compress_video","args":{"inputs":["a.mp4"],"crf":28}}]}',
    )
    assert st == "mismatch", st
    # A differing INPUT/path key is a real divergence (regression guard: native hallucinated
    # `inputs` while python omitted it must NOT normalize to a match).
    st, _ = plan_cmp(
        '{"plan":[{"tool":"resize_video","args":{"inputs":["v.mp4"],"keep_aspect_ratio":true}}]}',
        '{"plan":[{"tool":"resize_video","args":{"keep_aspect_ratio":true}}]}',
    )
    assert st == "mismatch", st
    # Plan mode must not answer an acceptance question without the contract in hand.
    try:
        compare(
            Row("d", "u", [], False),
            parse_plan_json('{"plan":[{"tool":"compress_video","args":{"crf":18}}]}\n', ""),
            parse_plan_json('{"plan":[{"tool":"compress_video","args":{"crf":28}}]}\n', ""),
            strict=False,
            plan_mode=True,
        )
        raise AssertionError("plan mode must refuse to compare without tool_defaults")
    except ValueError:
        pass
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
