# experiment: knaif vs. a premium agent (reproducible)

Per-request, real-world head-to-head — **result · speed · cost** — between the local
`knaif-cli` and a swappable premium coding agent. This is the runnable harness behind
[docs/experiments/2026-07-02-agent-vs-knaif-realworld.md](../../docs/experiments/2026-07-02-agent-vs-knaif-realworld.md).

## Run

```bash
just experiment-agent-vs-knaif                 # claude vs knaif, all scenarios, COLD
just experiment-agent-vs-knaif copilot          # swap the agent
uv run python scripts/agent_vs_knaif/run.py --agent claude --limit 3
```

Writes `RESULTS.md` + `RESULTS.json` next to this README, and prints a per-request table.

**Requires:** `ffmpeg`/`ffprobe` on `PATH` (or pass `--ffmpeg-dir <bin>`), the local model
GGUF for `--knaif-model` (default `knaif-qwen3-4b-v1`), and the chosen agent's CLI installed &
authenticated (`claude`, `copilot`, or `codex`).

## What each arm runs

- **knaif** — the real CLI: `knaif-cli run ffmpeg "<utt>" --backend llama-cpp --model
  knaif-qwen3-4b-v1 --no-dry-run --auto-approve` (best 4B via llama.cpp, thinking-off/json-off, free).
- **agent** — one headless invocation via an adapter in [`agents.py`](agents.py). Each adapter
  builds the argv and parses that CLI's own usage output into
  `{cost, cost_unit, in_tok, out_tok, duration_s, turns}`. `claude` reports real USD +
  tokens + turns; `copilot` reports credits + tokens (on **stderr**); `codex` reports tokens
  only (ChatGPT-subscription auth has no per-request $). [`pricing.py`](pricing.py)
  normalizes all three into a comparable **API-equivalent USD** from the token split.

Every produced file is verified with `ffprobe` (container/codec/resolution/duration/size),
not just "a file exists".

## Cold by default

Each invocation runs in a **fresh unique working directory**, so no request gets a warm
prompt-cache discount from a previous one — costs are consistent and comparable. The agent
arm runs in a unique system-temp dir (also **memory-free**: no `CLAUDE.md` in scope). Pass
`--warm` to instead reuse one dir per arm (demonstrates the cache discount).

## Add a scenario / add an agent

- **Scenario:** append to [`scenarios.yaml`](scenarios.yaml) — give `expect:` file-property
  checks (ffprobe) or `behavior: clarify|reject` for ambiguous/unsafe asks.
- **Agent:** add one `Adapter` to `AGENTS` in [`agents.py`](agents.py) (argv builder + output
  parser). Nothing else changes.

## Caveats

- knaif's inference time is steady-state; a cold `knaif-cli` process also loads the ~2.5 GB
  GGUF (~2s) each run.
- `copilot` may reset cwd to a detected git root — prefer running it from outside the repo.
- `codex` adapter is unverified — confirm its argv + usage-output shape before trusting numbers.
