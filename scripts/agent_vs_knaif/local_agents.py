"""The general agents of the local comparison, each run once per request on a local model.

Plan and rules: docs/plans/2026-11-25-local-agents-comparison.md. Every adapter:

- reaches the model only through the recording proxy (`RunCtx.endpoint`), never the server itself;
- runs from its own home folder (`RunCtx.home`: `HOME`, `USERPROFILE`, `APPDATA`, ...), so no
  user-level instructions, memory or credentials of this machine take part, and the agent's
  settings are the ones written here;
- is set to act without stopping to ask for approval, as in the earlier comparisons;
- receives the instruction text with the request, and never through a `.cmd` shim's command line:
  Windows cuts such an argument at its first newline (found 2026-11-27).

`parse` reads the agent's own output into `{final, agent_error, cleanup_error, agent_turns,
agent_tools}`. Tokens and turns are not taken from the agents: the harness reads them, the same way
for every agent, from the proxy's record and the model server's log.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: The text every agent receives, with the request in place of `{request}` (plan: *Prompting*).
INSTRUCTIONS = """\
You are working in the current folder, which holds the user's files. These programs are available from the shell:
- ffmpeg and ffprobe, for video and audio files;
- python, with the pypdf and pikepdf libraries, for PDF files.
Carry out the user's request below with these programs. Write the result as a new file in the current folder, next to the original.
Run the commands yourself with your tools; do not stop after saying what you will do.
Never delete or overwrite a file unless the request asks you to.
If the request is unclear or leaves out something you need, do not guess: ask one short question and stop.
When you are done, say in one sentence what you did.

Request: {request}
"""

#: The context every arm's model runs with (plan: *Serving*); Qwen3-4B's native length.
CONTEXT = 32768


@dataclass
class RunCtx:
    model: str  # the model's name on the server (e.g. `stock-qwen3-4b`)
    workdir: Path  # the run's fresh folder, holding the fixture
    message: str  # the instruction text with the request
    message_file: Path  # where the message is written for agents that read it from a file
    endpoint: str  # the recording proxy, e.g. http://127.0.0.1:11436
    home: Path  # this agent's own home folder for this run
    tools: Path  # the folder the agents are installed in
    timeout_s: int
    max_turns: int


@dataclass
class LocalAdapter:
    name: str
    #: argv printing the agent's version, relative to the tools folder
    version_argv: Callable[[Path], list[str]]
    build: Callable[[RunCtx], tuple[list[str], dict[str, str]]]
    parse: Callable[[str, str], dict]


def home_env(home: Path) -> dict[str, str]:
    """Environment that makes `home` the agent's home on Windows, Linux and macOS."""
    home.mkdir(parents=True, exist_ok=True)
    roaming = home / "AppData" / "Roaming"
    local = home / "AppData" / "Local"
    for d in (roaming, local, home / ".config", home / ".local" / "share", home / ".cache"):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(roaming),
        "LOCALAPPDATA": str(local),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }


def _node(tools: Path) -> Path:
    return next(tools.glob("node/node-v*-win-x64"), tools / "node")


def _empty(error: str | None = None) -> dict:
    return {
        "final": "",
        "agent_error": error,
        "cleanup_error": False,
        "agent_turns": None,
        "agent_tools": None,
    }


# ── OpenClaw ─────────────────────────────────────────────────────────────────
def _openclaw_build(ctx: RunCtx) -> tuple[list[str], dict[str, str]]:
    ctx.message_file.write_text(ctx.message, encoding="utf-8")
    env = home_env(ctx.home)
    env["OPENCLAW_STATE_DIR"] = str(ctx.home / ".openclaw")
    provider = {
        "baseUrl": ctx.endpoint,
        "models": [
            {
                "id": ctx.model,
                "name": ctx.model,
                "contextWindow": CONTEXT,
                "reasoning": False,
                "input": ["text"],
            }
        ],
    }
    argv = [
        str(ctx.tools / "npm" / "openclaw.cmd"),
        "agent",
        "exec",
        "--isolated",
        "--json",
        "--thinking",
        "off",
        "--timeout",
        str(ctx.timeout_s),
        "--cwd",
        str(ctx.workdir),
        "--model",
        f"ollama/{ctx.model}",
        "--config-override",
        json.dumps({"models": {"providers": {"ollama": provider}}}),
        "--message-file",
        str(ctx.message_file),
    ]
    return argv, env


def _openclaw_parse(stdout: str, stderr: str) -> dict:
    try:
        d = json.loads(stdout[stdout.index("{") :])
    except ValueError:
        return _empty(f"unparsed output: {(stdout or stderr)[-200:]}")
    err = d.get("error") or {}
    cleanup = err.get("kind") == "cleanup"
    return {
        "final": d.get("final") or "",
        "agent_error": None if (not err or cleanup) else f"{err.get('kind')}: {err.get('message')}",
        "cleanup_error": cleanup,
        "agent_turns": d.get("assistantTurns"),
        "agent_tools": (d.get("toolSummary") or {}).get("tools"),
    }


# ── Goose ────────────────────────────────────────────────────────────────────
def _goose_build(ctx: RunCtx) -> tuple[list[str], dict[str, str]]:
    ctx.message_file.write_text(ctx.message, encoding="utf-8")
    env = home_env(ctx.home)
    env.update(
        {
            "GOOSE_PROVIDER": "ollama",
            "GOOSE_MODEL": ctx.model,
            "OLLAMA_HOST": ctx.endpoint,
            "GOOSE_MODE": "auto",
            "GOOSE_DISABLE_KEYRING": "1",
            "GOOSE_TELEMETRY_ENABLED": "false",
            "GOOSE_CONTEXT_LIMIT": str(CONTEXT),
        }
    )
    argv = [
        str(ctx.tools / "goose" / "goose-package" / "goose.exe"),
        "run",
        "--instructions",
        str(ctx.message_file),
        "--no-session",
        "--with-builtin",
        "developer",
        "--max-turns",
        str(ctx.max_turns),
        "--output-format",
        "json",
    ]
    return argv, env


def _goose_parse(stdout: str, stderr: str) -> dict:
    try:
        d = json.loads(stdout[stdout.index("{") :])
    except ValueError:
        return _empty(f"unparsed output: {(stdout or stderr)[-200:]}")
    msgs = d.get("messages") or []
    final, tools, turns = "", [], 0
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        turns += 1
        for part in m.get("content") or []:
            if part.get("type") == "text":
                final = part.get("text") or final
            elif part.get("type") in ("toolRequest", "tool_request"):
                call = (part.get("toolCall") or {}).get("value") or part.get("toolCall") or {}
                tools.append(call.get("name") or "?")
    return {
        "final": final,
        "agent_error": d.get("error"),
        "cleanup_error": False,
        "agent_turns": turns or None,
        "agent_tools": tools or None,
    }


# ── OpenCode ─────────────────────────────────────────────────────────────────
def _opencode_build(ctx: RunCtx) -> tuple[list[str], dict[str, str]]:
    env = home_env(ctx.home)
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "permission": {"*": "allow"},
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama (local)",
                "options": {"baseURL": f"{ctx.endpoint}/v1"},
                "models": {
                    ctx.model: {
                        "name": ctx.model,
                        "tool_call": True,
                        "reasoning": False,
                        "limit": {"context": CONTEXT, "output": 8192},
                    }
                },
            }
        },
    }
    path = ctx.home / "opencode.json"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    env["OPENCODE_CONFIG"] = str(path)
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    argv = [
        str(ctx.tools / "npm" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"),
        "run",
        "--format",
        "json",
        "--model",
        f"ollama/{ctx.model}",
        "--auto",
        "--dir",
        str(ctx.workdir),
        ctx.message,
    ]
    return argv, env


def _opencode_parse(stdout: str, stderr: str) -> dict:
    final_parts: list[str] = []
    tools: list[str] = []
    steps = 0
    error = None
    seen = False
    for line in stdout.split("\n"):  # JSON events: never splitlines()
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        seen = True
        kind = evt.get("type")
        part = evt.get("part") or {}
        if kind == "text":
            final_parts.append(part.get("text") or "")
        elif kind == "tool_use":
            tools.append(part.get("tool") or "?")
        elif kind == "step_start":
            steps += 1
        elif kind == "error":
            e = evt.get("error") or {}
            error = (e.get("data") or {}).get("message") or e.get("name") or "error"
    if not seen:
        return _empty(f"unparsed output: {(stdout or stderr)[-200:]}")
    return {
        "final": final_parts[-1] if final_parts else "",
        "agent_error": error,
        "cleanup_error": False,
        "agent_turns": steps or None,
        "agent_tools": tools or None,
    }


# ── Open Interpreter (the Rust agent; Codex exec protocol) ───────────────────
def _interpreter_build(ctx: RunCtx) -> tuple[list[str], dict[str, str]]:
    env = home_env(ctx.home)
    oi_home = ctx.home / ".openinterpreter"
    oi_home.mkdir(parents=True, exist_ok=True)
    env["INTERPRETER_HOME"] = str(oi_home)
    env["OPEN_INTERPRETER_HOME"] = str(oi_home)
    env["CODEX_HOME"] = str(oi_home)
    provider = (
        f'{{name="Ollama (local)", base_url="{ctx.endpoint}/v1", wire_api="chat", '
        f"request_max_retries=0, stream_max_retries=0}}"
    )
    argv = [
        str(ctx.tools / "interpreter" / "bin" / "interpreter.exe"),
        "exec",
        "--json",
        "--chat-completions",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
        str(ctx.workdir),
        "-m",
        ctx.model,
        "-c",
        'model_provider="local"',
        "-c",
        f"model_providers.local={provider}",
        "-c",
        f"model_context_window={CONTEXT}",
        ctx.message,
    ]
    return argv, env


def _interpreter_parse(stdout: str, stderr: str) -> dict:
    final, tools, turns, error, seen = "", [], 0, None, False
    for line in stdout.split("\n"):  # JSON events: never splitlines()
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        seen = True
        item = evt.get("item") or {}
        if evt.get("type") == "item.completed":
            if item.get("type") == "agent_message":
                final = item.get("text") or final
            elif item.get("type") in ("command_execution", "mcp_tool_call", "file_change"):
                tools.append(item.get("type"))
        elif evt.get("type") == "turn.completed":
            turns += 1
        elif evt.get("type") in ("error", "turn.failed"):
            error = (evt.get("error") or {}).get("message") or evt.get("message") or "error"
    if not seen:
        return _empty(f"unparsed output: {(stdout or stderr)[-200:]}")
    return {
        "final": final,
        "agent_error": error,
        "cleanup_error": False,
        "agent_turns": turns or None,
        "agent_tools": tools or None,
    }


ADAPTERS: dict[str, LocalAdapter] = {
    "openclaw": LocalAdapter(
        "openclaw",
        lambda t: [str(t / "npm" / "openclaw.cmd"), "--version"],
        _openclaw_build,
        _openclaw_parse,
    ),
    "goose": LocalAdapter(
        "goose",
        lambda t: [str(t / "goose" / "goose-package" / "goose.exe"), "--version"],
        _goose_build,
        _goose_parse,
    ),
    "opencode": LocalAdapter(
        "opencode",
        lambda t: [
            str(t / "npm" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"),
            "--version",
        ],
        _opencode_build,
        _opencode_parse,
    ),
    "interpreter": LocalAdapter(
        "interpreter",
        lambda t: [str(t / "interpreter" / "bin" / "interpreter.exe"), "--version"],
        _interpreter_build,
        _interpreter_parse,
    ),
}


def agent_path(tools: Path, ffmpeg_dir: Path) -> str:
    """The PATH every agent gets: Windows itself, ffmpeg/ffprobe, the PDF Python, and Node for
    the agents that run on it. Nothing else of this machine (plan: *Tools*)."""
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    parts = [
        root / "System32",
        root,
        root / "System32" / "WindowsPowerShell" / "v1.0",
        ffmpeg_dir,
        tools / "pyenv" / "Scripts",
        _node(tools),
        tools / "npm",
    ]
    return os.pathsep.join(str(p) for p in parts)
