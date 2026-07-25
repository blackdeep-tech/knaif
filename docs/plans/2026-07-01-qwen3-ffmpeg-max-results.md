# Qwen3 Fine-Tuning Next Pass: Maximize ffmpeg on 1.7B/4B

**Status:** Done (superseded by pass 3) · **Created:** 2026-07-01 · **Completed:** 2026-07-02
**Owner:** eval
**Ref:** [2026-06-27-fine-tuning.md](2026-06-27-fine-tuning.md),
[2026-06-30-best-skill-model.md](2026-06-30-best-skill-model.md),
[2026-07-02-qwen3-finetuning-pass3.md](2026-07-02-qwen3-finetuning-pass3.md),
[../audits/2026-07-01-finetuning-study-findings.md](../audits/2026-07-01-finetuning-study-findings.md)

**Goal:** Push ffmpeg accuracy on the 1.7B/4B fine-tunes as far as targeted data plus SFT/DPO/distill
grids allow — the pass that produced `sft-v3-flat` and ruled out the levers that failed the gate.

> **Kept 2026-07-23** (S7 decision — research findings; the negative-result record for the
> whole fine-tuning arc). Its dead ends are the reason `FINE_TUNING.md` §5 can say "don't
> re-litigate these", and this is where the measurements behind them live: five SFT grid
> cells, a DPO attempt, a distillation attempt, and two clean ffmpeg-only scope tests.
>
> **Three corrections made — all of the "invites redoing failed work" kind:**
> 1. **The Cross-Skill Training Conclusion was refuted.** Its closing paragraph proposes
>    adding a structurally different skill to broaden planner diversity. Pass 3 ran exactly
>    that (added `io`, zero enum overlap) and it transferred **nothing**. The section now
>    carries a REFUTED box; `FINE_TUNING.md` §5 gained the *shape* of the failed theory, since
>    a persuasive refuted hypothesis re-suggests itself unless it is written down as tried.
> 2. **Tasks 8 and 9 contradicted their own checklist** — both marked `[x] completed in pass 3`
>    at the top, both still saying **"Status: Not started"** in the body.
> 3. **`docs/TODO.md` carried this as an open `- [ ]` item** for a plan that is Done, with a
>    stale `S6-delete` marker.
>
> Paths repointed: `src/skills/` → `skills/`, `training/` → `python/training/`,
> `tests/test_train_data_integrity.py` → `python/core/tests/`.
>
> **Status note:** Tasks 0–7 done here; the open items (quant pass, production-lane decision)
> were carried into and **completed by pass 3** ([2026-07-02-qwen3-finetuning-pass3.md](2026-07-02-qwen3-finetuning-pass3.md)),
> which also corrected this plan's Q6-inflated headline. The canonical how-to now lives in
> [../FINE_TUNING.md](../FINE_TUNING.md). Only `sft-v1-repeat` and the `r32` grid cells were
> never run (low priority). No further work planned on this file.

## Goal

Push Qwen3 model quality as high as practical for the current `knaif` JSON-plan task, with
special focus on ffmpeg hard cases. Keep the two deployment lanes separate:

- **Best general/shared model:** one model for `ffmpeg` + `documents`.
- **Best ffmpeg-only model:** allowed to optimize for a standalone ffmpeg app, but still
  compared against the shared model.

The current baselines remain:

| model | ffmpeg full | ffmpeg hard | documents full | documents hard | size |
|---|---:|---:|---:|---:|---:|
| 4B-Q4 untuned | 0.905 | 0.909 | 0.976 | 0.914 | 2.33 GB |
| 4B-Q4 tuned | 0.895 | 0.945 | 0.976 | 0.914 | 2.33 GB |
| 1.7B-ft-Q6 union | 0.881 | 0.873 | 0.976 | 0.914 | 1.32 GB |
| 1.7B-ffmpegv1-Q6 | 0.868 | 0.873 | n/a | n/a | 1.32 GB |

## Report

### Direct Answers

1. **4B:** no new 4B model was trained in this pass. The earlier 4B fine-tune is "improved"
   only if the objective is ffmpeg hard slice: it raises ffmpeg hard from `0.909` to `0.945`,
   but lowers full ffmpeg from `0.905` to `0.895`. Therefore it is not the default
   production candidate.
2. **1.7B ffmpeg-only:** yes, 1.7B ffmpeg-only has been tried:
   `qwen3-1.7b-ffmpeg-q6` on v2 data, then the clean `qwen3-1.7b-ffmpegv1-q6` on the same
   v1 ffmpeg data used by the union model. The clean ffmpeg-only v1 model did not beat the
   union model: hard was identical (`0.873`) and full ffmpeg was lower (`0.868` vs `0.881`).
   The v3 ffmpeg-only flat test also did not beat the shared v3-flat model: hard tied
   (`0.927`), full ffmpeg was lower (`0.871` vs `0.878`), and chain3 was lower
   (`0.875` vs `0.938`).
3. **Current best result:** `qwen3-1.7b-sft-v3-flat-q6` is the best balanced 1.7B candidate
   so far. It improves ffmpeg hard materially (`0.873 -> 0.927`) while keeping full ffmpeg
   nearly flat (`0.881 -> 0.878`) and documents close (`0.976 -> 0.970`).
   The follow-up `sft-v3-gentle2-q6` run pushed ffmpeg hard even higher (`0.964`) but
   regressed full ffmpeg (`0.866`) and documents (`0.957`), so it is a hard-slice
   specialist, not the default model.
   The first DPO attempt over `sft-v3-flat-q6` also failed: documents recovered to
   `0.976`, but ffmpeg regressed to `0.870` full and `0.909` hard.
   The verifier-filtered distill-v1 run also failed the ffmpeg gate: documents stayed strong
   (`0.976` full), but ffmpeg fell to `0.844` full and `0.891` hard.

### 4B Comparison

| model | training | ffmpeg full | ffmpeg hard | documents full | documents hard | conclusion |
|---|---|---:|---:|---:|---:|---|
| 4B-Q4 untuned | none | 0.905 | 0.909 | 0.976 | 0.914 | best current default if full ffmpeg is the primary metric |
| 4B-Q4 tuned | union v1 LoRA | 0.895 | 0.945 | 0.976 | 0.914 | hard-slice specialist; not a default because full ffmpeg regresses |

The 4B result shows that hard cases are trainable, but the previous 4B LoRA traded too much
general ffmpeg accuracy for the hard-slice gain. No 4B v3 retrain has been launched yet.

### 1.7B Scope Comparison

| model | training scope | data version | ffmpeg full | ffmpeg hard | documents full | conclusion |
|---|---|---|---:|---:|---:|---|
| 1.7B-ft-Q6 union | ffmpeg + documents | v1 | 0.881 | 0.873 | 0.976 | best pre-v3 1.7B shared model |
| 1.7B-ffmpeg-q6 | ffmpeg only | v2 | 0.884 | 0.836 | n/a | confounded by v2 data; not comparable as scope evidence |
| 1.7B-ffmpegv1-Q6 | ffmpeg only | v1 | 0.868 | 0.873 | n/a | clean scope test; no ffmpeg-only advantage |
| sft-v3-flat-q6 | ffmpeg-v3 + documents | v3 | 0.878 | 0.927 | 0.970 | best balanced v3 result |
| dpo-v1-q6 | ffmpeg-v3 + documents | v3 + 40 eval-failure preference pairs | 0.870 | 0.909 | 0.976 | preference attempt failed ffmpeg gate despite preserving documents |
| sft-v3-distill-v1-q6 | ffmpeg-v3 + documents | v3 + 45 verifier-filtered synthetic rows | 0.844 | 0.891 | 0.976 | distill attempt failed ffmpeg gate; documents preserved but ffmpeg regressed sharply |
| sft-v3-gentle2-q6 | ffmpeg-v3 + documents | v3, gentle weighted | 0.866 | 0.964 | 0.957 | best hard-slice result, but full/doc regressions are too large |
| sft-v3-ffmpeg-flat-q6 | ffmpeg only | v3 | 0.871 | 0.927 | n/a | clean v3 scope test; no ffmpeg-only advantage |

The clean v1 and v3 comparisons say skill scope is not the main lever. The union model does
not suffer measurable ffmpeg contamination from documents, and ffmpeg-only does not improve
the hard slice. Data quality and weighting matter more than skill isolation.

### 1.7B v3 Candidate Comparison

| model | training scope | weighting | ffmpeg full | ffmpeg hard | ffmpeg chain3 | documents full | documents hard | conclusion |
|---|---|---|---:|---:|---:|---:|---:|---|
| 1.7B-ft-Q6 union | ffmpeg + documents | none | 0.881 | 0.873 | n/a | 0.976 | 0.914 | baseline to beat |
| sft-v3-flat-q6 | ffmpeg-v3 + documents | none | 0.878 | 0.927 | 0.938 | 0.970 | 0.914 | best balanced 1.7B candidate |
| dpo-v1-q6 | ffmpeg-v3 + documents | 40 real failure preference pairs | 0.870 | 0.909 | 0.938 | 0.976 | 0.914 | failed gate; documents recover, but ffmpeg full and hard both regress vs flat |
| sft-v3-distill-v1-q6 | ffmpeg-v3 + documents | 45 verifier-filtered synthetic rows | 0.844 | 0.891 | 0.875 | 0.976 | 0.914 | failed gate; synthetic additions diluted ffmpeg behavior despite preserved documents |
| sft-v3-ffmpeg-flat-q6 | ffmpeg-v3 only | none | 0.871 | 0.927 | 0.875 | n/a | n/a | standalone ffmpeg test; loses to shared flat on full and chain3 |
| sft-v3-gentle2-q6 | ffmpeg-v3 + documents | gentle hard/chain/contrastive | 0.866 | 0.964 | 0.938 | 0.957 | 0.914 | best hard result, but full ffmpeg and documents losses fail default gate |
| sft-v3-hard3-q6 | ffmpeg-v3 + documents | hard/chain/contrastive | 0.861 | 0.945 | 0.906 | 0.988 | 0.971 | hard-specialist, but full ffmpeg loss is too large |
| sft-v3-low-lr-q6 | ffmpeg-v3 + documents | hard/chain/contrastive, lr `1e-4` | 0.843 | 0.927 | 0.875 | 0.957 | 0.914 | failed gate; lower LR did not fix weighted-data regression |

The v3 flat run is the first 1.7B model that cleanly closes the hard-slice gap while keeping
the full metric within the promotion tolerance. Gentle2 and hard3 prove extra hard-row
signal can move the hard slice even further, but weighted SFT damages routine ffmpeg
behavior before it becomes a better default. DPO-v1 shows that a tiny eval-failure
preference set is also not enough; it preserves documents but moves ffmpeg in the wrong
direction. Distill-v1 shows the same risk for small synthetic SFT additions: verifier
filtering catches invalid plans, but it does not guarantee that the extra rows improve the
model distribution.

### Promotion Conclusion

| lane | candidate | decision |
|---|---|---|
| best full-quality default | 4B-Q4 untuned | still strongest full ffmpeg score (`0.905`) |
| best 1.7B quality/byte | sft-v3-flat-q6 | new best balanced 1.7B candidate; hard improves by +5.4pt vs prior 1.7B-Q6 |
| hard-only specialist | sft-v3-gentle2-q6 | best hard-slice score (`0.964`), useful for analysis, not default promotion |
| ffmpeg-only app | no separate model | ffmpeg-only v1 and v3 both fail to beat their matched shared models |

Recommended next step: keep the shared `sft-v3-flat-q6` model as the 1.7B lane. A separate
ffmpeg-only lane is not justified by the current evidence. Further improvement likely needs
row-level correction of the remaining flat-model failures and a larger, held-out
bucket-eval loop; the first small DPO and synthetic distillation passes both failed.

### Cross-Skill Training Conclusion

The current evidence supports a working hypothesis: the shared model wins because it gets
more varied, well-formed instruction-to-plan examples, not because documents contains
ffmpeg knowledge. Documents appears to regularize the model toward the shared planner
contract: choose a tool, produce strict JSON, fill schema-valid args, clarify when needed,
reject out-of-scope work, and compose multi-step workflows.

This explains why ffmpeg-only fine-tuning has not improved the single skill. The ffmpeg
corpus is smaller and more locally correlated, so extra ffmpeg-only SFT can overfit surface
phrasing or over-bias certain tools/enums. The limiting errors are often planner errors
rather than media-knowledge errors: wrong JSON shape, enum drift, chain composition,
clarify/reject boundaries, and hallucinated tools. Those behaviors are cross-skill.

The result does **not** mean arbitrary skills should be added blindly. Extra skills are most
likely to help when they exercise the same JSON-plan muscles with different wording and
workflow shapes, while avoiding enum/tool-name contamination. The next useful experiment is
a broader shared corpus with one or two structurally different skills, many clarify/reject
and multi-step examples, and a held-out ffmpeg bucket probe to verify that planner diversity
improves ffmpeg rather than merely preserving documents.

> **⚠️ REFUTED — that experiment was run, and it failed (annotated 2026-07-23).** Pass 3 did
> exactly what the paragraph above proposes: it added `io` — a structurally different skill
> with **zero** enum overlap — to the union and probed ffmpeg. It transferred **nothing**.
> The planner-diversity hypothesis is therefore **not** the mechanism, and the recommendation
> in this section must not be re-run as written. See the dead-end list in
> [FINE_TUNING.md §5](../FINE_TUNING.md).
>
> What survives is only the *empirical* half, which is still solid and still in §5: the union
> beats ffmpeg-only at matched data (proven twice here, at v1 and v3). **Why** it does remains
> unexplained — the appealing explanation above was tested and lost. Do not design the next
> experiment on it.

## Starting Point

The winning training set is **v1**, not data v2. Data v2 added about 80 easy single-op
anti-contamination rows; it reduced a few enum mistakes but diluted the chain and
contrastive signal, dropping 1.7B f16 hard ffmpeg from 0.855 to 0.800. Therefore this branch
restores the v1 generator/data from commit `005c0d1`:

- `scripts/gen_train.py`
- `skills/ffmpeg/data/train.jsonl`
- `skills/documents/data/train.jsonl`

Keep the later `python/core/tests/test_train_data_integrity.py` guard. It catches stale or invalid train
rows without forcing data v2 back in.

## Experiment Rules

1. **Compare data and hyperparameters at f16 or Q6, not Q4.** Q4 hard-slice movement is too
   noisy and hides real improvements.
2. **Use `success` as the promotion metric.** Cheap routing is useful for smoke tests, but
   the final score must execute the plan.
3. **Report full and hard slices separately.** Full ffmpeg is near a corpus ceiling; hard
   ffmpeg is the differentiator.
4. **Inspect row-level flips for every apparent win.** A +2 row hard-slice move is not enough
   unless the failure modes make sense.
5. **Do not train on held-out hard rows verbatim.** Use neighboring contrastive examples,
   new files/parameters, and paraphrases.
6. **Keep documents as an anchor in shared-model runs.** Documents is saturated, so it should
   mostly prevent forgetting, not consume most of the gradient budget.

## Tasks

### Task Checklist

- [x] 0. Restore v1 training baseline
- [x] 1. Failure audit from current best models
- [x] 2. Author ffmpeg-v3 hard-focused training data
- [x] 3. Add weighted/curriculum dataset builder
- [~] 4. Run small SFT grid on 1.7B (flat/gentle2/hard3/low-lr done; `sft-v1-repeat` and `r32` never run)
- [x] 5. Run ffmpeg-only v3-flat standalone test
- [x] 6. Try preference tuning on real bad outputs
- [x] 7. Try verifier-filtered distillation
- [x] 8. Quantization pass — **completed in pass 3** (f16/Q5/Q6/Q8 curve; Q6 = 1.7B pick). See [2026-07-02-qwen3-finetuning-pass3.md](2026-07-02-qwen3-finetuning-pass3.md) Task 2.
- [x] 9. Decide production lane — **completed in pass 3**: 4B-v3 promoted for ffmpeg+documents. See pass-3 plan Task 7 + [../FINE_TUNING.md](../FINE_TUNING.md) §1.

### 0. Restore v1 training baseline

**Status:** Done

Restore the v1 generator and train data from `005c0d1` onto the current branch.

**Gate:** row counts match the winning union: ffmpeg 364, documents 334.

### 1. Failure audit from current best models

**Status:** Done

Build a row-level failure table for:

- `qwen3-1.7b-ft-q6` union
- `qwen3-1.7b-ffmpegv1-q6`
- `qwen3-4b-base-q4`
- `qwen3-4b-ft-q4`

Bucket each failure:

- wrong tool
- wrong enum/arg
- bad chain ordering or missing intermediate
- over-clarify
- over-reject
- unsafe request not rejected
- verifier/corpus ambiguity
- retrieval miss

**Result (2026-07-01):** audit written at
[../audits/2026-07-01-qwen3-ffmpeg-failure-audit.md](../audits/2026-07-01-qwen3-ffmpeg-failure-audit.md).
Hard failures are mostly invalid chain composition (tools collapsed into unsupported args)
plus clarify-boundary misses; cross-skill contamination is not the main residual issue.
`ffmpeg_252` is flagged as corpus/clarify-policy noise, and retrieval-miss rows are separated
from model-capacity failures.

**Gate:** done.

### 2. Author ffmpeg-v3 hard-focused training data

**Status:** Done

Create a new ffmpeg-focused training set branch from v1, not v2. Add rows only when they map
to real observed failures or deliberately targeted hard axes:

- 3-step chains and difficult 2-step chains
- contrastive near-pairs with minimal wording changes
- clarify vs reject boundary pairs
- file/parameter variations adjacent to the held-out hard slice
- multilingual rows only for languages whose tools are retrieved
- limited anti-contamination rows only where row-level failures still show it

Avoid large easy blocks. If an issue needs reinforcement, prefer 5-10 high-information
contrastive examples over 50 template clones.

**Result (2026-07-01):** added 32 targeted ffmpeg v3 rows to `scripts/gen_train.py` and
regenerated `skills/ffmpeg/data/train.jsonl` (396 rows). Documents remains a 334-row
anchor set. New rows target chain composition, clarify boundaries, audio conversion through
`extract_audio`, and impossible media-type rejects. Added a train/eval utterance duplication
guard for ffmpeg.

**Gate:** `python/core/tests/test_train_data_integrity.py` passes; train rows validate against live
registries; no held-out ffmpeg eval row is copied verbatim.

### 3. Add weighted/curriculum dataset builder

**Status:** Done

Extend the training data pipeline so experiments can oversample hard rows without physically
duplicating them in `train.jsonl`.

Implemented deterministic row replication controls:

- `--weight-tags hard=3,chain=3,contrastive=2`
- `--skill-weights ffmpeg=2,documents=1`
- skill-scoped tag weights such as `ffmpeg:contrastive=2`

Default behavior is unchanged. Tag weights use the maximum matching tag weight for a row;
skill weights multiply that result. Skill-scoped tag keys prevent accidentally expanding
documents when only ffmpeg is intended.

Example checked command:

```bash
uv run python python/training/build_dataset.py \
  --skills ffmpeg,documents \
  --out /tmp/knaif_union_v3_hard3_chat.jsonl \
  --weight-tags hard_target=3,ffmpeg:chain3=3,ffmpeg:contrastive=2
```

Result: ffmpeg `396 -> 462`, documents `334 -> 334`, total `796`.

**Gate:** emitted dataset includes a summary of source rows vs expanded rows by skill/tag.
Done.

### 4. Run small SFT grid on 1.7B

**Status:** In progress

Use Qwen3-1.7B first because it has headroom and trains quickly.

Run:

| label | data | weighting | rank | epochs | lr |
|---|---|---|---:|---:|---:|
| sft-v1-repeat | v1 | none | 16 | 3 | 2e-4 |
| sft-v3-flat | ffmpeg-v3 union | none | 16 | 3 | 2e-4 |
| sft-v3-gentle2 | ffmpeg-v3 union | gentle hard/chain weighted | 16 | 3 | 2e-4 |
| sft-v3-hard3 | ffmpeg-v3 union | hard/chain weighted | 16 | 3 | 2e-4 |
| sft-v3-low-lr | ffmpeg-v3 union | hard/chain weighted | 16 | 3 | 1e-4 |
| sft-v3-r32 | ffmpeg-v3 union | hard/chain weighted | 32 | 2-3 | 1e-4 |

Run checklist:

- [ ] `sft-v1-repeat`: not run in this pass; lower priority because current v1 Q6 baseline already exists
- [x] `sft-v3-flat`: trained, merged, Q6 quantized, ffmpeg/documents evaluated
- [x] `sft-v3-gentle2`: trained, merged, Q6 quantized, ffmpeg/documents evaluated
- [x] `sft-v3-hard3`: trained, merged, Q6 quantized, ffmpeg/documents evaluated
- [x] `sft-v3-low-lr`: trained, merged, Q6 quantized, ffmpeg/documents evaluated
- [ ] `sft-v3-r32`: not run yet

Evaluate f16 or Q6 first. Quantize only candidates that beat the current `1.7B-ft-Q6` in a
clean comparison.

**Interim result (2026-07-01):** completed four shared v3 Q6 candidates. All use
`max_tokens: 512` in `eval_backends.yaml`; the previous `2048` cap is unnecessary for
plan-only JSON and allowed long bad completions.

| label | ffmpeg full | ffmpeg hard | ffmpeg chain3 | documents full | documents hard | note |
|---|---:|---:|---:|---:|---:|---|
| current 1.7B-ft-Q6 union | 0.881 | 0.873 | n/a | 0.976 | 0.914 | v1 baseline |
| sft-v3-flat-q6 | 0.878 | 0.927 | 0.938 | 0.970 | 0.914 | best balance so far; hard +5.4pt, full −0.3pt vs 1.7B-ft-Q6 |
| sft-v3-gentle2-q6 | 0.866 | 0.964 | 0.938 | 0.957 | 0.914 | hard-specialist; best hard score, but full ffmpeg and documents regress too much |
| sft-v3-hard3-q6 | 0.861 | 0.945 | 0.906 | 0.988 | 0.971 | hard-specialist; full ffmpeg loss is too large for default promotion |
| sft-v3-low-lr-q6 | 0.843 | 0.927 | 0.875 | 0.957 | 0.914 | failed gate; lower LR on hard3 data worsened full ffmpeg and documents |

Flat v3 remains the current shared-model candidate because it materially improves the
ffmpeg hard slice while staying within the allowed full-ffmpeg loss. Gentle2 produced the
highest hard score (`0.964`), but it lost `1.2pt` full ffmpeg and `1.3pt` documents full
relative to flat. Hard3 and low-lr confirm the same pattern: SFT reweighting is useful as a
diagnostic, but the next improvement attempt should be preference tuning or targeted data
correction from actual bad plans, not more oversampling.

**Gate:** keep only candidates that improve ffmpeg hard without losing more than about 1
point on ffmpeg full or documents.

### 5. Run ffmpeg-only v3-flat standalone test

**Status:** Done

Train Qwen3-1.7B on only `skills/ffmpeg/data/train.jsonl` after the v3 additions, using
the same flat hyperparameters as the best shared candidate:

| label | data | weighting | rank | epochs | lr |
|---|---|---|---:|---:|---:|
| sft-v3-ffmpeg-flat | ffmpeg-v3 only | none | 16 | 3 | 2e-4 |

This is the clean standalone-app test. It answers whether removing documents helps once the
training data is purpose-built for ffmpeg hard cases.

**Gate:** promote a separate ffmpeg-only lane only if it beats `sft-v3-flat-q6` on ffmpeg
full or materially beats it on hard without a full-score regression.

**Result (2026-07-01):** trained, merged, Q6 quantized, and evaluated
`qwen3-1.7b-sft-v3-ffmpeg-flat-q6`. It scored ffmpeg full `0.871`, hard `0.927`, and chain3
`0.875`. This ties shared `sft-v3-flat-q6` on hard but loses full ffmpeg (`0.871` vs
`0.878`) and chain3 (`0.875` vs `0.938`), so the gate fails. Do not promote a separate
ffmpeg-only lane from this run.

### 6. Try preference tuning on real bad outputs

**Status:** Done; first attempt failed

Build a preference dataset from actual model failures:

- prompt = exact inference prompt
- chosen = validated expected plan
- rejected = model's bad plan

Start with DPO or ORPO after the best SFT checkpoint. This task is a better match for
over-clarify, over-reject, wrong enum, and hallucinated-tool errors than more SFT rows.

**Gate:** preference-tuned model beats its SFT parent on row-level failure buckets, not just
aggregate hard-slice noise.

**Result (2026-07-01):** built `python/training/ffmpeg_pref_v1.jsonl` with 40 real failure pairs.
The rejected answer is a failed `sft-v3-flat-q6` plan; the chosen answer is the best
passing plan for the same row from `gentle2`, `hard3`, `low-lr`, or `ffmpeg-flat`. Trained
a small custom DPO LoRA (`rank=8`, `epochs=2`, `lr=5e-5`, `beta=0.1`) on top of
`python/training/merged/qwen3-1.7b-sft-v3-flat`, then merged and quantized it as
`qwen3-1.7b-dpo-v1-q6`.

| model | ffmpeg full | ffmpeg hard | ffmpeg chain3 | documents full | documents hard | decision |
|---|---:|---:|---:|---:|---:|---|
| sft-v3-flat-q6 parent | 0.878 | 0.927 | 0.938 | 0.970 | 0.914 | parent |
| dpo-v1-q6 | 0.870 | 0.909 | 0.938 | 0.976 | 0.914 | failed gate |

DPO-v1 improved documents back to the original 1.7B-Q6 level, but the objective was ffmpeg
and both full and hard ffmpeg regressed. This attempt says the next preference run should
not reuse a tiny 40-row eval-failure set directly. If preference tuning is retried, first
build a larger non-eval training set with bucket labels and keep held-out bucket probes for
selection.

### 7. Try verifier-filtered distillation

**Status:** Done; first synthetic attempt failed

Generate paraphrases and candidate plans with a stronger teacher, then keep only rows that:

- pass `validate_plan`
- execute under the `success` verifier when execution is applicable
- are not duplicates of held-out eval rows
- improve coverage of audited failure buckets

Use this as data augmentation for 1.7B SFT, not as untrusted bulk data.

**Result (2026-07-01):** implemented a small verifier-filtered synthetic distillation
pipeline instead of bulk untrusted teacher data:

- `python/training/build_ffmpeg_distill.py` emits candidate ffmpeg rows for quality enums,
  codec/container boundaries, scale/thumbnail handling, clarify/reject edges, and 2-3 step
  chains.
- The builder rejects verbatim train/eval utterance duplicates, validates every plan through
  the live ffmpeg registry, and dry-runs executable plans through the ffmpeg skill.
- Two duplicate candidates were filtered; 45 accepted rows were written to
  `python/training/ffmpeg_distill_v1.jsonl`.
- `python/training/build_dataset.py --extra-jsonl ffmpeg=python/training/ffmpeg_distill_v1.jsonl`
  produced `python/training/union_v3_distill_v1_chat.jsonl` with 775 chat rows.
- Trained, merged, converted, Q6-quantized, and evaluated
  `qwen3-1.7b-sft-v3-distill-v1-q6`.

| model | ffmpeg full | ffmpeg hard | ffmpeg chain3 | documents full | documents hard | decision |
|---|---:|---:|---:|---:|---:|---|
| sft-v3-flat-q6 parent | 0.878 | 0.927 | 0.938 | 0.970 | 0.914 | parent |
| sft-v3-distill-v1-q6 | 0.844 | 0.891 | 0.875 | 0.976 | 0.914 | failed gate |

Documents stayed healthy, but ffmpeg regressed sharply. The failure is useful: schema and
dry-run filtering are necessary but not sufficient. The synthetic rows were valid, yet still
diluted the model's ffmpeg routing distribution. Do not promote this model. A future
distillation attempt should use row-level failed buckets from the parent model, include
negative or preference supervision, and keep a separate held-out bucket probe rather than
only appending valid synthetic SFT rows.

**Gate:** failed. Accepted rows have provenance and validation, but the resulting model did
not beat its SFT parent.

### 8. Quantization pass

**Status:** DONE — completed in [pass 3](2026-07-02-qwen3-finetuning-pass3.md) Task 2
(f16/Q5/Q6/Q8 curve; **Q6** is the 1.7B pick). *(Corrected 2026-07-23: the checklist said done
and this body still said "Not started".)*

For the best 1.7B candidate:

- Q5_K_M
- Q6_K
- Q8_0 only as a diagnostic
- optional imatrix/calibrated quantization using real `knaif` prompts

Do not promote Q8 unless it is a diagnostic winner by a wide quality margin; it is too close
to the 4B-Q4 footprint.

**Gate:** final comparison uses the same eval corpus and reports size, latency, ffmpeg full,
ffmpeg hard, documents full, documents hard.

### 9. Decide production lane

**Status:** DONE — completed in [pass 3](2026-07-02-qwen3-finetuning-pass3.md) Task 7:
**4B-v3 promoted** for ffmpeg + documents. Current lanes are in
[FINE_TUNING.md §1](../FINE_TUNING.md). *(Corrected 2026-07-23: body said "Not started" while
the checklist said done.)*

Decision rules:

- Keep **4B-Q4 untuned** as default unless a tuned model beats it on full ffmpeg without
  hidden regressions.
- Promote **1.7B-Q6** for quality-per-byte if it remains at least 1 GB smaller and closes
  the hard-slice gap materially.
- Keep a separate **ffmpeg-only** model only if it beats the shared model at matched data and
  quantization.

**Gate:** update this plan, `docs/audits/2026-07-01-finetuning-study-findings.md`, and `docs/TODO.md` with the
final decision and run folders.

## Initial Recommendation

The highest-value path is **failure-audited ffmpeg-v3 data + hard-row weighting + Q6**, then
preference tuning if the SFT grid plateaus. Do not spend more cycles on broad balanced data
or Q4-first comparisons.
