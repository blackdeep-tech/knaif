# Complex Two-Step Intents — model clarifies instead of chaining

**Status:** Superseded · **Created:** 2026-06-09 · **Completed:** 2026-06-27
**Owner:** core + ffmpeg · **Ref:** [fine-tuning](2026-06-27-fine-tuning.md)

> **Status note:** **Retired (2026-06-27) — never implemented.** Option A
> (multi-intent chaining) already shipped, covering the routing surface. The narrow
> `strip_audio` output-modifier fusion that the 2026-06-21 rewrite directive made this
> plan's sole deliverable was never built: `strip_audio` exists only as its own
> standalone intent, and the producer tools (`resize_video` / `convert_video` /
> `adjust_speed` / `rotate_video`) carry no fused `-an` flag. The residual problem —
> chain-execution fidelity (mis-wired `$prev` links, gemma dropping the second output) —
> moves to fine-tuning ([fine-tuning](2026-06-27-fine-tuning.md)). Kept for history; do
> not implement the A/B/C framing or the strip-audio flag below.
> **Risk:** — (retired)
>
> **Kept 2026-07-22** (S7 decision) as the record of a design direction that was
> **scoped and then deliberately abandoned**. Read the retirement above as a decision,
> not an omission: the narrow `strip_audio` output-modifier flag was specified below —
> boundary rule, target rows, negative check — and then dropped once Option A's routing
> fix landed and the residual turned out to be chain fidelity rather than composition.
> Nothing else in the tree records that this was considered, so re-proposing producer
> fusion should start here rather than from scratch.
>
> **Where this plan's conclusions now live:**
> - The **fusion boundary rule** (fuse only what adds a flag to the same command, with
>   no new input and no new output) is authoritative in
>   [TOOL_SCHEMA.md](../TOOL_SCHEMA.md#tool-granularity--a-flag-or-its-own-tool) as
>   general tool-granularity guidance.
> - The **Option C prohibition** (fusion must not live in the core optimizer) is in
>   [ARCHITECTURE.md](../ARCHITECTURE.md#plan-optimizer), with the reason.
> - The **measured** negative result — the prompt-only fix (`after-B1`) scored +0.000
>   and changed 0 rows, because qwen won't generalize output-declaration to new combos —
>   is recorded with its A/B table in
>   [ffmpeg-prompt-optimization](2026-06-18-ffmpeg-prompt-optimization.md). That plan is
>   the citable source for what was tried; this one is the record of what was *decided*.
> - **Chain-execution fidelity** became the held-out `chain3` eval slice; see
>   [FINE_TUNING.md](../FINE_TUNING.md) for measured results.

**Goal:** Decide how the model handles complex two-step intents — clarify instead of
emitting a fragile multi-step chain.

> ## Rewrite directive (2026-06-21)
>
> Discussion concluded this draft is ~half-OBE and must be re-scoped. Key findings
> from the 2026-06-18 eval round + the 2026-06-19 honest-chain run:
>
> - **Option A (multi-intent chaining) already shipped** (06-18 "after-step2":
>   producers accept `output`). The cheapest prompt-only fix (after-B1) was **tried
>   and failed** — qwen won't generalize output-declaration to new combos.
> - The residual is **not routing** — it's **chain-execution fidelity**: the model
>   emits both steps but **mis-wires the link** (step 2 reads the *original* file
>   instead of step 1's output), so one op is silently dropped. Model-general,
>   worse on gemma (which sometimes drops the second output entirely).
> - **A/B/C is decided, not open:** do **not** re-litigate it.
>
> **New scope = a single, bounded deterministic fusion, on Axis 2 (tool granularity),
> NOT Axis 1 (general chaining):**
> - Add an optional **`strip_audio` output-modifier flag** to producer tools
>   (`resize_video`, `adjust_speed`, `rotate_video`, `convert_video`) → expander emits
>   one `-vf … -an` command. One pass, deterministic, no chain to mis-wire.
> - **Boundary rule (the thing that keeps it isolated):** only fuse operations that
>   add a flag to the *same* command with **no new input and no new output**. `-an`
>   qualifies; `extract_audio` (new output) and replace-audio (new input) do **not** —
>   they stay separate intents / multi-output plans.
> - **Implement as a skill-local flag (Option B). Explicitly FORBID the optimizer
>   auto-fusion route (Option C)** — that one leaks into the general chaining path.
> - **General multi-step chaining stays on the fine-tuning / deterministic auto-link
>   track** — fusion does not substitute for it (rotate+compress, convert+resize,
>   gemma dropped-outputs remain there).
>
> **Acceptance target:** rows 117 / 227 / 279 (resize+strip) recover on qwen3-4b and
> gemma3-4b; **plus a negative check** that "extract the audio" does NOT mis-route to
> the new strip flag. Corpus must stay coherent about strip-vs-extract-vs-replace.

## Why

On local7 (qwen3-4b) a recurring false-clarify cluster is **compound, two-operation
utterances**. The model is asked to do A *and then* B in one line, can't see how to
express both, and bails to clarify. These are `expected_outcome: plan` rows the
model gets wrong by asking a needless question:

- `ffmpeg_122` "reverse clip.mp4 and then compress it" → clarify
- `ffmpeg_123` "make it play twice as fast with no sound" (speed + strip audio) → clarify
- `ffmpeg_129` "0.5x speed and then encode at CRF 25" → clarify
- `ffmpeg_131` "cut to 2s then optimize for Instagram" → clarify
- `ffmpeg_136` "compress clip_4k.mp4 to 1080p and then prepare for YouTube" → clarify
- `ffmpeg_138` "extract audio from clip.mov as mp3 and then reduce its bitrate" → clarify
- `ffmpeg_273` "rotate clip.mp4 90 degrees and then compress it" → clarify
- `ffmpeg_279` "resize clip_4k to 720p and then strip the audio" → clarify
- `ffmpeg_117` "scale clip.mp4 to 480p and then strip the audio" → plans but knaif=40% (only does one)
- `ffmpeg_227` "downscale mov to 480p and remove sound track" → knaif=25%

Two distinct failure shapes here:

1. **Bail-to-clarify** — model sees two verbs, asks instead of acting.
2. **Half-done plan** — model picks one operation, drops the other (the knaif=40%/25%
   rows): it emits `resize_video` and forgets `strip_audio`, so the output has audio.

Both stem from the same root: the system has **no first-class way to express
"do A then B"** to the model, and the expanders each produce a single-operation
recipe.

## The core question for discussion

**How should two chained operations be represented end to end?** Three layers
could own it, and we need to pick where the composition happens:

### Option A — Multi-intent plan (model emits two intent steps)

The model emits `{"plan": [{tool: resize_video, ...}, {tool: strip_audio, input: $prev}]}`.
The pipeline already supports multi-step plans and variable chaining (`$1`,
`$prev`). Each intent expands to its own workflow; the second consumes the first's
output.

- **Pros:** general — handles any A+B+C; reuses existing expander/chain machinery;
  no new ffmpeg knowledge.
- **Cons:** doubles the encode (resize re-encodes, then strip re-encodes again →
  quality loss + 2× time); requires the model to wire `$prev` correctly (small
  models are shaky at variable refs); intermediate file management.
- **Open:** does `optimize_plan()` already fuse adjacent single-input re-encodes,
  or would we add that? Fusing A+B back into one ffmpeg invocation is the
  deterministic win that makes this option not-wasteful.

### Option B — Compound options on one intent (deterministic fusion)

The model emits **one** intent with extra flags: `resize_video` with
`strip_audio: true`, or `trim_video` with `then_platform: instagram`. The expander
folds both into a single recipe / single ffmpeg command (`-vf scale=... -an`).

- **Pros:** one encode, one command, best quality/speed; matches how the baselines
  are written (`ffmpeg ... -vf scale=854:480 -an ... ` for ffmpeg_117); deterministic.
- **Cons:** combinatorial — every meaningful A×B pair needs an option; the prompt
  must teach the model the compound flags; doesn't generalize past the pairs we
  enumerate.
- **Observation:** the recipe builder already supports several of these implicitly
  (`resize` + `copy_audio:false` would strip; `_build_one_recipe` has `strip_audio`
  mode). The missing piece is letting an intent carry a *secondary* operation.

### Option C — Hybrid: model emits multi-intent (A), optimizer fuses (B)

Model emits the natural two-step plan (Option A's surface), and a deterministic
`optimize_plan()` pass fuses compatible adjacent operations on the same input into
a single recipe (Option B's execution). The model never learns compound flags; the
quality/speed win is recovered deterministically.

- **Pros:** general surface + efficient execution; model only needs to chain, which
  is easier to teach than a compound-flag vocabulary; fusion rules live in one
  deterministic place.
- **Cons:** fusion logic is non-trivial (which ops commute? resize+strip = safe;
  trim+reverse = order-dependent); needs a clear fuseability table.
- This is likely the right long-term answer; needs the fusion table specified.

## Sub-questions to settle in the discussion

1. **Which pairs actually occur?** Enumerate from the corpus (the `complex`-tagged
   rows). Likely set: resize+strip, speed+strip, trim+platform, reverse+compress,
   rotate+compress, resize+compress, convert+resize, extract_audio+bitrate. Sizing
   this tells us if Option B's combinatorics are tractable (looks like ~8 pairs).
2. **Order semantics.** "trim then reverse" ≠ "reverse then trim" for some ops.
   Which pairs are order-sensitive? Fusion must preserve intended order or refuse
   to fuse and fall back to two encodes.
3. **Quality contract.** Is a double-encode acceptable for v1 (Option A alone),
   deferring fusion? The half-done-plan rows (knaif 40%/25%) are a *correctness*
   bug independent of efficiency — fixing "model drops the second op" matters more
   than avoiding double-encode.
4. **Prompt vs structure.** Can a prompt change alone (teach the model to emit
   two intent steps with `$prev`) move the bail-to-clarify rows to plan, measured
   before we build any fusion? Cheapest experiment; run it first.
5. **Variable-chaining reliability.** If we lean on `$prev`/`$1`, measure how often
   qwen/gemma wire it correctly. If unreliable, Option B (single intent) is forced.
6. **Interaction with the NL clarify gate.** A compound utterance that also lacks a
   named file ("make it play twice as fast with no sound" — "it") must still
   clarify for the *file*, not the chaining. Order the gates: file-reference gate
   first, then chaining handling.

## Suggested investigation sequence (before committing to A/B/C)

1. **Measure the cheap prompt fix.** Add a prompt example showing a two-intent
   chain with `$prev`; re-run eval on the `complex` rows only. If bail-to-clarify
   drops substantially, Option A may be enough for v1.
2. **Characterize `$prev` reliability** from that run — count correct vs broken
   chains.
3. **Inventory the pairs** from the corpus; decide if Option B/C fusion is worth it.
4. **Spec the fusion table** (if C): for each pair, fuseable? order? single-command
   recipe shape?
5. Only then write the implementation plan.

## What this plan is NOT deciding yet

- The exact representation (A/B/C) — that is the discussion this document frames.
- Whether to touch the prompt, the expanders, the optimizer, or all three.

## Data to bring to the discussion

- The full list of `complex`-tagged corpus rows with their baselines (shows the
  intended single-command shape for each pair).
- local7 per-row outcomes for those rows (bail-to-clarify vs half-done).
- A count of how many `complex` rows the model already gets right (so we know the
  real size of the problem, not just the failures).
