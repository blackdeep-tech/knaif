# Eval Quality Fixes — Post Honest-Eval (success verifier)

**Status:** Done · **Created:** 2026-06-06 · **Completed:** —
**Owner:** eval · **Ref:** `evals/_archive/v2/local/ffmpeg_qwen3-4b_success.json`

> **Status note:** T1–T3 done (+7.6pp knaif, 74→12 low-scoring rows); T4 closed as
> won't-fix. Source data: honest `success` eval, qwen3-4b, 769 utterances
> (outcome 70.5% / avg knaif 84.1%).
>
> **Kept 2026-07-22** for the T3 triage record and the T4 decision. The durable rules
> now also live in the shipping docs — the `grade` field and routing-only rows, plus
> the token-set container/codec comparison, in [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md);
> the run-hygiene gotchas in [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md);
> and "refusal routing is a metric, not a guardrail" in
> [REQUIREMENTS.md](../REQUIREMENTS.md). Those are the current reference; this file
> records how the decisions were reached.
>
> Source references below were written against the pre-monorepo layout (`src/knaif/`,
> `src/skills/`) and the old `eval_results/` tree; they have been repointed to current
> paths. Line numbers are omitted where the code has since moved, and
> `cmd_resolve_inputs` has since become the shared
> [`ResolveInputs`](../../python/core/knaif/steps/_resolve_inputs.py) step.

**Goal:** Fix the quality issues surfaced by the honest (`success`-verifier) eval —
rows that routed correctly but produced a low artifact score.

## Context

The honest eval surfaced **74 rows that routed correctly but produced a low
artifact score (<0.5)**. Triaged, they are three different problems plus a
separate routing gap. Fix in the order below — deterministic fixes first (they
make every later number trustworthy), model/prompt work last.

Decomposition of the 74:
- ~55 `artifact_file_missing_or_not_produced` — dominated by **batch/glob rows**
  (`inputs: ["*.mp4"]`, `fixture: None`) that have no files to operate on (T2).
- ~5 **false failures** from the verifier's container check (T1).
- ~15 **genuine handler bugs** — resize not applying `scale`, extract_audio codec (T3).
Separately, the **safety/refusal routing** is weak (T4).

---

## T1 — Fix `success` verifier container (and codec) comparison ✅ DONE (2026-06-07)

- [x] **Root cause:** `success()` container check does an exact-token compare
  ([`skills/ffmpeg/eval/verifiers.py`](../../skills/ffmpeg/eval/verifiers.py)): `exp in containers`. When a
  row's `success_criteria.container` is multi-valued (e.g. `"matroska,webm"` for
  webm output, whose ffprobe `format_name` is `matroska,webm`), the whole string
  is never `in` the split list → false failure. `output_diff` already does this
  correctly with **set intersection** (same file, `output_diff`).
- [x] **Fix:** in `success()`, split the expected container on `,` and pass if
  `set(expected) & set(actual)` is non-empty (mirror `output_diff`). Audit the
  `audio_codec` / `video_codec` checks in the same file for the same
  string-vs-token fragility.
- [x] **Tests (RED first):** unit tests on the verifier — `container: "webm"`
  and `container: "matroska,webm"` both pass against a `matroska,webm` probe;
  a genuine mismatch (`mp4` vs `matroska,webm`) still fails. (4 tests added,
  244 ffmpeg tests pass, no regression.)
- [x] **Acceptance:** the ~5 `container: expected 'matroska,webm'…` false
  failures disappear; no currently-passing row regresses.

## T2 — Batch/glob rows: routing-only grading + glob unit tests (~26 "missing artifact") ✅ DONE (2026-06-07)

> **Correction during impl:** the plan named ffmpeg_076 as a `fixture: None`
> glob row — it actually has `fixture: clip.mp4` + `container: "matroska,webm"`,
> so its low score was the T1 container false-failure (now fixed), *and* it
> still produces no single artifact under a batch plan. Evidence from
> `evals/_archive/v2/local/`: all `batch`-tagged **plan** rows (029, 076, 077,
> 139, 206, 214, 229) route plan/plan but score 0.0 `artifact_file_missing`.
> So the routing-only set = batch-tagged plan rows (7), not just fixture-None.

**Decision (2026-06-07): Option B.** Rows with glob inputs (`*.mp4`) and
`fixture: None` (e.g. ffmpeg_029, ffmpeg_076) can't produce a single artifact in
the success eval (the agent's sandbox is `./sandbox`, not `sandbox/fixtures`,
and the execution block gates on a single `row.fixture` in
[`runner.py`](../../python/core/knaif/evalsuite/runner.py)).
Their *routing* is already correct, so grade them on outcome only and cover the
glob capability with unit tests where the logic actually lives — rather than
rebuilding the eval execution path (rejected option A).

- [x] **T2.1 — Corpus flag.** Add an optional `grade: "routing"` field to the row
  model in [`corpus.py`](../../python/core/knaif/evalsuite/corpus.py) (default `"full"`). Set it on the
  batch/glob rows: ffmpeg_029, ffmpeg_076, and any other `inputs: ["*.ext"]` /
  "all/every … files" row (grep the corpus for glob inputs and the `batch` tag).
- [x] **T2.2 — Verifier honours it.** In
  [`skills/ffmpeg/eval/verifiers.py`](../../skills/ffmpeg/eval/verifiers.py)
  `success()`, when the row is `grade: "routing"`, score on outcome only and
  **skip artifact grading** (no `artifact_file_missing` penalty). The runner
  already skips execution for `fixture: None`, so this is a
  scoring-side change; thread the flag through to the verifier call.
- [x] **T2.3 — Glob unit tests (the real coverage).** In the ffmpeg skill tests,
  add 2 unit tests on `cmd_resolve_inputs` (now `ResolveInputs`): `*.mp4` against a
  temp dir with 3 `.mp4`s + 1 `.mov` resolves to exactly the 3 `.mp4`s (sorted);
  a glob matching nothing yields an empty/clear result. This is where the batch
  capability is genuinely verified.
- [x] **Acceptance:** batch/glob rows no longer report `artifact_file_missing`;
  the `batch` tag's knaif score is no longer dragged to ~7% by ungradeable rows;
  `resolve_inputs` glob expansion is unit-tested.

## T3 — Genuine handler quality bugs ✅ DONE (2026-06-07)

> **Triage correction:** inspecting the actual failing rows in
> `evals/_archive/v2/local/` revised both hypotheses.

- [x] **resize/scale (ffmpeg_117, 227): NOT a handler bug — no change.**
  `resize_video` alone correctly renders `-vf scale=-2:480` (verified). These
  rows route to two *independent* single-file intents (resize clip → resized;
  strip clip → silent) and the success eval grades the strip_audio output, which
  legitimately has no scale. This is a corpus plan-structure question (should be
  a chained/multi-output row), not a deterministic handler fix. Left as a
  separate corpus decision; documented in the T3 commit.

  > **Closed since (verified 2026-07-22).** That corpus decision was made: both
  > rows are now multi-output (`outputs`, with
  > `expected_tools: ["resize_video", "strip_audio"]`), so each deliverable is
  > graded against its own criteria and the resize output is no longer judged by
  > the strip_audio artifact. See "Multi-output rows" in
  > [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md).
- [x] **mp3 codec bug was `adjust_volume`, not `extract_audio` (ffmpeg_269/270/
  271 `audio_codec mp3 got aac`):** `adjust_volume` on an audio-only `audio.mp3`
  rendered `.mp4 -c:v copy -c:a aac`. Two coordinated causes fixed:
  (1) `_dummy_probe` (dry-run fallback the eval uses to build commands) treated
  every file as 1920x1080 h264 → audio probed as video; now audio extensions
  probe audio-only. (2) `adjust_volume` gained an audio-only path: keep input
  container, pick the matching encoder (libmp3lame), drop the bogus `-c:v copy`.
- [x] **Tests (RED first):** `_dummy_probe` audio-extension test + adjust_volume
  audio-only render tests (mp3 output, libmp3lame, no `-c:v copy`, no aac) +
  a video-input regression guard. 852 tests pass.
- [x] **Acceptance:** re-run completed → `evals/_archive/v2/local2/`.
  avg_knaif 84.1% → **91.7%** (+7.6pp). Key lifts: `batch` +92.9pp (T2),
  `convert` +35.7pp (T1), `adjust_volume` +19.0pp (T3), `codec` +22.7pp (T1),
  `typo`/`uppercase` +100pp (T1). Remaining < 0.5 rows: 12 (down from 74),
  mostly `artifact_file_missing` on multi-step concat/complex chains —
  separate corpus/runner architecture question.

## T4 — Safety / refusal routing ❌ WON'T FIX at the model layer

**Decision (2026-06-07):** The model routes `clarify` instead of `reject` for
most safety-sensitive requests (reject 35%, safety 25%, exfiltration 12.5%,
sandbox_escape 0%). However:

1. The model is not *executing* exfiltration or sandbox-escape plans — it
   clarifies. Actual security is enforced by the deterministic sandbox-path
   validation and handler preflight, not the model's routing label.
2. The 27 failing rows span highly varied patterns (FTP upload, shell exec,
   overwrite-in-place, system-root access, impossible upscales, HDR→SDR). Prompt
   tuning a 4B model to reject all of these reliably is demonstrated-unreliable
   and risks over-refusal on legitimate `plan` rows.
3. Benchmark safety routing accuracy only becomes meaningful at ≥7B parameters
   where the model can hold multi-shot refusal patterns without forgetting them.

**The real guardrail is deterministic** and already in place. Safety routing
accuracy on small models is a metrics concern, not a security concern.

---

## Working notes

- TDD discipline: RED → GREEN → COMMIT per task (the established pattern).
- Deterministic fixes (T1–T3) before the prompt/model work (T4): the project
  thesis — and this session's evidence — is that structural fixes beat prompt
  tuning.
- The run-hygiene notes that were here (fresh `--save` dir per run; never pipe the
  eval through `tail`, which buffers until exit) now live in
  [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md).
