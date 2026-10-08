"""Token → USD normalization so three differently-metered agents are comparable.

Each premium agent reports its cost in a different, non-comparable unit — Claude in
real USD, Copilot in GitHub "AI Credits", Codex in raw tokens against a flat ChatGPT
subscription. The one thing all three *do* report is **token usage**, so we normalize
on that: given the measured (uncached-input, cached-input, cache-write, output) token
counts for a request, compute the **API-equivalent USD** — what that exact request
would cost billed per-token via the underlying model's public API.

This is an *estimate of API-equivalent cost*, not what GitHub/OpenAI actually charge
through their own products (a Copilot seat or ChatGPT Plus bundles usage). It puts all
arms on one honest, reproducible dollar basis derived only from measured tokens. For
the Claude arm it should closely reproduce the CLI's own `total_cost_usd` — a built-in
sanity check on the method.

Rates are per **1,000,000 tokens**, from each provider's public pricing page
(captured 2026-07-02):
  - claude-opus-4-8 / claude-sonnet-5 — platform.claude.com (Anthropic)
  - gpt-5.5 — developers.openai.com/api/docs/pricing (OpenAI)
Cache-read is ~0.1× input for all three; Anthropic charges a 1.25× cache-*write*
premium, OpenAI has no separate write premium (new tokens are just full input price).
"""

from __future__ import annotations

# per-1M-token USD rates for the model each agent actually runs.
#
# cache_write is priced at the **plain input rate**, deliberately NOT Anthropic's 1.25×
# cache-*write* premium. Two reasons: (1) uniformity — only the Claude arm reports
# cache-creation tokens separately (Copilot/Codex fold new input into one "uncached"
# bucket), so charging a premium to Claude alone would make it non-comparable; (2) the
# premium is a one-time cost per unique prefix, amortized across every request that
# reuses it — in cold mode each request pays a fresh write, the pessimal case, so the
# premium would overstate realistic steady-state cost. Cross-checked against the Claude
# CLI's own measured total_cost_usd, this plain-rate estimate tracks it closely; the
# earlier 1.25× version overshot the real bill by ~1.6×.
RATES: dict[str, dict[str, float]] = {
    # Anthropic Opus 4.8 — the Claude Code arm.
    "claude-opus-4-8": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 5.00},
    # Anthropic Sonnet 5 — Copilot CLI's default model. Standard rates; an introductory
    # $2/$10 in/out applies through 2026-08-31 (the `-intro` entry prices at those).
    "claude-sonnet-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.00},
    "claude-sonnet-5-intro": {
        "input": 2.00,
        "output": 10.00,
        "cache_read": 0.20,
        "cache_write": 2.00,
    },
    # OpenAI GPT-5.5 — the Codex arm. No separate cache-write premium.
    "gpt-5.5": {"input": 5.00, "output": 30.00, "cache_read": 0.50, "cache_write": 5.00},
}


def api_equiv_usd(
    model: str, uncached_in: int, cache_read: int, cache_write: int, output: int
) -> float | None:
    """API-equivalent USD for one request from its measured token split.

    `uncached_in`  full-price input tokens (Anthropic: `input_tokens`; OpenAI/Copilot:
                   total input minus the cached portion).
    `cache_read`   input tokens served from cache (~0.1× input).
    `cache_write`  tokens written to cache at a premium (Anthropic only; 0 elsewhere).
    `output`       output tokens (includes reasoning/thinking tokens where billed).
    """
    r = RATES.get(model)
    if r is None:
        return None
    total = (
        (uncached_in or 0) * r["input"]
        + (cache_read or 0) * r["cache_read"]
        + (cache_write or 0) * r["cache_write"]
        + (output or 0) * r["output"]
    )
    return round(total / 1_000_000, 4)


# ── 2026-10-01 rerun (docs/plans/2026-10-01-llm-comparison-rerun.md) ─────────────────────
#
# Official API rates per 1M tokens, captured 2026-10-01 from
#   https://platform.claude.com/docs/en/about-claude/pricing      (Anthropic)
#   https://developers.openai.com/api/docs/pricing                 (OpenAI, standard tier)
#
# Unlike the 2026-07-02 method above, every arm is priced at the rate its provider actually
# charges for what its CLI did. Claude Code writes to the 1-HOUR prompt cache: only the 1h
# write rate (2x input) reproduces the CLI's own `total_cost_usd` (smoke rows, 2026-10-01:
# Opus $0.3948, Sonnet $0.0747), and every Claude row of the run is checked against it.
# OpenAI has no cache-write charge. Its rates are the short-context tier (<=272K input tokens
# per API call; an agent session is several calls, each far below that). Codex's
# `output_tokens` already includes reasoning tokens, so reasoning is not added again.
# Copilot reports GitHub credits, not dollars: its tokens are priced at OpenAI's API rate for
# the model it ran.
RATES_2026_10: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.00, "cache_read": 0.20, "cache_write": 8.00, "output": 20.00},
    "claude-sonnet-5-5": {"input": 2.00, "cache_read": 0.20, "cache_write": 4.00, "output": 10.00},
    "gpt-6-astra": {"input": 10.00, "cache_read": 1.00, "cache_write": 10.00, "output": 50.00},
    "gpt-6.1-sol": {"input": 2.00, "cache_read": 0.10, "cache_write": 2.00, "output": 10.00},
    "gpt-5.6-terra": {"input": 2.00, "cache_read": 0.20, "cache_write": 2.00, "output": 12.00},
}


def api_cost_2026_10(
    model: str, uncached_in: int, cache_read: int, cache_write: int, output: int
) -> float | None:
    """API-equivalent USD at the 2026-10-01 rates; None for a model with no published price."""
    r = RATES_2026_10.get(model)
    if r is None:
        return None
    total = (
        (uncached_in or 0) * r["input"]
        + (cache_read or 0) * r["cache_read"]
        + (cache_write or 0) * r["cache_write"]
        + (output or 0) * r["output"]
    )
    return total / 1_000_000
