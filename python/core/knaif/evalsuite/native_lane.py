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
* **Each utterance gets its own directory**, holding every fixture (hard-linked). Rows cannot
  see each other's *outputs*, so a file left by an earlier row can never satisfy a later one —
  which is the property worth having. Inputs are shared because Python's verifiers run the whole
  corpus in one sandbox where every fixture is visible; withholding one from native scored a
  harness difference as a runtime defect.

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
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .corpus import CorpusRow
from .outcomes import NOT_IMPLEMENTED_PREFIX
from .provisioning import provision_row_dir
from .runner import AgentOutput

#: Marker `run` prefixes its plan envelope with under `$KNAIF_DUMP_PLAN` (see
#: `PLAN_DUMP_MARKER` in `apps/cli/src/main.rs`). A test pins the two identical.
PLAN_DUMP_MARKER = "===KNAIF-PLAN==="

#: `run` echoes each command it executes as `running: <argv>` on stderr.
_RUNNING_RE = re.compile(r"^running:\s*(.+)$", re.MULTILINE)

#: llama.cpp's device *enumeration* line. It names a device llama.cpp considered, NOT one the
#: weights ran on — keep it as a separate fact, never as the answer. See `parse_tensor_placement`.
_DEVICE_RE = re.compile(r"^llama_prepare_model_devices: using device (\S+)", re.MULTILINE)

#: Where each layer actually landed. This is the honest signal: llama.cpp emits one line per
#: layer, e.g. `load_tensors: layer   0 assigned to device CUDA0, is_swa = 0`.
_PLACEMENT_RE = re.compile(
    r"^load_tensors:\s+layer\s+\d+\s+assigned to device\s+([^,\s]+)", re.MULTILINE
)


def parse_tensor_placement(text: str) -> dict[str, int]:
    """Count how many layers each device actually received.

    The enumeration line is not evidence of anything running. Measured on an RTX 5080 with
    `knaif-qwen3-4b-v2-q4_k_m.gguf` (2026-09-21)::

        default              -> enumerated CUDA0, placement {"CUDA0": 37}
        KNAIF_N_GPU_LAYERS=0 -> enumerated CUDA0, placement {"CPU": 37}

    Same enumerated device, opposite reality. Recording the enumerated name made those two runs
    look comparable when one never touched the GPU — and L3b's point is that a silent CPU->CUDA
    change between runs is indistinguishable from whatever the runs were meant to compare.

    Returns an empty mapping when the binary said nothing, which callers must treat as "unknown"
    rather than "CPU".
    """
    placement: dict[str, int] = {}
    for device in _PLACEMENT_RE.findall(text):
        placement[device] = placement.get(device, 0) + 1
    return placement


@dataclass(frozen=True)
class BackendMeasurement:
    """What a run's weights actually ran on, and what the binary merely enumerated.

    Two fields on purpose. `summary` is what gets saved as the scalar `compute_backend`, so
    records written before 2026-09-21 stay readable — but it is now DERIVED FROM PLACEMENT
    rather than copied from the enumeration line, which is why a CPU-only run stops recording
    "CUDA0".
    """

    placement: dict[str, int]
    enumerated: str | None

    @property
    def summary(self) -> str | None:
        """The device that ran the most layers; `None` when the binary did not say."""
        return summarize_placement(self.placement)

    @property
    def detail(self) -> str:
        """One console line: what ran where, and what was merely offered."""
        if not self.placement:
            return "UNKNOWN (the binary did not say)"
        layers = ", ".join(f"{d} {n}" for d, n in sorted(self.placement.items()))
        if self.enumerated and self.enumerated != self.summary:
            return f"{self.summary}  (layers: {layers}; enumerated: {self.enumerated})"
        return f"{self.summary}  (layers: {layers})"


def summarize_placement(placement: dict[str, int]) -> str | None:
    """The device that ran the most layers, for the one-line summary and the saved scalar.

    Ties break on name so the value is stable across runs; `None` when nothing is known.
    """
    if not placement:
        return None
    return max(sorted(placement), key=lambda device: placement[device])


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


def provision_fixtures(fixture_dir: Path, work_dir: Path) -> int:
    """Make **every** fixture visible in this utterance's work directory.

    Thin wrapper over the shared rule in `provisioning.provision_row_dir`, kept so existing
    callers and their tests keep working. Both lanes go through that one implementation now:
    when each had its own, they disagreed about what a command was run against, and the
    difference was scored against the runtime rather than against the harness.

    Python's executing verifiers ran the whole corpus in one shared sandbox, so every fixture
    was visible to every row. The lane used to copy only the fixtures an utterance *named*,
    which is stricter — `ffmpeg_084` plans `clip.mp4`, a real fixture, while the row declares
    `clip_4k.mp4`, so native was handed one file and failed on a missing input while Python
    "passed" the same plan only because it could see the whole directory.

    **Output isolation is untouched and is the property actually worth having**: each
    utterance still gets its own directory, so a file produced by one row can never satisfy
    another.

    **Copied, not hard-linked — changed 2026-09-12.** Linking avoided ~7 GB of duplication
    across 847 utterances, and cost correctness for it: every rendered command carries `-y`,
    so a plan whose output lands on a fixture's *name* wrote **through the link and corrupted
    the shared fixture for every later row**. Nothing would have caught it — `.cache.json`
    hashes the generation command, not the bytes — and every score after that point would have
    been measured against media nobody chose. See T5b of
    docs/plans/2026-09-11-reject-clarify-taxonomy.md.
    """
    return provision_row_dir(fixture_dir, work_dir)


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


def detect_backend(lane: LaneConfig, skill: str, cwd: Path) -> BackendMeasurement:
    """Ask the binary which compute backend it loads the weights onto.

    Measured with one `--dry-run --verbose` probe rather than taken from an environment
    variable, because the thing that matters is what the run *did*, and a variable records
    what someone believed. It matters at all for the reason L3b gives: greedy argmax over
    different FP accumulation flips near-ties, so a CPU→CUDA change between two runs is
    indistinguishable from whatever the runs were meant to compare.

    Returns a `BackendMeasurement` whose `placement` is empty when the binary did not say —
    an unanswered question, never a guess.
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
        return BackendMeasurement(placement={}, enumerated=None)
    text = f"{proc.stdout}\n{proc.stderr}"
    match = _DEVICE_RE.search(text)
    return BackendMeasurement(
        placement=parse_tensor_placement(text),
        enumerated=match.group(1) if match else None,
    )


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
            provision_fixtures(fixture_dir, work_dir)
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
    return {**os.environ, "KNAIF_DUMP_PLAN": "1"}
