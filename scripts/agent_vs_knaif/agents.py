"""Pluggable premium-agent adapters for the knaif-vs-agent experiment.

Each adapter knows how to (1) build the argv to run one request headlessly and
(2) parse that CLI's own output into a normalized metrics dict:

    {cost, cost_unit, in_tok, out_tok, duration_s, turns}

Add a new agent by adding one `Adapter` to `AGENTS`. `claude` and `copilot` are
real CLIs on PATH; `codex` has no bare `codex` on PATH on this machine, so it
resolves the native binary shipped by the OpenAI Codex desktop app instead (see
`_codex_bin`) — avoids Windows npx/.cmd-shim subprocess quoting entirely.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass


def _ktoks(s: str) -> int:
    s = s.strip().lower().replace(",", "")
    if s.endswith("k"):
        return int(float(s[:-1]) * 1000)
    return int(float(s))


@dataclass
class Adapter:
    name: str
    default_model: str
    cost_unit: str
    # build argv given the request prompt, model, and (already-created) cwd
    build_argv: Callable[[str, str], list[str]]
    # parse the CLI's stdout + measured wall-clock seconds into normalized metrics
    parse: Callable[[str, float], dict]
    isolated_note: str = ""


# ── claude (Anthropic Claude Code) — full $/token/turn metering ──────────────
def _claude_argv(prompt: str, model: str) -> list[str]:
    return [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]


def _claude_parse(stdout: str, wall_s: float) -> dict:
    d = json.loads(stdout)
    mu = d.get("modelUsage") or {}
    # Anthropic's `inputTokens` is the UNCACHED input; cache read/creation are separate
    # sibling fields (unlike OpenAI/Copilot, where the ↑ total already includes cached).
    uncached_in = sum(u.get("inputTokens", 0) for u in mu.values())
    cache_read = sum(u.get("cacheReadInputTokens", 0) for u in mu.values())
    cache_write = sum(u.get("cacheCreationInputTokens", 0) for u in mu.values())
    out_tok = sum(u.get("outputTokens", 0) for u in mu.values())
    return {
        "cost": d.get("total_cost_usd"),  # CLI's own measured $ — ground truth for the method
        "cost_unit": "usd",
        "uncached_in": uncached_in or d.get("usage", {}).get("input_tokens"),
        "cache_read": cache_read,
        "cache_write": cache_write,
        "in_tok": (uncached_in + cache_read + cache_write)
        or d.get("usage", {}).get("input_tokens"),
        "out_tok": out_tok or d.get("usage", {}).get("output_tokens"),
        "duration_s": round((d.get("duration_ms") or 0) / 1000, 1),
        "turns": d.get("num_turns"),
    }


# ── copilot (GitHub Copilot CLI) — credits + tokens from the text footer ─────
def _copilot_argv(prompt: str, model: str) -> list[str]:
    argv = ["copilot", "-p", prompt, "--allow-all-tools"]
    if model:
        argv += ["--model", model]
    return argv


def _copilot_parse(stdout: str, wall_s: float) -> dict:
    credits = re.search(r"AI Credits\s+([\d.]+)\s*\((\d+)s\)", stdout)
    # between the ↑ and ↓ counts copilot inserts a "(Nk cached, Mk written)" aside —
    # skip over it with a not-↓ class rather than assuming a fixed separator.
    toks = re.search(r"Tokens\s+[↑^]\s*([\d.,]+k?)[^↓v]*[↓v]\s*([\d.,]+k?)", stdout)
    # the ↑ total includes cached; pull the "(Nk cached, …)" aside when present so we
    # can split full-price input from cache-read input for the cost estimate.
    cached_m = re.search(r"\(\s*([\d.,]+k?)\s*cached", stdout)
    in_tok = _ktoks(toks.group(1)) if toks else None
    cache_read = _ktoks(cached_m.group(1)) if cached_m else 0
    return {
        "cost": float(credits.group(1)) if credits else None,
        "cost_unit": "credits",
        "in_tok": in_tok,
        "uncached_in": (in_tok - cache_read) if in_tok is not None else None,
        "cache_read": cache_read,
        "cache_write": 0,
        "out_tok": _ktoks(toks.group(2)) if toks else None,
        "duration_s": float(credits.group(2)) if credits else round(wall_s, 1),
        "turns": None,
    }


# ── codex (OpenAI Codex CLI) ──────────────────────────────────────────────────
# Auth on this machine is a ChatGPT login (subscription), not an API key, so the
# CLI never reports a per-request dollar cost — only token usage. `--json` emits
# one JSONL event per line; the token counts live on the final `turn.completed`
# event and the reply text on the last `item.completed` (agent_message) event.
def _codex_bin() -> str:
    found = shutil.which("codex")
    if found:
        return found
    # `npx @openai/codex` invokes an npx.cmd/.ps1 shim, which subprocess with
    # shell=False can't launch on Windows. The Codex desktop app installs a real
    # native codex.exe at this fixed path (shares the same ~/.codex auth/config)
    # — prefer it when there's no bare `codex` on PATH.
    win_default = os.path.expandvars(r"%LOCALAPPDATA%\OpenAI\Codex\bin\codex.exe")
    if os.path.exists(win_default):
        return win_default
    return "codex"  # let it fail with a clear error if truly unavailable


def _codex_argv(prompt: str, model: str) -> list[str]:
    # workspace-write sandboxing on this machine spawns commands in an env that
    # doesn't see ffmpeg on PATH (confirmed: it reports ffmpeg "not available"
    # even though the same PATH entry works everywhere else) — bypass it, same
    # as the claude (--dangerously-skip-permissions) and copilot (--allow-all-tools) arms.
    argv = [
        _codex_bin(),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        prompt,
    ]
    if model:
        argv += ["-m", model]
    return argv


def _codex_parse(stdout: str, wall_s: float) -> dict:
    in_tok = out_tok = cached_tok = reasoning_tok = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except Exception:
            continue
        if evt.get("type") == "turn.completed":
            u = evt.get("usage", {})
            in_tok = u.get("input_tokens")
            cached_tok = u.get("cached_input_tokens")
            out_tok = u.get("output_tokens")
            reasoning_tok = u.get("reasoning_output_tokens")
    # OpenAI's `input_tokens` is the TOTAL (cached + uncached); split them for pricing.
    uncached_in = (in_tok - (cached_tok or 0)) if in_tok is not None else None
    return {
        "cost": None,  # ChatGPT-plan auth: consumes plan quota, not metered $/request
        "cost_unit": "tokens (ChatGPT plan)",
        "in_tok": in_tok,
        "uncached_in": uncached_in,
        "cache_read": cached_tok or 0,
        "cache_write": 0,
        "cached_tok": cached_tok,
        "out_tok": out_tok,
        "reasoning_tok": reasoning_tok,
        "duration_s": round(wall_s, 1),
        "turns": None,
    }


AGENTS: dict[str, Adapter] = {
    "claude": Adapter("claude", "claude-opus-4-8", "usd", _claude_argv, _claude_parse),
    # pinned explicitly rather than left to copilot's own (undocumented, log-only) default —
    # confirmed via `~/.copilot/logs/process-*.log`: "Using default model: claude-sonnet-5"
    "copilot": Adapter(
        "copilot",
        "claude-sonnet-5",
        "credits",
        _copilot_argv,
        _copilot_parse,
        isolated_note="copilot may reset cwd to a detected git root — run from a dir outside the repo",
    ),
    "codex": Adapter(
        "codex",
        "gpt-5.5",
        "tokens (ChatGPT plan)",
        _codex_argv,
        _codex_parse,
        isolated_note="no bare `codex` on PATH — resolves the native binary from the "
        "OpenAI Codex desktop install; ChatGPT-login auth so no per-request "
        "$ cost is reported, only tokens",
    ),
}
