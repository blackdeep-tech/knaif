# Big-LLM Comparison — premium agents vs. local knaif

**Status:** Done · **Created:** 2026-06-27 · **Updated:** 2026-07-02 · **Completed:** 2026-07-02
**Owner:** eval · **Ref:** docs/BIG_LLM_HANDOFF.md
**See also:** [Experiment — local knaif vs. a premium agent on real-world requests](../experiments/2026-07-02-agent-vs-knaif-realworld.md) (per-request result/speed/cost head-to-head; the "one user, one request" complement to this aggregate-scoreboard plan)

> **Kept 2026-07-23** (S7 decision — research findings, and the reproduction runbook for
> them). Tier 1 listed it as a zero-inbound delete; that is now **false** —
> [CORPUS_AUTHORING_STEPS.md](../CORPUS_AUTHORING_STEPS.md) points here as one of the two
> follow-on tracks after corpus authoring. Tasks 1–5 are done and Task 6 is an open
> invitation to add another provider, so this is a live runbook, not history.
>
> **The result is only half-published, which is why the extraction mattered.** The run at
> `evals/runs/2026-07-02_big-llm-comparison_success/` is covered by the `evals/**`
> allowlist: `report.md` and the premium arm's `score.json` are tracked, but the
> `COMPARISON.md` write-up and the `_generate_premium_arm.py` cost model are **not** and
> will not exist in a fresh clone. Extracted before that could strand them:
> - the **cost-and-speed methodology** — the token-estimate heuristic and its caveats, and
>   the structural point that the arms are *not token-symmetric* (local emits a JSON plan,
>   premium emits the raw ffmpeg line, so cost describes the premium side only) →
>   [BIG_LLM_HANDOFF.md](../BIG_LLM_HANDOFF.md#cost-and-speed--what-is-and-isnt-comparable),
>   which specified `elapsed_ms` but nothing about cost;
> - the **decomposition of the gap** — outcome accuracy 1.000 vs 0.905 against a success
>   spread of only 0.989 vs 0.967, i.e. the local model loses by *misrouting*, not by
>   generating worse ffmpeg → [MODELS.md §4.4](../MODELS.md), which had the headline
>   numbers but described the gap only as "the hard, ambiguous, and multilingual tail".
>
> Paths repointed (`eval_results/` → `evals/`, `src/skills/` → `skills/`) — the stale
> corpus path sat inside the prompt template meant to be pasted into a premium-agent
> session, so following the runbook would have handed the agent a file that no longer
> exists. Superseded original status note follows.
>
> **Original status note:** Extracted from the eval roadmap's Phase 3 (that roadmap has
> since been retired; its durable guidance lives in `docs/EVAL_VERIFICATION_SOP.md` and
> `docs/CORPUS_AUTHORING_STEPS.md`). The handoff **contract already exists**
> (`docs/BIG_LLM_HANDOFF.md`); what was missing at the time was a run.
>
> **2026-07-02 refresh:** The final-quality eval work has landed: `success_criteria`,
> `meta.json` timing, local-shaped `score-external` output, and the multi-arm report are
> in place. This plan is now a focused runbook for producing the first premium arm and
> comparing it against selected current local scoreboards.

**Goal:** Produce an apples-to-apples comparison of local knaif + small models vs.
premium agents running raw ffmpeg — same corpus, same `success` verifier, same report —
so **quality, speed, and cost** are directly comparable.

## Cost & speed dimension (added 2026-07-02)

Beyond quality (the `success` score) we compare **speed** and **cost** per generated line:

- **Local (llama.cpp)** — treated as **free** (local GPU). We track **inference time only**
  (`latency_ms` per row, already recorded by the runner); tokens are *not* tracked.
- **Premium (API model)** — the meaningful cost is API tokens. Since the accurate path
  (`count_tokens`) needs an API key we don't run here, the premium arm records a **rough
  estimate** per line in `meta.json`: `est_input_tokens` / `est_output_tokens`
  (a chars/≈3.8 heuristic — *not* a real Claude tokenizer, and not `tiktoken`, which
  undercounts Claude), `est_cost_usd` (`input/1e6·$5 + output/1e6·$25`, the Opus 4.8
  rates), and `est_llm_latency_ms` (a rough `output_tokens ÷ throughput` figure, not a
  measurement). `elapsed_ms` stays the real ffmpeg execution time (time-to-artifact).

The two arms are **not** token-symmetric by construction: the local model emits a JSON
*plan* that deterministic code turns into ffmpeg, while the premium model emits the raw
ffmpeg line — so "tokens"/cost apply to the premium API side only. Treat the premium cost
as an order-of-magnitude estimate for framing the quality gap ("does the local 4B's
quality gap justify $X/1000 requests of premium?"), not a billing figure.

---

## Background

The scorer already grades external (premium-agent) outputs with the **same `success`
verifier** as local models and emits the **same scoreboard schema**, so a premium arm
drops straight into the side-by-side HTML report. The full output layout the agent must
produce is defined in `docs/BIG_LLM_HANDOFF.md` — that contract is the source of truth;
this plan only sequences the run.

Eval runs follow the repo convention — save under
`evals/runs/<YYYY-MM-DD>_<label>_<verifier>/` and add a row to
`evals/INDEX.md`. For this comparison, use a focused comparison root rather
than all of `evals/runs/`; otherwise the report will include every historical
arm and become noisy.

## Tasks

> **Cross-platform:** run every shell block below in **bash** — native bash on Linux/WSL2,
> or **Git Bash** on Windows (which Claude Code uses for its Bash tool). All commands
> (`just`, `uv`, `cp`, `mkdir -p`, `source`, the heredoc, forward-slash paths) behave
> identically across platforms; the report opens via `uv run python -m webbrowser` (Task 4)
> rather than an OS-specific opener. Nothing here requires WSL2 — that was only needed for
> unsloth fine-tuning, which is not part of this plan.

### - [x] 1. Prepare the comparison root

First write the comparison variables to a **sourceable env file** so every task (and every
new shell) shares the same values instead of re-declaring throwaway shell vars:

```bash
# One-time: define the arm and persist it to a sourceable file.
cat > evals/runs/2026-07-02_big-llm-comparison.env <<'EOF'
export RUN_ROOT=evals/runs/2026-07-02_big-llm-comparison_success
export AGENT=claude-code           # harness / agent label
export MODEL=claude-opus-4-8       # model the agent runs on (e.g. claude-opus-4-8, claude-fable-5)
export ARM="${AGENT}_${MODEL}"     # report column + subdir; keep model in the name so arms are self-documenting
EOF

# At the start of every task's shell session, load them:
source evals/runs/2026-07-02_big-llm-comparison.env

mkdir -p "$RUN_ROOT/local" "$RUN_ROOT/$ARM"
just eval-fixtures ffmpeg
```

Both `AGENT` and `MODEL` are free variables — edit the `.env` file to swap in any
harness/model pair (e.g. `AGENT=gpt-agent MODEL=gpt-5`, or `MODEL=claude-fable-5` for the
frontier ceiling), then re-`source` it. The arm's subdirectory is `$ARM`, so distinct
pairs land in sibling folders and each renders as its own report column. `claude-opus-4-8`
is the recommended default premium model; use `claude-fable-5` only when you specifically
want the top-of-frontier arm. The `.env` file sits next to the run root (not inside it) so
`just eval-report` doesn't pick it up as an arm. These are shell/`export` variables only —
no tooling reads them; they exist to build paths and tell you which model to run.

Copy the current local baseline — the newest fixed 4B scoreboard — into the local arm
folder:

```bash
cp evals/runs/2026-07-02_4b-v3-cjkfix_success/ffmpeg_qwen3-4b-sft-v3-flat-q4_success.json "$RUN_ROOT/local/"
```

This is the most representative current local quality for a fair gap analysis. Copying a
scoreboard is the fast path but carries no output artifacts, so the HTML report shows
scores + latency for the local arm without playable local media. If you later want
playable local media side-by-side, rerun the arm with `--keep-artifacts` instead of
copying:

```bash
just eval-success ffmpeg \
  --backends qwen3-4b-sft-v3-flat-q4 \
  --config eval_backends.yaml \
  --fixture-dir sandbox/fixtures/ffmpeg/ \
  --save "$RUN_ROOT/local" \
  --keep-artifacts
```

### - [x] 2. Hand the corpus to a premium agent

Give the agent three things together, in one session:

1. **Contract:** `docs/BIG_LLM_HANDOFF.md` — read first; defines the exact output layout.
2. **Corpus:** `skills/ffmpeg/data/eval.jsonl`
3. **Fixtures:** `sandbox/fixtures/ffmpeg/` (run `just eval-fixtures ffmpeg` first).

Run the agent on the configured `$MODEL` (the default premium arm is `claude-opus-4-8`).

The premium agent runs in its own session and will **not** inherit these shell vars, so
print the resolved literal output path and paste that into the prompt:

```bash
source evals/runs/2026-07-02_big-llm-comparison.env
echo "$RUN_ROOT/$ARM"   # e.g. evals/runs/2026-07-02_big-llm-comparison_success/claude-code_claude-opus-4-8
```

Prompt template (paste into a new premium-agent session — substitute the resolved
`$RUN_ROOT/$ARM` path from the echo above into the output path):

```
You are given:
  - Contract:  docs/BIG_LLM_HANDOFF.md  (read this first — it defines the exact output layout)
  - Corpus:    skills/ffmpeg/data/eval.jsonl
  - Fixtures:  sandbox/fixtures/ffmpeg/

Follow the contract exactly. For every utterance of every row:
  1. Read utterances[utterance_idx].
  2. Produce and run an ffmpeg command using the fixture file as input.
  3. Write results to $RUN_ROOT/$ARM/<row_id>__<utterance_idx>/
     - cmd.txt   — the exact command you ran
     - out.<ext> — the output file
     - meta.json — {"elapsed_ms": N}

For clarify/reject rows write only cmd.txt: "clarify: <reason>" or "reject: <reason>".
```

### - [x] 3. Score the external arm

```bash
source evals/runs/2026-07-02_big-llm-comparison.env
just eval-score-external ffmpeg "$RUN_ROOT/$ARM" \
  --fixture-dir sandbox/fixtures/ffmpeg/
```

`score.json` lands in the same schema as local results — directly comparable.

### - [x] 4. Surface in the combined report

```bash
source evals/runs/2026-07-02_big-llm-comparison.env
just eval-report ffmpeg "$RUN_ROOT"
uv run python -m webbrowser "$RUN_ROOT/report.html"   # cross-platform open (Windows/macOS/Linux)
```

The report shows the selected local arms and the premium arm side by side, with
playable media where artifacts exist and latency from `meta.json`. Use it for gap
triage: where does local knaif lose to the premium arm, and is the gap a retrieval
or routing bug, an expander/workflow bug, a verifier/corpus issue, or a genuine
capability gap?

### - [x] 5. Record and triage the comparison

- Add the run to `evals/INDEX.md`.
- Mark the TODO checkpoint once local + premium arms render side by side.
- Capture the review-log triage for premium-wins/local-fails rows.

### - [ ] 6. (optional) Repeat per premium provider

Re-run Tasks 1–4 with a different `AGENT` / `MODEL` pair — each distinct `$ARM` lands
in its own sibling subdirectory under the same comparison root (e.g.
`$RUN_ROOT/gpt-agent_gpt-5/` or `$RUN_ROOT/claude-code_claude-fable-5/`), and the report
renders each as its own column. This is also how you compare two models on the *same*
harness (e.g. `claude-code_claude-opus-4-8` vs `claude-code_claude-fable-5`).

**Done when:** at least one premium arm is scored with the `success` verifier and
appears alongside the local arms in the report, and the local-vs-premium gaps are
triaged in the review log.
