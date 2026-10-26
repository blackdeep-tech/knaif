"""The exact-files check: after a run, the user's folder holds its inputs plus the deliverables.

**Why this is not part of the scored run.** The executing verifiers render a plan's commands
in dry run and replay the batch commands in a row folder (`runner.py`, `chain.py`). Handlers
never write there, so the scored run cannot see the two leftovers that only real execution
leaves: a preview clip (preview commands are not replayed) and a failed step's partial file
(the replay writes straight to the final name). The provisioning also wipes every row folder,
so no eval ever looked. This check instead **replays a recorded plan through the real execution
path**, `execute_plan(dry_run=False, confirmed=True)`, in a freshly provisioned folder. It needs
no model, so it can run over every row of a saved scoreboard.

**What counts as a deliverable.** An output that a step produced successfully and that no later
step consumed, or one the user named in the request (a file the user named is one they asked
for, even when a later step reads it). Everything else in the folder that is not an input is a
**leftover**: a chain intermediate, a preview clip, or a failed step's partial output.

**Known folders and alerts.** The replay points the known folders at ``<row>.known`` and the
alert recorder (``KNAIF_OS_SINK``) at ``<row>.alerts.jsonl``, both beside the row folder, so a
"save it in Downloads, beep when done" plan touches neither the user's Downloads nor their
desktop. A delivery there is reported as ``~/Downloads/clip.mp4``, a leftover there too, and the
recorded alerts as ``alerts``. A scoreboard stores the plan with its alert split off, so the
caller says whether the model asked for one (``alert=``) and the replay puts it back.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .provisioning import _media_files, provision_row_dir, row_alert_sink, row_known_dir


def _mentioned(name: str, utterance: str) -> bool:
    # One predicate for "the user named this file", shared with the agent's scratch routing.
    from knaif.agent import _mention_count

    return _mention_count(utterance, name.lower()) > 0


def _paths(value: Any) -> Iterable[str]:
    if isinstance(value, str) and value:
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                yield item


#: The step args that name what a documents step reads.
_INPUT_KEYS = ("input", "inputs", "image")


def _norm(path: str | Path, root: Path | None = None) -> Path:
    p = Path(path)
    return (p if p.is_absolute() or root is None else Path(root) / p).resolve()


def deliverables(
    results: list[dict[str, Any]],
    utterance: str,
    root: Path | None = None,
    *,
    routed: bool = False,
) -> set[Path]:
    """The outputs a run delivered: produced successfully and not consumed later, or named.

    Reads the handler results' ``outputs`` lists (``{input, output, returncode}`` per item, the
    shape every file-producing ffmpeg step returns). A ``preview_output`` is never a
    deliverable. A relative path resolves against *root*, the folder the run worked in.

    *routed*: the skill routes intermediates (``scratch_intermediates:``), so the run moved every
    one it did not keep to its scratch, and a consumed output the results place under *root* is
    one the agent kept on purpose (its keep rule, owner 2026-10-15). One it meant for the scratch
    that still lands in the folder stays a leftover.
    """
    produced: list[tuple[int, Path]] = []
    consumed: list[tuple[int, Path]] = []
    for idx, entry in enumerate(results):
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        for item in result.get("outputs") or []:
            if not isinstance(item, dict):
                continue
            for src in _paths(item.get("input")):
                consumed.append((idx, _norm(src, root)))
            rc = item.get("returncode")
            if rc not in (None, 0):
                continue
            for raw_out in _paths(item.get("output")):
                produced.append((idx, _norm(raw_out, root)))
        # documents' shape: inputs in the step's args, and the result names what it wrote
        # (`output`, or `outputs` as plain paths for a split). A failed step raises instead.
        if "outputs" not in result or all(isinstance(o, str) for o in result["outputs"] or []):
            args = entry.get("args")
            if not isinstance(args, dict):
                args = {}
            for key in _INPUT_KEYS:
                for src in _paths(args.get(key)):
                    consumed.append((idx, _norm(src, root)))
            if result.get("mode") != "dry_run":
                for raw_out in [*_paths(result.get("output")), *_paths(result.get("outputs"))]:
                    produced.append((idx, _norm(raw_out, root)))

    here = _norm(root) if routed and root is not None else None
    delivered: set[Path] = set()
    for idx, out in produced:
        used_later = any(c_idx > idx and c == out for c_idx, c in consumed)
        kept = here is not None and out.is_relative_to(here)
        if not used_later or kept or _mentioned(out.name, utterance):
            delivered.add(out)
    return delivered


def leftovers(folder: Path, *, inputs: set[str], delivered: set[Path]) -> list[str]:
    """Files in *folder* (recursive, POSIX-relative) that are neither inputs nor deliverables."""
    folder = folder.resolve()
    keep = {_norm(p) for p in delivered}
    out: list[str] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if rel in inputs or path.resolve() in keep:
            continue
        out.append(rel)
    return out


#: Outcomes whose recorded plan executed something, and so could have left a file behind.
#: `error` is included on purpose: a failed step is where a partial file is most likely.
_EXECUTED = ("plan", "error")


def select_rows(
    rows: list[dict[str, Any]],
    *,
    preview_tools: set[str] = frozenset(),  # type: ignore[assignment]
    everything: bool = False,
) -> list[dict[str, Any]]:
    """The scoreboard rows worth replaying: chains and previews, or every executed row.

    A chain is a recorded plan with two or more non-terminal steps. A preview is a step with
    ``preview: true``, or one of *preview_tools* (tools that preview unless told not to; the
    skill knows which, so the caller passes them).
    """
    terminal = {"clarify", "reject", "done"}
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("actual_outcome") not in _EXECUTED:
            continue
        steps = [
            s for s in ((row.get("plan") or {}).get("plan") or []) if s.get("tool") not in terminal
        ]
        if not steps:
            continue
        previews = any(
            s.get("tool") in preview_tools or (s.get("args") or {}).get("preview") is True
            for s in steps
        )
        if everything or len(steps) >= 2 or previews:
            out.append(row)
    return out


#: The alert tool a scoreboard's plan had split off (``planner.split_alert``).
_ALERT_TOOL = "alert_when_done"


def read_alerts(sink: Path) -> list[dict[str, Any]]:
    """The alerts recorded in *sink* (``KNAIF_OS_SINK``), one JSON object per line."""
    import json

    if not sink.is_file():
        return []
    return [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines() if line]


def alert_problem(report: dict[str, Any], *, planned: bool) -> str | None:
    """What is wrong with a replay's alerts, or None.

    A plan that asked for an alert records exactly one, ``failed`` when the replay failed and
    ``done`` otherwise; a plan that did not ask records none.
    """
    alerts = report.get("alerts") or []
    if not planned:
        return f"an alert that was not asked for: {alerts[0].get('outcome')}" if alerts else None
    if not alerts:
        return "no alert"
    if len(alerts) > 1:
        return f"{len(alerts)} alerts, not one"
    want = "failed" if report.get("error") else "done"
    got = alerts[0].get("outcome")
    return None if got == want else f"the alert said {got}, the run {want}"


def _known_view(paths: Iterable[str]) -> list[str]:
    # As the user sees it: the known folders' root is their home.
    return [f"~/{p}" for p in paths]


def replay_row(
    make_agent: Callable[[Path], Any],
    plan: dict[str, Any],
    utterance: str,
    fixture_dir: Path,
    row_dir: Path,
    *,
    alert: bool = False,
) -> dict[str, Any]:
    """Execute *plan* for real in a fresh copy of the fixtures and report what it left behind.

    *make_agent* builds an agent rooted at the given folder. Returns
    ``{delivered, leftovers, alerts, error}``; a plan that raises is reported, not re-raised,
    because a failure is exactly when a partial file is most likely to be left. *alert* puts
    the plan's alert back before the replay (see the module docstring).
    """
    import copy

    from knaif.alert import SINK_ENV
    from knaif.known_folders import ROOT_ENV

    from .runner import _env

    fixture_dir = Path(fixture_dir)
    provision_row_dir(fixture_dir, row_dir, fresh=True)
    inputs = {p.relative_to(fixture_dir).as_posix() for p in _media_files(fixture_dir)}
    known, sink = row_known_dir(row_dir), row_alert_sink(row_dir)
    payload = copy.deepcopy(plan)
    if alert:
        payload["plan"] = [*(payload.get("plan") or []), {"tool": _ALERT_TOOL, "args": {}}]
    agent = make_agent(row_dir)
    error: str | None = None
    results: list[dict[str, Any]] = []
    try:
        with _env(ROOT_ENV, str(known)), _env(SINK_ENV, str(sink)):
            results = agent.execute_plan(
                payload, utterance=utterance, dry_run=False, confirmed=True
            )
    except Exception as exc:  # noqa: BLE001 — reported, see docstring
        error = f"{type(exc).__name__}: {exc}"
    # A replay that stops before running (a clarify, a reject, a declined approval) ran
    # nothing, so it cannot vouch for the folder: report it, never as clean.
    first = results[0] if results else {}
    status = (first.get("result") or {}).get("status")
    if error is None and (first.get("tool") in ("clarify", "reject") or status == "declined"):
        said = first.get("args") or {}
        reason = said.get("question") or said.get("reason") or said.get("prompt") or first
        error = f"the replay stopped before running: {reason}"
    routed = getattr(agent, "scratch_intermediates", False) is True
    return _report(
        deliverables(results, utterance, root=row_dir, routed=routed), row_dir, inputs, error
    )


def _report(
    delivered: set[Path], row_dir: Path, inputs: set[str], error: str | None
) -> dict[str, Any]:
    """A replay's report: deliveries and leftovers in the row folder and in its known folders
    (as ``~/…``), and the alerts recorded in its sink."""
    known, sink = row_known_dir(row_dir), row_alert_sink(row_dir)
    in_known = sorted(
        p.relative_to(known.resolve()).as_posix() for p in delivered if known.resolve() in p.parents
    )
    return {
        "delivered": sorted(
            p.relative_to(row_dir.resolve()).as_posix()
            for p in delivered
            if row_dir.resolve() in p.parents
        )
        + _known_view(in_known),
        "leftovers": leftovers(row_dir, inputs=inputs, delivered=delivered)
        + (
            _known_view(leftovers(known, inputs=set(), delivered=delivered))
            if known.is_dir()
            else []
        ),
        "alerts": read_alerts(sink),
        "error": error,
    }


#: Line prefix the native binary prints, under `KNAIF_DUMP_PLAN`, before each rendered argv.
ARGV_DUMP_MARKER = "===KNAIF-ARGV==="


def native_results(stdout: str, stderr: str, row_dir: Path) -> list[dict[str, Any]]:
    """The binary's output as the handler-results shape :func:`deliverables` reads.

    Each `KNAIF_DUMP_PLAN` argv line is one command: its `-i` arguments are what it read and its
    last argument what it wrote. A command delivered when the plain view printed `✓ <output>`
    for it. Relative paths resolve against *row_dir*, the binary's working directory.
    """
    import json

    ticked = {
        line.strip()[1:].strip() for line in stdout.splitlines() if line.strip().startswith("✓")
    }

    def absolute(p: str) -> str:
        # Resolved, so a caller's relative row folder is applied once (not again as `root`).
        return str(Path(p) if Path(p).is_absolute() else Path(row_dir).resolve() / p)

    results: list[dict[str, Any]] = []
    for line in stderr.splitlines():
        if not line.startswith(ARGV_DUMP_MARKER):
            continue
        argv = json.loads(line[len(ARGV_DUMP_MARKER) :])
        if not argv:
            continue
        inputs = [absolute(argv[i + 1]) for i, tok in enumerate(argv[:-1]) if tok == "-i"]
        out = argv[-1]
        results.append(
            {
                "tool": "native",
                "result": {
                    "outputs": [
                        {
                            "input": inputs,
                            "output": absolute(out),
                            "returncode": 0 if out in ticked else 1,
                        }
                    ]
                },
            }
        )
    return results or _native_documents_results(stdout, stderr)


#: Line prefix of the plan the binary dumps under `KNAIF_DUMP_PLAN` (planned, before routing).
PLAN_DUMP_MARKER = "===KNAIF-PLAN==="
#: Line prefix of the plan the binary runs when a batch was lifted: one step per
#: file, which the `step N of M:` headers count. Read in place of the model's plan.
RUN_PLAN_DUMP_MARKER = "===KNAIF-RUN-PLAN==="


def _native_documents_results(stdout: str, stderr: str) -> list[dict[str, Any]]:
    """A skill that renders no argv (documents): each step's inputs come from the dumped plan,
    and what it wrote from the `✓ wrote <path>` lines under its `step N of M:` header (a single
    step prints no header). Paths stay as printed; :func:`deliverables` resolves relative ones.
    A routed intermediate (`<work>/…`) is in the scratch, never the row folder, so it is skipped."""
    import json
    import re

    steps: list[dict[str, Any]] = []
    lifted = False
    for line in stderr.splitlines():
        marker = next(
            (m for m in (RUN_PLAN_DUMP_MARKER, PLAN_DUMP_MARKER) if line.startswith(m)), None
        )
        if marker is None or (lifted and marker == PLAN_DUMP_MARKER):
            continue
        payload = json.loads(line[len(marker) :])
        steps = [s for s in payload.get("plan") or [] if isinstance(s, dict)]
        lifted = lifted or marker == RUN_PLAN_DUMP_MARKER
    if not steps:
        return []
    header = re.compile(r"^step (\d+) of \d+:$")
    written: dict[int, list[str]] = {}
    current = 1
    for line in stdout.splitlines():
        text = line.strip()
        if m := header.match(text):
            current = int(m.group(1))
        elif text.startswith("✓ wrote "):
            path = text[len("✓ wrote ") :].strip()
            if not path.startswith("<work>/"):  # a routed intermediate: in the scratch
                written.setdefault(current, []).append(path)
    return [
        {
            "tool": step.get("tool"),
            "args": step.get("args") or {},
            "result": {"outputs": written.get(idx, [])},
        }
        for idx, step in enumerate(steps, start=1)
    ]


def native_error_excerpt(returncode: int, stderr: str, stdout: str) -> str:
    """``exit N: <the binary's last lines>``, its plan and argv dumps left out: they carry
    absolute run-scratch paths (a home folder) and are not the error."""
    markers = (PLAN_DUMP_MARKER, RUN_PLAN_DUMP_MARKER, ARGV_DUMP_MARKER)
    text = "\n".join(
        line for line in (stderr or stdout).splitlines() if not line.startswith(markers)
    )
    return f"exit {returncode}: {text.strip()[-300:]}"


def replay_row_native(
    binary: Path,
    skill: str,
    plan: dict[str, Any],
    utterance: str,
    fixture_dir: Path,
    row_dir: Path,
    *,
    timeout: int = 600,
    alert: bool = False,
    routed: bool = False,
) -> dict[str, Any]:
    """:func:`replay_row` for the native runtime: the real binary, no model.

    The mock backend returns `$KNAIF_LLM_MOCK_RESPONSE` verbatim, so the recorded plan goes in
    as the "model output" and the binary runs everything after inference for real, exactly as
    the eval lane invokes it (`run <skill> --yes --overwrite -- <utterance>`, cwd = the row).
    *alert*, the known folders and the sink are as in :func:`replay_row`; *routed* is the
    skill's ``scratch_intermediates:`` (:func:`deliverables`).
    """
    import json
    import os
    import subprocess

    from knaif.alert import SINK_ENV
    from knaif.known_folders import ROOT_ENV

    fixture_dir = Path(fixture_dir)
    provision_row_dir(fixture_dir, row_dir, fresh=True)
    inputs = {p.relative_to(fixture_dir).as_posix() for p in _media_files(fixture_dir)}
    payload = dict(plan)
    if alert:
        payload["plan"] = [*(plan.get("plan") or []), {"tool": _ALERT_TOOL, "args": {}}]
    env = {k: v for k, v in os.environ.items() if k != "KNAIF_PDFIUM_PATH"}
    env.update(
        KNAIF_LLM_BACKEND="mock",
        KNAIF_LLM_MOCK_RESPONSE=json.dumps(payload),
        KNAIF_DUMP_PLAN="1",
    )
    # Absolute: the binary runs in the row folder, and the CLI hands a relative one.
    env[ROOT_ENV] = str(row_known_dir(row_dir).resolve())
    env[SINK_ENV] = str(row_alert_sink(row_dir).resolve())
    # `--allow-download` only for a URL row: a binary older than URL inputs rejects the flag.
    url = ["--allow-download"] if "://" in utterance else []
    argv = [str(binary), "run", skill, "--yes", "--overwrite", *url, "--", *utterance.split()]
    error: str | None = None
    try:
        proc = subprocess.run(
            argv,
            cwd=row_dir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        stdout, stderr = proc.stdout, proc.stderr
        if proc.returncode != 0:
            error = native_error_excerpt(proc.returncode, stderr, stdout)
    except subprocess.TimeoutExpired:
        stdout, stderr, error = "", "", f"timeout after {timeout}s"
    delivered = deliverables(
        native_results(stdout, stderr, row_dir), utterance, root=row_dir, routed=routed
    )
    return _report(delivered, row_dir, inputs, error)
