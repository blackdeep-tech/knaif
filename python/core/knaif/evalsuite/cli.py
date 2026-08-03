"""CLI entry point: uv run -m knaif.evalsuite <subcommand>"""

from __future__ import annotations

import argparse
import difflib
import functools
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from knaif import list_skills
from knaif._console import enable_utf8_console

from .runner import run_corpus


def _skill_artifact_runner(skill_name: str) -> Any | None:
    """Load *skill_name* and return its ARTIFACT_RUNNER (or None if missing)."""
    from knaif.skill import Skill

    skill_dir = Path("skills") / skill_name
    if not skill_dir.exists():
        return None
    return Skill.load(skill_dir).artifact_runner


def _execute_against_fixture(
    command_str: str,
    fixture_path: Path,
    out_dir: Path,
    *,
    skill: str = "ffmpeg",
) -> Path | None:
    """Re-execute a rendered artifact via the *skill*'s ARTIFACT_RUNNER hook.

    Backward-compat wrapper. Newer code should prefer ``agent.artifact_runner``.
    """
    runner = _skill_artifact_runner(skill)
    if runner is None:
        return None
    result: Path | None = runner(command_str, fixture_path, out_dir)
    return result


# ── helpers ──────────────────────────────────────────────────────────────────


def _load_skill_fixtures(skill: str) -> tuple[dict[str, str], dict[str, str]]:
    """Import FIXTURES and FIXTURE_EXTENSIONS from the skill's eval/fixtures.py."""
    skill_dir = Path("skills") / skill
    fixtures_path = skill_dir / "eval" / "fixtures.py"
    if not fixtures_path.exists():
        sys.exit(f"No fixtures.py found at {fixtures_path}.")
    spec = importlib.util.spec_from_file_location(f"_evalsuite_{skill}_fixtures", fixtures_path)
    if spec is None or spec.loader is None:
        sys.exit(f"Cannot load {fixtures_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "FIXTURES", {}), getattr(mod, "FIXTURE_EXTENSIONS", {})


def _load_skill_verifiers(skill: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Import VERIFIERS and VERIFIER_PREFLIGHT from the skill's eval/verifiers.py."""
    skill_dir = Path("skills") / skill
    verifiers_path = skill_dir / "eval" / "verifiers.py"
    if not verifiers_path.exists():
        sys.exit(f"No verifiers found at {verifiers_path}. Run the bootstrap playbook first.")
    spec = importlib.util.spec_from_file_location(f"_evalsuite_{skill}_verifiers", verifiers_path)
    if spec is None or spec.loader is None:
        sys.exit(f"Cannot load {verifiers_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "VERIFIERS", {}), getattr(mod, "VERIFIER_PREFLIGHT", {})


def _load_backends(config_path: Path) -> dict[str, Any]:
    with config_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("backends") or {}


MOCK_BACKEND = "mock"


def _resolve_backends(
    config_path: Path | None,
    backends_arg: str | None,
    *,
    allow_mock_fallback: bool = True,
) -> dict[str, Any]:
    """Resolve ``--config`` / ``--backends`` into a ``{name: cfg}`` map.

    ``mock`` is always addressable by name and resolves to ``None`` (no orchestrator),
    whether or not the config file lists it. An unknown name is a hard error: it used
    to be dropped silently, which left an empty selection that fell through to a mock
    run — so a typo produced real-looking numbers for a model that never ran.
    """
    all_backends: dict[str, Any] = {}
    if config_path and config_path.exists():
        all_backends = _load_backends(config_path)

    if backends_arg:
        chosen = [b.strip() for b in backends_arg.split(",") if b.strip()]
        unknown = [n for n in chosen if n != MOCK_BACKEND and n not in all_backends]
        if unknown:
            known = sorted({*all_backends, MOCK_BACKEND})
            lines = [f"ERROR: unknown backend(s): {', '.join(unknown)}"]
            for name in unknown:
                near = difflib.get_close_matches(name, known, n=3, cutoff=0.6)
                if near:
                    lines.append(f"       did you mean: {', '.join(near)}?")
            lines.append(f"       {len(known)} backends are defined in {config_path}")
            sys.exit("\n".join(lines))
        return {n: (None if n == MOCK_BACKEND else all_backends[n]) for n in chosen}

    if all_backends:
        return all_backends

    if allow_mock_fallback:
        print("No backends configured — falling back to the mock backend.", flush=True)
        return {MOCK_BACKEND: None}
    return {}


def _make_agent(skill: str, sandbox: Path, backend_cfg: dict[str, Any] | None) -> Any:
    from knaif import create_agent

    if not backend_cfg:
        return create_agent(skill, sandbox=sandbox)

    from knaif.orchestrator import InferenceOrchestrator

    orch = InferenceOrchestrator(
        backend=backend_cfg["backend"],
        model_config=backend_cfg.get("options"),
        model_path=backend_cfg.get("model_path"),
        ollama_url=backend_cfg.get("ollama_url", "http://localhost:11434"),
        model_name=backend_cfg.get("model"),
    )
    return create_agent(skill, sandbox=sandbox, orchestrator=orch)


def _corpus_path(skill: str) -> Path:
    return Path("skills") / skill / "data" / "eval.jsonl"


def _snapshot_path(skill: str) -> Path:
    return Path("skills") / skill / "data" / "eval_snapshot.json"


def _default_fixture_dir(sandbox: Path | str, skill: str) -> Path:
    """Return the default generated fixture directory for *skill*."""
    return Path(sandbox) / "fixtures" / skill


# ── subcommands ───────────────────────────────────────────────────────────────


def cmd_seed_baselines(args: argparse.Namespace) -> None:
    from .corpus import load_corpus, save_corpus
    from .runner import _extract_artifact

    corpus_path = Path(args.corpus) if args.corpus else _corpus_path(args.skill)
    if not corpus_path.exists():
        sys.exit(f"Corpus not found: {corpus_path}")

    corpus = load_corpus(corpus_path)
    rows_to_seed = corpus[: args.limit] if args.limit else corpus

    sandbox = Path(args.sandbox) if args.sandbox else Path("sandbox")
    sandbox.mkdir(parents=True, exist_ok=True)

    config_path = Path(args.config) if args.config else None
    resolved = _resolve_backends(config_path, args.backends, allow_mock_fallback=False)
    backend_cfg: dict[str, Any] | None = next(
        (cfg for cfg in resolved.values() if cfg is not None), None
    )

    agent = _make_agent(args.skill, sandbox, backend_cfg)

    changed = 0
    for row in rows_to_seed:
        existing_command = (row.baseline or {}).get("command")
        validated_by = (row.baseline or {}).get("validated_by")

        # Never overwrite human-validated rows; skip clarify/reject always
        if validated_by:
            continue
        if row.expected_outcome in ("clarify", "reject"):
            continue
        if row.expected_tool in ("clarify", "reject"):
            continue
        if existing_command and not args.force:
            continue

        try:
            plan_payload = agent.infer(row.utterances[0], use_mock=False)
        except Exception as exc:  # noqa: BLE001
            print(f"  [skip] {row.id}: infer failed: {exc}", flush=True)
            continue

        steps = (plan_payload or {}).get("plan") or []
        first_tool = steps[0]["tool"] if steps else None
        if first_tool in ("clarify", "reject", None):
            print(f"  [skip] {row.id}: outcome={first_tool}", flush=True)
            continue

        try:
            exec_results = agent.execute_plan(plan_payload, dry_run=True, confirmed=False)
        except Exception as exc:  # noqa: BLE001
            print(f"  [skip] {row.id}: execute failed: {exc}", flush=True)
            continue

        artifact = _extract_artifact(exec_results)
        if not artifact:
            print(f"  [skip] {row.id}: no command extracted", flush=True)
            continue

        # Strip the sandbox absolute path so baselines are platform-independent.
        # The skill's ARTIFACT_RUNNER replaces -i and rewrites the output path at
        # runtime, so only relative filenames need to survive in the corpus.
        # Utterance-specific filenames (e.g. "interview.mp4") are kept intentionally —
        # the extension signals the source format and helps the model reason correctly.
        sandbox_prefix = str(sandbox.resolve()).replace("\\", "/") + "/"
        artifact = artifact.replace("\\", "/").replace(sandbox_prefix, "")

        row.baseline = {**(row.baseline or {}), "command": artifact}
        print(f"  [seed] {row.id}: {artifact[:80]}", flush=True)
        changed += 1

    if changed:
        save_corpus(corpus, corpus_path)
        print(f"\nSeeded {changed} row(s) → {corpus_path}", flush=True)
    else:
        print("\nNothing to seed.", flush=True)


def _parse_entry_name(name: str) -> tuple[str, int] | None:
    """Parse '<row_id>__<utt_idx>' → (row_id, utt_idx), or None if not matching."""
    if "__" not in name:
        return None
    parts = name.rsplit("__", 1)
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def _find_output_file(entry_dir: Path) -> Path | None:
    """Return the first non-.txt, non-.json file in entry_dir, or None."""
    for p in entry_dir.iterdir():
        if p.is_file() and p.suffix not in (".txt", ".json"):
            return p
    return None


def _read_meta_json(entry_dir: Path) -> dict[str, Any]:
    """Read meta.json from an external agent's results entry, or return {}."""
    meta_path = entry_dir / "meta.json"
    if not meta_path.exists():
        return {}
    try:
        data: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _read_cmd_txt(entry_dir: Path) -> str | None:
    """Read the external agent's cmd.txt (the exact command), or None if absent.

    The success verifier grades command-text criteria (filters/flags/encoder) against
    the artifact *string*; the local runner passes the command there, so score-external
    must feed cmd.txt in the same way to keep grading apples-to-apples.
    """
    cmd_path = entry_dir / "cmd.txt"
    if not cmd_path.exists():
        return None
    try:
        return cmd_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _detect_external_outcome(entry_dir: Path) -> str:
    """Detect the outcome from cmd.txt: plan/clarify/reject/error."""
    cmd_path = entry_dir / "cmd.txt"
    if not cmd_path.exists():
        return "error"
    text = cmd_path.read_text(encoding="utf-8").strip()
    if text.lower().startswith("clarify:"):
        return "clarify"
    if text.lower().startswith("reject:"):
        return "reject"
    return "plan"


def cmd_score_external(args: argparse.Namespace) -> None:
    """Score external agent results and emit a local-shaped scoreboard.

    Reads each results/<row_id>__<idx>/ entry, runs the success verifier on
    the produced output file, reads elapsed_ms from meta.json, and emits
    score.json in the SAME schema as a local `run --save` scoreboard so
    local and premium arms are directly comparable in the report.

    Also emits a backward-compatible `entries` list at the top level.
    """
    from .corpus import load_corpus
    from .scoring import _latency_aggregate, _mark_warmup

    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        sys.exit(f"Results dir not found: {results_dir}")

    corpus_path = Path(args.corpus) if args.corpus else _corpus_path(args.skill)
    if not corpus_path.exists():
        sys.exit(f"Corpus not found: {corpus_path}")

    corpus = load_corpus(corpus_path)
    rows_by_id = {r.id: r for r in corpus}

    verifiers, _ = _load_skill_verifiers(args.skill)
    # Prefer success verifier (absolute spec); fall back to output_diff
    verifier_fn = verifiers.get("success") or verifiers.get("output_diff")

    sandbox = Path(getattr(args, "sandbox", None) or "sandbox")
    fixture_dir = (
        Path(args.fixture_dir)
        if getattr(args, "fixture_dir", None)
        else _default_fixture_dir(sandbox, args.skill)
    )
    baselines_dir = results_dir / ".baselines"

    scored_rows: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []  # backward-compat

    for entry_dir in sorted(results_dir.iterdir()):
        if not entry_dir.is_dir() or entry_dir.name.startswith("."):
            continue

        parsed = _parse_entry_name(entry_dir.name)
        if parsed is None:
            continue
        row_id, utt_idx = parsed

        row = rows_by_id.get(row_id)
        if row is None:
            continue

        meta = _read_meta_json(entry_dir)
        latency_ms: float | None = float(meta["elapsed_ms"]) if "elapsed_ms" in meta else None

        actual_outcome = _detect_external_outcome(entry_dir)

        # For clarify/reject rows, no output file is expected
        if actual_outcome in ("clarify", "reject"):
            outcome_correct = actual_outcome == row.expected_outcome
            scored_rows.append(
                {
                    "id": row_id,
                    "utterance": row.utterances[utt_idx] if utt_idx < len(row.utterances) else "",
                    "utterance_idx": utt_idx,
                    "expected_outcome": row.expected_outcome,
                    "actual_outcome": actual_outcome,
                    "outcome_correct": outcome_correct,
                    "tags": row.tags,
                    "latency_ms": latency_ms,
                    "error": None,
                    "artifact": None,
                    "artifact_path": None,
                    "knaif_score": None,
                    "knaif_matched": [],
                    "knaif_failed": [],
                    "verifier_kind": None,
                    "baseline_score": None,
                }
            )
            continue

        artifact_path = _find_output_file(entry_dir)
        if artifact_path is None:
            scored_rows.append(
                {
                    "id": row_id,
                    "utterance": row.utterances[utt_idx] if utt_idx < len(row.utterances) else "",
                    "utterance_idx": utt_idx,
                    "expected_outcome": row.expected_outcome,
                    "actual_outcome": "error",
                    "outcome_correct": False,
                    "tags": row.tags,
                    "latency_ms": latency_ms,
                    "error": "no_output_file",
                    "artifact": None,
                    "artifact_path": None,
                    "knaif_score": None,
                    "knaif_matched": [],
                    "knaif_failed": ["no_output_file"],
                    "verifier_kind": None,
                    "baseline_score": None,
                }
            )
            continue

        outcome_correct = actual_outcome == row.expected_outcome

        # Grade with the success verifier against success_criteria
        score: float | None = None
        matched: list[str] = []
        failed: list[str] = []
        verifier_kind: str | None = None

        if verifier_fn:
            criteria = getattr(row, "success_criteria", {}) or {}
            if getattr(row, "grade", "full") == "routing":
                criteria = {**criteria, "grade": "routing"}

            _artifact_path_ref = artifact_path
            _artifact_cmd = _read_cmd_txt(entry_dir)

            class _FakeOutput:
                artifact = _artifact_cmd
                artifact_path = _artifact_path_ref

            try:
                result = verifier_fn(_FakeOutput(), criteria, sandbox)
                score = result.score
                matched = result.matched
                failed = result.failed
                verifier_kind = result.verifier_kind
            except Exception as exc:  # noqa: BLE001
                failed = [str(exc)]

        # Backward-compat entries entry
        baseline_path: Path | None = None
        baseline_cmd = (row.baseline or {}).get("command")
        if baseline_cmd and row.fixture and fixture_dir.exists():
            _fp = fixture_dir / row.fixture
            fixture_file: Path | None = _fp if _fp.exists() else None
            if fixture_file is not None:
                out_dir = baselines_dir / row_id
                baseline_path = _execute_against_fixture(
                    baseline_cmd, fixture_file, out_dir, skill=args.skill
                )

        entries.append(
            {
                "id": row_id,
                "utterance_idx": utt_idx,
                "entry_dir": str(entry_dir),
                "artifact_path": str(artifact_path),
                "baseline_path": str(baseline_path) if baseline_path else None,
                "score": score,
                "matched": matched,
                "failed": failed,
            }
        )

        scored_rows.append(
            {
                "id": row_id,
                "utterance": row.utterances[utt_idx] if utt_idx < len(row.utterances) else "",
                "utterance_idx": utt_idx,
                "expected_outcome": row.expected_outcome,
                "actual_outcome": actual_outcome,
                "outcome_correct": outcome_correct,
                "tags": row.tags,
                "latency_ms": latency_ms,
                "error": None,
                "artifact": str(artifact_path),
                "artifact_path": str(artifact_path),
                "knaif_score": score,
                "knaif_matched": matched,
                "knaif_failed": failed,
                "verifier_kind": verifier_kind,
                "baseline_score": None,
            }
        )

    _mark_warmup(scored_rows)

    n = len(scored_rows)
    outcome_acc = sum(1 for r in scored_rows if r["outcome_correct"]) / n if n else 0.0
    scored_with_verify = [r for r in scored_rows if r["knaif_score"] is not None]
    avg_knaif: float | None = (
        sum(r["knaif_score"] for r in scored_with_verify) / len(scored_with_verify)
        if scored_with_verify
        else None
    )

    by_tag: dict[str, Any] = {}
    for r in scored_rows:
        for tag in r["tags"] or ["untagged"]:
            by_tag.setdefault(
                tag, {"total": 0, "outcome_correct": 0, "knaif_scores": [], "rows": []}
            )
            by_tag[tag]["total"] += 1
            if r["outcome_correct"]:
                by_tag[tag]["outcome_correct"] += 1
            if r["knaif_score"] is not None:
                by_tag[tag]["knaif_scores"].append(r["knaif_score"])
            by_tag[tag]["rows"].append(r)

    tag_summary = {
        tag: {
            "total": d["total"],
            "outcome_accuracy": d["outcome_correct"] / d["total"],
            "avg_knaif_score": (
                sum(d["knaif_scores"]) / len(d["knaif_scores"]) if d["knaif_scores"] else None
            ),
            "time_to_artifact_ms": _latency_aggregate(d["rows"]),
        }
        for tag, d in by_tag.items()
    }

    scoreboard = {
        "verifier": "success",
        "total": n,
        "outcome_accuracy": outcome_acc,
        "avg_knaif_score": avg_knaif,
        "avg_baseline_score": None,
        "time_to_artifact_ms": _latency_aggregate(scored_rows),
        "intent_metrics": {},
        "by_tag": tag_summary,
        "rows": scored_rows,
        # backward-compat
        "entries": entries,
    }

    score_file = results_dir / "score.json"
    score_file.write_text(json.dumps(scoreboard, indent=2, ensure_ascii=False), encoding="utf-8")
    avg_str = f"{avg_knaif:.3f}" if avg_knaif is not None else "n/a"
    print(f"Scored {n} entries, avg={avg_str} → {score_file}", flush=True)


def _load_reviewer(skill: str) -> Any:
    """Import REVIEWER class from the skill's eval/reviewer.py, or None if absent."""
    reviewer_path = Path("skills") / skill / "eval" / "reviewer.py"
    if not reviewer_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"_evalsuite_{skill}_reviewer", reviewer_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    reviewer_cls = getattr(mod, "REVIEWER", None)
    if reviewer_cls is None:
        return None
    try:
        return reviewer_cls()
    except Exception:
        return None


def cmd_review(args: argparse.Namespace) -> None:
    from .review_log import load_review_log, save_review_log

    log_path = Path(args.log)
    log = load_review_log(log_path)
    log.mark(
        row_id=args.row,
        utterance_idx=args.utterance_idx,
        status=args.status,
        notes=args.notes or "",
    )
    save_review_log(log, log_path)
    print(f"[{args.status}] {args.row}__{args.utterance_idx} → {log_path}", flush=True)


def cmd_report(args: argparse.Namespace) -> None:
    from .corpus import load_corpus
    from .report import (
        ArmEntry,
        discover_arms,
        load_arm_entries,
        render_report_html,
        render_report_md,
    )
    from .review_log import load_review_log

    results_dir = Path(args.results_dir)
    corpus_path = Path(args.corpus) if args.corpus else _corpus_path(args.skill)
    corpus = load_corpus(corpus_path) if corpus_path.exists() else []
    rows_by_id = {r.id: r for r in corpus}

    # Legacy: single score.json at root (score-external output placed directly in results_dir)
    score_file = results_dir / "score.json"
    if score_file.exists():
        arm_name = results_dir.name
        _, entries = load_arm_entries(score_file, corpus, args.skill)
        arms: dict[str, list[ArmEntry]] = {arm_name: entries}
    else:
        arms = discover_arms(results_dir, corpus, args.skill)
        if not arms:
            sys.exit(
                f"No score files found in {results_dir}. "
                "Run 'evalsuite score-external' or 'evalsuite run' first."
            )

    # Review log
    log_path_arg = getattr(args, "review_log", None)
    review_log_path = Path(log_path_arg) if log_path_arg else results_dir / "review_log.json"
    review_log = load_review_log(review_log_path)

    reviewer = _load_reviewer(args.skill)

    md = render_report_md(arms, rows_by_id, review_log)
    html = render_report_html(arms, rows_by_id, review_log, reviewer, report_dir=results_dir)

    (results_dir / "report.md").write_text(md, encoding="utf-8")
    (results_dir / "report.html").write_text(html, encoding="utf-8")
    print(
        f"Report written to {results_dir / 'report.md'} and {results_dir / 'report.html'}",
        flush=True,
    )


def _build_baseline_outputs(
    corpus: list[Any],
    outputs: list[Any],
    fixture_dir: Path,
    baselines_dir: Path,
    *,
    skill: str = "ffmpeg",
) -> dict[str, Path]:
    """Run each row's baseline.command against its fixture; return {row_id: output_path}."""
    rows_by_id = {r.id: r for r in corpus}
    baseline_paths: dict[str, Path] = {}

    for output in outputs:
        row = rows_by_id.get(output.id)
        if row is None:
            continue
        baseline_cmd = (row.baseline or {}).get("command")
        if not baseline_cmd or not row.fixture:
            continue
        if output.id in baseline_paths:
            continue  # already generated for this row

        # Find the fixture file
        _fp2 = fixture_dir / row.fixture
        fixture_file: Path | None = _fp2 if (fixture_dir.exists() and _fp2.exists()) else None
        if fixture_file is None:
            continue

        out_dir = baselines_dir / row.id
        result = _execute_against_fixture(baseline_cmd, fixture_file, out_dir, skill=skill)
        if result is not None:
            baseline_paths[row.id] = result

    return baseline_paths


def cmd_run(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    """Evaluate a single skill and return ``{backend_name: scoreboard}``.

    The return value lets the all-skills sweep (:func:`cmd_run_all_skills`)
    collect each skill's scoreboards without a file round-trip; the standalone
    CLI path ignores it.
    """
    from .corpus import load_corpus
    from .report import print_scoreboard, save_scoreboard_json
    from .scoring import score_corpus, score_corpus_output_diff
    from .snapshot import save_snapshot

    if not getattr(args, "skill", None) or args.skill == "all":
        sys.exit("run requires --skill NAME (or use --all-skills to sweep every skill)")

    corpus_path = Path(args.corpus) if getattr(args, "corpus", None) else _corpus_path(args.skill)
    if not corpus_path.exists():
        sys.exit(f"Corpus not found: {corpus_path}")

    corpus = load_corpus(corpus_path)
    verifiers, verifier_preflight = _load_skill_verifiers(args.skill)

    if args.verifier in verifier_preflight:
        try:
            verifier_preflight[args.verifier]()
        except RuntimeError as exc:
            sys.exit(f"ERROR: {exc}")

    if getattr(args, "keep_artifacts", False):
        if args.verifier not in ("honest", "output_diff"):
            print("Warning: --keep-artifacts has no effect with this verifier", flush=True)
        elif "honest" in verifiers:
            verifiers = dict(verifiers)
            verifiers["honest"] = functools.partial(verifiers["honest"], keep_artifacts=True)

    sandbox = Path(args.sandbox) if args.sandbox else Path("sandbox")
    sandbox.mkdir(parents=True, exist_ok=True)

    config_path = Path(args.config) if args.config else None
    backends_cfg = _resolve_backends(config_path, args.backends)

    use_output_diff = args.verifier == "output_diff"
    use_success = args.verifier == "success"
    fixture_dir = (
        Path(args.fixture_dir)
        if getattr(args, "fixture_dir", None)
        else _default_fixture_dir(sandbox, args.skill)
    )

    results: dict[str, dict[str, Any]] = {}
    for backend_name, backend_cfg in backends_cfg.items():
        print(f"\nRunning backend: {backend_name}", flush=True)
        backend_sandbox = sandbox / backend_name
        backend_sandbox.mkdir(parents=True, exist_ok=True)
        use_mock = backend_cfg is None
        # The agent only plans (dry_run), and its sandbox is the root that
        # stem resolution globs for input files. Eval inputs live in fixture_dir,
        # so point the agent there — otherwise extension-less corpus names
        # (e.g. "clip_4k") raise StemNotFoundError and force a spurious clarify.
        agent = _make_agent(args.skill, fixture_dir, None if use_mock else backend_cfg)

        apply_retrieval = not getattr(args, "no_retrieval", False)

        if use_output_diff:
            outputs = run_corpus(
                agent,
                corpus,
                limit=args.limit,
                use_mock=use_mock,
                verbose=args.verbose,
                execute=True,
                sandbox=backend_sandbox,
                fixture_dir=fixture_dir,
                apply_retrieval=apply_retrieval,
            )
            baselines_dir = backend_sandbox / "baselines"
            baseline_paths = _build_baseline_outputs(
                corpus, outputs, fixture_dir, baselines_dir, skill=args.skill
            )
            output_diff_fn = verifiers.get("output_diff")
            scoreboard = score_corpus_output_diff(
                outputs, corpus, output_diff_fn, backend_sandbox, baseline_paths
            )
        elif use_success:
            # success verifier: execute against fixtures so artifact_path is populated,
            # then grade each output file against success_criteria (no baseline needed).
            outputs = run_corpus(
                agent,
                corpus,
                limit=args.limit,
                use_mock=use_mock,
                verbose=args.verbose,
                execute=True,
                sandbox=backend_sandbox,
                fixture_dir=fixture_dir,
                apply_retrieval=apply_retrieval,
            )
            scoreboard = score_corpus(outputs, corpus, verifiers, "success", backend_sandbox)
        else:
            outputs = run_corpus(
                agent,
                corpus,
                limit=args.limit,
                use_mock=use_mock,
                verbose=args.verbose,
                apply_retrieval=apply_retrieval,
            )
            scoreboard = score_corpus(outputs, corpus, verifiers, args.verifier, backend_sandbox)

        results[backend_name] = scoreboard

        # Persist BEFORE rendering. Rendering is cosmetic; the run behind it can be an hour of
        # inference. Anything that can throw while printing (it has: a cp1252 console cannot
        # encode this report's box-drawing rules) must not be able to take the results with it.
        if args.save:
            out_path = Path(args.save) / f"{args.skill}_{backend_name}_{args.verifier}.json"
            save_scoreboard_json(scoreboard, out_path)

        print_scoreboard(scoreboard, backend=backend_name, verbose=args.verbose)

        if args.save:
            print(f"  Saved to {out_path}")

        if args.snapshot:
            snap_path = _snapshot_path(args.skill)
            save_snapshot(scoreboard, snap_path)
            print(f"  Snapshot saved to {snap_path}")

    return results


def _matrix_row(scoreboard: dict[str, Any]) -> dict[str, Any]:
    """Pull the four headline metrics from a scoreboard into a flat matrix cell."""
    intent = scoreboard.get("intent_metrics") or {}
    return {
        "outcome_accuracy": scoreboard.get("outcome_accuracy"),
        "avg_knaif_score": scoreboard.get("avg_knaif_score"),
        "tool_accuracy": intent.get("tool_accuracy"),
        "schema_validity": intent.get("schema_validity"),
        "total": scoreboard.get("total"),
    }


def _fmt(val: Any) -> str:
    return f"{val:.3f}" if isinstance(val, (int, float)) else "—"


def _write_matrix(matrix: dict[str, Any], save_dir: Path) -> None:
    (save_dir / "matrix.json").write_text(
        json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    meta = matrix.get("meta", {})
    lines = [
        f"# All-skills eval matrix — verifier: {matrix['verifier']}",
        "",
    ]
    if meta:
        lines += [
            f"- **build:** {meta.get('label', '—')}",
            f"- **date:** {meta.get('date', '—')}",
            f"- **git:** {meta.get('git_sha', '—')} ({meta.get('git_branch', '—')})",
            f"- **backends:** {', '.join(meta.get('backends', [])) or '—'}",
            "",
        ]
    lines += [
        "| skill | backend | outcome | knaif | tool | schema | n |",
        "|---|---|---|---|---|---|---|",
    ]
    for skill in sorted(matrix["skills"]):
        for backend in sorted(matrix["skills"][skill]):
            row = matrix["skills"][skill][backend]
            lines.append(
                f"| {skill} | {backend} | {_fmt(row['outcome_accuracy'])} "
                f"| {_fmt(row['avg_knaif_score'])} | {_fmt(row['tool_accuracy'])} "
                f"| {_fmt(row['schema_validity'])} | {row.get('total', '—')} |"
            )
    if matrix["skipped"]:
        lines += ["", f"Skipped (no corpus / stale): {', '.join(matrix['skipped'])}"]
    (save_dir / "matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_matrix(matrix: dict[str, Any]) -> None:
    print(f"\n=== All-skills matrix (verifier={matrix['verifier']}) ===", flush=True)
    header = f"{'skill':<14}{'backend':<14}{'outcome':>9}{'knaif':>9}{'tool':>9}{'schema':>9}"
    print(header)
    print("-" * len(header))
    for skill in sorted(matrix["skills"]):
        for backend in sorted(matrix["skills"][skill]):
            row = matrix["skills"][skill][backend]
            print(
                f"{skill:<14}{backend:<14}{_fmt(row['outcome_accuracy']):>9}"
                f"{_fmt(row['avg_knaif_score']):>9}{_fmt(row['tool_accuracy']):>9}"
                f"{_fmt(row['schema_validity']):>9}"
            )
    if matrix["skipped"]:
        print(f"\nSkipped (no corpus / stale): {', '.join(matrix['skipped'])}")


def _git_meta() -> dict[str, str | None]:
    """Best-effort {sha, branch} for the current checkout (None if unavailable)."""
    import subprocess

    def _run(rev_args: list[str]) -> str | None:
        try:
            out = subprocess.run(
                ["git", *rev_args], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            return out or None
        except (OSError, subprocess.SubprocessError):
            return None

    return {
        "sha": _run(["rev-parse", "HEAD"]),
        "branch": _run(["rev-parse", "--abbrev-ref", "HEAD"]),
    }


def _build_meta(args: argparse.Namespace, backends: list[str]) -> dict[str, Any]:
    """Model-build identity stamped into matrix.json so a skill's history is
    reconstructable from the run folders alone (date + code SHA + label + backends)."""
    from datetime import datetime, timezone

    git = _git_meta()
    label = getattr(args, "label", None) or Path(args.save).name
    return {
        "label": label,
        "date": datetime.now(timezone.utc).isoformat(),
        "git_sha": git["sha"],
        "git_branch": git["branch"],
        "backends": backends,
    }


def cmd_run_all_skills(args: argparse.Namespace) -> None:
    """Sweep every active skill for one model build, writing per-skill scoreboards
    and a `matrix.{json,md}` summary into a single dated run folder."""
    if not getattr(args, "save", None):
        sys.exit(
            "run --all-skills requires --save DIR — the sweep owns the run folder "
            "and writes every skill's scoreboard plus matrix.{json,md} there."
        )

    rejected = [
        flag
        for flag, val in (
            ("--corpus", getattr(args, "corpus", None)),
            ("--fixture-dir", getattr(args, "fixture_dir", None)),
            ("--snapshot", getattr(args, "snapshot", False)),
        )
        if val
    ]
    if rejected:
        sys.exit(
            f"{', '.join(rejected)} not allowed with --all-skills: each skill uses its own "
            "in-skill corpus and default fixture dir, and snapshots are per-skill "
            "(use `run --skill X --snapshot`)."
        )

    save_dir = Path(args.save)
    save_dir.mkdir(parents=True, exist_ok=True)

    matrix: dict[str, Any] = {"verifier": args.verifier, "skills": {}, "skipped": []}

    for skill in list_skills():  # active skills only — stale ones are excluded
        corpus_path = _corpus_path(skill)
        if not corpus_path.exists():
            print(f"\n[skip] {skill}: no corpus at {corpus_path}", flush=True)
            matrix["skipped"].append(skill)
            continue

        print(f"\n{'=' * 60}\n=== Skill: {skill} ===\n{'=' * 60}", flush=True)
        per_skill = argparse.Namespace(**vars(args))
        per_skill.skill = skill
        per_skill.all_skills = False
        per_skill.save = str(save_dir)
        results = cmd_run(per_skill) or {}
        matrix["skills"][skill] = {backend: _matrix_row(sb) for backend, sb in results.items()}

    backends = sorted({b for cells in matrix["skills"].values() for b in cells})
    matrix["meta"] = _build_meta(args, backends)
    _write_matrix(matrix, save_dir)
    _print_matrix(matrix)
    print(f"\nMatrix written to {save_dir / 'matrix.json'} and {save_dir / 'matrix.md'}")


def cmd_trend(args: argparse.Namespace) -> None:
    """Print one skill's metric history across model builds by reading the
    `matrix.json` files written by `run --all-skills` under a results directory."""
    results_dir = Path(args.results_dir) if getattr(args, "results_dir", None) else Path("evals")
    skill = args.skill

    builds: list[dict[str, Any]] = []
    for matrix_path in results_dir.glob("**/matrix.json"):
        try:
            with matrix_path.open(encoding="utf-8") as fh:
                matrix = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        cells = matrix.get("skills", {}).get(skill)
        if not cells:
            continue  # this build did not cover the requested skill
        meta = matrix.get("meta", {})
        builds.append(
            {
                "date": meta.get("date", ""),
                "label": meta.get("label", matrix_path.parent.name),
                "git_sha": meta.get("git_sha"),
                "verifier": matrix.get("verifier"),
                "cells": cells,
            }
        )

    builds.sort(key=lambda b: b["date"])
    if getattr(args, "last", None):
        builds = builds[-args.last :]

    if not builds:
        print(f"No matrix.json runs covering skill {skill!r} found under {results_dir}")
        return

    print(f"\n=== Trend: {skill} (across {len(builds)} model build(s)) ===")
    header = (
        f"{'date':<22}{'build':<20}{'sha':<10}{'backend':<14}"
        f"{'outcome':>9}{'knaif':>9}{'tool':>9}{'schema':>9}"
    )
    print(header)
    print("-" * len(header))
    backend_filter = getattr(args, "backend", None)
    for b in builds:
        sha = (b["git_sha"] or "")[:8]
        date = (b["date"] or "")[:19]
        for backend in sorted(b["cells"]):
            if backend_filter and backend != backend_filter:
                continue
            row = b["cells"][backend]
            print(
                f"{date:<22}{b['label']:<20}{sha:<10}{backend:<14}"
                f"{_fmt(row.get('outcome_accuracy')):>9}{_fmt(row.get('avg_knaif_score')):>9}"
                f"{_fmt(row.get('tool_accuracy')):>9}{_fmt(row.get('schema_validity')):>9}"
            )


def cmd_compare(args: argparse.Namespace) -> None:
    from .corpus import load_corpus
    from .report import print_scoreboard
    from .runner import run_corpus
    from .scoring import score_corpus

    corpus_file = _corpus_path(args.skill)
    if not corpus_file.exists():
        sys.exit(f"Corpus not found: {corpus_file}")

    corpus = load_corpus(corpus_file)
    verifiers, verifier_preflight = _load_skill_verifiers(args.skill)

    if args.verifier in verifier_preflight:
        try:
            verifier_preflight[args.verifier]()
        except RuntimeError as exc:
            sys.exit(f"ERROR: {exc}")

    if getattr(args, "keep_artifacts", False):
        if args.verifier != "honest":
            print("Warning: --keep-artifacts has no effect without --verifier honest", flush=True)
        elif "honest" in verifiers:
            verifiers = dict(verifiers)
            verifiers["honest"] = functools.partial(verifiers["honest"], keep_artifacts=True)

    sandbox = Path(args.sandbox) if args.sandbox else Path("sandbox")
    sandbox.mkdir(parents=True, exist_ok=True)
    # Stem resolution globs the agent sandbox for input files; eval inputs live
    # in fixture_dir (see cmd_run). Point the agent there so extension-less
    # corpus names resolve instead of forcing a spurious clarify.
    fixture_dir = (
        Path(args.fixture_dir)
        if getattr(args, "fixture_dir", None)
        else _default_fixture_dir(sandbox, args.skill)
    )

    config_path = Path(args.config)
    if not config_path.exists():
        sys.exit(f"Config not found: {config_path}")
    backends_cfg = _resolve_backends(config_path, args.backends, allow_mock_fallback=False)
    if not backends_cfg:
        sys.exit(f"No backends to compare. Add one to {config_path} or pass --backends.")

    for backend_name, backend_cfg in backends_cfg.items():
        print(f"\nRunning backend: {backend_name}", flush=True)
        backend_sandbox = sandbox / backend_name
        backend_sandbox.mkdir(parents=True, exist_ok=True)
        agent = _make_agent(args.skill, fixture_dir, backend_cfg)
        apply_retrieval = not getattr(args, "no_retrieval", False)
        outputs = run_corpus(
            agent,
            corpus,
            limit=args.limit,
            use_mock=backend_cfg is None,
            verbose=args.verbose,
            apply_retrieval=apply_retrieval,
        )
        scoreboard = score_corpus(outputs, corpus, verifiers, args.verifier, backend_sandbox)
        print_scoreboard(scoreboard, backend=backend_name, verbose=args.verbose)


def cmd_regression(args: argparse.Namespace) -> None:
    from .snapshot import diff_snapshots, load_snapshot

    snap_path = _snapshot_path(args.skill)
    if not snap_path.exists():
        sys.exit(
            f"No snapshot found at {snap_path}. Run with --snapshot first to create a baseline."
        )

    baseline = load_snapshot(snap_path)

    current_arg = getattr(args, "current", None)
    current: dict[str, Any]
    if current_arg:
        current_path = Path(current_arg)
        # Hard-fail on a bad --current rather than silently falling back to the self-compare
        # below: a typo'd path used to look identical to a real, passing gate (C0 in the
        # 2026-08-02 macOS support plan / docs/TODO.md) — the file the caller pointed at not
        # existing is a caller error, not "nothing to compare against".
        if not current_path.exists():
            sys.exit(f"--current file not found: {current_path}")
        with current_path.open(encoding="utf-8") as fh:
            current = json.load(fh)
    else:
        # No --current: compare the snapshot to itself. This is a smoke check that the snapshot
        # file loads and is internally consistent, NOT a regression gate — it always passes. See
        # the "self-compare false green" note in docs/EVAL_VERIFICATION_SOP.md. Said aloud rather
        # than left implicit, because a silent no-op that prints "No regressions... OK" reads
        # identically to a real check.
        current = baseline
        print(
            "⚠ no --current given — comparing the snapshot to itself; this always "
            "passes and is not a regression check. Pass --current <scoreboard.json> "
            "(see docs/EVAL_VERIFICATION_SOP.md).",
            file=sys.stderr,
        )

    diff = diff_snapshots(baseline, current, threshold=args.threshold)

    if diff["regressions"]:
        print(f"\nREGRESSIONS (threshold={diff['threshold']}):")
        for r in diff["regressions"]:
            print(
                f"  {r['metric']}: {r['baseline']:.3f} -> {r['current']:.3f}"
                f" (delta={r['delta']:+.3f})"
            )
        sys.exit(1)
    else:
        print(f"\nNo regressions above threshold={diff['threshold']}. OK")
        if diff["improvements"]:
            print("Improvements:")
            for imp in diff["improvements"]:
                print(
                    f"  {imp['metric']}: {imp['baseline']:.3f} -> {imp['current']:.3f}"
                    f" (delta={imp['delta']:+.3f})"
                )


def _backend_from_scoreboard_name(fname: str, skill: str, verifier: str) -> str:
    """Extract the backend segment from a `{skill}_{backend}_{verifier}.json` name."""
    stem = fname[:-5] if fname.endswith(".json") else fname
    return stem[len(skill) + 1 : len(stem) - len(verifier) - 1]


def cmd_regression_all_skills(args: argparse.Namespace) -> None:
    """Aggregate cross-skill regression gate (catastrophic-forgetting detector).

    For every active skill, diff each current scoreboard in ``--current-run`` against
    that skill's in-skill ``eval_snapshot.json``. Exits non-zero if **any** skill/backend
    regresses beyond threshold.

    Critically, this reads the *current* scoreboards from the run folder — unlike the
    per-skill ``regression`` command it never falls back to comparing a snapshot to
    itself (which would be a false green).
    """
    from .snapshot import diff_snapshots, load_snapshot

    run_dir = Path(args.current_run) if getattr(args, "current_run", None) else None
    if run_dir is None or not run_dir.exists():
        sys.exit(
            "regression --all-skills requires --current-run DIR pointing at the "
            "`run --all-skills` output folder (its {skill}_{backend}_{verifier}.json files)."
        )

    rows: list[tuple[str, str, str, list[dict[str, Any]]]] = []
    failed = False

    for skill in list_skills():
        snap_path = _snapshot_path(skill)
        if not snap_path.exists():
            rows.append((skill, "—", "no baseline", []))
            continue

        baseline = load_snapshot(snap_path)
        verifier = baseline.get("verifier", "cheap")
        currents = sorted(run_dir.glob(f"{skill}_*_{verifier}.json"))
        if not currents:
            # Distinguish a real coverage gap from a cross-verifier run:
            #  - the snapshot's verifier WAS produced for other skills but not this one
            #    → the sweep should have covered it → hard fail.
            #  - the snapshot's verifier was never run in this sweep (e.g. a `success`
            #    baseline against a `cheap` sweep) → can't compare → skip, don't fail.
            verifier_run_elsewhere = any(run_dir.glob(f"*_{verifier}.json"))
            if verifier_run_elsewhere:
                rows.append((skill, "—", f"no current scoreboard (verifier={verifier})", []))
                failed = True
            else:
                rows.append(
                    (skill, "—", f"skipped (snapshot verifier={verifier} not in this run)", [])
                )
            continue

        # Per-skill threshold override (optional), falling back to the global one.
        threshold = baseline.get("regression_threshold", args.threshold)
        for cur_path in currents:
            backend = _backend_from_scoreboard_name(cur_path.name, skill, verifier)
            with cur_path.open(encoding="utf-8") as fh:
                current = json.load(fh)
            diff = diff_snapshots(baseline, current, threshold=threshold)
            if diff["regressions"]:
                failed = True
                rows.append((skill, backend, "REGRESSED", diff["regressions"]))
            else:
                rows.append((skill, backend, "ok", []))

    print("\n=== Cross-skill regression gate ===")
    for skill, backend, status, regressions in rows:
        print(f"  {skill:<14} {backend:<14} {status}")
        for r in regressions:
            print(
                f"      {r['metric']}: {r['baseline']:.3f} -> {r['current']:.3f}"
                f" (delta={r['delta']:+.3f})"
            )

    if failed:
        print("\nFAIL: at least one skill regressed or is missing a current scoreboard.")
        sys.exit(1)
    print("\nOK: no cross-skill regressions.")


def cmd_show_baseline(args: argparse.Namespace) -> None:
    from .corpus import load_corpus
    from .report import print_baseline_row

    corpus_file = _corpus_path(args.skill)
    if not corpus_file.exists():
        sys.exit(f"Corpus not found: {corpus_file}")

    corpus = load_corpus(corpus_file)
    for row in corpus:
        if row.id == args.id:
            baseline_cmd = (row.baseline or {}).get("command") or "n/a"
            print_baseline_row(
                {
                    "id": row.id,
                    "utterance": row.utterances[0],
                    "knaif_artifact": "(run eval to see knaif output)",
                    "baseline_artifact": baseline_cmd,
                }
            )
            return
    print(f"Row {args.id!r} not found in corpus.")


def cmd_fixtures_regen(args: argparse.Namespace) -> None:
    import hashlib
    import subprocess

    fixtures, extensions = _load_skill_fixtures(args.skill)
    sandbox = Path(args.sandbox) if args.sandbox else Path("sandbox")
    fixture_dir = _default_fixture_dir(sandbox, args.skill)
    fixture_dir.mkdir(parents=True, exist_ok=True)

    cache_path = fixture_dir / ".cache.json"
    cache: dict[str, str] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    updated = False
    for name, cmd_template in fixtures.items():
        # name is the full filename, e.g. "clip.mp4"
        out_path = fixture_dir / name
        full_cmd = cmd_template.replace("{output}", str(out_path))
        sha = hashlib.sha256(full_cmd.encode()).hexdigest()

        if not args.force and cache.get(name) == sha and out_path.exists():
            print(f"  [skip]  {name}  (cached)", flush=True)
            continue

        print(f"  [regen] {name}", flush=True)
        try:
            result = subprocess.run(
                full_cmd.split(),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                print(
                    f"  Warning: fixture {name!r} failed "
                    f"(exit {result.returncode}): {result.stderr[-300:]}",
                    flush=True,
                )
            else:
                cache[name] = sha
                updated = True
        except FileNotFoundError:
            print(f"  Warning: ffmpeg not found on PATH — skipping fixture {name!r}", flush=True)
        except subprocess.TimeoutExpired:
            print(f"  Warning: fixture {name!r} timed out after 120s", flush=True)

    if updated:
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def cmd_retrieval(args: argparse.Namespace) -> None:
    """Measure retrieval quality (recall@k / MRR) independent of any model."""
    from .. import list_skills
    from .retrieval import check_regression, evaluate, format_report

    skills = list_skills() if (not args.skill or args.skill == "all") else [args.skill]
    results = evaluate(skills, top_k=args.top_k)
    print(format_report(results))
    if args.check:
        baseline = json.loads(Path(args.check).read_text(encoding="utf-8"))
        regressions = check_regression(results, baseline, tol=args.tol)
        if regressions:
            print(f"\nRETRIEVAL REGRESSION vs {args.check} (tol {args.tol}):")
            for skill, sl, br, cr in regressions:
                print(f"  {skill} [{sl}]: recall {br} -> {cr}")
            sys.exit(1)
        print(f"\nOK — no retrieval regression vs {args.check} (tol {args.tol}).")
    if args.save:
        # Strip per-utterance miss detail from the saved baseline; keep the aggregates.
        summary = {
            "top_k": results["top_k"],
            "skills": {
                s: {k: v for k, v in r.items() if k != "misses"}
                for s, r in results["skills"].items()
            },
        }
        Path(args.save).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nSaved baseline -> {args.save}")


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    enable_utf8_console()
    parser = argparse.ArgumentParser(
        prog="uv run -m knaif.evalsuite",
        description="knaif eval suite — run, compare, and regression-check skill evaluation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Run evaluation against a corpus")
    p_run.add_argument(
        "--skill", default=None, help="Skill name (e.g. ffmpeg), or 'all' to sweep every skill"
    )
    p_run.add_argument(
        "--all-skills",
        action="store_true",
        dest="all_skills",
        help="Sweep every active skill for one model build (requires --save; "
        "writes a matrix.{json,md} into the run folder)",
    )
    p_run.add_argument("--corpus", default=None, metavar="FILE", help="Override corpus path")
    p_run.add_argument("--config", default="eval_backends.yaml")
    p_run.add_argument("--backends", default=None, help="Comma-separated backend names")
    p_run.add_argument(
        "--verifier", default="cheap", choices=["cheap", "honest", "output_diff", "success"]
    )
    p_run.add_argument("--fixture-dir", default=None, metavar="DIR", help="Fixture directory")
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--workers", type=int, default=1)
    p_run.add_argument("--sandbox", default=None)
    p_run.add_argument("--save", default=None, metavar="DIR", help="Save scoreboard JSON to DIR")
    p_run.add_argument(
        "--label",
        default=None,
        help="Model-build label stamped into matrix.json (default: --save folder name)",
    )
    p_run.add_argument("--snapshot", action="store_true", help="Update the regression snapshot")
    p_run.add_argument("--verbose", action="store_true", help="Print per-row detail after summary")
    p_run.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep sandbox files produced by --verifier honest for manual inspection",
    )
    p_run.add_argument(
        "--no-retrieval",
        action="store_true",
        dest="no_retrieval",
        help="Disable retrieve_tools() filtering — measures the full unfiltered prompt (diagnostic)",
    )

    # compare
    p_cmp = sub.add_parser("compare", help="Compare multiple backends side-by-side")
    p_cmp.add_argument("--skill", required=True)
    p_cmp.add_argument("--config", default="eval_backends.yaml")
    p_cmp.add_argument("--backends", default=None)
    p_cmp.add_argument(
        "--verifier", default="cheap", choices=["cheap", "honest", "output_diff", "success"]
    )
    p_cmp.add_argument("--limit", type=int, default=None)
    p_cmp.add_argument("--sandbox", default=None)
    p_cmp.add_argument("--fixture-dir", default=None, metavar="DIR", help="Fixture directory")
    p_cmp.add_argument("--parallel-backends", action="store_true")
    p_cmp.add_argument("--verbose", action="store_true", help="Print per-row detail after summary")
    p_cmp.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep sandbox files produced by --verifier honest for manual inspection",
    )
    p_cmp.add_argument(
        "--no-retrieval",
        action="store_true",
        dest="no_retrieval",
        help="Disable retrieve_tools() filtering — measures the full unfiltered prompt (diagnostic)",
    )

    # regression
    p_reg = sub.add_parser("regression", help="Check current results against snapshot")
    p_reg.add_argument("--skill", default=None)
    p_reg.add_argument(
        "--all-skills",
        action="store_true",
        dest="all_skills",
        help="Aggregate gate: diff every active skill's current scoreboard against its "
        "in-skill snapshot (requires --current-run)",
    )
    p_reg.add_argument("--threshold", type=float, default=0.02)
    p_reg.add_argument("--current", default=None, metavar="FILE")
    p_reg.add_argument(
        "--current-run",
        default=None,
        dest="current_run",
        metavar="DIR",
        help="Folder of `run --all-skills` scoreboards to diff against snapshots",
    )

    # trend
    p_trend = sub.add_parser(
        "trend", help="Show a skill's metric history across model builds (reads matrix.json)"
    )
    p_trend.add_argument("--skill", required=True)
    p_trend.add_argument(
        "--results-dir",
        default="evals",
        dest="results_dir",
        metavar="DIR",
        help="Root to scan for run matrix.json files (default: evals)",
    )
    p_trend.add_argument("--backend", default=None, help="Only show this backend")
    p_trend.add_argument("--last", type=int, default=None, help="Only the last N model builds")

    # retrieval
    p_ret = sub.add_parser(
        "retrieval", help="Measure retrieval quality (recall@k / MRR) per skill, sliced by script"
    )
    p_ret.add_argument("--skill", default="all", help="Skill name, or 'all' (default)")
    p_ret.add_argument("--top-k", type=int, default=5, dest="top_k")
    p_ret.add_argument(
        "--save", default=None, metavar="FILE", help="Write the aggregate baseline JSON"
    )
    p_ret.add_argument(
        "--check",
        default=None,
        metavar="FILE",
        help="Compare against a saved baseline; exit 1 if recall regresses beyond --tol",
    )
    p_ret.add_argument(
        "--tol", type=float, default=0.02, help="Recall regression tolerance (default 0.02)"
    )

    # show-baseline
    p_sb = sub.add_parser("show-baseline", help="Show baseline command for a corpus row")
    p_sb.add_argument("--skill", required=True)
    p_sb.add_argument("--id", required=True)

    # fixtures
    p_fix = sub.add_parser("fixtures", help="Manage evaluation fixtures")
    fix_sub = p_fix.add_subparsers(dest="fixtures_command", required=True)
    p_regen = fix_sub.add_parser("regen", help="Regenerate fixtures into sandbox/fixtures/<skill>/")
    p_regen.add_argument("--skill", required=True)
    p_regen.add_argument("--sandbox", default=None)
    p_regen.add_argument("--force", action="store_true", help="Regenerate even if cached")

    # review
    p_rev = sub.add_parser("review", help="Mark an eval entry as reviewed/rejected/pending")
    p_rev.add_argument("--log", required=True, metavar="FILE", help="Path to review_log.json")
    p_rev.add_argument("--row", required=True, help="Row id to mark")
    p_rev.add_argument("--utterance-idx", type=int, default=0, dest="utterance_idx")
    p_rev.add_argument("--status", required=True, choices=["reviewed", "rejected", "pending"])
    p_rev.add_argument("--notes", default=None)

    # report
    p_rep = sub.add_parser("report", help="Emit report.md and report.html from a results directory")
    p_rep.add_argument("--skill", required=True)
    p_rep.add_argument("--results-dir", required=True, metavar="DIR")
    p_rep.add_argument("--corpus", default=None, metavar="FILE")
    p_rep.add_argument(
        "--review-log",
        default=None,
        metavar="FILE",
        dest="review_log",
        help="Path to review_log.json (default: <results-dir>/review_log.json)",
    )

    # score-external
    p_ext = sub.add_parser(
        "score-external",
        help="Score an external agent's results dir (success verifier, output_diff fallback)",
    )
    p_ext.add_argument("--skill", required=True)
    p_ext.add_argument("--results-dir", required=True, metavar="DIR")
    p_ext.add_argument("--corpus", default=None, metavar="FILE")
    p_ext.add_argument("--fixture-dir", default=None, metavar="DIR")
    p_ext.add_argument("--sandbox", default=None)

    # seed-baselines
    p_seed = sub.add_parser(
        "seed-baselines", help="Populate baseline.command for unseeded corpus rows"
    )
    p_seed.add_argument("--skill", required=True)
    p_seed.add_argument("--corpus", default=None, metavar="FILE", help="Override corpus path")
    p_seed.add_argument("--config", default="eval_backends.yaml")
    p_seed.add_argument("--backends", default=None, help="Comma-separated backend names")
    p_seed.add_argument("--sandbox", default=None)
    p_seed.add_argument("--limit", type=int, default=None)
    p_seed.add_argument(
        "--force",
        action="store_true",
        help="Re-seed rows that already have a command (but never validated rows)",
    )

    args = parser.parse_args()
    if args.command == "fixtures":
        if args.fixtures_command == "regen":
            cmd_fixtures_regen(args)
        return

    if args.command == "run":
        if getattr(args, "all_skills", False) or args.skill == "all":
            cmd_run_all_skills(args)
        else:
            cmd_run(args)
        return

    if args.command == "regression":
        if getattr(args, "all_skills", False) or args.skill == "all":
            cmd_regression_all_skills(args)
        elif not args.skill:
            sys.exit("regression requires --skill NAME (or use --all-skills)")
        else:
            cmd_regression(args)
        return

    dispatch = {
        "compare": cmd_compare,
        "trend": cmd_trend,
        "show-baseline": cmd_show_baseline,
        "seed-baselines": cmd_seed_baselines,
        "score-external": cmd_score_external,
        "report": cmd_report,
        "review": cmd_review,
        "retrieval": cmd_retrieval,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
