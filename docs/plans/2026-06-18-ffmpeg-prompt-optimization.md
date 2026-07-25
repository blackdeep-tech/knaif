# FFmpeg Prompt Optimization — Measured A/B Loop

**Status:** Done · **Created:** 2026-06-18 · **Completed:** —
**Owner:** ffmpeg · **Ref:** PR #18 · feature/more-tools-ffmpeg

> **Status note:** A/B round complete. Winning variants (after-trim + after-step2)
> merged in PR #18; control re-locked 0.862 → 0.933 outcome (+0.071). Losing arms
> reverted (see Results log). Open tail: fine-tuning escalation and multi-output
> baseline-review tooling.
>
> **Kept 2026-07-22** (S7 decision) — the measured record of what was tried and what
> failed. Every code claim re-verified: `_link_chain_intermediates` is live in
> [`agent.py`](../../python/core/knaif/agent.py), `output` is in `resize_video`'s
> `optional_args`, and all three named tests exist
> (`test_chain_intermediate_linking.py`, `test_producer_output_arg.py`,
> `test_evalsuite_stem_sandbox.py`).
>
> **Method extracted to the shipping docs** — this file is the worked example behind
> them, cite the docs first:
> - the **harness-vs-product fork** (a failing row may be the test rig, not the model),
>   **classify the whole failure set before editing**, **a fix that only satisfies the
>   cheaper verifier is not a fix**, **establish the noise floor first**, and **churn in
>   one layer masks signal in another** → [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md);
> - **an `outputs` row with empty `criteria` measures nothing** →
>   [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md) (*Multi-output rows*).
>
> **Reading the checkboxes below:** the **Results log is authoritative**, not the Step
> headings. The protocol was written up-front and the round then diverged from it — Step 1's
> diagnosis redirected the effort, so Arms A/B/C were superseded by the Cat-A/B/C work the
> log records. Unchecked Step boxes mean "this arm as originally specified was not run",
> not "this work is outstanding". Annotated inline.
>
> Commands were written for the pre-monorepo tree; `.\.venv\Scripts\python.exe -m` is now
> `uv run -m`, and `eval_results/` is now `evals/`. Line numbers in the diagnosis
> (`agent.py:814`, `agent.py:873`, `prompt.py:39`) predate the restructure and are dropped —
> the symbol names are the durable anchors.

**Goal:** Run a measured A/B loop on the ffmpeg prompt (qwen3-4b) and merge only the
variants that improve outcome accuracy.

## Goal

Improve qwen3-4b routing quality on the ffmpeg corpus by iterating on `prompt.yaml`
(plus one retrieval code arm), measuring each variant against the locked snapshot, and
**keeping only variants that beat the control beyond run-to-run noise**. If no variant
clears the bar, revert to the original prompt and escalate to fine-tuning.

This is an experiment, not a refactor: every change is a hypothesis that must pay for
itself in the scoreboard.

## Decisions (locked)

- **Target model:** qwen3-4b is the optimization target. gemma3-4b runs as a
  **non-regression guard** — a variant that helps qwen but regresses gemma beyond noise
  is rejected. (They have opposite failure modes; see the gemma guard results below.)
- **Win metric:** `outcome_accuracy` is the headline gate. A variant is adopted only if
  outcome_accuracy rises beyond the noise floor **and** no per-category bucket
  (reject/clarify/convert/…) drops materially. No trading reject gains for plan-routing losses.
- **Scope:** `prompt.yaml` text edits, plus one isolated code arm — `min_score=3` in
  `run()`. `infer_stream` and broader retrieval rewiring are **out of scope** for this round.

## Control (the thing we beat)

`skills/ffmpeg/data/eval_snapshot.json` — current locked baseline:

| Metric | Value |
|---|---|
| total rows | 297 |
| outcome_accuracy | 0.862 |
| tool_accuracy | 0.811 |
| schema_validity | 0.987 |

**Weak categories (where the points are):**

| Category | Correct / Total | Rate |
|---|---|---|
| reject | 9 / 19 | 47% |
| clarify | 28 / 39 | 72% |
| speed | 2 / 3 | 67% |
| platform | 9 / 11 | 82% |

Everything else is ≥85%. **reject and clarify hold ~21 recoverable rows** — that is the
entire opportunity. Optimization effort goes there, not at the already-strong buckets.

## Harness (already exists — no tooling to build)

All runs save under `evals/` (never a root `runs/` — see CLAUDE.md "Running
Evaluations" and `evals/INDEX.md`).

```bash
# One variant run → scoreboard JSON, qwen primary + gemma guard
uv run -m knaif.evalsuite run --skill ffmpeg \
  --config eval_backends.yaml --backends qwen3-4b,gemma3-4b \
  --verifier cheap --save evals/runs/<YYYY-MM-DD>_<variant>_cheap

# Diff a run against the locked snapshot (per-metric regressions/improvements)
uv run -m knaif.evalsuite regression --skill ffmpeg \
  --current evals/runs/<YYYY-MM-DD>_<variant>_cheap/ffmpeg_qwen3-4b_cheap.json --threshold <noise-floor>

# Side-by-side multi-arm HTML/MD across all saved variants
uv run -m knaif.evalsuite report --skill ffmpeg --results-dir evals/runs
```

- Sampling is greedy (`temperature 0.0`), so deltas are meaningful.
- `--save` writes `<dir>/ffmpeg_<backend>_cheap.json` (full per-row `rows`).
- `--snapshot` **re-locks** `eval_snapshot.json` — only used when *adopting* a winner.
- `--corpus <file>` overrides the corpus → used for the fast weak-category sub-corpus below.

## Protocol

### - [x] Step 0 — Establish the noise floor (gate-defining, do first)

**Result (2026-06-18):** noise floor = **0**. Two full qwen3-4b runs (`evals/runs/control-a`,
`evals/runs/control-b`) gave identical 0.8620 / 0.8114 and **0 per-row flips**. Greedy +
json_mode-off is fully deterministic, so every row-level delta is trustworthy signal.
`control-a` reproduces the snapshot exactly. THRESHOLD kept at 0.02 for the regression
guard, but decisions may rely on single-row deltas.

GPU float nondeterminism can flip a few tokens even at temp 0. We must know the noise
band before any delta is trustworthy.

- [ ] Run the **unchanged** prompt twice into `evals/runs/control-a` and `evals/runs/control-b`.
- [ ] Compute `|outcome_a − outcome_b|` and the max per-category swing.
- [ ] Set **`THRESHOLD = max(0.02, observed_noise)`**. If runs are bit-identical, keep 0.02.
- [ ] Confirm `control-a` reproduces the snapshot's 0.862 (sanity: snapshot is current).

**Acceptance:** noise floor is a written number; both control runs land within it.

### - [x] Step 1 — Diagnose the actual failures (look before editing)

**Result (2026-06-18): the dominant failure mode is NOT prompt-addressable.**
Of 41 failures (qwen3-4b control), the per-row root-cause split is:

| Cat | Count | Root cause | Prompt-tunable? |
|---|---|---|---|
| **A** | 9 | **Eval-harness stem-path mismatch.** `execute_plan` calls `resolve_stems` with `agent.sandbox = sandbox/`, but fixtures live in `sandbox/fixtures/`. Extension-less corpus names (`clip_4k`, `clip_no_audio`) raise `StemNotFoundError` → forced clarify. Model emits the correct plan. | No — harness wiring |
| **B** | 11 | **Chaining intermediate flagged.** Model produces a correct multi-step chain but omits step-1 `output`, so `_hallucinated_filename` (`agent.py`) sees step-2's invented intermediate (`clip_resized.mp4`) as a hallucinated input → clarify. (3 of these also hit Cat A via `clip_4k`.) | Partly — model must declare step-1 `output` |
| **C** | 21 | **Genuine model errors.** under-reject (reject→plan/clarify): out-of-scope/unsafe slips through — overwrite original, download URL, server-wide, 8K upscale, 0x0, read system config (~11). under-clarify (clarify→plan): vague requests get planned — "best format", "rotate" w/o angle, "adjust audio levels" (~7). plus 3 errors/parse. | **Yes — the real prompt/fine-tune target** |

Verified by capturing raw pre-gate model plans (`/tmp/repro*.py`) and confirming
`resolve_stems({'inputs':['clip_4k']}, sandbox/)` raises while against `sandbox/fixtures/`
it resolves to `clip_4k.mp4`.

**Implication:** ~20 of 41 failures (Cat A + chaining-B ≈ 6.7 pts of the 13.8-pt gap) are
infra/guard artifacts, not prompt problems. Fixing those lifts outcome_accuracy toward
~0.93 *without touching the prompt*. Prompt optimization (and any later fine-tuning) should
target **Cat C — reject/clarify discrimination — the only genuinely model-driven bucket.**
The original Arm A (`min_score=3`) addresses only part of Cat C and is now de-prioritised
behind the harness fix.

---
_Original Step 1 plan (superseded by the result above):_

Don't guess prompt edits. Pull the failing reject/clarify rows from the control's per-row
`rows` and read what qwen actually emitted.

- [ ] From ``evals/runs/control-a`\ffmpeg_qwen3-4b_cheap.json`, extract rows where
      `outcome_correct == false` and tag ∈ {reject, clarify}.
- [ ] Bucket each failure: wrong-tool-substitution / under-rejecting / over-clarifying /
      missing-file mis-handling. Record counts.
- [ ] Build a **focused sub-corpus** of just these rows
      (`skills/ffmpeg/data/eval_weak.jsonl`) for fast iteration via `--corpus`.

**Acceptance:** a failure-mode table + a <40-row sub-corpus that reproduces the gap.

### - [x] Step 2 — Arm A: `min_score=3` in `run()` (code, isolated)

> **Ran post-merge, discarded.** Both `min_score=3` (−0.027) and the gentler
> `min_score=1` (−0.007, knaif −0.049) lost rows — the filter drops
> description-only-matched tools off legitimate rows, and Cat C already handles
> out-of-scope. Reverted; see the Results log.

`run()` currently retrieves with `min_score=0` (`agent.py`), so unsupported requests
still surface weakly-related tools — the mock path already uses 3. Hypothesis: aligning
them pushes out-of-scope requests toward clarify/reject (directly the weak buckets).

- [ ] RED: add a test asserting an out-of-scope utterance ("add subtitles to clip.mp4")
      retrieves nothing above threshold in the `run()` path (per the project TDD rule).
- [ ] GREEN: change the `min_score` default at the `run()` call site only.
- [ ] Verify nothing legitimately scoring 1–2 now wrongly falls to clarify (check the
      sub-corpus first, then full).
- [ ] Run `evals/runs/arm-min-score`, regression-check vs snapshot at THRESHOLD.

**Acceptance:** outcome_accuracy ≥ control + THRESHOLD on qwen, gemma not regressed; OR
discard and note the result.

### - [x] Step 2.5 — Cat B: chaining output-declaration (prompt/example-selection)

> **Resolved, but not by the prompt.** B1 (pin a chain exemplar) measured +0.000.
> The fix that worked was deterministic — `_link_chain_intermediates` plus `output`
> on the producer tools. B3 was rejected on principle; see the sections below.

**Root cause (2026-06-18):** `select_examples` (`prompt.py`) ranks few-shot examples by
tool-overlap with the retrieved set, so a `resize→strip_audio` chain sees only single-op
examples — no chaining exemplar — and the model improvises a chain without declaring
step-1 `output`, which `_hallucinated_filename` (`agent.py`) then flags as a hallucinated
input. ~10 rows (117/121/122/138/226/244/273/278/279/284) + 228.

**B1 measured (2026-06-18): 0 improvement → discarded/reverted.** The chain example
*was* pinned into the prompt (verified), but qwen still omits step-1 `output` for the
`resize→strip_audio` combo — it does not generalize the output-declaration from a
trim→concat→convert exemplar. No rows changed.

**Key structural finding:** these rows are graded `full` (success_criteria require the
final file to reflect *both* operations). The model's chain is semantically intended but
structurally incomplete — step-1 declares no `output`, so step-2 has nothing to consume.
Therefore **B3 (relax the guard) is rejected**: suppressing the clarify would pass the
*cheap* verifier's routing check but the chain stays unexecutable → fails the *success*
verifier. That's gaming the metric, not fixing it.

Remaining real fixes (neither is a prompt tweak):
- **B-repair — optimizer auto-link.** When a multi-step plan has a later step whose input
  filename is absent from the utterance and not produced by any explicit earlier `output`,
  and an earlier step lacks an `output`, assign that filename as the earlier step's
  `output` — linking the chain so it is correctly routed AND executable. Deterministic;
  touches `planner.py` (core). Fixes ~10 rows on both cheap and success verifiers.
- **B-finetune.** Train the model to emit step-1 `output` on chains. Deferred to the
  fine-tuning track; the Cat-B rows become targeted training examples.

### - [x] Step 3 — Arm B: targeted reject/clarify few-shots (prompt)

> **Ran as the Cat-C arm** (after-C1 / C2 / trim), adopted in PR #18. The sub-corpus
> went 0/21 → 11/21; the full-corpus gain was masked by Cat-B churn until the
> auto-link landed. See *Cat C — measured*.

Driven by Step 1's failure table — `prompt.yaml` has only one reject example and one
out-of-scope→clarify path today. Add few-shots matching the *observed* failure patterns.
Keep the TOOL SCOPE list (it is the out-of-scope guardrail; do not delete it).

- [ ] Iterate on `eval_weak.jsonl` (seconds, not minutes) until the sub-corpus lifts.
- [ ] Confirm on full corpus → `evals/runs/arm-reject-fewshots`, regression-check.

**Acceptance:** reject and/or clarify bucket rises, no other bucket drops > THRESHOLD,
headline outcome_accuracy up beyond noise.

### - [~] Step 4 — Arm C (optional): leaner retrieved prompt (prompt)

> **Partly superseded.** No standalone `arm-lean` run: the *after-trim* variant did the
> concise-wording work and was adopted. The retrieved prompt stayed ~8,055 chars (under
> its 10,000 ceiling); growth was confined to the unfiltered full prompt. A dedicated
> lean arm was never needed.

Only if Arms A/B leave headroom. Trim genuinely redundant synonym lines (those duplicated
by rich tool descriptions) while **keeping** the scope list and all arg-value mappings
(quality/CRF/platform — these can't live in retrieval keywords). Hypothesis: smaller prompt
= less distraction for a 4B model, same accuracy.

- [ ] Measure prompt char count before/after; run `evals/runs/arm-lean`, regression-check.

**Acceptance:** outcome_accuracy holds within noise at materially smaller prompt size
(a size win that doesn't cost accuracy is a keep; an accuracy win is a bonus).

### - [ ] Step 5 — Validity check: fixture-name leakage

> **Genuinely not run** — the one protocol step still outstanding. Corpus rows and
> `prompt.yaml` examples have never been checked for shared filenames, so the
> possibility that the baseline is flattered by leakage is untested.

Confirm the corpus isn't flattering us via shared filenames between few-shots and rows.

- [ ] Compare filenames used in `prompt.yaml` examples vs `eval.jsonl` rows.
- [ ] If overlap is high, run one variant with varied example names
      (`wedding.mov`, `lesson.webm`, …); if outcome drops, the baseline was inflated —
      record it as a measurement caveat, not a prompt regression.

## Decision rule

For each variant, against the locked snapshot at THRESHOLD:

1. **Adopt** if qwen `outcome_accuracy` improves > THRESHOLD AND no category regresses
   > THRESHOLD AND gemma `outcome_accuracy` not regressed > THRESHOLD.
2. On adoption, re-run with `--snapshot` to re-lock `eval_snapshot.json`, and commit the
   prompt/code change + new snapshot together (matches the repo's "re-lock eval snapshot"
   commit convention).
3. **Stack** winners: the next variant's control becomes the newly-locked snapshot.
4. **Discard** otherwise; `git checkout -- prompt.yaml` (or revert the code arm) and
   record the negative result in this file's results log.

## Exit criteria → fine-tuning fallback

- If, after Arms A–C, **no variant clears the bar** (or only within noise): revert to the
  original prompt, lock the snapshot back to 0.862, and conclude prompt-engineering is
  saturated for qwen3-4b at this corpus.
- That negative result is the trigger for fine-tuning (`docs/TRAINING_DATA_GENERATION.md`).
  The diagnosed failure table from Step 1 becomes the training-data targeting spec — we
  already know reject/clarify are the gaps, so the SFT set is weighted there.

## Risks & guardrails

- **Noise > threshold:** if Step 0 shows noise ≥ 0.02, small wins are unmeasurable on 297
  rows; only pursue changes expected to move ≥ several rows, or expand the corpus first.
- **gemma divergence:** a prompt tuned to qwen may regress gemma (json_mode on, different
  failure mode). The guard run is mandatory each arm, not optional.
- **Overfitting to the corpus:** Step 5 guards leakage; resist editing the prompt to win
  specific rows rather than the underlying behavior.
- **Scope creep:** `infer_stream` parity and broader retrieval changes are explicitly
  deferred — they add confounds. One lever per arm.

## Chain-execution depth — diagnosed + drafted (2026-06-18)

The step-2 success-verifier caveat (chains route but grade partial) was diagnosed
end-to-end. **Verdict: grader gap, not a product bug** — same harness-vs-product fork as
Cat A. Running 117's chain for real produced a final file that is *both* 480p *and* silent
(`resized_to_480=True, audio_stripped=True`). The eval under-measured it because the row
lacked `outputs` metadata, so the runner graded a single artifact instead of the chained
result.

Fix is **corpus metadata, not code**: with `outputs` present the runner chains the model's
commands and grades the final file against the human-validated `success_criteria`. Proven
on 117 (0.4 → 1.0). Auto-seeded `outputs` for the chain rows (left **unvalidated** for human
notebook review). Net on the full corpus: knaif **0.966 → 0.974**; 117/130/226 → 1.0, no
regressions, routing/cheap unchanged.

**Subtlety learned:** the success verifier grades `outputs` **per-output** (expects N
produced files), so a seed must match the actual chain structure. 2 over-eager seeds
reverted (139 = batch+chain, 273 = unreliable 2nd output); 9 rows skipped (model does both
ops in one command — no chaining needed). **13 seeds remain as a working-tree draft.**

### Handoff — PR #19 (separate from #18's validated routing fixes)

- [x] Human-validated the chain `outputs` via the notebook baseline reviewer (PR #19).
- [x] Rebuilt the references properly after discovering the per-output grading was hollow.

**Rebuild (2026-06-19).** Multi-output rows are graded by `grade_outputs` per-output against
`outputs[i].criteria` — which the auto-seed left **empty**, so the prior knaif 0.966→0.974
was hollow (it only checked that N files were produced). Rebuilt: dropped 278/279 (no
`success_criteria` to grade), gave the **11** remaining rows meaningful final-deliverable
criteria (file-property subset of each row's human-validated `success_criteria`) and corrected
5 broken model-derived command chains (123/127/130/226/227, verified end-to-end). All 11
references human-validated in the widget (`validated_by: human`).

Honest re-grade (model's *actual* chains vs real criteria): avg_knaif 0.974→0.971, but the
per-row truth now shows up — 117 (final not resized), 130 (mp4 not mkv), 226 (aac not mp3),
127/227 (no usable 2nd deliverable) score <1.0. The corpus now **measures chain correctness
honestly** and exposes real model chaining weakness — concrete fine-tuning signal.

- [ ] (later) Tighten loose `success_criteria` that still can't catch a broken chain
  (e.g. 123 has no speed/duration check, so its broken speed chain still passes).
- [ ] (later) Make `notebooks/baseline_authoring.ipynb` dual-mode. Its Step-3 pending
  filter only surfaces un-validated single-command baselines, so multi-output (chain)
  rows can't be reviewed out of the box — we hit a "0 rows awaiting validation" dead-end
  this session and patched it ad-hoc (reverted). A proper fix: (a) a `baseline` vs
  `outputs` mode toggle, and (b) an `outputs_validated_by` marker so reviewed chains drop
  out of the pending list (today `validated_by` lives on the shared `baseline`, so there's
  no per-`outputs` review state). The reviewer engine (`baseline_reviewer.py` `_run_multi`)
  already supports chains — only the notebook's pending query and the marker convention
  are missing.

## Results log

| Variant | qwen outcome | Δ vs control | gemma outcome | weak-cat notes | Decision |
|---|---|---|---|---|---|
| control-a / -b | 0.862 | — | _tbd_ | noise floor = 0 (identical runs) | baseline |
| **after-A** (harness stem fix) | **0.892** | **+0.030** | _tbd_ | +9 Cat-A rows; avg_knaif 0.946→0.978; no regressions | **adopt** |
| after-B1 (pin chain example) | 0.892 | +0.000 | — | 0 rows changed; qwen won't generalize output-decl to new combos | **discard (reverted)** |
| after-C1 (reject/clarify rules) | 0.9125 | +0.020 | _tbd_ | +12/-6; 086 over-rejects plain upscale | refine |
| **after-C2** (086 narrowed) | 0.9057 | +0.014 | _tbd_ | +11/-7; 10/11 gains are Cat C; 5/7 losses are Cat-B chain churn; 086 fixed | superseded |
| **after-BC2** (C2 + schema-aware auto-link) | 0.9158 | +0.054 vs orig | — | +13/-6 vs committed-A; verbose prompt | superseded by trim |
| **after-trim** (concise wording, +ceiling) | 0.9158 | +0.054 vs orig | +0.020 | committed f7d2813; gemma guard PASSED | adopted |
| **after-step2** (producers accept `output`) | **0.9327** | **+0.071 vs orig** | _tbd_ | +7/-2; recovers 6 resize/reverse/speed/rotate-producer chains (117/122/129/130/136/273) | **adopted (merged, #18)** |
| arm-min-score=3 (post-merge) | 0.9057 | −0.027 vs 0.933 | — | filters desc-only-matched tools off legit rows; Cat C already handles out-of-scope | **discard (reverted)** |
| arm-min-score=1 (post-merge) | 0.9259 | −0.007 / knaif −0.049 | — | no improvement; gentler filter still costs rows | **discard (reverted)** |

### Cat B step 2 — output-capable producers (2026-06-18)

The auto-link only fired for producers whose schema declared an `output` arg. Added
`output` (optional) to resize_video / reverse_video / adjust_speed / adjust_volume /
rotate_video — both the schema (`tools.yaml`) and the `output → options["output_path"]`
threading in each expander (`handlers.py`), mirroring trim/strip. Now the auto-link
links chains those tools produce, and the chains execute to the named file. Also a
genuine user feature (explicit output naming for those ops). Tests:
`skills/ffmpeg/python/tests/test_producer_output_arg.py`. qwen 0.916 → **0.933 (+5 net)**.

**Success-verifier integrity check (honest caveat):** outcome 0.891 → 0.906 (+0.015),
knaif **flat at 0.966** (-0.0035) — no artifact-quality regression. But the cheap +5
overstates real capability for these rows: of the 6 recovered chains, only 122/129
grade knaif=1.0; 117/130/136/273 grade 0.4-0.75 — they **route** correctly but don't
fully **execute** both ops. Root cause is multi-intent chain materialization during
grading (these corpus rows lack `outputs` metadata, so the eval grades a single artifact,
not the chained result). Step 2 is still net-positive (routing genuinely fixed, knaif
flat), but **full chain execution for resize/rotate/convert producers is the next
investigation** — separate from this routing fix.

### Gemma non-regression guard (2026-06-18) — PASSED

Ran gemma3-4b on committed-A (orig prompt) vs the full change (Cat C + auto-link):

| Model | Baseline | New | Δ outcome |
|---|---|---|---|
| qwen3-4b (target) | 0.892 | **0.916** | **+0.024** |
| gemma3-4b (guard) | 0.808 | **0.828** | **+0.020** |

Both models improve. Gemma net +14/-8 (its own chain churn + a few unsupported-op flips:
176 subtitles→plan, 019 watermark→reject — residual, documented). Adoption gate met:
qwen up beyond noise, gemma not regressed.

### Prompt size

Full prompt 13,175 → 13,902 (+~700, reject/clarify rules); audit ceiling raised
13,400 → 14,000 with justification. The **retrieved/operative prompt stays ~8,055**
(under its 10,000 ceiling, ~same as baseline) — growth is confined to the unfiltered
full prompt. Golden snapshots regenerated.

### Cat B — schema-aware auto-link (2026-06-18)

`_link_chain_intermediates` (agent.py) binds an undeclared chain intermediate to the
nearest preceding **output-capable** producer step's `output`, run before
`_hallucinated_filename`. Capability-gated: writing `output` to a tool that doesn't
declare it (resize_video, reverse_video) fails schema validation — those chains stay
unfixed (Cat-B residue for the fine-tuning track). Tests:
`python/core/tests/test_chain_intermediate_linking.py`. Net with Cat-C: 0.892 → 0.916 (+7 rows).
Residual regressions: chain churn on non-output-capable producers + 030 (vague→plan)
and 174 (encode→error) from the Cat-C prompt.

### Cat C — measured (2026-06-18)

Strengthened SAFETY (external I/O, overwrite-original, impossible results → reject) and
sharpened the missing-param / unsupported-op clarify rules in `prompt.yaml`. On the 21-row
Cat-C sub-corpus: **0/21 → 11/21**. On the full corpus the targeted gain is ~+10 Cat-C rows,
but **Cat-B chain fragility churns ±5–7 rows non-deterministically under any prompt change**,
netting +4 (C2) to +6 (C1). The C1↔C2 difference is that churn, not quality — C2 is
semantically correct (086 "upscale to 4K" must plan, not reject).

**Conclusion:** the Cat-C arm works, but its full value is gated by Cat-B noise. Fixing
Cat-B (optimizer auto-link) first would both recover ~10 chain rows AND stop the chain
regressions, letting the full +10 Cat-C gain land cleanly. Recommend: land Cat-B repair,
then re-measure and finalize Cat-C against a stable baseline.

### Fix A — landed (2026-06-18)

`python/core/knaif/evalsuite/cli.py` `cmd_run` + `cmd_compare` now build the eval agent with
`fixture_dir` (where eval inputs live) as its sandbox, so `resolve_stems` finds
extension-less corpus names instead of forcing clarify. Product `resolve_stems`
is unchanged (its own tests place inputs in the sandbox root — correct for prod).
Regression test: `python/core/tests/test_evalsuite_stem_sandbox.py`. Verified +0.030 outcome,
no regressions vs snapshot. Note: row 228 was mis-binned as A — its clarify is
`_hallucinated_filename` at infer-time (model invents `clip_4k.mp4`), so it's Cat B.
