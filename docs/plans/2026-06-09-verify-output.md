# Output Verification — assert produced artifacts match intent

**Status:** Draft · **Created:** 2026-06-09 · **Completed:** —
**Owner:** ffmpeg (+ tiny core touch) · **Ref:** —

> **Status note:** **Kept, ready to build, queued behind the OSS-readiness P0s**
> (decided 2026-06-21). Reviewed and endorsed: highest-leverage of the 2026-06-09
> drafts (general runtime net for the "exit 0 ≠ goal achieved" class; also catches the
> mis-wired-chain residual from
> [complex-two-step-intents](2026-06-09-complex-two-step-intents.md)). Open Q1
> resolved below; build when the P0 backlog clears.
> **Risk:** low — reuses existing ffprobe machinery; mostly wiring, no new dependency.
>
> **Re-verified 2026-07-22** (S7 decision — **kept**). Every claim below was checked
> against the current tree and **all of them still hold**; the gap is live, not
> historical. Source references have been repointed from the pre-monorepo `src/` layout,
> and the symbol names updated for the OOP refactor — the verify handlers are now `Step`
> classes in [`steps.py`](../../skills/ffmpeg/python/steps.py), not `cmd_*` functions in
> `handlers.py`. Line numbers are dropped where the code has moved; the class and
> function names are the durable anchors.
>
> | Claim | State on 2026-07-22 |
> |---|---|
> | `verify_outputs` asserts nothing | `VerifyOutputsStep.handle` still returns `{"verified": True, …}` unconditionally |
> | `verify_preview`'s comparison is a no-op | the loop exists, but `_build_preview_block` still passes only `preview_output` — no `expected` |
> | recipes carry no `expected` | `_build_one_recipe` in [`_engine.py`](../../skills/ffmpeg/python/_engine.py) emits none |
> | `_step_failed` ignores verification | still `returncode`-only, in [`agent.py`](../../python/core/knaif/agent.py) |
>
> Q1 is resolved (below). **Q2–Q4 are deliberately left open** — `expected` from recipe
> vs intent, size thresholds, and op-spec vs inline are cheaper to settle while writing
> T1 than to argue on paper.

**Goal:** Give the existing output verifier teeth — assert that produced artifacts
actually match the requested intent at runtime, not just that the command exited 0.

> ## Review decisions (2026-06-21)
> - **Disposition:** keep as a ready draft; do not start until the OSS P0s
>   (packaging / CI) are cleared. Cheap + high-leverage once picked up.
> - **Open Q1 (hard-stop vs warn) — RESOLVED by chain-vs-batch topology** (see Q1).
> - **Factor the comparator as ONE shared `_check_expected`** used by both runtime
>   verify *and* the eval `success()` verifier, so the two cannot drift.

## TL;DR — the verifier already exists; it has no teeth

The ffmpeg skill **already** runs an output verification step. `_build_batch_block`
([intents.py](../../skills/ffmpeg/python/intents.py)) ends with:

```
render_batch_commands → run_batch → verify_outputs → generate_report
```

[`VerifyOutputsStep`](../../skills/ffmpeg/python/steps.py) ffprobes every produced file
and builds a `summary` (container, width, height, video/audio codec, has_audio,
duration, size_bytes via `_summarise_probe`). **But it unconditionally returns
`"verified": True`** — it records the probe and asserts nothing against what the user
actually asked for. The assertion half is missing in three specific places:

1. **No `expected` is ever produced.** `_build_one_recipe`
   ([`_engine.py`](../../skills/ffmpeg/python/_engine.py)) knows the target (container,
   height cap, audio-strip, etc.) but never emits an `expected` block.
2. **`verify_outputs` doesn't compare.** Its sibling
   [`VerifyPreviewStep`](../../skills/ffmpeg/python/steps.py) *does* diff `summary`
   against an `expected` dict and produce `issues` — but `_build_preview_block` calls it
   with **no `expected`** (`args: {"preview_output": "$preview_run"}`), so it too is a
   no-op.
3. **`_step_failed` ignores verification.**
   [`agent.py`](../../python/core/knaif/agent.py) only checks `returncode` — the
   top-level one and per-output entries. `verify_outputs` returns no returncode and no
   `verified: False` is inspected, so a mismatch never stops the run or surfaces.

So today: model leaks a bad arg → ffmpeg exits 0 → file is silently wrong (e.g. an
mp3 stream in a `.flac` container — the exact bug commit `a329425`
*"fix(ffmpeg): coerce model-leaked NL args to valid ffmpeg values"* hand-patched) →
pipeline reports **success**. This plan closes that by populating `expected` and making
the existing verify step assert and surface.

## Why it's worth it (the evidence)

- The class is **live and recurring**, not hypothetical. The coercion commit
  [a329425] recovered **7 utterances** scoring knaif 0% because the model leaked NL
  into the command and ffmpeg produced wrong/no output. That whole commit, plus the
  earlier `crf`/`width`/`height` coercion fixes, are reactive special-cases of one
  general failure: *exit 0 ≠ goal achieved*. Output verification is the general
  mechanism; it would have flagged all 7 at runtime, before the eval did.
- It **closes the project's one verification gap.** Every other stage is
  deterministic-validated (normalize, validate, stem-resolve, coerce, preflight,
  gate) — but all *before* dispatch. After `run_batch` the only success signal is
  `returncode == 0`. The guarantee should extend to the artifact.
- It **brings the eval metric into runtime.** knaif-% is the criterion trusted
  enough to gate merges; today it only exists in the harness. Asserting probe-vs-
  intent at runtime is that same comparison, live.
- It's **cheap and deterministic.** ffprobe is already run on inputs and outputs;
  the marginal cost is comparison logic. No model call, no new dependency.

Honest limits (kept in scope deliberately): post-hoc (can't un-waste a long encode —
`run_preview` is the prevention, this is the net), and it only covers
deterministically-probeable goals (dimensions, codec, stream presence, size delta,
duration). Fuzzy goals ("cinematic") stay at eval-time with an LLM judge.

## Design

### 1. Recipes carry an `expected` block

In [`_build_one_recipe`](../../skills/ffmpeg/python/_engine.py) (which already
computes container, `max_w`/`max_h`, `mode`, `audio_only`), attach an `expected`
dict keyed to `_summarise_probe`'s output keys. Only assert what the intent
*determines* — leave the rest unconstrained:

| Intent / mode | `expected` keys (only the determined ones) |
|---|---|
| convert / platform → container | `container == "mp4"` |
| resize / max dimensions | `height <= cap` and/or `width <= cap` (≤, not ==) |
| extract_audio → flac | `audio_codec == "flac"`, `has_audio == True` |
| strip audio | `has_audio == False` |
| compress / "smallest" | `size_bytes < input_size_bytes` (needs input size threaded in) |
| speed / slow factor | `duration ≈ input_duration / factor` (tolerance band) |

Comparisons need richer operators than `VerifyPreviewStep`'s current exact-equality
loop (`if summary.get(key) != want` in [`steps.py`](../../skills/ffmpeg/python/steps.py)).
Introduce a small spec: `{key: {"op": "eq|le|lt|approx", "value": …, "tol": …}}`.
`approx` for duration, `le` for dimension caps, `lt` for size-smaller, `eq` for
codec/container/has_audio.

### 2. `verify_outputs` (and `verify_preview`) assert against `expected`

- Pass `expected` from recipes into both verify steps (preview gets the recipe's
  expected; batch gets per-output expected aligned by `input`/`output`).
- Replace the exact-equality loop with the op-aware comparator. Emit `issues` and
  set `verified = not issues` — `verify_preview` already has this exact shape; lift
  it into a shared `_check_expected(summary, expected) -> list[str]` used by both.
- Keep `dry_run`/`skip_execution`/missing-file → `skipped` behavior unchanged.

### 3. Surface a verification failure (small core touch)

A failed `verify_outputs` must not report success. Options (decide in review):

- **a. Skill-local:** `verify_outputs` returns `returncode: 1` (or a sentinel) when
  any item has `issues`, so the existing `_step_failed` stops the run — zero core
  change, but overloads "returncode" semantically.
- **b. Core-aware:** extend `_step_failed` to also treat
  `result.get("verified") is False` (or any `verifications[*].issues`) as failure.
  Cleaner semantics, ~3 lines in core, domain-agnostic (a "verified" flag is generic).

Recommendation: **b** — a `verified: False` signal is generic enough for core to
honor without knowing anything about ffmpeg, matching how it already honors generic
`returncode`. `generate_report` then renders the issues for the user
(`GenerateReportStep` in [`steps.py`](../../skills/ffmpeg/python/steps.py)).

### 4. Report shows what failed

`generate_report` already renders res/container/size per output. Extend it to print
`issues` ("still 2160p — expected ≤1080; audio_codec=mp3 — expected flac") so a
verification failure is legible, not just a status flip.

## Test plan (TDD)

Unit (skill):
- `_check_expected`: eq/le/lt/approx pass + fail tables (the comparator is the core
  logic — test it first, exhaustively).
- `_build_one_recipe` emits the right `expected` per mode (resize→`height le cap`,
  strip-audio→`has_audio eq False`, flac→`audio_codec eq flac`, …).
- `verify_outputs` with a probe summary that violates `expected` → `verified: False`
  + populated `issues`; a conforming summary → `verified: True`.

Integration (`tests/test_ffmpeg_skill.py` + `tests/test_agent.py`):
- The flac case (a329425's bug): output container `.flac` but `audio_codec==mp3` →
  `verify_outputs` flags it → run stops / report shows the issue. (Regression guard
  for the exact silent-success this plan targets.)
- resize to 1080p but recipe left height at 2160 → flagged.
- `dry_run=True` → verification skipped, no false failure.
- conforming output → `verified: True`, run completes normally.

Eval (regression):
- Re-run qwen3-4b / gemma3-4b; confirm no SAFE plan row newly reports a verification
  failure (no over-strict `expected`). Then deliberately un-patch one coercion and
  confirm the corresponding row now fails verification instead of silently passing —
  proof the net catches what coercion used to.

## Tasks

- [ ] T1 — `_check_expected(summary, expected)` op-aware comparator (eq/le/lt/approx),
      pure + table-tested first.
- [ ] T2 — `_build_one_recipe` emits `expected` per mode; input size/duration threaded
      from probes for `lt`/`approx` checks.
- [ ] T3 — `verify_outputs` + `verify_preview` consume `expected`, set
      `verified`/`issues` via the shared comparator. Wire `expected` through
      `_build_batch_block` / `_build_preview_block`.
- [ ] T4 — Surface failure: option (b) — extend `_step_failed` to honor
      `verified: False`. Core unit test.
- [ ] T5 — `generate_report` renders `issues`.
- [ ] T6 — Integration + eval regression; record numbers.

## Open questions for the discussion

1. **Hard stop vs. warn — RESOLVED (2026-06-21): decide by topology.**
   - **Chain (dependent steps, step N+1 consumes step N's output):** a verification
     failure **halts the chain at the failure** — everything after it depends on the
     bad output and would fail anyway, so don't waste the encodes. **But preserve
     outputs already produced** by earlier successful steps (and prior deliverables) —
     do not roll back or delete good work; report partial success + the failing step.
   - **Batch (independent outputs from one intent):** **annotate the failing output and
     continue** the rest; never let one bad output halt the others.
   - Implementation: per-output `verified`/`issues`; the run-stop decision keys off
     whether later steps reference the failed step's output (chain) vs not (batch).
2. **`expected` source of truth.** Derive purely from the recipe (what we *asked*
   ffmpeg to do) or also from the *intent* (what the user said)? Recipe is safer
   (it's deterministic); intent risks re-introducing NL ambiguity into the check.
3. **Size/duration thresholds.** "Smallest" → `size_bytes < input` is weak (any
   shrink passes). Do we want a target ratio, or is "didn't grow" enough for v1?
4. **Op-spec vs. keep it simple.** Is the `{op, value, tol}` spec worth it, or do we
   special-case the handful of comparisons inline? (Comparator count is small.)

## Non-goals

- Fuzzy / perceptual quality judgement — stays at eval-time, no runtime LLM judge.
- Re-encoding or auto-repair on failure — verification reports; it does not retry.
- Verifying non-ffmpeg skills — the mechanism (a generic `verified` flag honored by
  core) generalizes, but only ffmpeg ships expectations in this plan.
