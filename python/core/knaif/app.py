"""CLI entry point for knaif.

Usage:
    knaif-cli run <skill> "<prompt>" [options]
    knaif-cli skills
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import click
import yaml

from . import create_agent, list_skills
from ._console import enable_utf8_console
from .models import build_orchestrator, load_models_registry

# The model is shown the tools retrieval surfaces for the request, never the whole registry: the
# eval lane and the native binary both prompt this way, and a CLI that skipped it planned with a
# prompt nobody measured (L3 2026-09-27: 11.9% disagreement with native, where the lanes differ
# by 1.97%).
from .registry import retrieve_tools

#: Prefixes the plan dump under $KNAIF_DUMP_PLAN. The same string as native `run`
#: (apps/cli/src/main.rs) and scripts/parity_check.py; a test holds them together.
PLAN_DUMP_MARKER = "===KNAIF-PLAN==="
#: One line per rendered ffmpeg command under $KNAIF_DUMP_PLAN: the exact argv as JSON. The
#: display line joins with spaces and quotes nothing, so it cannot carry `silent clip.mp4` or a
#: filter's escapes; L3 compares this instead. Same string as native `run` (a test holds it).
ARGV_DUMP_MARKER = "===KNAIF-ARGV==="


def rendered_argvs(results: list[dict[str, Any]]) -> list[list[str]]:
    """The ffmpeg argv each intent rendered, in plan order: what the user is shown as `$ …`.

    Taken from the batch steps (`run_batch`/`run_concat`), the same place the dry-run display
    reads (`skills/ffmpeg/python/_reporting.py`); a preview render is not one of them.
    """
    argvs: list[list[str]] = []
    for result in results:
        if result.get("tool") not in ("run_batch", "run_concat"):
            continue
        r = result.get("result")
        if not isinstance(r, dict):
            continue
        # One argv per command that runs. `run_concat` stores its single command both at the top
        # level and in `outputs`, so the top level counts only when `outputs` carries none;
        # reading both dumped every concat twice, and L3 read each as a port bug (R5c,
        # 2026-09-28). Not de-duplicated by value: a batch may really run a command twice.
        found = [
            out["command"]
            for out in r.get("outputs") or []
            if isinstance(out, dict) and isinstance(out.get("command"), list) and out["command"]
        ]
        if not found and isinstance(r.get("command"), list) and r["command"]:
            found = [r["command"]]
        argvs.extend([str(a) for a in cmd] for cmd in found)
    return argvs


# ── helpers ───────────────────────────────────────────────────────────────────


def _echo_step(step: dict[str, Any]) -> None:
    """Print one execution step with all its result fields (verbose mode)."""
    tool = step["tool"]
    result = step.get("result", {})
    duration = step.get("duration_ms", 0)

    if tool == "reject":
        icon, color = "✗", "red"
    elif tool == "clarify":
        icon, color = "?", "yellow"
    else:
        icon, color = "✓", "green"

    click.echo(
        click.style(f"\n  {icon} {tool}", fg=color, bold=True)
        + click.style(f"  ({duration:.0f}ms)", fg="bright_black")
    )

    if isinstance(result, dict):
        for k, v in result.items():
            if v is not None:
                click.echo(f"    {k}: {v}")
    elif result is not None:
        click.echo(f"    {result}")


def _render_items(items: list[dict[str, Any]]) -> int:
    """Render structured items uniformly; return an exit code (0 or 1).

    Item kinds:
      - "error"   → red ``✗ {message}`` to stderr, sets exit-code to 1
      - "command" → ``$ {message}`` (bright_black ``$`` prefix)
      - "output"  → green ``✓ {message}``
      - "info"    → yellow ``{message}``
    """
    exit_code = 0
    for item in items:
        kind = item.get("kind", "info")
        message = item.get("message", "")
        if kind == "error":
            click.echo(click.style(f"  ✗ {message}", fg="red"), err=True)
            exit_code = 1
        elif kind == "command":
            click.echo("  " + click.style("$", fg="bright_black") + " " + message)
        elif kind == "output":
            click.echo(click.style(f"  ✓ {message}", fg="green"))
        else:  # "info" or anything unknown
            click.echo(click.style(f"  {message}", fg="yellow"))
    return exit_code


def _read_recommended_model(skill_dir: Path) -> str | None:
    """Read skill.yaml's recommended_model without exec'ing handlers.py."""
    manifest = skill_dir / "skill.yaml"
    if not manifest.exists():
        return None
    try:
        with manifest.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return None
    value = data.get("recommended_model") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def _build_orchestrator(
    backend: str,
    model: str | None,
    model_path: str | None,
    ollama_url: str,
    skill_dir: Path | None,
    verbose: bool = False,
) -> Any:
    """Resolve --backend / --model / --model-path into an orchestrator.

    ``backend == "mock"`` returns None (no inference). All other backends route
    through the models.yaml resolver. ``--model`` that doesn't appear in the
    registry falls back to a raw Ollama model name when ``backend == "ollama"``
    (legacy escape hatch); for other backends it raises.
    """
    if backend == "mock":
        return None

    from .orchestrator import InferenceOrchestrator

    skill_recommended = _read_recommended_model(skill_dir) if skill_dir else None

    try:
        registry = load_models_registry()
        return build_orchestrator(
            name=model,
            skill_recommended=skill_recommended,
            model_path=model_path,
            ollama_url=ollama_url,
            registry=registry,
            verbose=verbose,
            backend=backend,
        )
    except KeyError as exc:
        if backend == "ollama" and model:
            # Ad-hoc `--backend ollama --model X` for a model with no models.yaml entry,
            # so there are no options to inherit. Mirror nk.local_ollama()'s defaults:
            # leaving thinking on lets Ollama keep its reasoning in `message.thinking`
            # instead of contaminating `content`, and reasoning is charged against
            # max_tokens, so 256 would be spent before the plan begins.
            return InferenceOrchestrator(
                backend="ollama",
                model_name=model,
                ollama_url=ollama_url,
                model_config={"thinking_enabled": True, "max_tokens": 2048},
            )
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        sys.exit(1)
    except RuntimeError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        sys.exit(1)


# ── CLI group ─────────────────────────────────────────────────────────────────


@click.group()
@click.version_option(package_name="knaif")
def cli() -> None:
    """knaif — natural-language command agent."""


# ── run ───────────────────────────────────────────────────────────────────────


def approval_required(*, auto_approve: bool | None, confirm: bool) -> bool:
    """Whether `run` asks `Proceed?`: only when opted into (--confirm / -Y); -y always skips it."""
    if auto_approve:
        return False
    return confirm or auto_approve is False


def planned_outputs(results: list[dict[str, Any]]) -> list[str]:
    """Paths a dry-run preview says the plan would write, that already exist on disk.

    Walks the nested preview results for the keys the skills use (`output`, `outputs`,
    `preview_output`) and for a rendered ffmpeg `command`, whose last argument is its output path
    (any other program's last argument might be an input, so it is not read).
    """
    found: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value and value not in found and Path(value).exists():
            found.append(value)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("output", "preview_output"):
                    add(value)
                elif key == "outputs" and isinstance(value, list):
                    for item in value:
                        add(item)
                elif key == "command" and isinstance(value, list) and value:
                    if Path(str(value[0])).stem.lower() == "ffmpeg":
                        add(value[-1])
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for step in results:
        walk(step.get("result"))
    return found


def _existing_outputs(agent: Any, payload: dict[str, Any], prompt: str) -> list[str]:
    """Preview *payload* silently (no prompts, no per-intent rendering, nothing written)."""
    import copy

    saved = agent.intent_completed
    agent.intent_completed = None
    try:
        preview = agent.execute_plan(
            copy.deepcopy(payload),
            utterance=prompt,
            dry_run=True,
            confirmed=False,
            show_plan=False,
            require_approval=False,
        )
    except ValueError:
        return []  # the real run reports it, once, in its own words
    finally:
        agent.intent_completed = saved
    return planned_outputs(preview)


def _ask_yes_no(question: str, default_yes: bool) -> bool | None:
    """`question [y/N]` on the terminal (Enter takes the default); None when there is no terminal."""
    if not sys.stdin.isatty():
        return None
    click.echo(f"{question} {'[Y/n]' if default_yes else '[y/N]'} ", nl=False, err=True)
    try:
        c = click.getchar()
    except (KeyboardInterrupt, EOFError):
        click.echo(err=True)
        return False
    click.echo(err=True)
    if c in ("\r", "\n"):
        return default_yes
    return c in ("y", "Y")


def overwrite_gate(existing: list[str], overwrite: bool, ask: Any) -> bool:
    """Ask before replacing each existing output (default No). --overwrite approves up front;
    with nobody to ask it stops, naming the flag. False means the user said no."""
    if not existing or overwrite:
        return True
    for path in existing:
        answer = ask(f"Replace {path}?", False)
        if answer is None:
            raise click.ClickException(
                f"{path} already exists. Re-run with --overwrite to replace it, "
                "or name a different output."
            )
        if not answer:
            return False
    return True


@cli.command("run")
@click.argument("skill")
@click.argument("prompt", nargs=-1, required=True)
@click.option(
    "-d/-D",
    "--dry-run/--no-dry-run",
    default=False,
    show_default=True,
    help="Preview commands without executing side effects.",
)
@click.option(
    "-p/-P",
    "--show-plan/--no-show-plan",
    default=True,
    show_default=True,
    help="Print a human-readable summary for each intent step before it runs.",
)
@click.option(
    "-y/-Y",
    "--auto-approve/--require-approval",
    default=None,
    help="-y: never ask Proceed? (the default). -Y: ask, like --confirm.",
)
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Ask 'Proceed? [Y/n]' before each step instead of acting straight away.",
)
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Replace files that already exist. Otherwise an existing output asks "
    "'Replace <file>? [y/N]' (Enter keeps it) and, without a terminal, stops.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Print every execution step and its full result.",
)
@click.option(
    "-b",
    "--backend",
    type=click.Choice(["mock", "auto", "ollama", "llama-cpp"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Inference backend. 'auto' resolves --model against models.yaml; "
    "'mock' skips inference; 'ollama' lets --model fall back to a raw Ollama "
    "model name when not in the registry.",
)
@click.option(
    "-m",
    "--model",
    default=None,
    metavar="NAME",
    help="Model entry name in models.yaml (e.g. qwen3-4b). Falls back to the "
    "skill's recommended_model and then to models.yaml's default.",
)
@click.option(
    "-M",
    "--model-path",
    default=None,
    type=click.Path(),
    metavar="PATH",
    help="Path to a GGUF model file. Bypasses models.yaml entirely (escape hatch).",
)
@click.option(
    "--ollama-url",
    default="http://localhost:11434",
    show_default=True,
    metavar="URL",
    help="Ollama server URL.",
)
@click.option(
    "-t",
    "--max-tokens",
    default=256,
    show_default=True,
    metavar="N",
    help="Maximum tokens for the model response.",
)
@click.option(
    "-s",
    "--silent",
    is_flag=True,
    default=False,
    help="Suppress all output (errors still shown on stderr).",
)
def run_cmd(
    skill: str,
    prompt: tuple[str, ...],
    dry_run: bool,
    show_plan: bool,
    auto_approve: bool | None,
    confirm: bool,
    overwrite: bool,
    verbose: bool,
    silent: bool,
    backend: str,
    model: str | None,
    model_path: str | None,
    ollama_url: str,
    max_tokens: int,
) -> None:
    """Run SKILL with the given natural-language PROMPT (one or more words).

    \b
    Examples (unquoted):
      knaif-cli run ffmpeg trim video.mp4 to 10 seconds
      knaif-cli run ffmpeg compress video.mp4 --model qwen3-4b
      knaif-cli run ffmpeg resize to 720p --auto-approve
      knaif-cli run ffmpeg extract audio --model-path ./models/qwen.gguf
      knaif-cli run ffmpeg compress video.mp4 --backend mock --dry-run
      knaif-cli run ffmpeg convert test1.mov to mp4 --verbose
    \b
    Quoted form still works:
      knaif-cli run ffmpeg "convert test1.mov to mp4"
    """
    use_mock = backend == "mock"
    prompt_str = " ".join(prompt)

    # --silent suppresses all informational output and skips interactive prompts.
    # --dry-run makes no changes, so the approval gate would be pointless noise.
    effective_show_plan = False if silent else show_plan
    effective_require_approval = (
        False
        if (silent or dry_run)
        else approval_required(auto_approve=auto_approve, confirm=confirm)
    )

    from . import _DEFAULT_SKILLS_ROOT

    skill_dir = _DEFAULT_SKILLS_ROOT / skill

    # ── orchestrator ──────────────────────────────────────────────────────────
    orchestrator = None
    if not use_mock:
        orchestrator = _build_orchestrator(
            backend, model, model_path, ollama_url, skill_dir, verbose
        )
        if orchestrator is None:
            click.echo(
                click.style(
                    "No model resolved (empty models.yaml and no --model / "
                    "--model-path). Falling back to mock inference.",
                    fg="yellow",
                ),
                err=True,
            )
            use_mock = True

    # ── plan display / approval / per-intent callbacks ───────────────────────
    def _plan_display(summary: str) -> None:
        click.echo("  " + click.style("•", fg="cyan") + f" {summary}")

    def _plan_confirmer(_: str) -> bool:
        prompt = "    " + click.style("Proceed? [Y/n]: ", fg="yellow")
        click.echo(prompt, nl=False)
        try:
            c = click.getchar()
        except (KeyboardInterrupt, EOFError):
            click.echo()
            return False
        if c in ("\r", "\n", "y", "Y"):
            click.echo("Y")
            return True
        click.echo("n")
        return False

    # Track exit code across per-intent render callbacks.
    exit_code_holder = [0]

    def _on_intent_completed(sub_results: list[dict[str, Any]], *, dry_run: bool) -> None:
        """Render this intent's result items right after it finishes."""
        if silent:
            return
        if verbose or agent.result_formatter is None:
            return
        items = agent.result_formatter(sub_results, dry_run=dry_run)
        total_ms = sum(r.get("duration_ms", 0) for r in sub_results)
        total_s = total_ms / 1000
        timing_label = "dry-run" if dry_run else f"{total_s:.1f}s"
        for item in items:
            kind = item.get("kind", "info")
            message = item.get("message", "")
            prefix = "    "
            if kind == "error":
                click.echo(prefix + click.style(f"✗ {message}", fg="red"), err=True)
                exit_code_holder[0] = 1
            elif kind == "command":
                click.echo(prefix + click.style("$", fg="bright_black") + " " + message)
            elif kind == "output":
                click.echo(prefix + click.style(f"✓ {message}", fg="green"))
            else:
                click.echo(prefix + click.style(message, fg="yellow"))
        click.echo("    " + click.style(timing_label, fg="bright_black"))

    # ── agent ─────────────────────────────────────────────────────────────────
    try:
        agent = create_agent(
            skill,
            orchestrator=orchestrator,
            show_plan=effective_show_plan,
            require_approval=effective_require_approval,
            plan_display=_plan_display,
            plan_confirmer=_plan_confirmer,
        )
    except ValueError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        sys.exit(1)

    agent.intent_completed = _on_intent_completed

    # ── infer ─────────────────────────────────────────────────────────────────
    _t_infer = time.perf_counter()
    try:
        payload = agent.infer(
            prompt_str,
            use_mock=use_mock,
            ollama_model=model or "mistral",
            max_tokens=max_tokens,
            registry_override=retrieve_tools(prompt_str, agent.registry),
        )
    except Exception as exc:  # noqa: BLE001
        click.echo(click.style(f"\nInference error: {exc}", fg="red"), err=True)
        sys.exit(1)
    _infer_ms = (time.perf_counter() - _t_infer) * 1000
    # The same contract as native `run` under $KNAIF_DUMP_PLAN: the post-gate plan, one marker
    # line on stderr. L3 reads both sides' plans from it to tell a port bug (same plan,
    # different commands) from plan disagreement (release plan R0).
    if os.environ.get("KNAIF_DUMP_PLAN"):
        click.echo(PLAN_DUMP_MARKER + json.dumps(payload, ensure_ascii=False), err=True)

    # ── header ────────────────────────────────────────────────────────────────
    if not silent:
        _infer_s = _infer_ms / 1000
        click.echo(click.style(f"intent: {_infer_s:.1f}s", fg="bright_black"))
        click.echo(
            click.style(f"{skill}", fg="magenta", bold=True)
            + click.style(f" › {prompt_str}", fg="white")
        )
        if not use_mock and verbose:
            tag = model_path or model or _read_recommended_model(skill_dir) or backend
            click.echo("  " + click.style(f"backend: {backend}  model: {tag}", fg="bright_black"))
        if dry_run:
            click.echo(
                "  " + click.style("mode: dry-run (no files will be modified)", fg="bright_black")
            )

    # ── early-exit for clarify / reject ───────────────────────────────────────
    plan_steps = payload.get("plan") or []
    if plan_steps and plan_steps[0].get("tool") in ("clarify", "reject"):
        tool = plan_steps[0]["tool"]
        args = plan_steps[0].get("args") or {}
        msg = args.get("question") or args.get("reason") or args.get("message") or repr(args)
        if not silent:
            icon = "❓" if tool == "clarify" else "🚫"
            color = "yellow" if tool == "clarify" else "red"
            click.echo(click.style(f"\n{icon} {tool.upper()}: {msg}", fg=color))
        sys.exit(0)

    # ── execute ───────────────────────────────────────────────────────────────
    # When running for real, the user's y/n via plan_confirmer authorises
    # destructive tools; pass confirmed=True so those tools don't double-block.
    confirmed = not dry_run

    if not dry_run:
        # Replacing a file is the one thing that always asks, whatever -y says: preview the
        # plan (no side effects) to learn which outputs it would write, and ask before any
        # of them is replaced. Decline = the same "plan declined" as a declined Proceed?.
        existing = _existing_outputs(agent, payload, prompt_str)
        if not overwrite_gate(existing, overwrite, _ask_yes_no):
            click.echo(click.style("\n  (plan declined — no steps executed)", fg="yellow"))
            sys.exit(0)

    try:
        results = agent.execute_plan(
            payload,
            utterance=prompt_str,
            dry_run=dry_run,
            confirmed=confirmed,
        )
    except ValueError as exc:
        click.echo(click.style(f"\n{exc}", fg="red"), err=True)
        sys.exit(1)
    if os.environ.get("KNAIF_DUMP_PLAN"):
        for argv in rendered_argvs(results):
            click.echo(ARGV_DUMP_MARKER + json.dumps(argv, ensure_ascii=False), err=True)

    # The NL clarify gate (run inside execute_plan) can downgrade the plan to a
    # clarify/reject — e.g. an under-specified file or an ungrounded password
    # (grounded_args). Render it like the pre-execution early-exit.
    if results and results[0].get("tool") in ("clarify", "reject"):
        tool = results[0]["tool"]
        args = results[0].get("args") or {}
        res = results[0].get("result") or {}
        msg = (
            args.get("question")
            or args.get("reason")
            or res.get("question")
            or res.get("reason")
            or repr(args)
        )
        if not silent:
            icon = "❓" if tool == "clarify" else "🚫"
            color = "yellow" if tool == "clarify" else "red"
            click.echo(click.style(f"\n{icon} {tool.upper()}: {msg}", fg=color))
        sys.exit(0)

    if not results:
        if not silent:
            click.echo(click.style("\n  (plan declined — no steps executed)", fg="yellow"))
        sys.exit(0)

    # ── output ────────────────────────────────────────────────────────────────
    if not silent:
        if verbose:
            click.echo(click.style("\n⚡ Steps:", fg="cyan", bold=True))
            for step in results:
                _echo_step(step)
            click.echo()
        else:
            # Per-intent items already rendered via _on_intent_completed. Render a
            # generic fallback only when no result_formatter is configured.
            if agent.result_formatter is None:
                items = [
                    {"kind": "info", "message": f"{r['tool']} ({r.get('duration_ms', 0):.0f}ms)"}
                    for r in results
                ]
                click.echo()
                ec = _render_items(items)
                if ec != 0:
                    exit_code_holder[0] = ec

    if exit_code_holder[0] != 0:
        sys.exit(exit_code_holder[0])


# ── plan ──────────────────────────────────────────────────────────────────────


@cli.command("plan")
@click.argument("skill")
@click.argument("prompt", nargs=-1, required=False)
@click.option(
    "-b",
    "--backend",
    type=click.Choice(["mock", "auto", "ollama", "llama-cpp"], case_sensitive=False),
    default="auto",
    show_default=True,
)
@click.option("-m", "--model", default=None, metavar="NAME")
@click.option("-M", "--model-path", default=None, type=click.Path(), metavar="PATH")
@click.option("--ollama-url", default="http://localhost:11434", metavar="URL")
@click.option("-t", "--max-tokens", default=256, show_default=True, metavar="N")
@click.option(
    "--batch",
    default=None,
    type=click.Path(exists=True),
    metavar="FILE",
    help="Read one utterance per line; load the model ONCE and emit one JSON plan per "
    "line, in order. Avoids the per-utterance model reload.",
)
def plan_cmd(
    skill: str,
    prompt: tuple[str, ...],
    backend: str,
    model: str | None,
    model_path: str | None,
    ollama_url: str,
    max_tokens: int,
    batch: str | None,
) -> None:
    """Infer and print the validated plan envelope as JSON (no execution).

    The plan-level counterpart to native `knaif plan --skill S --json`. Emits the same
    envelope the model produced after parse/validate/normalize/defaults — before intent
    expansion or the execute-time clarify-gate — so the two runtimes can be compared at the
    plan level (used by scripts/parity_check.py --mode plan). Errors print {"plan": []}.

    With ``--batch FILE`` the model loads once and every line is planned against it, one JSON
    envelope per output line (order preserved) — the fast path for large parity runs.
    """
    use_mock = backend == "mock"
    from . import _DEFAULT_SKILLS_ROOT

    skill_dir = _DEFAULT_SKILLS_ROOT / skill
    orchestrator = None
    if not use_mock:
        orchestrator = _build_orchestrator(backend, model, model_path, ollama_url, skill_dir, False)
        if orchestrator is None:
            use_mock = True

    def _plan_one(agent: Any, utterance: str) -> dict[str, Any]:
        try:
            result: dict[str, Any] = agent.infer(
                utterance,
                use_mock=use_mock,
                ollama_model=model or "mistral",
                max_tokens=max_tokens,
                registry_override=retrieve_tools(utterance, agent.registry),
            )
            return result
        except Exception as exc:  # noqa: BLE001
            return {"plan": [], "error": str(exc)}

    try:
        agent = create_agent(skill, orchestrator=orchestrator, show_plan=False)
    except Exception as exc:  # noqa: BLE001
        click.echo(json.dumps({"plan": [], "error": str(exc)}))
        return

    if batch:
        # Model already loaded in the orchestrator; each line reuses it (one line out per line in).
        for line in Path(batch).read_text(encoding="utf-8").splitlines():
            click.echo(json.dumps(_plan_one(agent, line), ensure_ascii=False))
            sys.stdout.flush()  # stream: emit each plan immediately for a live reader
        return

    if not prompt:
        click.echo(json.dumps({"plan": [], "error": "no prompt given (or use --batch FILE)"}))
        return
    click.echo(json.dumps(_plan_one(agent, " ".join(prompt)), ensure_ascii=False))


# ── skills ────────────────────────────────────────────────────────────────────


@cli.command("skills")
def skills_cmd() -> None:
    """List all available skills."""
    available = list_skills()
    if not available:
        click.echo("No skills found.")
        return
    click.echo(click.style("Available skills:", fg="cyan", bold=True))
    for name in available:
        click.echo(f"  • {name}")


# ── entry point ───────────────────────────────────────────────────────────────


def _fix_windows_stderr() -> None:
    """One-time startup guard for Windows.

    click creates a _WindowsConsoleWriter when GetConsoleMode succeeds on
    stderr. That writer calls WriteConsoleW, which fails (ERROR_INVALID_HANDLE)
    under just's spawned PowerShell because the child process receives a
    detached or ConPTY-style handle that passes GetConsoleMode but rejects
    WriteConsoleW. Python's own file I/O (WriteFile) works on the same handle.

    In a normal VS Code / Windows Terminal session GetConsoleMode already
    returns 0 (stderr is a pipe), so this function exits early and has no
    effect. It only activates — and only needs to activate — when just
    allocates a Win32 console handle for the subprocess.

    The fix: wrap sys.stderr so that fileno() raises UnsupportedOperation.
    click._is_console() returns False for such a stream and skips the
    _WindowsConsoleWriter entirely, falling back to the regular write path.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import io
        import msvcrt

        fileno = sys.stderr.fileno()
        handle = msvcrt.get_osfhandle(fileno)
        mode = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return  # stderr is already a pipe — click won't use _WindowsConsoleWriter

        # GetConsoleMode succeeded: click WILL try _WindowsConsoleWriter →
        # WriteConsoleW, which may fail on this handle. Wrap stderr so that
        # click's _is_console() (which calls f.fileno()) sees UnsupportedOperation
        # and skips the Win32 console path. Writes are delegated to the original
        # stream, which uses WriteFile and works correctly.
        _orig = sys.stderr

        class _Stderr:
            encoding = getattr(_orig, "encoding", "utf-8")
            errors = getattr(_orig, "errors", "replace")

            def write(self, s: str) -> int:
                return _orig.write(s)

            def flush(self) -> None:
                _orig.flush()

            def fileno(self) -> int:
                raise io.UnsupportedOperation("fileno")

            def isatty(self) -> bool:
                return False

            def __getattr__(self, name: str):
                return getattr(_orig, name)

        sys.stderr = _Stderr()
    except Exception:
        pass


def main() -> None:
    enable_utf8_console()
    _fix_windows_stderr()
    cli()


if __name__ == "__main__":
    main()
