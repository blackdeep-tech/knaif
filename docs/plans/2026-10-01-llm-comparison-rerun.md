# Rerun the knaif vs premium-agent comparison on the shipped native build

**Status:** Done · **Created:** 2026-10-01 · **Completed:** 2026-10-01
**Owner:** eval · **Ref:** [release-1.2.1](2026-09-30-release-1.2.1.md) ·
[2026-07-02 experiment](../experiments/2026-07-02-agent-vs-knaif-realworld.md) ·
`scripts/agent_vs_knaif/` · `site/org/src/lib/comparison.ts`
**Release:** 1.2.1

**Goal:** Measure again what knaif.org/vs reports — the same real-world requests sent to knaif and
to Claude Code, GitHub Copilot CLI and OpenAI Codex CLI — now with the build users install (native,
CUDA, `knaif-qwen3-4b-v2`) instead of July's Python CLI with v1. Compare with the 2026-07-02 run
first; whether the site and docs change is decided after seeing both.

Not an artifact change: nothing here ships in a release file, so the patch rule allows it. It does
not gate 1.2.1; the numbers should describe the 1.2.1 build, so it runs on that build.

---

## Decisions (owner, 2026-10-01)

1. **knaif arm = the native binary on CUDA**, this box (RTX 5080), `knaif-qwen3-4b-v2`. Not the
   Python CLI: users get the binary.
2. **Only comparable quantities are compared:** tokens, API-equivalent cost, and time.
   Subscription cost is **not** compared: a flat plan has no per-request price, and its limits are
   not published in tokens, so any per-request figure would be invented.
3. **API-equivalent cost** = each request's measured tokens × the model's official per-token API
   price, captured from the provider's pricing page on the run day and cited with that date. It
   is what the request would cost through the API, not what a subscriber pays, and is labelled so.
   knaif's is $0: no API; hardware and electricity are not counted, and the page says so.
4. **The comparison table times knaif as a daemon-mode run would: without the model load.** The
   agents' models are already loaded server-side; knaif loads its model per run until daemon mode
   exists. The table's knaif time is inference + ffmpeg execution. The load is measured on every
   run and stated next to the table ("the first run also loads the model: N s"), never hidden.
5. **Each agent works in its own sandbox folder**, a fresh copy of the fixture per request, separate
   per arm. No arm sees another's files.
6. **Compare before replacing.** The 2026-07-02 snapshot stays until the owner has seen both runs
   side by side. If the site changes, the whole `comparison.ts` snapshot is replaced (its own rule),
   never individual numbers.

## What is measured, per request and arm

Written before the run; the run may not change it.

| Quantity | knaif | Agents |
|---|---|---|
| Outcome | the 07-02 grading, unchanged: ffprobe checks on the produced file (`expect`), or the expected clarify / reject (`behavior`) | same |
| Tokens | prompt + generated tokens of the plan inference (`KNAIF_TIMING`: `prompt_decode (N tokens)`, `generation (N tokens)`) | as each CLI reports them: input (uncached, cache-read, cache-write where reported) and output, reasoning included |
| API-equivalent cost | $0 | tokens × the run-day API rates (`pricing.py`, refreshed and dated) |
| Time (table) | inference + ffmpeg execution, **no model load** (as in daemon mode) | wall clock of the CLI invocation (startup, network, tool calls and ffmpeg included — the CLIs cannot split it) |
| Time (stated beside the table) | model load, measured every run; also kept: inference alone, ffmpeg alone, full wall clock | — |

**The arms are not token-symmetric, and the report says so:** knaif's model emits a JSON plan once;
an agent reads files, calls tools and may loop. Token counts are comparable as "what each needed to
do the request", not as the same unit of work.

## Rules for the run

- **Same scenarios as 07-02** (`scripts/agent_vs_knaif/scenarios.yaml`, unchanged), so the two runs
  are comparable row by row. New scenarios would be a separate run.
- **Repeats (owner, 2026-10-01):** 3 per scenario per arm, in a fixed interleaved order (all six
  arms on a request, then the next request; then the second round). Outcome is reported for every repeat; time and tokens as the median, with min–max.
  07-02 ran once each, so its comparison uses the first repeat as well as the median.
- **Cold for the agents, as in 07-02:** a fresh working folder per invocation, so no request gets a
  prompt-cache discount from a previous one.
- **Versions recorded before the first request:** each CLI's `--version`, the model each actually
  used (from its own output), the knaif binary's sha256 and build kind, GPU driver.
- **Arms and models (owner, 2026-10-01), each at medium effort:**

  | Arm | CLI | Model | Effort flag |
  |---|---|---|---|
  | knaif | native CUDA build | `knaif-qwen3-4b-v2` | — |
  | Claude Code · Opus | `claude` | `claude-opus-5-5` | `--effort medium` |
  | Claude Code · Sonnet | `claude` | `claude-sonnet-5-5` | `--effort medium` |
  | Codex · Astra | `codex exec` | `gpt-6-astra` | `-c model_reasoning_effort="medium"` |
  | Codex · Sol | `codex exec` | `gpt-6.1-sol` | `-c model_reasoning_effort="medium"` |
  | Copilot · Terra | `copilot` | `gpt-5.6-terra` | `--reasoning-effort medium` |

  The Claude and Codex IDs are confirmed from the CLIs (`--help`; Codex's own model list). Copilot's
  is confirmed by one request before the run (C3). 6 arms × 11 requests × 3 repeats = 198 runs.
  07-02 had 4 arms with other models (opus-4-8, sonnet-5, gpt-5.5), so the comparison with it is
  per tool, not per model, and says so.
- **A model with no public API price** gets "no public API price" in the cost column, never an
  estimate from a neighbouring model.
- **User-level instructions and memory** of each CLI (e.g. `~/.codex/AGENTS.md`, Claude's user
  memory) apply in any folder. They are left as they are — that is what a user of this box gets —
  and recorded (present / absent, not their content) with the versions.
- **knaif runs only on an idle GPU (owner, 2026-10-01).** Before every knaif run the harness reads
  the GPU's utilization three times; above 10% it waits, and after 30 minutes it stops the run. The
  reading is saved with the row. (Added after the first full attempt was stopped at 11 of 198 runs:
  the GPU was in use for other work. The repo must not change during a run either — an attempt
  before that was stopped by the guard after 7 runs because of edits to the harness.)
- **No retries, no hand-editing of outputs.** A failed request is a result.
- **Manual verification** of every produced file and every clarify/reject per
  [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md) before any number is quoted.
- **Saved** to `evals/runs/<date>_agent-vs-knaif-native_ffprobe/` with a row in `evals/INDEX.md`.
  No absolute paths or usernames in anything saved (AGENTS.md, *Public Output Hygiene*).

## Safety of the run

The agents run with their tool permissions bypassed (as in 07-02), and on 07-02's destructive
request two of them deleted a file. So:

- **Run root beside the checkout, not in `sandbox/` (owner's rule, 2026-10-01: inside the repo
  only if each agent can be limited to its folder).** It cannot be, on Windows, with these CLIs:
  Codex's sandbox limits writes, not reads, and on 07-02 hid ffmpeg from its commands; Claude Code's
  and Copilot's path rules are checks inside the CLI, not an operating-system boundary, and a shell
  command can still reach other folders. So: one folder per arm and one fresh folder per request,
  beside the repo. Permissions stay as on 07-02 (each CLI's bypass flag), so the agents work as
  their users run them.
- **A guard around every agent invocation:** `git status --porcelain` and a hash of the tracked tree
  before and after; any change stops the run.
- Fixture copies only; no model files, no credentials in the run root.

## Tasks

TDD for every harness change; parsers are tested on recorded output, not on a live CLI.

### - [x] C1 — Native knaif arm

`run_knaif` calls `knaif-cli` (Python) and reads its `intent:` line. Add a native arm: the CUDA
binary (`knaif run ffmpeg … --yes`, `KNAIF_TIMING=1`, plain view since piped), parsing load,
inference, prompt and generated tokens from `[knaif-timing]` and ffmpeg time from the run.

### - [x] C2 — Sandbox per arm and the repo guard

`run_agent` uses `tempfile.mkdtemp` in system temp. Move every arm to the run root above, per arm
and per request, and add the before/after guard.

### - [x] C3 — Rates and versions

Refresh `pricing.py` from the official pricing pages on the run day (date and URL in the file).
Add any model a CLI now defaults to. Record versions (rule above).

### - [x] C4 — Run

All arms, 3 repeats, the rules above. Pair with a progress watcher; hand the owner the terminal
command to follow it.

### - [x] C5 — Verify

Every output and every clarify/reject checked by hand per the SOP; disagreements with the
automatic grade recorded.

### - [x] C6 — Compare with 2026-07-02

Side by side, row by row: outcome, tokens, API-equivalent cost, time. Note what changed besides
knaif (agent versions, models, prices) so a difference is not credited to the wrong side.

### - [x] C7 — Owner decision, then docs and site

The owner decides whether `/vs` changes. If yes: replace the whole `comparison.ts` snapshot and
`MEASURED`, update the experiment write-up (new dated section; the 07-02 one stays), MODELS.md
§4.4 where it quotes this experiment, and run the site gates in [SITE.md](../SITE.md) before merging
to `main`. If no: record why here.

**Done 2026-10-01 — owner: "yes, use the new result for vs; keep the previous results for
history".** `comparison.ts` regenerated whole from the run's raw rows (median of three runs per
cell; six columns), the 07-02 module archived unchanged as `comparison-2026-07-02.ts`, `/vs`
rewritten for the rerun with a section linking the first measurement; new write-up
`docs/experiments/2026-10-01-agent-vs-knaif-native.md`, the 07-02 one kept with a pointer. Also
updated where the old figures were quoted: knaif.org about page, knaif.dev safety / corpus /
models pages, MODELS.md §4.4 (the corpus-level 0.989 vs 0.967 is now dated as July, v1, not
re-measured). Corrected on the way: the safety page called Copilot's Sonnet 5 "the same model
that refused" (Claude Code ran opus-4-8). Site gates: check, build, links, a11y all pass.

## Open questions

None; settled by the owner 2026-10-01 (run root, repeats, models).
