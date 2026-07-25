# Local3 Outcome-Accuracy Fixes — qwen3-4b

**Status:** Done · **Created:** 2026-06-08 · **Completed:** 2026-06-09
**Owner:** ffmpeg + core · **Ref:** evals/_archive/v2/local4_success/

> **Status note:** Tasks 1–5 landed and **VERIFIED end-to-end** (2026-06-09,
> `local4_success`). Task 6 (030 prompt change) **REVERTED** after local4 re-score:
> ffmpeg_030 stayed `plan` on both backends (gate not met) and gemma regressed a plan
> row (ffmpeg_221 plan→clarify); ffmpeg_030 deferred to fine-tuning. Tasks 7, 8, and
> Corpus Trim were deferred.
>
> **Kept 2026-07-22** (S7 decision) for the per-row forensics and the still-open Corpus
> Trim. The durable rules it established now also live in the shipping docs — the
> utterance-equivalence contract, the cross-backend corpus-bug fingerprint, and the
> corpus-composition principles in [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md); the
> all-backends rule for prompt edits in
> [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md); and `normalize_plan()`'s two
> passes plus the honor-the-value principle in [ARCHITECTURE.md](../ARCHITECTURE.md).
> Those are the current reference; this file records how the decisions were reached.
>
> **Two follow-ups have since resolved.** Task 7 (the unresolvable-input → clarify gate)
> shipped as the NL clarify gate (PR #14); Task 8 (META analyzer) shipped as
> [descriptor-mixed-intent-analyzer](2026-06-09-descriptor-mixed-intent-analyzer.md).
> **Corpus Trim remains unexecuted and is now unblocked** — it was sequenced to follow
> Task 7, and all 13 rows of the `ffmpeg_253–265` descriptor cluster are still in
> `eval.jsonl`. Tracked in [TODO.md](../TODO.md).
>
> Source references below were written against the pre-monorepo layout (`src/knaif/`,
> `src/skills/`) and the old `eval_results/` tree; they have been repointed to current
> paths, and line numbers are omitted where the code has since moved. Note that
> `extract_frame` no longer exists as a separate tool — it was merged into
> `create_thumbnail` (see
> [ffmpeg-geometry-and-thumbnail-merge](2026-06-17-ffmpeg-geometry-and-thumbnail-merge.md)),
> so the 071/072 analyses below describe a tool surface that has since changed shape.

**Goal:** Raise qwen3-4b/gemma3-4b outcome accuracy on the local3 corpus via
deterministic error→plan coercion (honor reasonable args, split mixed-intent rows).

> **HANDOFF — ready for execution.** Implement **tasks 1–6 only**, in order, TDD per
> task (RED → GREEN → commit), re-scoring BOTH backends (qwen3-4b + gemma3-4b) after
> each. Decisions are RESOLVED (see "Decisions — RESOLVED 2026-06-08"): honor
> reasonable args (don't strip), split mixed-intent rows (don't rename), attempt the
> 030 prompt change but revert if either backend regresses. Tasks 7 (106 clarify-gate),
> 8 (META analyzer), and Corpus Trim are DEFERRED — do not start them. Re-run eval to a
> NEW dir `evals/_archive/v2/local4/` (don't overwrite local3); `just check` must pass.
> After tasks 1–3, re-score ffmpeg_161 — if green on both backends it closes the 161
> open decision.

**Verification (apples-to-apples, both `success` verifier, full 769 utt, real ffmpeg):**

| Backend | local3 (pre-fix) | local4_success (post-fix) | Δ outcome | knaif |
|---|---|---|---|---|
| qwen3-4b | 0.7022 | 0.7165 | +1.4 pts (net +10 utt) | 0.938→0.937 |
| gemma3-4b | 0.7464 | 0.7880 | +4.2 pts (net +30 utt) | 0.894→0.897 |

Blast radius fully cleared: `missing args:['input']` qwen 5→0 / gemma 19→0; `unsupported:['crf']`
qwen 2→0 / gemma 5→0; `unsupported:['output']` qwen 13→10 (3 scoped targets cleared) /
gemma 1→0. Regressions (qwen 3, gemma 4) are llama.cpp re-sampling noise — Tasks 1–5 are
deterministic error→plan coercion and cannot change a model's plan/clarify/reject choice,
and the prompt is identical to local3 (Task 6 reverted). gemma remains the stronger outcome
model; gap widened 0.044→0.071 as it absorbed more of the fixes. See **Follow-up: `output`
on convert/compress/strip** below.

**Source data:** `evals/_archive/v2/local3/` (success, 769) → `evals/_archive/v2/local4/` (cheap,
275, utt[0]) → `evals/_archive/v2/local4_success/` (success, 769, post-fix — the definitive run).

This plan tracks **outcome-accuracy** failures (expected vs. actual `clarify`/`plan`
mismatches), which `avg_knaif_score` does NOT surface — a clarify row scores 1.0 via
`no_criteria` even when the outcome is wrong, so these only show up in
`outcome_accuracy`.

**Cardinal rule (carried over from the local2 plan):** any prompt edit must be
re-scored on BOTH active backends (qwen3-4b, gemma3-4b). A change that helps one
model can regress the other. Prefer no change over a regressing change.

---

## Execution plan — order, risk, and dependencies

Work in ascending risk. Deterministic, model-independent fixes first; the prompt
change and the broad clarify-gate last. Re-run to a NEW dir `evals/_archive/v2/local4/`
(do not overwrite local3). Backends: qwen3-4b + gemma3-4b only.

| Order | Task | Type | Risk | Clears | Status |
|---|---|---|---|---|---|
| 1 | **071** `inputs`→`input` coercion in `normalize_plan` | core, schema-driven | low | 24 cells (5 qwen / 19 gemma); also 072-A, 161 (qwen) | ✅ commit 1a4a12b |
| 2 | **094** add `crf` arg → existing CRF path | handler+schema | low | 3 error cells | ✅ commit f450574 |
| 3 | **072-B** `output` on extract_frame/create_thumbnail | handler+schema | low–med | 1 cell + closes 161 (qwen `output`) | ✅ commit b318141 |
| 4 | **082-A** add `grade:"routing"` | corpus | low | 3 score-0.0 cells | ✅ commit df3c798 |
| 5 | **082-B / 106-corpus** split mixed-intent rows | corpus | low | clarify mis-bins | ✅ commit 3d1d45f (082 only; 106 split deferred pending task 7 gate) |
| 6 | **030** promote vague-operation clarify rule | prompt | med (can regress) | 1 row, both backends | ❌ REVERTED (commit 3663a96) — 030 stayed plan on both; gemma_221 regressed plan→clarify. Deferred to fine-tuning. |
| 7 | **106** unresolvable-input → clarify gate | core/pipeline | **high** | ~6+ cells, broad | DEFERRED |
| 8 | **META** non-equivalent-utterance analyzer | tooling | n/a | finds more 082/106-type rows | DEFERRED |

Dependencies: 072-A is subsumed by task 1 (verify, no code). Tasks 1–3 together close
the **ffmpeg_161 open decision** (the qwen regression was `inputs`+`output` on
extract_frame) — ✅ RESOLVED 2026-06-08: both qwen3 failures cleared by tasks 1+3;
gemma3 was already passing; [[project_ffmpeg_161_open]] memory deleted. Confirmed green
on both backends in `local4_success`.

## Round 2 (2026-06-09) — data-ranked from systematic error aggregation

Lesson from round 1: the triage measured cherry-picked error strings, not an aggregate.
Before fixing, we ran a `Counter` over EVERY validation error in `local4_success` by
`(error-string, tool)`. Ranked worklist (140 total error-outcome utterances):

| # | hits | qwen | gem | error | tool | status |
|---|---|---|---|---|---|---|
| 1 | 26 | 24 | 2 | missing `['inputs']` | adjust_volume | ✅ fixed (bidir coercion) |
| 2 | 24 | 15 | 9 | missing `['inputs']` | prepare_for_platform | ✅ fixed |
| 3 | 22 | 17 | 5 | missing `['inputs']` | adjust_speed | ✅ fixed |
| 4 | 9 | 9 | 0 | missing `['inputs']` | extract_audio | ✅ fixed |
| — | 2 | 2 | 0 | missing `['inputs']` | convert_video | ✅ fixed |
| 5 | 7 | 7 | 0 | unsupported `['output']` | convert_video | see output follow-up |
| 6 | 6 | 1 | 5 | any-of `[inputs/base/append]` | concat_video | open |
| 7 | 4 | 1 | 3 | unsupported `['quality']` | extract_audio | open |
| 8 | 3 | 3 | 0 | unsupported `['height']` | convert_video | open |

**The `missing ['inputs']` class = 83 of 140 (59%) of all validation errors** — the
top 4 ranks plus convert_video. It was invisible to round 1 because the triage grepped
`['input']` (scalar), never `['inputs']` (plural). The original plan's claim that the
symmetric `input`→`inputs` coercion was "not needed (no observed failures)" was an
unverified assumption.

**✅ Task 9 — bidirectional input/inputs coercion (commit 0f857d1).** Extended Pass-2 of
`normalize_plan` to also wrap a scalar `input` string into `[input]` when the tool's
schema declares plural `inputs`. Mirror of Task 1, same registry-driven approach, no tool
named (core stays skill-agnostic). Of qwen's 67 cases, 59 are cleanly coercible; the 8
remaining have no input at all (genuinely missing file → correctly stay error/clarify).

**✅ CONFIRMED (`local5_success`, 2026-06-09):**

| Backend | local4 | local5 | Δ | fixed | regressed | `missing ['inputs']` |
|---|---|---|---|---|---|---|
| qwen3-4b | 0.7165 | **0.7750** | **+5.85** | 45 | **0** | 67 → 8 |
| gemma3-4b | 0.7880 | **0.7932** | +0.52 | 4 | **0** | 18 → 14 |

qwen: 45 outcome-fixes, **zero regressions** — the largest single jump of the effort.
The 8 residual qwen cases are the no-file-at-all kind (non-coercible).

**Key finding — qwen and gemma fail `missing ['inputs']` for OPPOSITE reasons:**
- qwen puts the file under the wrong key (`{input: "clip.mp4"}`) → **coercible** (45 cleared).
- gemma *omits the file entirely* (`{speed: 4.0}`, no input key) → **nothing to coerce**.
  Gemma's 14 leftovers are (a) CJK/embedded-filename extraction failures, expected `plan`
  (`将clip.mp4准备好…`, `encode clip2.mp4 at 500 kbps` — gemma dropped a named file: a
  comprehension limit), and (b) descriptor/vague rows, expected `clarify` (`ускори видеото
  4 пъти`) where gemma errored instead of clarifying — the deferred **Task 7
  unresolvable-input→clarify gate** would convert those error→clarify.

**Strategic implication:** deterministic arg-coercion overwhelmingly helps qwen (a
wrong-key emitter); gemma's residual failures are comprehension/clarify-shaped and need
the clarify-gate or fine-tuning, not coercion. Model gap nearly closed across the effort:
qwen 0.702→0.775, gemma 0.746→0.793 (gemma's lead 4.4→1.8 pts). See [[project_qwen_gemma_failure_modes]].

### Follow-up (next round): `output` on convert/compress/strip
`local4_success` surfaced **10 residual qwen `unsupported args: ['output']` errors** on
tools Task 3 never scoped: `convert_video` (7), `compress_video` (2), `strip_audio` (1).
Task 3 only added `output` to the single-file image tools (extract_frame,
create_thumbnail). Models also emit an explicit output filename on multi-input video
tools. Options mirror 072-B: (a) honor `output` on these tools too (thread into the batch
block — but multi-input/glob outputs need care, only sensible for single-input), or (b)
a scoped `normalize_plan` strip of a lone `output` arg the tool doesn't accept. Gemma had
only 1 such error (1→0 here), so this is largely a qwen-arg-quality surface. Not in this
round; size it against fine-tuning first.

## Round 3 (2026-06-09) — Step 1 arg-surface cleanup (deterministic, mostly qwen)

Data-ranked from the **local5** error landscape (total errors 140→79 after Task 9; balance
flipped — gemma 44 now > qwen 35). Step 1 finishes the deterministic "model emits a
reasonable arg the schema rejects / a string the handler chokes on" surface:

- [x] **Task 10a — honor `output` on `convert_video`/`compress_video`/`strip_audio`** ✅
  commit ee9d1b8. Thread `args["output"]→options["output_path"]`; convert infers container
  from output extension when not explicit (explicit container wins). Known caveat:
  `output`+glob collides — out of scope.
- [x] **Task 10b — coerce string `width`/`height` to int in `expand_resize_video`** ✅
  commit 16cb58e. `_coerce_dimension` (also strips a trailing `p`: "480p"→480). Fixes the
  `int > str` crash (ffmpeg_117 gemma).
- [x] **Task 10c — extract_audio `format`/`container` aliases + `quality`** ✅ commit e22204b.
  audio_format falls back format→container; bitrate-shaped quality ("56kbps") → audio_bitrate,
  profile-name quality ("high_quality") accepted+ignored. Clears 124/138/172.
- [x] **Task 10d — extract_audio `start`/`end` trim-while-extract** ✅ commit 883c742.
  Real feature: renders `-ss`/`-to` for "audio from 3 to 5s" (ffmpeg_119), matches baseline.
- [x] **Task 10e — guard `int(crf)` against non-numeric quality words** ✅ commit 3491aed.
  local6 exposed a latent Task-2 crash: `crf:"balanced"` → `int("balanced")`. `_quality_from_crf`
  passes non-numeric crf through to load_quality_profile. Fixes ffmpeg_139 crash.
- **Deferred:** ffmpeg_113 `quality:'flac'` (model mis-slots the format into quality —
  ambiguous, corpus/fine-tuning); `at_time` used for a range on extract_audio (gemma model slip).

**✅ CONFIRMED (`local6_success`, 2026-06-09):**

| Backend | local5 | local6 | Δ | fixed | regressed |
|---|---|---|---|---|---|
| qwen3-4b | 0.7750 | **0.7945** | **+1.95** | 21 | 6 |
| gemma3-4b | 0.7932 | **0.7997** | +0.65 | 20 | 15 |

All targeted classes cleared: `output` qwen 10→0, `int>str` gemma 1→0, extract_audio
unsupported qwen 4→0 / gemma 8→0. **No rendering regressions: all 10 stable-row knaif_score
drops are "plan-differs" (model re-sampling to a worse plan), zero same-plan drops** — Step 1's
shared rendering changes (container inference, trim flags, dimension coercion, format aliases)
broke nothing that was passing. The 6/15 outcome regressions are the perennial unstable rows
(030/154/221/094/126/111/207…) flipping on re-sample, plus gemma's omit-file misses; net +15/+5.
One real bug found & fixed (10e, crf word). Cumulative: qwen 0.702→0.795, gemma 0.746→0.800.
**913 tests passing.**

Minor follow-up (not blocking): gemma put `output` on `resize_video` (2 new rows) — a tool
Step 1 didn't add `output` to. Consider extending `output` to resize for consistency.

### Frontier (NOT Step 1) — multi-step chaining / composition (the `complex` tag)
Surfaced by **ffmpeg_117** ("scale to 480p AND strip audio"): every passing utterance caps
at **knaif_score 0.4** because the model emits two *parallel* steps (`resize_video{clip.mp4}`
+ `strip_audio{clip.mp4}`) that both read the original and produce separate outputs — but
`success_criteria` wants ONE output that is both 480p AND silent. Correct plan = chain
(resize→its output→strip reads that) or a single resize step that also strips audio. This
is a distinct, larger class (`complex` tag = 149 utt), NOT arg-shaped (Step 1 won't touch
it) and NOT clarify-shaped (Task 7 won't touch it). Part model-composition limit, part
possibly-addressable (a chaining prompt example, or an expander that detects same-input
sequential ops and chains them). Scope as its own investigation after Steps 1–2; likely a
fine-tuning candidate. ffmpeg_117 also shows a 2nd issue: qwen *hallucinates* a clarify
with an invented intermediate filename on "and then" phrasing (model/prompt, not addressed).

TDD per task: RED test → GREEN → COMMIT. After EACH task re-score the affected tags on
BOTH backends before moving on (a fix that helps one model can regress the other).

## Decisions — RESOLVED 2026-06-08

1. **072-B / 094 args** → **honor the value** (thread `output`; fold `crf` into CRF path).
2. **082-B / 106 mixed rows** → **split** into separate `plan`/`clarify` rows.
3. **106 clarify-gate** → **defer** until tasks 1–6 land + re-eval; then build as its own
   focused change. NOT in this round.
4. **030 prompt change** → **attempt** (revert if either backend regresses).
5. **META analyzer** → **follow-up**, not this round (not selected).
6. **Strategic** → targeted per-arg fixes (default; honor-the-value aligns with #1).

**This round = tasks 1–6** (071, 094, 072-B, 082-A, 082-B/106-corpus split, 030).
Tasks 7 (106 gate) and 8 (META) are deferred to a later round.

## Decisions needed before execution (now resolved — see above)

1. **072-B `output` handling** — honor it (thread through `_derive_output_path`,
   reconcile extension; more wiring) vs. strip the unsupported arg (lighter, but
   discards the user's filename). *Recommend: honor.*
2. **082-B / 106 vague utterances** — split mixed-intent rows into separate
   `plan`/`clarify` rows vs. reword utterances to align with one intent. *Recommend:
   split.*
3. **106 clarify-gate** — build the deterministic unresolvable-input→clarify gate this
   round (broadest blast radius, needs investigation of where `actual_outcome` is
   derived) vs. defer 106 to fine-tuning and ship tasks 1–6 now. *Recommend: defer the
   gate to its own focused change AFTER 1–6 land, so the low-risk wins aren't blocked
   by the riskiest item.*
4. **030 prompt change** — attempt the prompt tightening (revert if either backend
   regresses) vs. skip straight to fine-tuning. *Recommend: attempt — it's cheap and
   reversible.*
5. **META analyzer** — build it this round (surfaces more false-negative rows before
   re-eval) vs. follow-up after the fixes land. *Recommend: follow-up — it informs
   corpus edits but isn't needed for the fixes above.*
6. **Strategic (cross-cutting)** — 071/072-B/094 are all "model emits a reasonable arg
   the strict schema rejects." Keep them as targeted per-arg fixes vs. build one
   general arg-normalization layer. *Recommend: targeted — a blanket "strip/coerce
   unknown args" layer risks masking genuine model errors; each fix here honors the
   value rather than dropping it.*

---

## ffmpeg_030 — "apply the same ffmpeg settings to every mp4 file here" — clarify miss

- [x] ~~Promote vague-operation rule to standalone top-level bullet~~ — **REVERTED** (commit 3663a96)

**Outcome (local4 re-score, 2026-06-08):** the prompt change did NOT flip ffmpeg_030
to clarify — it stayed `plan` on BOTH qwen3-4b and gemma3-4b (identical to local3).
Per the acceptance/fallback rule above, the change was reverted. It was also associated
with a real plan-row regression on gemma (ffmpeg_221 "compresser … pour envoi par email"
plan→clarify) and a worsened vague-op row (ffmpeg_154 "fix clip.mp4" clarify→plan).
**ffmpeg_030 is a known small-model limit, deferred to fine-tuning.** Do not re-attempt
a prompt-only fix for this single row.

**Expected:** `clarify` · **qwen3 actual:** `plan` (`convert_video{inputs:["*.mp4"]}`,
no settings) · **gemma3 actual:** `plan` (`compress_video{inputs:["*.mp4"],
target:"all/every mp4 files"}`) · **both fail.**

### Root cause
The utterance trips two adjacent prompt rules ([prompt.yaml](../../skills/ffmpeg/prompt.yaml)):
1. **Batch phrasing** ("every mp4 file here") → express as glob `["*.mp4"]`, don't clarify about the missing filename. ✅ both models did this correctly.
2. **Vague operation** ("apply the same settings" references settings never defined) → still clarify. ❌ both models ignored this.

The vague-operation clarify is buried as a sub-clause *inside* the batch-phrasing
EXCEPTION. Models latch onto the first half ("batch satisfies the file requirement")
and treat the request as complete, never noticing there is no actual operation.
The exact phrase "apply the same settings" is already enumerated as a clarify
trigger — yet the structure ranks it below the glob cue, so it loses.

### Proposed small prompt change (low-risk, try first)
Promote the vague-operation rule out of the batch sub-clause into its own top-level
clarify bullet near [prompt.yaml](../../skills/ffmpeg/prompt.yaml), e.g.:

> - If the request names NO concrete operation or parameters — only a reference to
>   unspecified settings ("apply the same settings", "do the usual", "the same as
>   before", "process every file") — emit clarify, **even when files are given**. A
>   glob/folder satisfies only the file requirement, never the operation requirement.

Keep the existing batch EXCEPTION text but trim its trailing "still clarify" clause
(now redundant with the new standalone bullet) so there's a single, unambiguous home
for the rule.

### Acceptance / fallback
- Re-score ffmpeg_030 on qwen3-4b AND gemma3-4b → both flip to `clarify`.
- Re-run the `batch` and `convert` tags on both backends → no batch/glob row that
  should produce a plan regresses to a false clarify.
- **If either backend still emits a plan, or a real batch row regresses → revert the
  prompt change and mark ffmpeg_030 a known small-model limit deferred to
  fine-tuning.** Do not iterate the prompt further for this single row.

---

## ffmpeg_071 — "snapshot/screenshot at 3s from clip.mp4" — `inputs` vs `input` schema mismatch

- [x] Add schema-driven `inputs`→`input` coercion in `normalize_plan` ✅ commit 1a4a12b

**Expected:** `plan` (4 utterances). **Failures:** RU "снимок…" fails on BOTH backends;
ES "captura de pantalla…" also fails on gemma3. All with the identical validation
error:
`Plan step 1 invalid: Tool 'extract_frame' missing required args: ['input']`.

### Root cause — argument name, not routing
Routing to `extract_frame` is correct for "snapshot/screenshot/снимок/captura". The
plan is rejected because the model emits `extract_frame{inputs:["clip.mp4"]}` (plural
list) but the tool requires scalar `input` ([tools.yaml](../../skills/ffmpeg/tools.yaml)).

Only THREE tools take scalar `input` — `trim_video`
([tools.yaml](../../skills/ffmpeg/tools.yaml)), `create_thumbnail`
([tools.yaml](../../skills/ffmpeg/tools.yaml)), `extract_frame`
([tools.yaml](../../skills/ffmpeg/tools.yaml)) — the other ~12 take plural
`inputs`. The model over-generalizes the dominant `inputs` pattern onto the rare
scalar tool. It gets `create_thumbnail{input}` right (more common) but trips on
`extract_frame`. This is the `extract_frame` singular/plural oddity from the
ffmpeg_161 open decision surfacing again.

### Proposed deterministic fix (preferred — fixes the whole class, model-independent)
Extend [`normalize_plan()` in planner.py](../../python/core/knaif/planner.py) with a
**generic, registry-driven** coercion (NOT skill-specific — it reads the tool's own
declared schema, so it stays in core legitimately):

- For each step, look up its `ToolDef`. If the schema declares scalar `input`
  (`input` ∈ required_args ∪ optional_args and `inputs` ∉ either) AND the step
  supplied `inputs` but not `input`: when `inputs` is a single-element list or a bare
  string, set `args["input"] = inputs[0]` (or the string) and delete `args["inputs"]`.
- If `inputs` has ≥2 elements, leave it — let validation reject it (a scalar-input
  tool genuinely can't take multiple files; coercing would hide a real error).

Symmetric `input`→`inputs` is NOT needed (no observed failures) — keep the change
minimal and one-directional.

### Test (RED first)
- `extract_frame{inputs:["clip.mp4"], at_time:"00:00:03"}` → after normalize,
  validates and renders `-vframes 1` on `clip.mp4`.
- `create_thumbnail{inputs:["clip.mp4"]}` and `trim_video{inputs:["clip.mp4"]}` →
  likewise coerced.
- A plural-input tool (`convert_video{inputs:["a.mp4","b.mp4"]}`) → untouched.
- `extract_frame{inputs:["a.mp4","b.mp4"]}` → still rejected (multi-file scalar tool).

### Acceptance
- ffmpeg_071 RU + ES utterances flip to `plan` on both backends; `create_thumbnail`
  and `extract_frame` tags show no regression.
- `just check` + re-run the `create_thumbnail` / `extract_frame` tags on qwen3 AND
  gemma3.
### Measured blast radius (verified against local3)
This is NOT a one-row fix. EVERY "missing required args: ['input']" failure in local3
is a single-element `inputs` (or bare-string) that the coercion clears:
- **qwen3-4b: 5 utterance-failures** — ffmpeg_071, 092, 119, 161, 232.
- **gemma3-4b: 19 utterance-failures** — ffmpeg_071, 072, 090, 092, 135, 200, 202,
  216, 232, 266, 268.
- 0 non-coercible cases (none were genuinely missing a file or multi-file). So the
  single normalizer is expected to clear all 24 with no judgment calls.

After implementing, re-grep local3 (and the fresh re-run) for the same error string to
confirm it drops to 0, and diff `outcome_accuracy` per backend.

---

## ffmpeg_072 — "extract a single frame at 5s from clip.mp4" — TWO arg-schema errors

- [x] Issue A — covered by ffmpeg_071 `inputs`→`input` normalizer ✅ commit 1a4a12b
- [x] Issue B — honor `output` on extract_frame and create_thumbnail ✅ commit b318141

**Expected:** `plan` (4 utterances). All four route to `extract_frame` correctly;
failures are pure arg-schema rejections.

| Utterance | qwen3 | gemma3 |
|---|---|---|
| EN ×2 | plan ✅ | plan ✅ |
| DE "Einzelbild… extrahieren" | error: unsupported `['output']` | error: missing `['input']` |
| BG "извлечи кадър…" | plan ✅ | error: missing `['input']` |

### Issue A — `inputs` vs `input` (gemma DE + BG)
`extract_frame{inputs:["clip.mp4"]}` → "missing required args: ['input']". **Identical
to ffmpeg_071**; both rows are already in that fix's 19-row gemma blast radius. No new
work — just confirm they clear when the 071 normalizer lands.

### Issue B — `output` rejected (qwen DE) — NEW
qwen emits `extract_frame{input, at_time, output:"frame_at_5_seconds.png"}` →
"unsupported args: ['output']". `extract_frame`
([tools.yaml](../../skills/ffmpeg/tools.yaml)) and `create_thumbnail`
([tools.yaml](../../skills/ffmpeg/tools.yaml)) are the only single-file
tools that do NOT allow `output`; most tools (e.g. `trim_video`) do. The output name
is auto-derived in [`_derive_output_path`](../../skills/ffmpeg/python/_engine.py)
as `{stem}_frame.{image_format}`, and `expand_extract_frame` (since replaced by the intent classes in [intents.py](../../skills/ffmpeg/python/intents.py))
never reads an `output` arg. Small models naturally add a filename → hard error.

**Option 1 — honor `output` (the real fix, recommended pending investigation):**
add `output` to `optional_args` for `extract_frame` + `create_thumbnail`, thread it
into `options` → `_derive_output_path`/`build_recipes`, and reconcile the extension
(an explicit `frame.png` should imply `image_format=png` and override the default
jpg). INVESTIGATE the batch block (`_build_batch_block`) first — output naming is
currently auto-derived per input, so honoring an explicit single output needs care
(and only makes sense for single-input image extraction, not globs).

**Option 2 — strip unsupported `output` (generic, low-risk fallback):** in
`normalize_plan`, registry-driven, drop any arg not in a tool's
`required_args ∪ optional_args`. Flips to `plan`, renders at the auto-derived name,
but silently discards the user's chosen filename. Lower effort, less faithful.
Caveat: a blanket arg-stripper is more aggressive than the targeted `output` case —
if chosen, scope it to `output` only, not all unknown args (stripping arbitrary args
could mask genuine model mistakes).

### Acceptance
- DE/BG utterances flip to `plan` on both backends (A via 071 fix, B via chosen
  option); EN rows unchanged.
- If Option 1: rendered command writes to the requested filename with the matching
  extension; no `create_thumbnail`/`extract_frame` regression on either backend.
- `just check` + re-run `extract_frame` + `create_thumbnail` tags on qwen3 AND gemma3.

---

## ffmpeg_082 — "join/concatenate two mp4 files" — TWO unrelated problems in one row

- [x] Problem A — add `grade: "routing"` ✅ commit df3c798
- [x] Problem B — split vague utterances into new clarify row ffmpeg_082b ✅ commit 3d1d45f

Row: `expected_outcome: plan`, `fixture: clip.mp4` (single), `success_criteria:
{container: mp4}`, 4 utterances. ([eval.jsonl](../../skills/ffmpeg/data/eval.jsonl))

| # | Utterance | qwen3 | gemma3 |
|---|---|---|---|
| 1 | "join clip.mp4 and clip.mov into one file" | plan, score 0.0 | plan, score 0.0 |
| 2 | "concatenate two mp4 files" | clarify ❌ | clarify ❌ |
| 3 | "Zwei MP4-Dateien zusammenfuegen" (DE) | plan (glob `*.mp4`), score 0.0 | error (no inputs) ❌ |
| 4 | "обедини два mp4 файла" (BG) | clarify ❌ | clarify ❌ |

### Problem A — 0.0 on correctly-routed concat = missing second fixture (DETERMINISTIC)
Concat needs ≥2 inputs but the row copies only `clip.mp4`; the second input never
materializes → no artifact → `{container: mp4}` unverifiable → 0.0, even though
routing is correct (cells 1-qwen, 1-gemma, 3-qwen). **Identical to ffmpeg_215/231**,
which were fixed with `"grade": "routing"` (commit b46abe8); 082 was missed in that
pass. **Fix:** add `"grade": "routing"` to ffmpeg_082. Recovers the 3 correctly-routed
cells; `grade:routing` still fails a genuine mis-route, so it's safe.

### Problem B — vague "two mp4 files" utterances → clarify is defensible (CORPUS, not model)
Utterances 2/4 (and DE-3) give NO filenames ("two mp4 files"). Models respond
`clarify` ("which files?") — arguably the CORRECT behavior; gemma's DE `error`
(concat with zero inputs) is the failed version of the same underspecification.
Forcing a `plan` here would require the model to fabricate filenames or blindly glob
`*.mp4` (qwen's DE guess). This is the corpus over-demanding, NOT a fixable model gap,
and pushing a prompt rule to force-plan would encourage input fabrication elsewhere
(contradicts the 030 "vague → clarify" principle).
**Decision (corpus authoring):** either (a) split utterances 2/4 (+ DE) into a
separate row with `expected_outcome: clarify`, or (b) reword them to name two concrete
files. Do NOT attempt a prompt change to force-plan. Recommendation: (a) — it rewards
the honest clarify behavior and keeps the realistic vague phrasings in the corpus.

### Acceptance
- After A: cells 1-qwen, 1-gemma, 3-qwen flip to pass (outcome-graded). Re-check
  `concat_video` tag on both backends — no correctly-routed concat regresses.
- After B: the vague utterances are scored against the outcome they actually deserve
  (clarify), on both backends.
- Note: A and B are independent — A is a clean commit on its own; B is a corpus-design
  call to make deliberately.

---

## META — Detect "non-equivalent-utterance" rows corpus-wide (false-negative source)

- [ ] Build an offline analyzer over `evals/_archive/v2/local3/` to shortlist mixed-intent rows

**Motivation:** ffmpeg_082 is not unique. Any corpus row bundles N utterances under ONE
shared `expected_outcome` + `success_criteria`, on the contract that all utterances are
true paraphrases of one intent. When that contract is violated — some utterances name
their inputs (→ `plan`), others are underspecified (→ `clarify` is correct) — the row
manufactures permanent false negatives: the model is penalized for doing the right
thing on the divergent utterances. We should find ALL such rows, not fix them one at a
time by stumbling onto them.

**This is doable from existing eval JSON — no model re-runs.** The eval already expands
each row into one result per (utterance × backend), recording `actual_outcome`.

### Detector heuristic (cross-backend outcome divergence)
For each corpus `id`, build `utterance → {backend → actual_outcome}` (treat `error`
from underspecification — e.g. concat with zero inputs — as a `clarify`-class outcome).
Flag the row as a **mixed-intent candidate** when ALL of:
1. Its utterances do NOT all share one outcome (intra-row divergence exists).
2. Per utterance, the outcome is **consistent across both backends** (both models agree
   for that utterance) — this isolates utterance-driven divergence from model noise.
3. At least one utterance's stable outcome ≠ the row's `expected_outcome`, while at
   least one other utterance matches it.

Rank candidates by how many utterances contradict the expectation (severity).

### Why the cross-backend gate matters
- **Corpus bug fingerprint:** same utterance → same outcome on both models, but
  *different utterances* → different outcomes. The utterances are not equivalent.
- **Model-noise (NOT flagged):** the *same* utterance disagrees *between* backends
  (qwen `plan`, gemma `clarify`). That's model variance, a different problem.

### Secondary lexical signal (reduce false positives)
For `plan`-expected rows, corroborate by checking which divergent utterances lack any
explicit file token (no `.mp4`/`.mov`/path/quoted name) while sibling utterances have
one. A missing-file-reference utterance that consistently routes to `clarify` is a
near-certain mis-bin. Surface this as a column, not an auto-decision.

### Output & usage
Emit a ranked report (`id`, expected_outcome, per-utterance stable outcomes, the
divergent utterances, lexical flag) for human triage. Each flagged row gets the same
A/B treatment as ffmpeg_082: split into separate rows by outcome, reword to align
intent, or re-bin. The analyzer SHORTLISTS — it does not auto-edit the corpus.

### Caveats
- Only 2 active backends now → "consistent across backends" = 2-of-2 agreement (weaker
  than 3). Still a strong filter; revisit if more backends are added.
- A blind spot shared by both small models could mimic a corpus bug; the lexical signal
  and a human look guard against that.
- This finds OUTCOME-divergence rows (plan-vs-clarify). A sibling analyzer could flag
  CONTENT-divergence rows (same outcome, but `success_criteria` fits only some
  utterances — cf. ffmpeg_080's Russian "extract audio" vs strip-audio). Note as a
  possible follow-up; not in scope for the first pass.

---

## CORPUS TRIM — consolidate redundant descriptor-reference clarify rows (eval speed)

- [ ] Trim the descriptor→clarify cluster in EVAL; keep distinct-trigger clarifies

> **Live work, unblocked (2026-07-22).** This was deferred behind Task 7, which has since
> shipped as the NL clarify gate (PR #14) — so the sequencing condition below is now
> satisfied and descriptor→clarify *is* deterministic. All 13 rows of the
> `ffmpeg_253–265` cluster remain in `eval.jsonl`; the row counts below are as measured
> in 2026-06-08 and should be re-measured before acting. The durable principles behind
> this section (distinct triggers vs. redundant restatements, deterministic behavior
> belongs in unit tests, trim eval ≠ trim train) are in *Corpus composition* in
> [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md).

**Context (measured 2026-06-08):** clarify = 66 rows / **181 utterances = 24%** of the
769-utterance suite. They split by what they actually test:

- **Distinct-trigger clarifies — KEEP ALL (real safety value):**
  - Unsupported feature (watermark, subtitles, color grade): 012, 019, 048, 049,
    176–180, 204, 208 — tests the model does NOT substitute a wrong tool.
  - Vague operation: 011, 018, 157, 272 ("adjust audio levels in clip.mp4" — file
    named, but how?) — the 030-style "vague op → clarify".
  - Edge / bad param: 151, 155, 165, 169–173, 261 — invalid time, out-of-range.
- **Descriptor-reference clarifies — REDUNDANT, consolidate:** the **ffmpeg_253–265
  cluster (~13 rows)** + 104/110/207. All test ONE behavior — "a descriptive reference
  ('the 4K video', 'the mov clip', 'the silent clip') is not a filename → clarify" —
  re-run across many operations × 5 utterances × 4 languages × 2 backends. The
  operation is irrelevant to the decision, so this is ~13× redundancy.

**Plan:**
1. Keep a **canonical 3–4** descriptor→clarify rows (cover a couple of operations + the
   language spread); remove the rest of the 253–265 cluster from `eval.jsonl`. Saves
   ~10 rows / ~50 utterances (~7% of the suite ×2 backends), no behavior-coverage loss.
2. Optionally cut kept descriptor rows from 5 → 2 utterances (1 EN + 1 non-EN).
3. **Do NOT rename descriptor utterances to real filenames** — that converts a clarify
   test into a plan test (already 188 plan rows); it shifts load and erases deliberate
   clarify coverage. Renaming is reserved for the mixed-intent SPLIT (082/106), which
   is a correctness fix, not a speed one.

**Sequencing:** this is strongest AFTER task 7 (the ffmpeg_106 deterministic
unresolvable-input→clarify gate). Once descriptor→clarify is deterministic, a few unit
tests on the gate cover it better than corpus volume, so the eval only needs a token
sample. Trim then, or trim now and keep the canonical sample either way.

**Caveat — eval vs train:** trimming is an EVAL speed/value call only. Descriptor→
clarify examples may still be wanted in `train.jsonl` to TEACH the behavior for
fine-tuning. Trim eval; separately review whether training should keep/add them.

**Acceptance:** clarify utterance count drops materially; the distinct-trigger clarify
behaviors (unsupported / vague-op / edge-param) remain covered; total eval wall-clock
on both backends measurably lower.

---

## ffmpeg_094 — "compress clip.mp4 using CRF 30" — `crf` reachable only via the `quality` string

- [x] Add `crf` as a first-class optional arg on `compress_video` + `convert_video`,
      folded into the existing CRF→profile path ✅ commit f450574

**Same SYMPTOM as 072-B** (`Tool 'compress_video' has unsupported args: ['crf']` →
`error`), **but a NEW root cause:** the CRF capability already exists — it's just
reachable only through the `quality` STRING slot, not a structured `crf` key.

Row: `expected_outcome: plan`, `fixture: clip.mp4`, `success_criteria: {container:mp4,
video_codec:h264}`, 5 utterances all meaning "compress with CRF 30".
([eval.jsonl](../../skills/ffmpeg/data/eval.jsonl))

| Utterance | qwen3 | gemma3 |
|---|---|---|
| EN "using CRF 30" | plan ✅ | error (`crf:30`) |
| EN "quality 30" | plan ✅ | plan ✅ |
| DE "mit CRF 30" | plan ✅ | plan, score 0.0 (`target:"30"`, no artifact) |
| BG "с CRF 30" | plan ✅ | error (`crf:"30"`) |
| ZH "CRF 30压缩" | error (`crf:30`) | plan ✅ (`quality:"crf 30"`) |

### Root cause
CRF is parsed only from the `quality` arg: [`LoadQualityProfileStep`](../../skills/ffmpeg/python/steps.py)
matches `quality:"crf 30"` via [`_CRF_RE`](../../skills/ffmpeg/python/_engine.py) →
[`_crf_to_profile_name`](../../skills/ffmpeg/python/_engine.py). gemma's ZH row
proves the channel works (`quality:"crf 30"` → 1.0). But `compress_video`
([tools.yaml](../../skills/ffmpeg/tools.yaml)) has no `crf` in
`optional_args`, so models that emit the natural structured `crf: 30` are rejected.
High per-utterance variance (`crf:30` / `quality:"crf 30"` / `target:"30"` / preset)
shows models can't reliably guess CRF must be smuggled into the `quality` string. This
is an interface-ergonomics gap, NOT a missing capability — distinct from 071 (wrong
arg name) and 072-B (`output` not honored at all).

### Proposed deterministic fix (preferred)
Add `crf` to `optional_args` for `compress_video` AND `convert_video` (the latter
already lists `crf` in its keywords). In their expanders, when `crf` is present, route
it through the existing CRF path — e.g. set `quality = f"crf {int(crf)}"` (or call
`_crf_to_profile_name` directly) so it reuses the validated `_CRF_RE` logic. Reuses
working code; honors the explicit value. Re-uses the same conversion `_crf_to_profile_name`
already applies, so output is identical to the working `quality:"crf 30"` case.

### Out of scope / separate notes
- **gemma DE `target:"30"` → 0.0** is a different misroute (CRF read as a `target`
  preset), NOT fixed by adding `crf`. Leave as a model arg-quality limit unless it
  recurs broadly.
- **Criteria too weak:** `{container:mp4, video_codec:h264}` never verifies CRF=30, so
  every preset approximation scores 1.0 and only hard errors fail. Merely *stripping*
  the unsupported `crf` would also make the row pass — but silently drops the user's
  "30". Prefer honoring it. (Optional follow-up: a criterion that probes the encoded
  CRF/bitrate so the corpus actually checks what the utterance asked for.)

### Acceptance
- The three erroring cells (qwen ZH, gemma EN, gemma BG) flip to `plan` and score 1.0
  on both backends; the already-passing cells don't regress.
- Rendered command for `crf:30` matches the `quality:"crf 30"` case (same profile).
- `just check` + re-run `compress` + `crf` + `convert` tags on qwen3 AND gemma3.

---

## ffmpeg_106 — "grab a frame from the 4K video at 2s" — descriptor stuffed into `input` slot

- [ ] INVESTIGATE + build: deterministic "unresolvable input → clarify" gate
- [ ] Corpus: flag the borderline "clip_4k" utterance via the META analyzer

**Expected:** `clarify`. The utterances reference "the 4K video"/"clip_4k"/"4K-Video"/
"4K视频" — descriptions, never a real filename (fixture `clip_4k.mp4`). The row wants
"which file?". ([eval.jsonl](../../skills/ffmpeg/data/eval.jsonl))

| Utterance | qwen3 | gemma3 |
|---|---|---|
| "the 4K video" | plan `input:"4K video"` ❌ | plan ❌ |
| "clip_4k" | plan `input:"clip_4k"` ❌ | plan ❌ |
| "4K-Video" (DE) | clarify ✅ | clarify ✅ |
| "4K видеото" (BG) | plan ❌ | clarify ✅ |
| "4K视频" (ZH) | plan ❌ | clarify ✅ |

qwen 1/5 clarify, gemma 3/5. Failure mode: the model stuffs the descriptive noun
phrase into `input` (`input:"4K video"`) instead of recognizing no concrete file was
named.

### Is it an LLM limit? Partly — and not reliably fixable by prompt/model
Recognizing "the 4K video" as a referring expression (not a filename) is subtle for 4B
models; gemma 3/5 vs qwen 1/5 shows it's model-dependent and only partly reachable.
The prompt ALREADY says "no explicit file path → clarify"
([prompt.yaml](../../skills/ffmpeg/prompt.yaml)) and it still fails →
prompt-only is not a reliable fix. Reasonable fine-tuning target, but low confidence.

### Deterministic solution (preferred — model-independent, principled)
Confirmed: [`ResolveInputs`](../../python/core/knaif/steps/_resolve_inputs.py)
does NO fuzzy/stem matching — a non-existent, non-glob input is passed through verbatim
(line 724-725) and [`InspectMediaStep`](../../skills/ffmpeg/python/steps.py)
flags it `not found`. So every one of these plans references a file that does not exist.

**Gate:** if a plan's input does not resolve to a real sandbox file — not found, not a
glob pattern, not the declared output of a prior step — emit `clarify` ("which file?")
instead of attempting a doomed plan. This catches "4K video"/"4K视频"/"clip_4k"
deterministically on both backends, and aligns with the existing safety model
(pre-execution path validation, [CLAUDE.md](../../CLAUDE.md) safety #3) — it just turns a
guaranteed "file not found" into the USEFUL clarify rather than a hard error.

### INVESTIGATE before building (non-trivial)
- Where does the harness derive `actual_outcome`? The gate is a runtime/resolution
  decision, not the model's emitted plan/clarify choice — confirm a deterministic
  downgrade to clarify is recorded as a `clarify` outcome by the eval.
- Insert point: likely a new validation/clarify step after `resolve_inputs`/
  `inspect_media`, or a pre-flight in the agent pipeline. Must NOT fire when the input
  is a glob, an absolute/relative real path, or a chained `$output` from a prior step.
- Regression risk: any row whose plan legitimately names a file the eval fixture
  doesn't copy could be wrongly downgraded — scope the gate to the planning preflight
  and re-run ALL tags on both backends. This is the broadest-blast-radius change in
  this plan; treat with care.

### Corpus note
"extract a still from clip_4k at 2s" uses the fixture's stem — proceeding is
defensible, so demanding `clarify` there is borderline (non-equivalent-utterance smell;
the META analyzer should flag it). The deterministic gate clarifies it correctly anyway
(`clip_4k` ≠ `clip_4k.mp4`), but note the utterance is weaker than its siblings.

### Acceptance
- All plan cells flip to `clarify` on both backends (gate fires on unresolvable input);
  no row with a legitimately-named/globbed/chained input regresses to a false clarify.
- `just check` + full re-run on qwen3 AND gemma3, with special attention to the
  `clarify` tag and any row whose fixture differs from the model's emitted filename.
