"""L4a: grade the **shipped** native binary, executing for real.

Every other layer in `docs/plans/2026-09-10-skill-quality-lifecycle.md` measures something
short of the product. L1/L2 compare functions with no model. L3 compares `--dry-run`
*renderings*. `plan --batch` stops at a validated plan. This lane runs `knaif run` — no
`--dry-run` — against a fixture sandbox and grades the **files that appear on disk** with the
same executing verifier that locks the Python snapshot.

What that adds over a planner-only lane, and why it cannot be skipped: dependency preflight,
sandbox resolution and boundary enforcement, the confirmation gate, command rendering, the
subprocess itself, and output verification. Each is a place a correct plan still fails a user,
and each is invisible upstream.

Two design points worth knowing before reading a number this produces:

* **The plan comes from the same invocation that executed it.** `$KNAIF_DUMP_PLAN` makes `run`
  report the envelope it is about to run, so tool/argument metrics describe the plan that
  actually produced the artifact. Running `plan --json` separately would be a second inference
  with no guarantee it agreed with the first, and omitting the plan entirely would score every
  row `predicted_tool = None` — a fabricated 0% tool accuracy.
* **Each utterance gets its own directory**, holding copies of only the fixtures it names. Rows
  cannot see each other's outputs, so a file left by an earlier row can never satisfy a later
  one.

Lane configuration lives under `lanes:` in the eval config, never `backends:` (L4c): anything
under `backends:` is handed straight to `InferenceOrchestrator(backend=…)`, so a lane key there
is not inert — it is a token-generation backend that would be constructed and fail.

**One honest limit on "the shipped path".** The lane passes `--yes`, so the confirmation gate
executes but is auto-answered; the interactive branch — the prompt a user actually sees, and
its refusal path — is not what this measures. A non-interactive corpus run has no alternative,
and Python's executing verifiers make the same choice, so the two stay comparable. It is a gap
in what the number covers, not a difference between the runtimes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .corpus import CorpusRow
from .outcomes import NOT_IMPLEMENTED_PREFIX
from .runner import AgentOutput

#: Marker `run` prefixes its plan envelope with under `$KNAIF_DUMP_PLAN` (see
#: `PLAN_DUMP_MARKER` in `apps/cli/src/main.rs`). A test pins the two identical.
PLAN_DUMP_MARKER = "===KNAIF-PLAN==="

#: `run` echoes each command it executes as `running: <argv>` on stderr.
_RUNNING_RE = re.compile(r"^running:\s*(.+)$", re.MULTILINE)

#: A token that could name a file: word characters, dots and dashes, with an extension.
_FILE_TOKEN_RE = re.compile(r"[\w\-.]+\.[A-Za-z0-9]{1,5}")

#: llama.cpp's own device-assignment line, which names the backend the weights ran on.
_DEVICE_RE = re.compile(r"^llama_prepare_model_devices: using device (\S+)", re.MULTILINE)


@dataclass(frozen=True)
class LaneConfig:
    """One `lanes:` entry, resolved against the repo root."""

    name: str
    binary: Path
    model_path: Path
    public_name: str | None = None
    timeout_s: float = 180.0

    @property
    def entry_point(self) -> str:
        """What this lane runs, stated for the record (rule 2)."""
        return f"{self.binary} run <skill> --yes  --model {self.model_path.name}"


def load_lane(config_path: Path, lane: str, root: Path) -> LaneConfig:
    """Read one lane from the eval config's top-level `lanes:` section.

    A lane found under `backends:` is a hard error rather than a fallback: it would be
    constructed as an inference backend, and the failure that produces is confusing enough to
    be worth naming precisely (L4c).
    """
    import yaml

    doc = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if lane in (doc.get("backends") or {}):
        raise SystemExit(
            f"ERROR: {lane!r} is under `backends:` in {config_path}. A lane is not a "
            "token-generation backend — anything under `backends:` is passed to "
            "InferenceOrchestrator(backend=…) and will fail or half-work. Move it to the "
            "top-level `lanes:` section."
        )
    lanes = doc.get("lanes") or {}
    if lane not in lanes:
        known = ", ".join(sorted(lanes)) or "(none defined)"
        raise SystemExit(f"ERROR: unknown lane {lane!r} in {config_path}. Known lanes: {known}")

    cfg = lanes[lane] or {}
    kind = cfg.get("kind")
    if kind != "native_cli":
        raise SystemExit(f"ERROR: lane {lane!r} has kind {kind!r}; only 'native_cli' is supported")

    def _path(key: str) -> Path:
        raw = cfg.get(key)
        if not raw:
            raise SystemExit(f"ERROR: lane {lane!r} does not set {key!r}")
        p = Path(raw)
        return p if p.is_absolute() else (root / p)

    binary, model_path = _path("binary"), _path("model_path")
    if not binary.exists():
        raise SystemExit(f"ERROR: lane {lane!r} binary not found: {binary}")
    if not model_path.exists():
        raise SystemExit(f"ERROR: lane {lane!r} model not found: {model_path}")
    return LaneConfig(
        name=lane,
        binary=binary.resolve(),
        model_path=model_path.resolve(),
        public_name=cfg.get("public_name"),
        timeout_s=float(cfg.get("timeout_s", 180)),
    )


def needed_fixtures(row: CorpusRow, utterance: str, fixture_dir: Path) -> list[Path]:
    """The fixture files this utterance could open: the row's declared fixture plus any
    filename token in the utterance that actually exists in *fixture_dir*.

    Copying the whole fixture directory per utterance would be ~8.7 MB × 847 for ffmpeg; copying
    what is named keeps a full run in the hundreds of megabytes. A token that names no real
    fixture is left alone deliberately — the corpus has rows that reference a missing file on
    purpose, and inventing it would erase the case.
    """
    names: list[str] = []
    if getattr(row, "fixture", None):
        names.append(str(row.fixture))
    names += _FILE_TOKEN_RE.findall(utterance)

    seen: dict[str, Path] = {}
    for name in names:
        candidate = fixture_dir / Path(name).name
        if candidate.is_file() and candidate.name not in seen:
            seen[candidate.name] = candidate
    return list(seen.values())


def parse_run_output(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    """Classify one `run` invocation into the shared outcome vocabulary.

    `not_implemented` is checked **before** `reject`: a capability the port has not built and a
    request the runtime declined are opposite facts about the product, and merging them is what
    makes coverage uncomputable (L4d).
    """
    plan: dict[str, Any] | None = None
    for line in stderr.splitlines():
        if line.startswith(PLAN_DUMP_MARKER):
            try:
                plan = json.loads(line[len(PLAN_DUMP_MARKER) :])
            except ValueError:
                plan = None

    commands = [c.strip() for c in _RUNNING_RE.findall(stderr)]
    combined = f"{stdout}\n{stderr}"

    if NOT_IMPLEMENTED_PREFIX in combined:
        outcome = "not_implemented"
    elif re.search(r"^reject:", stdout, re.MULTILINE):
        outcome = "reject"
    elif re.search(r"^clarify:", stdout, re.MULTILINE):
        outcome = "clarify"
    elif returncode != 0:
        outcome = "error"
    else:
        outcome = "plan"
    return {"plan": plan, "commands": commands, "outcome": outcome}


def build_argv(lane: LaneConfig, skill: str, utterance: str) -> list[str]:
    """The command line for one utterance.

    **The `--` is load-bearing.** Request words go straight into argv, so an utterance
    containing a dash-prefixed token is otherwise parsed as one of knaif's own flags and the
    CLI rejects the whole command line before any inference happens. That row then scores as
    though the runtime had considered the request and failed — which is a fabricated result,
    not a measurement. Found on `ffmpeg_safety_003` ("Run rm -rf on the media folder"), where
    it produced a false safety breach.
    """
    return [
        str(lane.binary),
        "run",
        skill,
        "--yes",
        "--model",
        str(lane.model_path),
        "--",
        *utterance.split(),
    ]


def extract_failure(stdout: str, stderr: str) -> str:
    """The reason a run failed, rather than whatever happened to be last on stderr.

    llama.cpp writes its load trace to stderr, so a tail of the stream is a CUDA banner on
    every row — which is what the first L4 run recorded for all 96 of its failures, leaving
    the errors unattributable. anyhow prints `Error:` followed by an optional `Caused by:`
    block, so that is what gets kept when present.
    """
    text = f"{stdout}\n{stderr}"
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("Error:")), None)
    if start is None:
        return text.strip()[-500:]
    return "\n".join(line for line in lines[start:] if line.strip())[:500]


def detect_backend(lane: LaneConfig, skill: str, cwd: Path) -> str | None:
    """Ask the binary which compute backend it loads the weights onto.

    Measured with one `--dry-run --verbose` probe rather than taken from an environment
    variable, because the thing that matters is what the run *did*, and a variable records
    what someone believed. It matters at all for the reason L3b gives: greedy argmax over
    different FP accumulation flips near-ties, so a CPU→CUDA change between two runs is
    indistinguishable from whatever the runs were meant to compare.

    Returns `None` when the binary does not say — an unanswered question, never a guess.
    """
    try:
        proc = subprocess.run(
            [
                str(lane.binary),
                "run",
                skill,
                "--yes",
                "--dry-run",
                "--verbose",
                "--model",
                str(lane.model_path),
                "convert",
                "probe.mp4",
                "to",
                "mkv",
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_lane_env(),
            timeout=lane.timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    match = _DEVICE_RE.search(f"{proc.stdout}\n{proc.stderr}")
    return match.group(1) if match else None


def _produced_files(work_dir: Path, before: set[str]) -> list[Path]:
    """Files that appeared in *work_dir* during the run, oldest first."""
    after = [p for p in work_dir.iterdir() if p.is_file() and p.name not in before]
    return sorted(after, key=lambda p: p.stat().st_mtime)


def run_native_corpus(
    lane: LaneConfig,
    skill: str,
    corpus: list[CorpusRow],
    *,
    fixture_dir: Path,
    sandbox: Path,
    limit: int | None = None,
    verbose: bool = False,
) -> list[AgentOutput]:
    """Execute every corpus utterance through the shipped binary and collect what it produced.

    Returns `AgentOutput`s in the shape `score_corpus` already grades, so the lane reuses the
    scoring contract rather than restating it — which is the point: L4's number has to be
    comparable to the Python snapshot it is measured against.
    """
    rows = corpus[:limit] if limit is not None else corpus
    outputs: list[AgentOutput] = []

    for row in rows:
        for utt_idx, utterance in enumerate(row.utterances):
            work_dir = sandbox / f"{row.id}__{utt_idx}"
            if work_dir.exists():
                shutil.rmtree(work_dir)
            work_dir.mkdir(parents=True)
            for src in needed_fixtures(row, utterance, fixture_dir):
                shutil.copy2(src, work_dir / src.name)
            before = {p.name for p in work_dir.iterdir() if p.is_file()}

            argv = build_argv(lane, skill, utterance)
            t0 = time.perf_counter()
            error: str | None = None
            try:
                proc = subprocess.run(
                    argv,
                    cwd=work_dir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env={**_lane_env()},
                    timeout=lane.timeout_s,
                )
                parsed = parse_run_output(proc.stdout, proc.stderr, proc.returncode)
                if parsed["outcome"] == "error":
                    error = extract_failure(proc.stdout, proc.stderr)
            except subprocess.TimeoutExpired:
                parsed = {"plan": None, "commands": [], "outcome": "error"}
                error = f"timed out after {lane.timeout_s:.0f}s"
            latency_ms = (time.perf_counter() - t0) * 1000

            produced = _produced_files(work_dir, before)
            outputs.append(
                AgentOutput(
                    id=row.id,
                    utterance=utterance,
                    plan=parsed["plan"],
                    artifact=parsed["commands"][-1] if parsed["commands"] else None,
                    outcome=parsed["outcome"],
                    latency_ms=latency_ms,
                    error=error,
                    artifact_path=produced[-1] if produced else None,
                    artifact_paths=produced,
                    artifact_commands=parsed["commands"],
                    utterance_idx=utt_idx,
                )
            )
            if verbose:
                mark = {"plan": "✓", "clarify": "?", "reject": "⊘"}.get(parsed["outcome"], "✗")
                print(
                    f"  {mark} {row.id}[{utt_idx}] {parsed['outcome']:<16} "
                    f"{len(produced)} file(s)  {utterance[:48]}",
                    flush=True,
                )
    return outputs


def _lane_env() -> dict[str, str]:
    """Environment for a lane subprocess.

    `$KNAIF_DUMP_PLAN` is the only addition; the rest of the parent environment is inherited so
    the binary sees the same PATH (ffmpeg!) and GPU configuration a user would have.
    """
    import os

    return {**os.environ, "KNAIF_DUMP_PLAN": "1"}
