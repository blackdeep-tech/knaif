"""Run a CommandAgent over a corpus; capture plan, artifact, and latency."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .chain import run_command_chain
from .corpus import CorpusRow

if TYPE_CHECKING:
    from knaif.agent import CommandAgent
    from knaif.registry import ToolDef


@dataclass
class AgentOutput:
    id: str
    utterance: str
    plan: dict[str, Any] | None
    artifact: str | None  # rendered command string, or None for clarify/reject/errors
    outcome: str  # "plan" | "clarify" | "reject" | "error" | "parse_error"
    latency_ms: float
    error: str | None = None
    parse_error: str | None = None  # set when the model produced invalid JSON/plan syntax
    execution_results: list[dict[str, Any]] = field(default_factory=list)
    artifact_path: Path | None = None
    # All produced output files, in plan order. Single-output rows hold one entry
    # (mirroring artifact_path); multi-output rows hold one per deliverable.
    artifact_paths: list[Path] = field(default_factory=list)
    utterance_idx: int = 0


def _extract_artifact(results: list[dict[str, Any]]) -> str | None:
    """Extract a rendered command string from handler execution results.

    Walks results in reverse so the last meaningful command wins (e.g. the
    full-encode step rather than the preview step in a two-phase workflow).
    """
    for result in reversed(results):
        r = result.get("result") or {}
        if not isinstance(r, dict):
            continue
        # cmd_run_preview / cmd_run_batch in dry_run → {"command": [...], ...}
        cmd = r.get("command")
        if isinstance(cmd, list) and cmd:
            return " ".join(str(a) for a in cmd)
        # cmd_run_batch dry_run → {"outputs": [{"command": [...]}], ...}
        outputs = r.get("outputs")
        if isinstance(outputs, list) and outputs:
            first_cmd = outputs[0].get("command") if isinstance(outputs[0], dict) else None
            if isinstance(first_cmd, list) and first_cmd:
                return " ".join(str(a) for a in first_cmd)
    return None


def _cmd_string(result: dict[str, Any]) -> str | None:
    """Extract a single command string from one handler result dict, or None."""
    cmd = result.get("command")
    if isinstance(cmd, list) and cmd:
        return " ".join(str(a) for a in cmd)
    outputs = result.get("outputs")
    if isinstance(outputs, list) and outputs:
        first_cmd = outputs[0].get("command") if isinstance(outputs[0], dict) else None
        if isinstance(first_cmd, list) and first_cmd:
            return " ".join(str(a) for a in first_cmd)
    return None


def _extract_artifacts(results: list[dict[str, Any]]) -> list[str]:
    """Extract one command per executed batch, in plan order.

    A multi-intent plan flattens into one ``run_batch``/``run_concat`` result per
    intent (each consuming the previous intent's output). Walks forward and
    collects each batch command so callers can run them as a dependent chain.
    Falls back to the last meaningful command (``_extract_artifact``) when no
    batch step is present, so single-intent rows still yield one command.
    """
    cmds: list[str] = []
    for result in results:
        if result.get("tool") not in ("run_batch", "run_concat"):
            continue
        r = result.get("result") or {}
        if not isinstance(r, dict):
            continue
        cmd = _cmd_string(r)
        if cmd:
            cmds.append(cmd)
    if not cmds:
        single = _extract_artifact(results)
        return [single] if single else []
    return cmds


def _build_registry_override(agent: Any, utterance: str) -> dict[str, ToolDef] | None:
    """Return a retrieved registry subset for *utterance*, or None if unavailable."""
    registry = getattr(agent, "registry", None)
    if not isinstance(registry, dict):
        return None
    from knaif.registry import retrieve_tools

    return retrieve_tools(utterance, registry)


def run_corpus(
    agent: CommandAgent,
    corpus: list[CorpusRow],
    *,
    limit: int | None = None,
    use_mock: bool = False,
    verbose: bool = False,
    execute: bool = False,
    sandbox: Path | None = None,
    fixture_dir: Path | None = None,
    apply_retrieval: bool = True,
) -> list[AgentOutput]:
    """Run each corpus row through the agent pipeline, returning AgentOutput objects.

    When execute=True, iterates all utterances per row, runs the agent in dry_run
    mode to get the command string, then executes it against the row's fixture file.
    sandbox and fixture_dir are required when execute=True.

    apply_retrieval controls whether retrieve_tools() is called per utterance and
    the result passed as registry_override to infer().  Defaults to True so the
    eval suite measures the same prompt the agent uses in production.  Pass
    apply_retrieval=False (or --no-retrieval on the CLI) to measure the full
    unfiltered prompt for diagnostic comparisons.
    """
    rows = corpus[:limit] if limit is not None else corpus
    outputs: list[AgentOutput] = []

    for row in rows:
        utterances = row.utterances if execute else [row.utterances[0]]

        for utt_idx, utterance in enumerate(utterances):
            t0 = time.perf_counter()
            plan_payload: dict[str, Any] | None = None
            exec_results: list[dict[str, Any]] = []
            outcome = "error"
            artifact: str | None = None
            error: str | None = None

            registry_override = (
                _build_registry_override(agent, utterance) if apply_retrieval else None
            )

            parse_error: str | None = None

            try:
                plan_payload = agent.infer(
                    utterance, use_mock=use_mock, registry_override=registry_override
                )
                # Expose parse failures as a distinct outcome so they don't hide
                # inside the normal clarify bucket.  Guard against mock agents
                # where the attribute is a MagicMock rather than a real string.
                _raw_pe = getattr(agent, "last_parse_error", None)
                parse_error = _raw_pe if isinstance(_raw_pe, str) and _raw_pe else None
                steps = (plan_payload or {}).get("plan") or []
                first_tool = steps[0]["tool"] if steps else None

                if parse_error:
                    outcome = "parse_error"
                elif first_tool in ("clarify", "reject"):
                    outcome = first_tool
                else:
                    try:
                        exec_results = agent.execute_plan(
                            plan_payload,
                            utterance=utterance,
                            dry_run=True,
                            confirmed=False,
                        )
                        # NL gate may have downgraded the plan to a clarify step.
                        if exec_results and exec_results[0].get("tool") == "clarify":
                            outcome = "clarify"
                        else:
                            artifact = _extract_artifact(exec_results)
                            # Plan-based skills (e.g. documents) don't render a
                            # command string, so _extract_artifact returns None.
                            # Fall back to the plan JSON so a plan-replaying
                            # artifact_runner can materialize the real output.
                            # ffmpeg is unaffected (its artifact is non-None).
                            if artifact is None and agent.artifact_runner is not None:
                                artifact = json.dumps(plan_payload)
                            outcome = "plan"
                    except Exception as exec_exc:  # noqa: BLE001
                        error = str(exec_exc)
                        outcome = "error"
            except Exception as exc:  # noqa: BLE001
                error = str(exc)

            latency_ms = (time.perf_counter() - t0) * 1000

            artifact_path: Path | None = None
            artifact_paths: list[Path] = []
            row_outputs = getattr(row, "outputs", None)
            if (
                execute
                and artifact
                and row.fixture
                and sandbox is not None
                and fixture_dir is not None
            ):
                _fp = fixture_dir / row.fixture
                if _fp.exists():
                    row_dir = sandbox / f"{row.id}__{utt_idx}"
                    row_dir.mkdir(parents=True, exist_ok=True)
                    if row_outputs:
                        # Multi-output: run the plan's batch commands as a chain so
                        # each intent's intermediate output materializes for the next.
                        commands = _extract_artifacts(exec_results)
                        chain = run_command_chain(commands, fixture_dir, row_dir)
                        artifact_paths = [
                            Path(r["output"]) for r in chain if Path(r["output"]).exists()
                        ]
                        artifact_path = artifact_paths[-1] if artifact_paths else None
                    elif agent.artifact_runner is not None:
                        try:
                            artifact_path = agent.artifact_runner(artifact, _fp, row_dir)
                        except Exception:  # noqa: BLE001
                            artifact_path = None
                        if artifact_path is not None:
                            artifact_paths = [artifact_path]

            output = AgentOutput(
                id=row.id,
                utterance=utterance,
                plan=plan_payload,
                artifact=artifact,
                outcome=outcome,
                latency_ms=latency_ms,
                error=error,
                parse_error=parse_error,
                execution_results=exec_results,
                artifact_path=artifact_path,
                artifact_paths=artifact_paths,
                utterance_idx=utt_idx,
            )
            outputs.append(output)

            if verbose:
                status = "OK  " if outcome == row.expected_outcome else "FAIL"
                print(
                    f"  [{row.id}]  {status}  {outcome:<8}  {latency_ms:6.0f}ms  {utterance[:55]}",
                    flush=True,
                )

    return outputs


def _find_fixture_extension(fixture_dir: Path, fixture_name: str) -> str | None:
    """Return the file extension for a fixture by scanning fixture_dir."""
    for path in fixture_dir.iterdir():
        if path.stem == fixture_name:
            return path.suffix
    return None
