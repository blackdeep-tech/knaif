# Deterministic Clarify — Move Structural Gatekeeping Out of the Model

**Status:** Done · **Created:** 2026-06-06 · **Completed:** —
**Owner:** core · **Ref:** —

> **Status note:** T1–T17 implemented and committed. T16 resolved as a no-op (corpus
> was correct). See **Implementation log** and **Architecture decision** at the bottom.
>
> **Kept 2026-07-22** (S7 decision) for the architecture decision at the bottom. The
> durable rules it established now also live in the shipping docs — the governing rule,
> the clarify gate's input-only scoping, and the static-planning rationale in
> [ARCHITECTURE.md](../ARCHITECTURE.md), the `defaults` tool key in
> [TOOL_SCHEMA.md](../TOOL_SCHEMA.md), and the chaining guidance in
> [VARIABLE_BINDING.md](../VARIABLE_BINDING.md). Those are the current reference; this
> file is the record of how the decisions were reached.
>
> Source references below were written against the pre-monorepo layout (`src/knaif/`,
> `src/skills/`) and have been repointed to current paths; line numbers are omitted where
> the code has since moved.
>
> **Constraint:** This fix must stand on its own with the **base, un-fine-tuned**
> small model (qwen3-4b today, qwen3-1.7b later). Fine-tuning is an *optional* later
> step and MUST NOT be a prerequisite for any behaviour described here.

**Goal:** Move structural gatekeeping (when to clarify vs. act) out of the model and
into deterministic code, so the base small model behaves reliably without fine-tuning.

---

## Problem

`clarify` is currently a tool the small model must *choose*. That makes the
model the gatekeeper for two unrelated jobs:

1. **Intent + argument extraction** — which tool, which files, which values.
   (The model is good at this.)
2. **Structural validation** — are all files specified? do they exist? is a
   required arg missing? (The model is *unreliable* at this, and gets worse as
   the model shrinks.)

This violates the project's founding principle (CLAUDE.md): *"The model only
proposes a plan; deterministic code validates, expands, confirms, and
executes it."*

### Live evidence

`just cli ffmpeg concatenate clip.mov and clip_no_audio.mp4` →

```
❓ CLARIFY: You didn't mention 'combined_video.mp4' in your request —
   which file should I work on?
```

The model understood the intent perfectly, derived a correct output name, then
**flip-flopped into clarify** because of this prompt rule:

> "If the user does not provide an explicit file path → emit clarify asking
> which file."

That rule is meant for *input* files but the model applies it to the *output*
arg too. The Phase-1 cheap eval shows the same class of failure at scale:

| Failure group | Rows | Root cause |
|---|---|---|
| `concat_video` → clarify | 8 | model invents output name, then clarifies about it |
| trim "from X to Y" → clarify (EN/ES/FR/BG/RU) | 6 | structural over-clarify on a fully-specified request |
| missing-file cases surface as `error` | several | preflight raises instead of clarifying |
| invented args (`target`, `output`, `width`) → `error` | 5 | validate_plan raises instead of a clean outcome |

None of these are intent failures. They are the model doing — and the pipeline
crashing on — deterministic validation work.

---

## Design principle: split clarify by category

| Category | Examples | Owner | Mechanism |
|---|---|---|---|
| **A. Structural / factual** | missing file, file doesn't exist, required arg with no default, path escapes sandbox, invalid arg value (`0x0`) | **Deterministic core + skill checks** | downgrade plan → clarify / reject |
| **B. Semantic ambiguity** | "best format", "suitable for streaming", "improve magically", out-of-scope intent (email/cloud) | **Model** (prompt examples) | model emits clarify / reject |

**Category A is the whole point of this plan** and is solved entirely by
deterministic code — no model reliability assumption, no fine-tuning. It is
also **language-independent**: a file-existence check does not care that the
request was Bulgarian, so all multilingual structural failures close at once.

### Works in real CLI mode, not just the eval sandbox

The plan refers to "sandbox files" because that is the eval context, but the
mechanism is **identical for the real `knaif` CLI**, which runs in *open mode*
(`create_agent(skill, ...)` with no `sandbox` → `sandbox=None`; paths resolve
against `root` = cwd, no boundary enforced — [`__init__.py`](../../python/core/knaif/__init__.py),
[`agent.py`](../../python/core/knaif/agent.py)).

The existing preflight is already mode-agnostic:

```python
base = sandbox if sandbox is not None else root   # _preflight_inputs
```

So the file-existence check that drives a deterministic clarify runs against the
**sandbox dir in eval mode** and against **cwd in real CLI mode** — same code
path. The sandbox-escape → `reject` rule is only meaningful when a sandbox is
set; in open CLI mode there is no escape concept (the user's filesystem is fair
game), and that branch is already guarded by `if sandbox is not None`
([`_reporting.py`](../../skills/ffmpeg/python/_reporting.py)). No CLI-specific work is
needed.

**Category B stays with the model** but shrinks to genuinely semantic cases.
It is improved with **prompt examples only** — explicitly NOT fine-tuning.
Fine-tuning, if ever run, would further help B; it is never required for A.

> We are not eliminating model-side clarify. We are ensuring the model is never
> the arbiter of a *fact it cannot know* (does this file exist?). That is the
> honest, defensible line.

---

## What already exists (verified — do not rebuild)

- **`PREFLIGHTS`** (then `src/skills/ffmpeg/handlers.py`; the OOP refactor since replaced
  the dict with `FFmpegSkill.preflight` in
  [`handlers.py`](../../skills/ffmpeg/python/handlers.py), delegating to
  `_preflight_inputs` in [`_reporting.py`](../../skills/ffmpeg/python/_reporting.py)) —
  skill-registered deterministic checks. `_preflight_inputs` already resolves input paths
  against the sandbox, detects missing files, detects sandbox escape, and even
  distinguishes a mismatched chained intermediate. **It currently returns error
  strings that `agent.py` turns into a hard `raise ValueError("Cannot proceed…")`**.
- **`validate_plan`** — rejects unknown tools, missing required args, unsupported
  args. Raises on failure.
- **Terminal outcomes** — `clarify` / `reject` / `done` are already first-class
  terminal tools (`_TERMINAL_TOOLS` in [`agent.py`](../../python/core/knaif/agent.py);
  `_execute_steps` stops on them).
- **Expander layer** — `expand_concat_video` (since become `ConcatVideoIntent.expand` in
  [`intents.py`](../../skills/ffmpeg/python/intents.py)) already builds the concat
  sub-plan; it reads `args["output"]` directly (so a missing output currently fails
  upstream in `validate_plan`).
- **Preview / confirmer gate** — StepA/StepB already exist to show the user the
  plan (including a defaulted output name) and let them approve or rename.

So the change is **mechanism, not new infrastructure**: convert deterministic
*failures* into clarify/reject *outcomes*, supply defaults for required args that
have an obvious one, and stop instructing the model to gatekeep.

---

## Source context

Read before implementing:

- Pipeline + terminal handling: [`python/core/knaif/agent.py`](../../python/core/knaif/agent.py)
  (`execute_plan`, `_expand_plan`, the preflight block, `_execute_steps`)
- Validation: [`python/core/knaif/planner.py`](../../python/core/knaif/planner.py) (`validate_plan`)
- Registry schema: [`python/core/knaif/registry.py`](../../python/core/knaif/registry.py) (`ToolDef`)
- ffmpeg preflight: [`skills/ffmpeg/python/_reporting.py`](../../skills/ffmpeg/python/_reporting.py)
  (`_preflight_inputs`), [`skills/ffmpeg/python/handlers.py`](../../skills/ffmpeg/python/handlers.py)
  (`FFmpegSkill.preflight`), [`skills/ffmpeg/python/intents.py`](../../skills/ffmpeg/python/intents.py)
  (`ConcatVideoIntent`)
- Prompt rules: [`skills/ffmpeg/prompt.yaml`](../../skills/ffmpeg/prompt.yaml)
- Eval failures referenced above: `evals/_archive/v2_cheap/ffmpeg_qwen3-4b_cheap.json`
  (was `eval_results/`; archived 2026-06-17 — not comparable to the current corpus)

---

## Plan

### Phase 1 — Core: a "clarify outcome" channel for deterministic failures

Today preflight failure = `raise ValueError`. We add a way for the deterministic
layer to *emit a clarify* (a terminal result) instead of crashing, while keeping
hard raises for truly malformed plans (unknown tool, bad output syntax).

- [ ] **T1 — Introduce a structured `ClarifyNeeded` signal in core.**
  Add a small exception/result type in `python/core/knaif/planner.py` (or a shared
  module) carrying `reason: str` and `kind: "clarify" | "reject"`. Generic — no
  skill knowledge.
- [ ] **T2 — Preflight returns outcomes, not just error strings.**
  In `agent.py`, when preflight errors are present, instead of
  `raise ValueError("Cannot proceed…")`, synthesize a terminal result and return
  it as the plan outcome, per the governing rule:
  - missing / nonexistent **input** file → `{"tool": "clarify", ...}`
  - sandbox-escape (only when `sandbox is not None`) → `{"tool": "reject", ...}`
  - Resolution base is `sandbox if sandbox is not None else root`, so this is
    correct for both eval and real CLI with no extra code.
  - Keep the raise path only for programmer errors (preflight fn itself throws).
- [ ] **T3 — Tests (RED first).**
  `python/core/tests/test_agent.py`: missing input file (no sandbox / cwd base) → outcome is
  `clarify` (not an exception, not `error`); missing input with sandbox base →
  `clarify`; sandbox-escape path → `reject`; a valid plan still executes. Assert
  the clarify `reason` contains the offending filename.

### Phase 2 — Core: skill-declared defaults for required args

So the model never has to invent or clarify an output name.

- [ ] **T4 — Add optional `defaults: dict` to `ToolDef`** in `python/core/knaif/registry.py`,
  loaded from `tools.yaml` (mirrors how `mock_args` / `any_of_args` load).
  Generic; values live in the skill.
- [ ] **T5 — Apply defaults deterministically before validate/expand.**
  In the expand/validate path, fill any *missing required arg* that has a
  declared default. A defaulted value is surfaced through the existing plan
  preview (StepA) so the user sees "will write combined.mp4" and can rename via
  the confirmer.
- [ ] **T6 — Tests (RED first).**
  `python/core/tests/test_planner.py` / `test_registry.py`: `defaults` loads from
  YAML; a missing required arg with a default is filled; a missing required arg
  **without** a default still raises (so we don't silently invent values where
  there's no sensible default).

### Phase 3 — ffmpeg skill: declare the defaults + make output optional

- [ ] **T7 — `concat_video`: move `output` to `optional_args`,** add
  `defaults: {output: "combined.mp4"}` in `skills/ffmpeg/tools.yaml`.
- [ ] **T8 — `expand_concat_video`: read `args.get("output", "combined.mp4")`**
  instead of `args["output"]` (defensive; defaults should already be applied by
  core, but the expander must not KeyError).
- [ ] **T9 — Audit every ffmpeg intent tool** for a required arg that has an
  obvious deterministic default (output names are the main one). Only add a
  default where it is genuinely unambiguous; leave the rest to clarify.
- [ ] **T10 — Skill tests (RED first).**
  `skills/ffmpeg/python/tests/test_ffmpeg_skill.py`: concat with no output produces
  a plan writing `combined.mp4`; concat with explicit output preserves it.

### Phase 4 — Prompt: stop instructing the model to gatekeep structurally

The model's contract narrows to **intent + argument extraction**. Remove the
rules that make it clarify on structural grounds; keep the rules that make it
clarify/reject on *semantic* grounds (out-of-scope ops, unmappable intent).

- [ ] **T11 — Edit `skills/ffmpeg/prompt.yaml`** to match the governing rule:
  - **Input:** keep guidance to extract the file the user named (preserve full
    paths), but stop making the model the gatekeeper — the deterministic layer
    now emits clarify when an input is missing/nonexistent. The model should
    still attempt its best extraction rather than pre-emptively clarifying.
  - **Output:** remove any instruction to clarify about a missing output name;
    the system supplies a default. The model may omit `output` entirely.
  - Keep: "If the request does NOT map to a supported tool (subtitles, watermark,
    color grading…) → clarify. NEVER substitute the closest tool." (semantic — B)
  - Keep/strengthen: reject examples for out-of-scope intent (email, cloud,
    shell exec, overwrite-in-place, process-whole-server). These are semantic
    refusals the model should still make.
- [ ] **T12 — Add concat routing examples** (no explicit output) so the model
  emits `concat_video` with `inputs` and lets the default fill output — covers
  the 8 Group-1 rows including multilingual (keywords already in `tools.yaml`).

### Phase 5 — Re-measure (no fine-tuning involved)

- [ ] **T13 — `just check`** — full suite green.
- [ ] **T14 — `just eval ffmpeg --backends qwen3-4b --save evals/runs/<date>_clarify_cheap/`**
  Compare `outcome_accuracy` and the by-tag table against the previous run. Expect
  Groups 1 & 2 to close, missing-file `error` rows to become correct `clarify`, and no
  regression on currently-passing tags. (Actually run as `eval_results/v2_clarify/`,
  archived to `evals/_archive/v2_clarify/`.)
- [ ] **T15 — Re-run the live concat case** from the Problem section; confirm it
  now produces a plan writing `combined.mp4` instead of a clarify.

---

## Governing rule (decided 2026-06-06): input vs. output asymmetry

The clarify decision is driven by one deterministic, mode-agnostic rule:

> **Input missing or unresolvable → clarify. Output missing → default it and proceed (plan).**

Concretely:

- **Input file** — resolve each required input against `root` (cwd) / `sandbox`.
  If a required input is absent, or names a file that does **not exist** → emit
  **clarify** ("which file?"). The system must never guess *which* file to act on.
- **Output file** — if only the output is missing, supply the skill's declared
  default (e.g. `combined.mp4`) and **proceed**. The system *may* name an output;
  it just can't invent an input. The defaulted name is shown via the plan preview
  so the user can rename.
- **Sandbox escape** (eval/notebook only) → **reject**.

This is fully deterministic and identical across CLI and eval — only the base
directory differs. The model is never the arbiter of a fact it cannot know.

### Consequence for the corpus

This rule resolves the Phase-1 `clarify → plan` "the 4K video" cases by the
data, not by judgment: if the model resolves the description to a file that
**exists**, the input is effectively specified → `plan` is correct. Several
Group-6 rows currently marked `expected_outcome: clarify` (e.g. ffmpeg_104
"resize the 4K video to 1080p", where `clip_4k.mp4` exists as a fixture) are
therefore **corpus errors** — the model was right. Reclassify them to `plan`.

Note the data-dependent (but rule-consistent) behaviour: the *same* utterance
"resize the 4K video" yields `plan` in eval (the fixture exists) and `clarify`
in a real CLI directory with no matching file (the guessed name doesn't exist).
That is the correct outcome of one rule applied to different filesystems — not
two different behaviours.

- [x] **T16 — Corpus reclassification → resolved as NO-OP (premise was wrong).**
  Investigation (2026-06-06) showed the plan's premise — "the model resolves the
  description to a file that exists" — is false. `build_prompt` injects header +
  tools + examples + history and **no file listing** ([`prompt.py`](../../python/core/knaif/prompt.py), `build_prompt`), so the
  model cannot map "the 4K video" → `clip_4k.mp4`. It instead emits the
  description verbatim as a fake input (`{"inputs": ["4K video"]}`), which passes
  only the cheap routing verifier and would fail the `success` verifier. These
  rows are therefore **correctly labeled `clarify`** under the governing rule
  ("input unresolvable → clarify"); the model is wrong to plan them, not the
  corpus. No rows reclassified.

  **Decision (2026-06-06):** indirect/description-based file references stay
  `clarify`. We will **not** build file-listing injection: (a) a real directory
  can overflow the small-model context window; (b) a property like "4K" cannot be
  derived from a filename — it would require probing each file, which is more
  complexity than the feature is worth. If indirect resolution is ever wanted, it
  must be a deterministic probe-and-match step, decided separately.

  > **Superseded in part (2026-07-22).** The "we will not build this" half no
  > longer holds: [2026-06-09-context-injection.md](2026-06-09-context-injection.md)
  > is **parked, not retired** — deliberately deferred until the ffmpeg UI work,
  > which supplies exactly the host-side file listing this note ruled out. It was
  > separately decided, as this note required. `nl_clarify_gate()` already takes an
  > `injected_files` argument for that world.
  >
  > What still holds: the reasoning in (a) and (b), and the fallback behaviour —
  > with injection OFF, an indirect reference is unresolvable and stays `clarify`.

  **Carve-out (firm requirement):** a missing *output* name must never trigger
  clarify — the plan must proceed. Single-step omission is covered by T7 defaults;
  the chained case ("concat 1.mov and 2.mov, then extract their audio") relies on
  the model inventing an output name for the producing step and referencing it in
  the consumer, per the prompt's chaining rule. Verified in T17.

- [x] **T17 — Chained output-omission verified.** Live testing exposed a real
  bug: for "concat A and B, then extract their audio" the model produced a
  *correct* chained plan but with **malformed JSON** — it nested
  `"output": "combined.mp4"` *inside* the `inputs` array, so `parse_plan` failed
  and the user saw "Could not parse" (effectively a clarify). Root-caused to a
  missing few-shot: the model handles concat with a *user-named* output and
  `trim→…` chains (scalar `input`) correctly, but had no example of inventing an
  output for a concat (array `inputs`) that feeds a later step. Fixed by
  repointing the concat+extract prompt example to the bare phrasing ("Merge … ,
  then extract the audio") so it demonstrates an invented `output` as a sibling
  key. Verified 3/3 on the live qwen3-4b (concat→extract, merge→extract their
  audio, join→convert) — all parse and chain correctly. Full suite green (836);
  aggregate eval flat within run-to-run variance (regressed rows were unrelated
  non-chaining cases).

---

## Why this satisfies the no-fine-tuning constraint

Every Category-A fix is pure deterministic code: file checks, sandbox checks,
declared defaults, and removing prompt rules that ask the model to do
deterministic work. None of it depends on the model learning anything. The base
qwen3-4b (and base 1.7b) benefit immediately. Fine-tuning, if later run, only
helps Category B — and even there, B is addressed first by prompt examples, so
the system is correct without it.

---

## Implementation log (2026-06-06)

T1–T15 implemented via TDD (RED→GREEN→COMMIT) and committed on
`feature/extend-corpus`:

- **T1–T3** — preflight failures now return `clarify`/`reject` outcomes instead
  of `raise ValueError` (`classify_preflight_errors` in planner.py; outcome
  synthesis in agent.py). Commit `f6663b5`.
- **T4–T6** — `ToolDef.defaults` + `apply_defaults()` wired into `execute_plan`
  between `normalize_plan` and `validate_plan`. Commit `4ff0eb5`.
- **T7–T10** — `concat_video.output` moved to optional with
  `defaults: {output: "combined.mp4"}`; expander reads `args.get(...)`. Commit
  `5c188e7`.
- **T11–T13** — prompt de-gatekeeping + concat no-output examples. Commit
  `91f76a1`.
- **T14** — eval `evals/_archive/v2_clarify/`: 73.7% → **77.4%** outcome accuracy.

### The real chaining blocker, found during T15

Re-running multi-step cases (`trim clip.mp4 to 5s, then resize to 720p`) still
produced a spurious clarify — but **not** from the prompt. The message
*"You didn't mention 'clip_trimmed.mp4'…"* came from a deterministic guard,
`CommandAgent._hallucinated_filename` ([agent.py](../../python/core/knaif/agent.py)), which iterated **every** string
arg — including the model-invented `output` — and flagged any filename absent
from the utterance. It was overriding correct plans.

This is the same class of bug this plan targets (deterministic code making a
gatekeeping decision it shouldn't), so the fix belongs here:

- **Guard fix** — `_hallucinated_filename` now skips the `output` arg and any
  value matching a filename an earlier step declares it will produce (legitimate
  chained intermediates), while still flagging genuinely invented *input*
  filenames. TDD, commit `0c7f74e`.
- **Result** — eval `evals/_archive/v2_guard_fix/`: **80.3%** outcome accuracy
  (+2.9 over v2_clarify). `trim` 44% → **84%**, `resize` 59% → 73%,
  `multilingual` 81% → 89%, `multi_output` 0% → 100%. One ~20-line deterministic
  fix outperformed all prompt edits combined — consistent with this plan's
  thesis that structural decisions belong in code, not the model.

Lesson recorded: trace a user-facing string to its exact source before
theorizing about model/prompt behaviour. The earlier prompt-engineering attempts
(rule rewrites, a trim→resize example) were reverted because they targeted the
wrong layer.

---

## Architecture decision (2026-06-06): static planning, not re-planning

While fixing the chaining case we evaluated whether dependent multi-step plans
(later steps consuming earlier outputs) should move to a **re-planning loop**
(`run()`: infer one step → execute → feed the result back → infer the next).
The motivating long-term example was a trading skill: *"buy BTC, use it to buy
ETH, sell for USDT."*

**Decision: keep static, full-plan-up-front execution with variable binding.
Do not adopt re-planning as the default mechanism.**

### Why

The distinction that settled it:

- **Data dependency** — the *sequence* of steps is known up front; only the
  *values* flowing between them are runtime-determined (a fill amount, a probe
  result). Handled by **variable binding**: the model emits the whole plan with
  `output: "$btc"` / `$btc.filled_qty` references, and `resolve_args()` wires the
  real value in just before each step runs. One inference call.
- **Decision dependency** — the *next action itself* is unknowable until an
  earlier result is observed (branching, unknown-length loops, failure recovery).
  This is the only case that genuinely needs re-planning.

The trading example, and every chaining case we have, is **data dependency** —
which variable binding already covers. Critically, re-planning has a safety
property that disqualifies it for irreversible actions:

> Re-planning **executes step A before the model has proposed step B.** For a
> trade that means buying BTC for real before the system has decided to buy ETH.
> Static planning lets the approval gate (`plan_confirmer`, StepB) show the
> *entire* chain — "spend $1000 on BTC, roll proceeds into ETH, sell for USDT" —
> and execute nothing until the user approves all of it.

For irreversible/outward-facing actions, "approve the whole chain up front" is a
safety feature, not a limitation; re-planning structurally cannot offer it.

### Consequence / guidance for future skills

- The re-planning loop (`run()`) stays available but is **not** the default and
  not on the critical path. Reserve it for genuine *decision* dependency
  (conditional branches, loops, recovery) in some future skill.
- Chaining principle for skill authors:
  - **Intermediate knowable up front** (an output filename the model invents) →
    chain with a **literal value** (what ffmpeg does; the guard fix unblocked it).
  - **Intermediate is a runtime value** (a fill amount, a discovered quantity) →
    chain with **`$var.field`** (what a trading skill would use).
- No new mechanism is required today — variable binding already exists and is
  tested; this decision is about *not* building the riskier alternative.
