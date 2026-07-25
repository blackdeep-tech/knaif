# Best Skill Model — data v2 + 1.7B-focused tuning + quant/base selection

**Status:** Done · **Created:** 2026-06-30 · **Completed:** 2026-06-30
**Owner:** eval · **Ref:** [2026-06-27-fine-tuning.md](2026-06-27-fine-tuning.md), docs/TRAINING_DATA_GENERATION.md

> **Kept 2026-07-23** (S7 decision — confirms the tier-1 illustration; research findings).
> The "0 inbound refs" basis is stale: **`eval_backends.yaml` cites this plan by task from
> config** (two stanza-group comments, Tasks 2 and 3), plus `docs/MODELS.md`, `evals/INDEX.md`,
> the consolidated audit, and a sibling plan. Its scorecard is the evidence behind MODELS.md
> §4.1–§4.3, and the Appendix is the only consolidated cross-run table of every model
> evaluated in the study — the per-run `score.json` files are tracked, but nothing else joins
> them.
>
> **Repaired:** Task 1 was unticked and marked `← start here` even though data v2 had been
> built, evaluated and **rejected** by Task 2 — an instruction to redo work already proven
> worse. Its contamination guard also shipped (as a test) without the checkbox moving.
>
> **Extracted to the shipping docs:**
> - the **data-v2 dead end** — ~80 bulk single-op anti-contamination rows cut enum errors 3→2
>   but cost the hard slice (0.855 → 0.800), and the contrast with the *contrastive near-pair*
>   rows that later worked → [FINE_TUNING.md §5](../FINE_TUNING.md). The dead-end list had the
>   later success but not this failure, leaving "add rows for the failure you see" unrefuted.
> - the **contamination guard** — `test_train_data_integrity.py` and the three value-level
>   checks it adds over `validate_plan` → [TRAINING_DATA_GENERATION.md](../TRAINING_DATA_GENERATION.md),
>   whose validation step had listed only `just test-skill` plus manual spot-checks.
>
> Paths repointed (`eval_results/` → `evals/`). The recommendation itself **held**: MODELS.md
> ships 4B-Q4 as default with the 1.7B-Q6 quality-per-byte build ready but not deployed,
> realized later on the sft-v3 adapter rather than this plan's v1 one.
>
> **Status note:** Direct follow-on to [fine-tuning](2026-06-27-fine-tuning.md) (Done). That
> study found: (a) `Qwen/Qwen3-4B` is the **instruct** model — we already tune instruct, and
> the production `Qwen3-4B-Q4_K_M` is a same-model different build; (b) fine-tuning pays off
> on the **1.7B** (headroom) but is net-neutral-to-negative on the saturated 4B; (c) the
> union LoRA induced small **cross-skill contamination** on ffmpeg (`quality:"small"` vs
> `small_file`, a hallucinated `convert_audio`, over-clarify/over-reject); and (d) the 1.7B
> **hard-slice gap is mostly quantization, not size** — f16 0.855 vs q4 0.691 on the ffmpeg
> hard slice (4B = 0.909). *(That "Not started" was true when written; all five tasks have
> since completed — see Results.)*

**Goal:** Find the single shared model — chosen base × tuning × quantization — that serves
the current skills (ffmpeg + documents) best, optimizing quality-per-byte. Deployment stays
**one shared model**, so all tuning is on the **union**.

**Incumbent = 4B-Q4 (2.5 GB).** It is the current best and the bar to beat. The catch
(noted 2026-06-30): a small model is only worth shipping if it is **meaningfully smaller**
*and* close on quality. 1.7B-**Q8** is ~1.9 GB — only ~0.6 GB under 4B-Q4 — so if the 1.7B
needs Q8 to be good, the saving doesn't justify the quality loss and **4B-Q4 wins**. The
experiment that matters is therefore narrow: **can 1.7B be good enough at a *small* quant
(Q4–Q6, ~1.1–1.4 GB, i.e. ≥1 GB under 4B-Q4)?** If yes → real win. If "good enough" only
arrives at ~Q8 → default to 4B-Q4.

---

## What we already know (the bar to beat)

ffmpeg/documents `success`, current corpus (from
`evals/runs/2026-06-{29,30}_finetune-{pre,post}_success`):

| model | ffmpeg full | ffmpeg hard | documents full | documents hard | disk (q4) |
|---|---|---|---|---|---|
| **4B instruct (untuned ≈ prod)** | 0.905 | 0.909 | 0.976 | 0.914 | 2.5 GB |
| 1.7B-ft-f16 | 0.878 | 0.855 | 0.982 | 0.914 | (3.4 GB f16) |
| 1.7B-ft-q4 | 0.869 | **0.691** | 0.957 | 0.914 | **1.1 GB** |

The 1.7B-ft already **matches 4B on documents** and is within ~3pt on full ffmpeg; the one
real gap is the **ffmpeg hard slice at q4**, and the f16→q4 collapse (0.855→0.691) says that
gap is **quantization-dominated**. The held-out hard slice (Task 3 of the prior plan) remains
the primary instrument and must stay held out of training.

## Selection scorecard (how "best" is decided)

Score every candidate on one grid; pick the best quality-per-byte operating point:

- **Quality:** ffmpeg + documents `success` outcome/knaif, reported **full** and **hard
  slice separately** (hard is the differentiator).
- **Footprint:** GGUF size on disk (GB) and VRAM at `n_gpu_layers:99`.
- **Latency:** time-to-artifact p50/p95 from the eval harness.
- **Safety/contamination:** the cross-skill contamination check (Task 1.4) and
  unsupported→`clarify` / unsafe→`reject` correctness.
- **Baselines on the grid:** 4B-instruct-untuned (the bar) and the current production
  `qwen3-4b` for reference.

**Decision rule (size-gated):** approximate GGUF sizes — 1.7B Q4 ≈ 1.1 GB · Q5 ≈ 1.2 ·
Q6 ≈ 1.4 · Q8 ≈ 1.9; 4B-Q4 ≈ 2.5. Replace the 4B-Q4 incumbent **only** if a 1.7B build is
**≥1 GB smaller** (so Q4–Q6, ≤~1.4 GB) **and** within a small, agreed quality margin on both
full and hard slices. A 1.7B that only matches at Q8 (~1.9 GB) does **not** justify replacing
4B-Q4 → keep 4B-Q4. If nothing beats it on quality-per-byte, **shipping 4B-Q4 is the correct
outcome**, not a failure.

---

## Tasks

> **Ordering note (2026-06-30):** Data comes first. An earlier draft front-loaded a quant
> pre-check, but that would characterize the **v1 (contaminated)** adapter — the wrong
> artifact, and a misleading basis for a go/no-go. The binding quant comparison is Task 3 —
> which in the event ran on the **v1** adapter, not v2, because Task 2 rejected v2. (Optional, non-binding: the existing `qwen3-1.7b-ft-f16.gguf` can be
> quantized to Q5/Q6/Q8 and hard-sliced any time for a rough orientation on the quant knee —
> but it does not gate anything and the 1.7B path is **not** killed on v1 numbers.)

### - [x] 1. Training data v2 — fix the observed failures, then scale ✅ DONE 2026-06-30 — **result rejected**

> **Do not "start here" (corrected 2026-07-23 — the checkbox read unticked with a
> `← start here` marker, which invited redoing work already proven worse).** Data v2 was
> generated and evaluated; **Task 2 rejected it** — v1 data wins. Sub-item status: (1)
> anti-contamination rows — built, and they are *why* v2 lost (see Task 2); (3) volume growth
> — superseded by the sft-v3 corpus in
> [qwen3-ffmpeg-max-results](2026-07-01-qwen3-ffmpeg-max-results.md); (4) contamination guard
> — **shipped** as `python/core/tests/test_train_data_integrity.py` (cross-skill enum/tool
> leakage, canonical enum values, eval-verbatim holdout), now referenced from
> `docs/TRAINING_DATA_GENERATION.md`; (5) holdout integrity — covered by that same test.
> The transferable lesson is in [FINE_TUNING.md §5](../FINE_TUNING.md) under the dead ends.

Regenerate (not append) via `scripts/gen_train.py`, targeting the specific regressions the
post-run surfaced — quality over raw volume:

1. **Anti-contamination.** Add ffmpeg rows mapping "tiny/small/smallest" → `quality:
   small_file` (reinforce the correct enum) and keep documents `compress_quality: small`
   visibly distinct; reinforce audio→audio conversion routes through `convert_video` /
   `extract_audio` (kill the hallucinated `convert_audio`). No documents enum/tool token
   should ever appear in an ffmpeg target and vice-versa.
2. **Clarify vs reject boundaries.** The LoRA over-rejected/over-clarified borderline rows
   (e.g. "boomerang effect" → it emitted `reject`; correct is `clarify`-unsupported). Add
   contrastive rows that pin unsupported-operation → `clarify` vs unsafe/out-of-scope →
   `reject`.
3. **Volume + phrasing diversity.** Grow toward ~500/skill with more *genuine* phrasing
   variety (not template clones) to reduce small-model overfitting at low epochs.
4. **Contamination guard (new check).** Add a `scripts/`-level lint (and a test) that scans
   each skill's `train.jsonl` for cross-skill enum/tool-name leakage; fail loudly. Keep the
   existing dual validation (`validate_plan` + enum-value check).
5. **Holdout integrity.** Stay distinct (files + ops) from the frozen Task-3 hard slice.
   ZH/CJK still excluded until the CJK retrieval fix lands.

**Gate:** `just test-skill` green; contamination guard passes; `build_dataset.py` rebuilds
the union; coverage check shows every model-visible tool represented.

### - [x] 2. 1.7B-focused re-tune + light hyperparameter check ✅ DONE 2026-06-30

Re-tuned 1.7B on data v2 at two configs (baseline r16/3ep; lighter r8/2ep/lr1e-4). **Result:
data v2 did not help — v1 data wins.** Clean f16 read (q4 was quant-noise-dominated, so the
comparison was re-run at f16): ffmpeg hard **v1 0.855** vs v2 0.800 vs v2-light 0.782;
documents flat. The ~80 single-op anti-contamination rows cut enum errors 3→2 but diluted the
chain/contrastive signal and over-nudged `clarify`, a net loss on the hard slice. **Winning
config = the existing v1 adapter `qwen3-1.7b-ft` (r16/3ep)** — carried into Task 3 (no
retrain). Runs: `2026-06-30_datav2-config{,-f16}_success`.

### - [x] 3. Quant sweep + head-to-head selection ✅ DONE 2026-06-30

Built Q4/Q5/Q6/Q8 of the v1 1.7B adapter; swept both skills. **Q6 is the 1.7B sweet spot**
(curve in the scorecard). Q8 adds nothing over Q6; the hard slice is non-monotonic at n=55
(noise). Scorecard below.

### - [x] 4. Gemma 3 spike — alternative base ✅ DONE 2026-06-30 (stop at untuned)

`gemma-3-4b-it` Q4 untuned: ffmpeg **0.857/0.891**, documents 0.970/0.914, **1515 ms median —
4× slower** than Qwen3-4B-Q4. Worse on quality *and* latency → **not competitive; do not
fine-tune Gemma3.** Qwen3 stays the base. (Step 2 cancelled.)

### - [x] 5. Decision + write-up ✅ DONE 2026-06-30 — see Results

---

> **Consolidated findings + agent handoff:** the full study (this plan + the fine-tuning plan +
> the single-skill ffmpeg-only follow-ups, with retractions and open questions) is written up as
> a standalone, self-contained document: [docs/audits/2026-07-01-finetuning-study-findings.md](../audits/2026-07-01-finetuning-study-findings.md).
> Use that one to brief other agents / compare runs.

## Results (2026-06-30) — model selection scorecard

`success` verifier, current corpus. ffmpeg is the differentiator (documents saturates).

| model | size | ffmpeg full | ffmpeg hard | docs full | docs hard | ff median ms |
|---|---|---|---|---|---|---|
| **4B-Q4 untuned (incumbent)** | 2.33 GB | **0.905** | **0.909** | 0.976 | 0.914 | 368 |
| 4B-Q4 fine-tuned | 2.33 GB | 0.895 | 0.945 | 0.976 | 0.914 | ~370 |
| gemma3-4B-Q4 untuned | 2.32 GB | 0.857 | 0.891 | 0.970 | 0.914 | **1515** |
| 1.7B-ft Q4 | 1.03 GB | 0.869 | 0.691 | 0.957 | 0.914 | ~200 |
| 1.7B-ft Q5 | 1.17 GB | 0.871 | 0.818 | 0.976 | 0.914 | ~200 |
| **1.7B-ft Q6** | **1.32 GB** | 0.881 | 0.873 | 0.976 | 0.914 | **198** |
| 1.7B-ft Q8 | 1.71 GB | 0.881 | 0.800 | 0.988 | 0.943 | ~210 |

### Recommendation

**Two defensible choices; lead recommendation: keep 4B-Q4 as the default, adopt 1.7B-ft-Q6
when footprint/latency become a priority.**

- **Max quality → 4B-Q4 (incumbent).** Best ffmpeg full (0.905) and a solid hard slice
  (0.909). The safe default; nothing dethrones it on raw quality.
- **Quality-per-byte → 1.7B-ft-Q6 (1.32 GB).** The size-gated rule's condition is **met**:
  it's **1.0 GB smaller** (≥1 GB), **1.9× faster** (198 vs 368 ms), **matches 4B on
  documents**, and trails 4B on ffmpeg by only ~2.4pt full / ~3.6pt hard. The first sub-1.5 GB
  build to get within striking distance of 4B on the hard slice. The residual ffmpeg-hard gap
  (~3–9pt, noisy) is the price of the smaller model.
- **Q8 is pointless here** (1.7 GB, no better than Q6 — and at ~1.9 GB it's barely under
  4B-Q4, the trap noted up front). **Gemma3 is out** (worse + 4× slower).

**Resolved findings:** (a) the v1 quant-tax story refines — the 1.7B hard slice recovers from
0.691 (Q4) to ~0.87 (Q6), so q4 was the problem, not 1.7B capacity; (b) data v2 / lighter
tuning did **not** help — the original v1 data is the better training set; (c) contamination
is small and not the deciding factor. Adoption into production is the owner's call.

**Done:** scorecard filled across 4B (±ft), Gemma3, and 1.7B at Q4–Q8; the size↔quality curve
and latency are stated; the recommendation (4B-Q4 default; 1.7B-ft-Q6 the quality-per-byte
pick) is committed with its rationale.

---

## Appendix — all `success` evaluation runs (every model evaluated)

Complete record of every `--verifier success` run across the fine-tuning + model-selection
work, per saved folder under `evals/runs/`. **Columns:** `n` = utterance-evaluations
(ffmpeg ≈ 846, documents ≈ 164); `outcome` / `knaif` / `tool` / `schema` = full corpus; `hard
out` / `hard knaif` = the held-out Task-3 hard slice (ffmpeg 55 utt, documents 35 utt). Model
identity reminder: `*-base-*` = untuned Qwen3-4B/1.7B **instruct** (our build); `*-ft-*` = +
union LoRA (v1 data); `*-ftv2*` = data-v2 (rejected); `qwen3-4b` = prequantized **production**
`Qwen3-4B-Q4_K_M.gguf`; `gemma3-4b` = `gemma-3-4b-it-Q4_K_M` untuned.

### 2026-06-29_finetune-pre_success — untuned bases
| skill | backend | n | outcome | knaif | tool | schema | hard out | hard knaif |
|---|---|---|---|---|---|---|---|---|
| documents | qwen3-1.7b-base-f16 | 164 | 0.951 | 0.971 | 0.921 | 0.994 | 0.914 | 1.000 |
| documents | qwen3-1.7b-base-q4 | 164 | 0.951 | 0.959 | 0.896 | 0.994 | 0.886 | 0.985 |
| documents | qwen3-4b-base-f16 | 164 | 0.982 | 1.000 | 0.970 | 1.000 | 0.914 | 1.000 |
| documents | qwen3-4b-base-q4 | 164 | 0.976 | 1.000 | 0.970 | 0.994 | 0.914 | 1.000 |
| ffmpeg | qwen3-1.7b-base-f16 | 846 | 0.798 | 0.942 | 0.803 | 0.974 | 0.764 | 0.833 |
| ffmpeg | qwen3-1.7b-base-q4 | 846 | 0.734 | 0.950 | 0.768 | 0.970 | 0.636 | 0.821 |
| ffmpeg | qwen3-4b-base-f16 | 846 | 0.897 | 0.963 | 0.840 | 0.980 | 0.891 | 0.874 |
| ffmpeg | qwen3-4b-base-q4 | 846 | 0.905 | 0.965 | 0.837 | 0.982 | 0.909 | 0.944 |

### 2026-06-30_finetune-post_success — fine-tuned (union v1 data)
| skill | backend | n | outcome | knaif | tool | schema | hard out | hard knaif |
|---|---|---|---|---|---|---|---|---|
| documents | qwen3-1.7b-ft-f16 | 164 | 0.982 | 0.998 | 0.982 | 1.000 | 0.914 | 1.000 |
| documents | qwen3-1.7b-ft-q4 | 164 | 0.957 | 0.987 | 0.951 | 0.988 | 0.914 | 1.000 |
| documents | qwen3-4b-ft-f16 | 164 | 0.976 | 0.996 | 0.976 | 0.994 | 0.914 | 1.000 |
| documents | qwen3-4b-ft-q4 | 164 | 0.976 | 0.998 | 0.982 | 0.994 | 0.914 | 1.000 |
| ffmpeg | qwen3-1.7b-ft-f16 | 846 | 0.878 | 0.958 | 0.870 | 0.969 | 0.855 | 0.941 |
| ffmpeg | qwen3-1.7b-ft-q4 | 846 | 0.869 | 0.948 | 0.862 | 0.963 | 0.691 | 0.851 |
| ffmpeg | qwen3-4b-ft-f16 | 846 | 0.902 | 0.973 | 0.898 | 0.974 | 0.927 | 0.970 |
| ffmpeg | qwen3-4b-ft-q4 | 846 | 0.895 | 0.971 | 0.892 | 0.967 | 0.945 | 0.947 |

### 2026-06-30_instruct-vs-ft_success — production instruct (ffmpeg only)
| skill | backend | n | outcome | knaif | tool | schema | hard out | hard knaif |
|---|---|---|---|---|---|---|---|---|
| ffmpeg | qwen3-4b (prod Qwen3-4B-Q4_K_M) | 846 | 0.904 | 0.966 | 0.833 | 0.983 | 0.909 | 0.921 |

### 2026-06-30_datav2-config_success — data-v2 configs, Q4 (rejected)
| skill | backend | n | outcome | knaif | tool | schema | hard out | hard knaif |
|---|---|---|---|---|---|---|---|---|
| documents | qwen3-1.7b-ftv2-q4 | 164 | 0.963 | 0.985 | 0.957 | 0.994 | 0.886 | 1.000 |
| documents | qwen3-1.7b-ftv2l-q4 | 164 | 0.939 | 0.976 | 0.933 | 0.963 | 0.914 | 1.000 |
| ffmpeg | qwen3-1.7b-ftv2-q4 | 846 | 0.846 | 0.955 | 0.865 | 0.949 | 0.564 | 0.854 |
| ffmpeg | qwen3-1.7b-ftv2l-q4 | 846 | 0.839 | 0.944 | 0.832 | 0.941 | 0.727 | 0.779 |

### 2026-06-30_datav2-config-f16_success — data-v2 configs, f16 (clean comparison, rejected)
| skill | backend | n | outcome | knaif | tool | schema | hard out | hard knaif |
|---|---|---|---|---|---|---|---|---|
| documents | qwen3-1.7b-ftv2-f16 | 164 | 0.970 | 0.998 | 0.970 | 1.000 | 0.914 | 1.000 |
| documents | qwen3-1.7b-ftv2l-f16 | 164 | 0.963 | 0.990 | 0.951 | 0.988 | 0.914 | 1.000 |
| ffmpeg | qwen3-1.7b-ftv2-f16 | 846 | 0.885 | 0.957 | 0.885 | 0.969 | 0.800 | 0.859 |
| ffmpeg | qwen3-1.7b-ftv2l-f16 | 846 | 0.846 | 0.949 | 0.843 | 0.959 | 0.782 | 0.762 |

### 2026-06-30_1.7b-quant-sweep_success — 1.7B v1 adapter, quant sweep
| skill | backend | n | outcome | knaif | tool | schema | hard out | hard knaif |
|---|---|---|---|---|---|---|---|---|
| documents | qwen3-1.7b-ft-q5 | 164 | 0.976 | 0.998 | 0.976 | 1.000 | 0.914 | 1.000 |
| documents | qwen3-1.7b-ft-q6 | 164 | 0.976 | 0.998 | 0.976 | 1.000 | 0.914 | 1.000 |
| documents | qwen3-1.7b-ft-q8 | 164 | 0.988 | 0.998 | 0.988 | 1.000 | 0.943 | 1.000 |
| ffmpeg | qwen3-1.7b-ft-q5 | 846 | 0.871 | 0.954 | 0.866 | 0.970 | 0.818 | 0.891 |
| ffmpeg | qwen3-1.7b-ft-q6 | 846 | 0.881 | 0.946 | 0.869 | 0.970 | 0.873 | 0.901 |
| ffmpeg | qwen3-1.7b-ft-q8 | 846 | 0.881 | 0.958 | 0.870 | 0.972 | 0.800 | 0.941 |

### 2026-06-30_gemma3-baseline_success — Gemma 3 4B untuned
| skill | backend | n | outcome | knaif | tool | schema | hard out | hard knaif |
|---|---|---|---|---|---|---|---|---|
| documents | gemma3-4b | 164 | 0.970 | 0.990 | 0.957 | 0.994 | 0.914 | 0.990 |
| ffmpeg | gemma3-4b | 846 | 0.857 | 0.937 | 0.851 | 0.986 | 0.891 | 0.787 |

*(q4 ↔ f16 numbers for the same data-v2 adapters differ because q4 quantization adds ±~15pt
of hard-slice noise — the reason the config comparison was settled at f16.)*
