"""CLI entry point for knaif.

Usage:
    knaif-cli run <skill> "<prompt>" [options]
    knaif-cli skills
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import yaml

from . import create_agent, list_skills, ui
from ._console import enable_utf8_console
from .agent import STEP_ATTR, PlanWouldFail, _without_folder, is_unsafe_request
from .models import build_orchestrator, load_models_registry

# The model is shown the tools retrieval surfaces for the request, never the whole registry: the
# eval lane and the native binary both prompt this way, and a CLI that skipped it planned with a
# prompt nobody measured (L3 2026-09-27: 11.9% disagreement with native, where the lanes differ
# by 1.97%).
from .registry import retrieve_tools
from .run_view import (
    QuietPlanning,
    RunView,
    chain_failure_context,
    choose_style,
    downloaded_name,
    shown,
    shown_argv,
)
from .scratch import display

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
                click.echo(display(f"    {k}: {v}"))
    elif result is not None:
        click.echo(f"    {result}")


def make_preview_confirmer(
    *,
    echo: Callable[..., None] = click.echo,
    getchar: Callable[[], str] = click.getchar,
    launch: Callable[[str], Any] = click.launch,
) -> Callable[[str, dict[str, Any] | None], bool]:
    """The interactive answer to a mid-workflow gate (``wait_for_confirmation``).

    When the gate carries a preview clip, its path is shown first and ``o`` opens it in the
    default player before asking again. This is the one scratch path knaif prints on purpose:
    the clip lives in the run scratch and is deleted when the run ends, whatever the answer.
    """

    def confirm(prompt: str, preview: dict[str, Any] | None) -> bool:
        clip = (preview or {}).get("preview_output")
        has_clip = isinstance(clip, str) and Path(clip).is_file()
        if has_clip:
            echo("    " + click.style("Preview: ", fg="cyan") + str(clip))
        choices = "[Y/n/o=open]" if has_clip else "[Y/n]"
        while True:
            echo("    " + click.style(f"{prompt} {choices}: ", fg="yellow"), nl=False)
            try:
                c = getchar()
            except (KeyboardInterrupt, EOFError):
                echo()
                return False
            if has_clip and c in ("o", "O"):
                echo("o")
                launch(str(clip))
                continue
            if c in ("\r", "\n", "y", "Y"):
                echo("Y")
                return True
            echo("n")
            return False

    return confirm


def _echo_item(item: dict[str, Any], indent: str) -> int:
    """Render one structured item; return 1 for an error, else 0.

    Item kinds:
      - "error"   → red ``✗ {message}`` to stderr, sets exit-code to 1
      - "command" → ``$ {message}`` (bright_black ``$`` prefix)
      - "output"  → green ``✓ {message}``
      - "text"    → the message as is (a read's answer), every line indented, no colour
      - "info"    → yellow ``{message}``
    """
    kind = item.get("kind", "info")
    message = display(item.get("message", ""))
    if kind == "error":
        click.echo(indent + click.style(f"✗ {message}", fg="red"), err=True)
        return 1
    if kind == "command":
        click.echo(indent + click.style("$", fg="bright_black") + " " + message)
    elif kind == "output":
        click.echo(indent + click.style(f"✓ {message}", fg="green"))
    elif kind == "text":
        for line in message.splitlines() or [""]:
            click.echo(indent + line)
    else:  # "info" or anything unknown
        click.echo(indent + click.style(message, fg="yellow"))
    return 0


def _render_items(items: list[dict[str, Any]]) -> int:
    """Render structured items uniformly (see `_echo_item`); return an exit code (0 or 1)."""
    exit_code = 0
    for item in items:
        exit_code |= _echo_item(item, "  ")
    return exit_code


def _trained_for_note(
    model: str | None, skill_recommended: str | None, registry: dict[str, Any]
) -> str | None:
    """The line to print when the model about to run was not trained for the prompt this knaif
    serves (owner, 2026-10-26): models.yaml's `trained_for` against the declared generation.
    None for a model models.yaml does not know, or when the generation file is missing: a note
    must never stop a run."""
    from .models import resolve_model_name
    from .prompt_generation import declared, note_for

    resolved = resolve_model_name(model, skill_recommended, registry)
    models = registry.get("models") or {}
    entry = models.get(resolved) if resolved else None
    if resolved is None or not isinstance(entry, dict):
        return None
    try:
        generation = declared().generation
    except (OSError, KeyError, ValueError):
        return None
    trained_for = entry.get("trained_for")
    pull = next(
        (
            name
            for name, e in models.items()
            if isinstance(e, dict) and e.get("trained_for") == generation
        ),
        None,
    )
    return note_for(
        trained_for if isinstance(trained_for, int) else None, generation, resolved, pull=pull
    )


def _say_trained_for(model: str | None, model_path: str | None, skill_dir: Path | None) -> None:
    """Print the trained-for note, before planning goes quiet (a GGUF path says nothing)."""
    if model_path:
        return
    recommended = _read_recommended_model(skill_dir) if skill_dir else None
    note = _trained_for_note(model, recommended, load_models_registry())
    if note:
        click.echo(note, err=True)


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


def _model_or_path(model: str | None) -> tuple[str | None, str | None]:
    """``--model`` as ``(name, path)``: a models.yaml entry or a GGUF file, as native's
    ``--model NAME|PATH`` (``-M/--model-path`` folded into it). A file wins."""
    if model and Path(model).is_file():
        return None, model
    return model, None


#: The names ``--allow`` takes, in the order its error lists them.
PERMISSIONS = ("download", "overwrite", "model", "all")


@dataclass(frozen=True)
class Allow:
    """What a run may do without asking (owner, 2026-10-23): ``--allow download,
    overwrite,model``, a comma list or a repeated flag; ``all``, or ``--yes``, is every
    permission, now and later. ``contracts/parity/cli_flag_cases.json``, native ``Allow``."""

    download: bool = False  # fetch an http(s) URL input
    overwrite: bool = False  # replace a file that already exists
    model: bool = False  # download the recommended model (native only: Python never does)
    all: bool = False  # everything, including what a later release adds; also skips Proceed?

    def asks_proceed(self, *, confirm: bool) -> bool:
        """Whether each step asks ``Proceed? [Y/n]``: only under ``--confirm``, and never when
        everything is allowed (``--confirm --yes`` is ``--yes``)."""
        return confirm and not self.all


def allowed(names: Sequence[str], *, yes: bool) -> Allow:
    """The permissions ``--allow`` (already checked by :func:`_permissions`) and ``--yes`` give."""
    every = yes or "all" in names
    return Allow(
        download=every or "download" in names,
        overwrite=every or "overwrite" in names,
        model=every or "model" in names,
        all=every,
    )


def _permissions(_ctx: Any, _param: Any, values: tuple[str, ...]) -> tuple[str, ...]:
    """``--allow``'s values as a flat tuple of known names: comma lists split, each trimmed and
    without case; an unknown name is a usage error listing the valid ones."""
    names: list[str] = []
    for value in values:
        for name in value.split(","):
            name = name.strip().lower()
            if name not in PERMISSIONS:
                raise click.BadParameter(
                    f"unknown permission '{name}': use download, overwrite, model or all"
                )
            names.append(name)
    return tuple(names)


_SEQUENCE_TOKEN = re.compile(r"%0?\d*d")


def _existing_matches(value: str) -> list[str]:
    """The files an output path names that exist. An image-sequence pattern (`frame_%03d.png`) is
    expanded by ffmpeg into many files, so it names every file in its folder that fits."""
    path = Path(value)
    token = _SEQUENCE_TOKEN.search(path.name)
    if token is None:
        return [value] if path.exists() else []
    fits = re.compile(
        re.escape(path.name[: token.start()]) + r"\d+" + re.escape(path.name[token.end() :]) + "$"
    )
    folder = path.parent
    if not folder.is_dir():
        return []
    shown = folder if str(path.parent) != "." else None
    return sorted(
        str(shown / f.name) if shown else f.name for f in folder.iterdir() if fits.match(f.name)
    )


def planned_outputs(results: list[dict[str, Any]]) -> list[str]:
    """Paths a dry-run preview says the plan would write, that already exist on disk.

    Walks the nested preview results for the keys the skills use (`output`, `outputs`,
    `preview_output`) and for a rendered ffmpeg `command`, whose last argument is its output path
    (any other program's last argument might be an input, so it is not read).
    """
    found: list[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str) or not value:
            return
        for hit in _existing_matches(value):
            if hit not in found:
                found.append(hit)

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


def _named_outputs(node: Any) -> list[str]:
    """Output paths the plan itself names (`output` / `output_path` args), for when it cannot be
    previewed."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("output", "output_path") and isinstance(value, str):
                found.extend(_existing_matches(value))
            else:
                found.extend(_named_outputs(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_named_outputs(item))
    return found


def _existing_outputs(
    agent: Any, payload: dict[str, Any], prompt: str, seen: dict[str, Any] | None = None
) -> list[str]:
    """Preview *payload* (no prompts, no per-intent rendering, nothing written) and return the
    outputs it would replace.

    `confirmed=True` so an internal `wait_for_confirmation` (reverse_video expands through one)
    lets the preview reach the intents after it; `dry_run=True` is what keeps it side-effect free.
    A chain that cannot be previewed (a later step reads a file an earlier step has not written)
    falls back to the outputs the plan names, and the real run reports the error in its own words.
    *seen*, when given, keeps what the preview would have drawn: ``plan`` (steps, notes) and
    ``steps``, one ``(head args, results)`` per step, so the CLI can draw the step that would
    replace a file before it asks.
    """
    import copy

    # No display: the preview is not the run (the display hooks would draw the plan twice, and a
    # file that fails in a dry run, which the rehearsal now finds first, was reported twice).
    hooks = ("intent_completed", "plan_ready", "intent_started", "batch_failed", "item_shown")
    saved = {h: getattr(agent, h, None) for h in hooks}
    for h in hooks:
        setattr(agent, h, None)
    if seen is not None:
        seen["steps"] = []
        agent.plan_ready = lambda steps, notes: seen.__setitem__("plan", (steps, notes))
        agent.intent_started = lambda *head: seen["steps"].append([head, None])
        agent.intent_completed = lambda results, dry_run: (
            seen["steps"][-1].__setitem__(1, results) if seen["steps"] else None
        )
    try:
        preview = agent.execute_plan(
            copy.deepcopy(payload),
            utterance=prompt,
            dry_run=True,
            confirmed=True,
            show_plan=False,
            require_approval=False,
        )
    except Exception:  # noqa: BLE001 - any preview failure falls back, never fails open
        return list(dict.fromkeys(_named_outputs(payload.get("plan"))))
    finally:
        for h, value in saved.items():
            setattr(agent, h, value)
    return planned_outputs(preview)


def _draw_before_replace(
    view: Any,
    agent: Any,
    seen: dict[str, Any],
    existing: list[str],
    plan_ready: Callable[[list[dict[str, Any]], list[str]], None],
) -> int | None:
    """Draw what native has drawn when it asks to replace a file: the plan, the step that
    writes it, that step's commands and ``⚠ <file> already exists``. The step's 1-based
    index, or ``None`` when the preview did not show which step it is."""
    taken = {Path(e).name.lower() for e in existing}
    for head, results in seen.get("steps") or []:
        if results is None or agent.result_formatter is None:
            continue
        items = agent.result_formatter(results, dry_run=True)
        commands = [i for i in items if i.get("kind") == "command" and i.get("argv")]
        if not any(Path(str(i["argv"][-1])).name.lower() in taken for i in commands):
            continue
        if "plan" in seen:
            plan_ready(*seen["plan"])
        view.step(*head)
        for item in commands:
            view.item(item, dry_run=True)
        for path in existing:
            view.exists(path)
        return int(head[0])
    return None


def _skip_drawn(agent: Any, view: Any, index: int) -> None:
    """After a yes, the run goes on from the question: the plan, step *index*'s head and its
    commands are already on screen."""
    plan_ready, started, shown_item = agent.plan_ready, agent.intent_started, agent.item_shown
    state: dict[str, Any] = {"plan": False, "step": None}

    def _plan(steps: list[dict[str, Any]], notes: list[str]) -> None:
        if state["plan"]:
            plan_ready(steps, notes)
        state["plan"] = True

    def _started(i: int, total: int, step: dict[str, Any]) -> None:
        state["step"] = i
        if i != index:
            started(i, total, step)

    def _item(item: dict[str, Any]) -> None:
        if not (state["step"] == index and item.get("kind") == "command"):
            shown_item(item)

    agent.plan_ready, agent.intent_started = _plan, _started
    if shown_item is not None:
        agent.item_shown = _item


def _stop_unconfirmed(agent: Any, payload: dict[str, Any], prompt: str, view: Any) -> None:
    """``--confirm`` with no terminal: native lists the first step's commands (or files) on
    stderr and stops, ``N destructive <noun>(s) pending``. Previewed with the display off."""
    import copy

    first: list[list[dict[str, Any]]] = []
    hooks = ("intent_completed", "plan_ready", "intent_started")
    saved = {h: getattr(agent, h, None) for h in hooks}
    for h in hooks:
        setattr(agent, h, None)
    agent.intent_completed = lambda results, dry_run: first.append(results)
    try:
        agent.execute_plan(
            copy.deepcopy(payload),
            utterance=prompt,
            dry_run=True,
            confirmed=True,
            show_plan=False,
            require_approval=False,
        )
    except Exception:  # noqa: BLE001 - the real run reports a plan that cannot run
        pass
    finally:
        for h, value in saved.items():
            setattr(agent, h, value)
    formatter = agent.result_formatter
    items = formatter(first[0], dry_run=True) if first and formatter else []
    base = Path.cwd()
    pending: list[str] = []
    noun = "command"
    for item in items:
        if item.get("argv"):
            pending.append(shown_argv(item["argv"], base))
            noun = f"{Path(str(item['argv'][0])).stem} command"
        elif item.get("kind") == "preview":
            view.item({"kind": "text", "message": str(item.get("message", ""))}, dry_run=False)
            pending.extend(shown(str(o), base) for o in item.get("outputs") or [])
            noun = "output file"
    for line in pending:
        click.echo(f"  {line}", err=True)
    steps = len(payload.get("plan") or []) or 1
    view.stopped(
        chain_failure_context(0, steps),
        f"{len(pending)} destructive {noun}(s) pending. Re-run with --yes to proceed, or "
        "--dry-run to preview.",
    )
    sys.exit(1)


def _stdin_is_terminal() -> bool:
    """Whether a person can answer on stdin. On Windows `isatty()` is True for the NUL device,
    and the CLI then waited for a key nobody could press; a console handle is
    what native's `IsTerminal` checks, so this asks Windows for its console mode."""
    try:
        if not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt
    from ctypes import wintypes

    try:
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    except (OSError, ValueError):
        return False
    mode = wintypes.DWORD()
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))


def _ask_yes_no(question: str, default_yes: bool) -> bool | None:
    """`question [y/N]` on the terminal (Enter takes the default); None when there is no terminal."""
    if not _stdin_is_terminal():
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


def wire_downloads(agent: Any, *, allow_download: bool, interactive: bool, ask: Any) -> None:
    """URL inputs: ``--allow download`` (or ``--yes``) approves every download up front;
    otherwise a person at a terminal is asked once per URL (default No), and with nobody to ask
    the run stops, naming the permission (``CommandAgent._fetch_url_inputs``)."""
    agent.allow_download = allow_download
    agent.download_confirmer = (
        (lambda url, cap: bool(ask(f"Download {url} (up to {cap // 1_000_000:,} MB)?", False)))
        if interactive and not allow_download
        else None
    )


def download_lines(downloads: list[dict[str, Any]]) -> list[str]:
    """One line per URL input: what a dry run would fetch, or what a run fetched, the file named
    as native names it (``<name> (downloaded)``)."""
    lines = []
    for d in downloads:
        where = downloaded_name(str(d.get("path", "")))
        if d.get("dry_run"):
            lines.append(f"would download {d['url']} → {where}")
        else:
            lines.append(f"downloaded {d['url']} ({d.get('bytes', 0)} bytes) → {where}")
    return lines


def overwrite_gate(existing: list[str], overwrite: bool, ask: Any) -> bool:
    """Ask before replacing each existing output (default No). --overwrite approves up front;
    with nobody to ask it stops, naming the flag. False means the user said no."""
    if not existing or overwrite:
        return True
    for path in existing:
        answer = ask(f"Replace {path}?", False)
        if answer is None:
            raise click.ClickException(
                f"{path} already exists. Re-run with --allow overwrite to replace it, "
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
    # click shows a flag pair's default as its first off switch, `[default: D]`.
    help="Preview commands without executing side effects.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    default=False,
    help="Allow everything a run could ask about, now and in later releases: --allow all.",
)
@click.option(
    "--allow",
    multiple=True,
    metavar="WHAT",
    callback=_permissions,
    help="Approve up front what a run would otherwise ask about, as a comma list or a "
    "repeated flag: download (fetch a URL input), overwrite (replace an existing file), "
    "model (download the recommended model), all. Without it each one asks (Enter is no), "
    "and without a terminal the run stops, naming the permission.",
)
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Ask 'Proceed? [Y/n]' before each step instead of acting straight away. --yes skips it.",
)
@click.option(
    "--stdout",
    is_flag=True,
    default=False,
    help="Print the result's text alone on stdout, for a pipe, and save nothing; everything "
    "else goes to stderr. For a step whose answer is text (extract_text); any other refuses it.",
)
@click.option(
    "--keep-intermediates",
    is_flag=True,
    default=False,
    help="Keep every step's output in your folder. Otherwise a file one step makes only for "
    "the next goes to a temporary folder. Words in the request ('keep the intermediate files') "
    "do the same.",
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
    metavar="NAME|PATH",
    help="A model entry name in models.yaml (e.g. qwen3-4b), or a GGUF file path, which "
    "bypasses models.yaml. Without it: the skill's recommended_model, then models.yaml's "
    "default.",
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
    yes: bool,
    allow: tuple[str, ...],
    confirm: bool,
    stdout: bool,
    keep_intermediates: bool,
    verbose: bool,
    silent: bool,
    backend: str,
    model: str | None,
    ollama_url: str,
    max_tokens: int,
) -> None:
    """Run SKILL with the given natural-language PROMPT (one or more words).

    \b
    Examples (unquoted):
      knaif-cli run ffmpeg trim video.mp4 to 10 seconds
      knaif-cli run ffmpeg compress video.mp4 --model qwen3-4b
      knaif-cli run ffmpeg resize to 720p --confirm
      knaif-cli run ffmpeg extract audio --model ./models/qwen.gguf
      knaif-cli run --allow download,overwrite ffmpeg compress https://example.com/a.mp4
      knaif-cli run ffmpeg compress video.mp4 --backend mock --dry-run
      knaif-cli run ffmpeg convert test1.mov to mp4 --verbose
    \b
    Quoted form still works:
      knaif-cli run ffmpeg "convert test1.mov to mp4"
    """
    # `$KNAIF_LLM_BACKEND=mock` picks the mock, as on native (with `$KNAIF_LLM_MOCK_RESPONSE` as
    # the model's output, `CommandAgent._mock_response`).
    use_mock = backend == "mock" or os.environ.get("KNAIF_LLM_BACKEND") == "mock"
    prompt_str = " ".join(prompt)
    permitted = allowed(allow, yes=yes)
    model, model_path = _model_or_path(model)

    # --silent suppresses all informational output and skips interactive prompts.
    # --dry-run makes no changes, so the approval gate would be pointless noise.
    effective_require_approval = (
        False if (silent or dry_run) else permitted.asks_proceed(confirm=confirm)
    )

    from . import _DEFAULT_SKILLS_ROOT

    skill_dir = _DEFAULT_SKILLS_ROOT / skill

    # `--stdout`: the result's text alone goes to stdout, so everything else this
    # run prints goes to stderr; the text is written to the real stdout at the end.
    text_out = sys.stdout
    if stdout:
        sys.stdout = sys.stderr

    # What the user sees: native's tree in a terminal, its plain lines anywhere else
    # (`knaif.run_view`).
    view = RunView(skill, choose_style(), base=Path.cwd(), silent=silent)
    view.text_to_stdout = stdout
    # A library's own warnings are not the run's to print: pypdf logged "invalid pdf header" and
    # "EOF marker not found" into the tree, twice, where native prints none and the step's error
    # says what went wrong (owner, 2026-10-24). `--verbose` keeps them.
    if not verbose:
        for name in _QUIET_LOGGERS:
            logging.getLogger(name).setLevel(logging.ERROR)

    # ── plan display / approval / per-intent callbacks ───────────────────────
    def _plan_confirmer(_: str) -> bool:
        # Native's question, on stderr: inside the tree, or a plain line.
        prompt = " │   Proceed? [Y/n] " if view.style is not None else "Proceed? [Y/n] "
        click.echo(prompt, nl=False, err=True)
        asked = time.perf_counter()
        try:
            c = click.getchar()
        except (KeyboardInterrupt, EOFError):
            click.echo(err=True)
            return False
        finally:
            view.waited(time.perf_counter() - asked)
        if c in ("\r", "\n", "y", "Y"):
            click.echo("Y", err=True)
            return True
        click.echo("n", err=True)
        return False

    # Track exit code across per-intent render callbacks.
    exit_code_holder = [0]

    def _on_intent_completed(sub_results: list[dict[str, Any]], *, dry_run: bool) -> None:
        """This intent's result items, right after it finishes."""
        if verbose or agent.result_formatter is None:
            return
        for item in agent.result_formatter(sub_results, dry_run=dry_run):
            if view.item(item, dry_run=dry_run):
                exit_code_holder[0] = 1

    # ── agent ─────────────────────────────────────────────────────────────────
    # Made before any model, as native screens a request first: an unsafe phrase never costs a
    # model load.
    try:
        agent = create_agent(
            skill,
            orchestrator=None,
            show_plan=False,
            require_approval=effective_require_approval,
            plan_confirmer=_plan_confirmer,
            # Ask at a preview gate only when a person is answering; otherwise the gate
            # takes `confirmed`, as it always has.
            confirmer=make_preview_confirmer() if effective_require_approval else None,
        )
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    view.file_kinds = agent.file_kinds
    agent.to_stdout = stdout
    agent.intent_completed = _on_intent_completed
    if not verbose and agent.result_formatter is not None:
        # A step's lines as they happen (each file of a batch as it is done), as native.
        def _on_item_shown(item: dict[str, Any]) -> None:
            if view.item(item, dry_run=False):
                exit_code_holder[0] = 1

        agent.item_shown = _on_item_shown
    told: list[bool] = []

    def _tell_downloads() -> None:
        """The download lines, once, before the plan as native prints them."""
        if told:
            return
        told.append(True)
        for line in download_lines(agent.last_downloads):
            view.item({"kind": "note", "message": line}, dry_run=dry_run)

    def _plan_ready(steps: list[dict[str, Any]], notes: list[str]) -> None:
        _tell_downloads()
        view.downloads = {str(d.get("path")) for d in agent.last_downloads}
        view.plan(steps, notes)

    agent.plan_ready = _plan_ready
    agent.intent_started = view.step
    agent.keep_intermediates = keep_intermediates
    # A batch chain's failures, as they happen: on stderr like any error, even with --silent.
    agent.batch_failed = lambda line: click.echo(line, err=True)
    wire_downloads(
        agent,
        allow_download=permitted.download,
        interactive=_stdin_is_terminal() and not dry_run,
        ask=_ask_yes_no,
    )

    # An unsafe phrase never reaches the model, and is answered in native's words.
    if is_unsafe_request(prompt_str, agent.unsafe_phrases):
        reason = f"this request is blocked by the {skill} skill's safety policy."
        view.control({"tool": "reject", "args": {"reason": reason}})
        view.finish(dry_run=dry_run)
        sys.exit(0)

    # ── model and inference: the one silent stretch ───────────────────────────
    quiet = not verbose and not any(
        os.environ.get(v) for v in ("KNAIF_DEBUG", "KNAIF_DUMP_PLAN", "KNAIF_DUMP_PROMPT")
    )
    load_s: float | None = None
    if not use_mock:
        _say_trained_for(model, model_path, skill_dir)
    with QuietPlanning(view.style, active=quiet and not use_mock):
        if not use_mock:
            started = time.perf_counter()
            agent.orchestrator = _build_orchestrator(
                backend, model, model_path, ollama_url, skill_dir, verbose
            )
            load_s = time.perf_counter() - started
            if agent.orchestrator is None:
                use_mock, load_s = True, None
        started = time.perf_counter()
        try:
            payload = agent.infer(
                prompt_str,
                use_mock=use_mock,
                ollama_model=model or "mistral",
                max_tokens=max_tokens,
                registry_override=retrieve_tools(prompt_str, agent.registry),
            )
        except Exception as exc:  # noqa: BLE001
            infer_error: Exception | None = exc
        else:
            infer_error = None
        infer_s = time.perf_counter() - started
    if not use_mock and agent.orchestrator is None:
        click.echo(
            "No model resolved (empty models.yaml and no --model). Falling back to mock inference.",
            err=True,
        )
    model_file = getattr(agent.orchestrator, "model_file", None)
    label = (
        ui.model_label(Path(model_file).stem) if model_file else (model if not use_mock else None)
    )
    view.header(label)
    if infer_error is not None:
        view.stopped(f"inference failed: {infer_error}")
        sys.exit(1)
    if not use_mock:
        view.planning(load_s, infer_s)
    # The same contract as native `run` under $KNAIF_DUMP_PLAN: the post-gate plan, one marker
    # line on stderr. L3 reads both sides' plans from it to tell a port bug (same plan,
    # different commands) from plan disagreement (release plan R0).
    if os.environ.get("KNAIF_DUMP_PLAN"):
        click.echo(PLAN_DUMP_MARKER + json.dumps(payload, ensure_ascii=False), err=True)
    if not use_mock and verbose:
        tag = model_path or model or _read_recommended_model(skill_dir) or backend
        click.echo(f"backend: {backend}  model: {tag}  inference: {infer_s:.3f} s", err=True)

    # ── a question, a refusal or nothing to do ends the run ───────────────────
    plan_steps = payload.get("plan") or []
    if plan_steps and plan_steps[0].get("tool") in ("clarify", "reject", "done"):
        view.control(plan_steps[0])
        view.finish(dry_run=dry_run)
        sys.exit(0)

    # `--stdout` prints a text answer: a step with none (a video, a PDF) refuses it up front.
    if stdout:
        for step in plan_steps:
            tool = str(step.get("tool"))
            declared = agent.registry.get(tool)
            if tool not in ("clarify", "reject", "done") and not (declared and declared.stdout):
                view.stopped(
                    f"--stdout prints a text answer, and {tool} has none: run it without --stdout."
                )
                sys.exit(1)

    # ── execute ───────────────────────────────────────────────────────────────
    # When running for real, the user's y/n via plan_confirmer authorises
    # destructive tools; pass confirmed=True so those tools don't double-block.
    confirmed = not dry_run

    if effective_require_approval and not _stdin_is_terminal():
        # `--confirm` with nobody to answer: list what the first step would do and stop, as
        # native does.
        _stop_unconfirmed(agent, payload, prompt_str, view)

    if not dry_run and not permitted.overwrite:
        # Replacing a file asks unless `--allow overwrite` (or `--yes`) approved it: preview the
        # plan (no side effects) to learn which outputs it would write, and ask before any
        # of them is replaced. Decline = the same "plan declined" as a declined Proceed?.
        seen: dict[str, Any] = {}
        existing = [
            shown(e, Path.cwd()) for e in _existing_outputs(agent, payload, prompt_str, seen)
        ]
        if existing and view.style is not None:
            # As native: the plan, the step that would replace the file, its commands and a
            # warning, then the question (or the stop); a yes then goes on without drawing them
            # again.
            drawn = _draw_before_replace(view, agent, seen, existing, _plan_ready)
            if drawn is not None:
                _skip_drawn(agent, view, drawn)
        asked = time.perf_counter()
        try:
            replace = overwrite_gate(existing, permitted.overwrite, _ask_yes_no)
        except click.ClickException as exc:
            # Nobody to ask: stopped where native stops, at the step that writes the file.
            steps = payload.get("plan") or []
            taken = {Path(e).name for e in existing}
            idx = next(
                (
                    i
                    for i, s in enumerate(steps)
                    if Path(str((s.get("args") or {}).get("output") or "")).name in taken
                ),
                0,
            )
            view.stopped(chain_failure_context(idx, len(steps) or 1), exc.message)
            sys.exit(1)
        view.waited(time.perf_counter() - asked)
        if not replace:
            view.declined()
            view.finish(dry_run=dry_run)
            sys.exit(0)

    try:
        results = agent.execute_plan(
            payload,
            utterance=prompt_str,
            dry_run=dry_run,
            confirmed=confirmed,
        )
    except PlanWouldFail as exc:
        # The rehearsal found a step that fails, before anything ran.
        view.stopped(exc.where, _without_folder(exc.cause, Path.cwd()))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - an error is printed as native does, never a traceback
        if os.environ.get("KNAIF_DEBUG"):
            raise
        # What the step said, without the working folder (pikepdf named the file by its
        # absolute path); where it stopped when a step raised it, as native's error chain.
        cause = _without_folder(str(exc) or type(exc).__name__, Path.cwd())
        step = getattr(exc, STEP_ATTR, None)
        if step is None:
            view.stopped(cause)
        else:
            view.stopped(chain_failure_context(*step), cause)
        sys.exit(1)
    _tell_downloads()
    if results and (results[0].get("result") or {}).get("status") == "declined":
        view.declined()
        view.finish(dry_run=dry_run)
        sys.exit(0)
    if os.environ.get("KNAIF_DUMP_PLAN"):
        for argv in rendered_argvs(results):
            click.echo(ARGV_DUMP_MARKER + json.dumps(argv, ensure_ascii=False), err=True)

    # The NL clarify gate (run inside execute_plan) can downgrade the plan to a
    # clarify/reject — e.g. an under-specified file or an ungrounded password
    # (grounded_args). Render it like the pre-execution early-exit.
    if results and results[0].get("tool") in ("clarify", "reject", "done"):
        view.control(results[0])
        view.finish(dry_run=dry_run)
        sys.exit(0)

    if not results:
        view.declined()
        view.finish(dry_run=dry_run)
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
                for r in results:
                    item = {
                        "kind": "info",
                        "message": f"{r['tool']} ({r.get('duration_ms', 0):.0f}ms)",
                    }
                    view.item(item, dry_run=dry_run)

    # A batch chain ends with its counts, time and size (stderr: stdout stays the result), and
    # exits non-zero when any file failed (owner, 2026-10-13).
    report = agent.last_batch_report
    if report is not None:
        # The plain view's summary line; the tree puts the failed count and the size on its
        # closing line instead, and a dry run there says nothing more (owner, 2026-10-25).
        if view.style is None:
            if not silent:
                click.echo(report.summary(), err=True)
        elif not dry_run:
            view.close_extra = report.close_extra()
        if report.failed:
            exit_code_holder[0] = 1

    view.finish(dry_run=dry_run)
    if stdout and not dry_run:
        texts = []
        for r in results:
            declared = agent.registry.get(str(r.get("tool")))
            field = declared.stdout if declared else None
            value = (r.get("result") or {}).get(field) if field else None
            if isinstance(value, str):
                texts.append(value)
        if texts:
            joined = "\n".join(t.rstrip("\n") for t in texts) + "\n"
            # As bytes, so the pipe gets plain `\n` on every OS, as native writes it: a
            # text stream turns each into `\r\n` on Windows.
            buffer = getattr(text_out, "buffer", None)
            if buffer is not None:
                text_out.flush()
                buffer.write(joined.encode("utf-8"))
                buffer.flush()
            else:
                text_out.write(joined)
                text_out.flush()
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
@click.option("-m", "--model", default=None, metavar="NAME|PATH")
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
        name, model_path = _model_or_path(model)
        _say_trained_for(name, model_path, skill_dir)
        orchestrator = _build_orchestrator(backend, name, model_path, ollama_url, skill_dir, False)
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


#: Libraries whose own warnings a run does not print unless `--verbose` (pypdf: "invalid pdf
#: header", "EOF marker not found"). Native prints none.
_QUIET_LOGGERS = ("pypdf",)


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
        import msvcrt

        fileno = sys.stderr.fileno()
        handle = msvcrt.get_osfhandle(fileno)
        mode = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return  # stderr is already a pipe — click won't use _WindowsConsoleWriter

        # GetConsoleMode succeeded: click WILL try _WindowsConsoleWriter →
        # WriteConsoleW, which may fail on this handle.
        sys.stderr = _console_safe_stderr(sys.stderr)
    except Exception:
        pass


def _console_safe_stderr(orig: Any) -> Any:
    """*orig* wrapped so that click's _is_console() (which calls f.fileno()) sees
    UnsupportedOperation and skips the Win32 console path. Writes are delegated to the original
    stream, which uses WriteFile and works correctly. `knaif_real` is that stream: the terminal
    view looks through the wrapper to it (`run_view._is_terminal`), or the CLI printed the plain
    view in every terminal (owner, 2026-10-24)."""
    import io

    class _Stderr:
        encoding = getattr(orig, "encoding", "utf-8")
        errors = getattr(orig, "errors", "replace")
        knaif_real = orig

        def write(self, s: str) -> int:
            return int(orig.write(s))

        def flush(self) -> None:
            orig.flush()

        def fileno(self) -> int:
            raise io.UnsupportedOperation("fileno")

        def isatty(self) -> bool:
            return False

        def __getattr__(self, name: str) -> Any:
            return getattr(orig, name)

    return _Stderr()


def main() -> None:
    enable_utf8_console()
    _fix_windows_stderr()
    cli()


if __name__ == "__main__":
    main()
