# Qwen3 Fine-Tuning Pass 3: Confirm, De-noise, Test the Real Lever

**Status:** Done · **Created:** 2026-07-02 · **Completed:** 2026-07-02
**Owner:** eval
**Ref:** [2026-07-01-qwen3-ffmpeg-max-results.md](2026-07-01-qwen3-ffmpeg-max-results.md),
[../FINE_TUNING.md](../FINE_TUNING.md), [../audits/2026-07-01-finetuning-study-findings.md](../audits/2026-07-01-finetuning-study-findings.md),
[../audits/2026-07-01-qwen3-ffmpeg-failure-audit.md](../audits/2026-07-01-qwen3-ffmpeg-failure-audit.md)

**Goal:** Confirm and de-noise the pass-2 fine-tuning headline, run the 1.7B quant sweep, and
retrain plus promote 4B-v3 for ffmpeg+documents.

> **Kept 2026-07-23** (S7 decision — research findings; the pass that *corrected* pass 2 and
> produced the promoted model). Cited from `docs/FINE_TUNING.md`'s header as the latest-pass
> write-up, plus `evals/INDEX.md` and three sibling plans.
>
> **Repaired the same defect as pass 2, at five times the scale.** The checklist marked Tasks
> 2, 3, 4, 5 and 7 done *with results*, while **every one of those task bodies still read
> "Status: Not started"** — including Task 4, whose body invites retraining a 4B model that was
> trained, gated, and **promoted to production**. Only Task 6 (genuinely open) was accurate.
> All five bodies now carry their outcome.
>
> **Still open, and worth stating plainly:** Task 1's independent confirmation probe was never
> built. The promoted model's hard-slice margin has therefore never been confirmed on data
> independent of the slice it was selected on — the matched f16/Q6 re-read and chain3
> consistency are partial substitutes, not the probe. Now a methodology rule in
> [FINE_TUNING.md §4](../FINE_TUNING.md) rule 4, which had rule 3's *noise* point but not the
> distinct *selection-bias* one (best-of-8 on n=55 inflates the winner, and re-running does not
> fix it).
>
> Stale key names repointed: the promoted model was `qwen3-4b-v3` when this was written and is
> **`knaif-qwen3-4b-v1`** since 2026-07-20 — which is what `models.yaml` and both `skill.yaml`
> files carry today. The `eval_results/INDEX.md` link text was fixed. **The snapshot re-lock
> follow-up below is still open as of 2026-07-23** (ffmpeg cheap/297, documents success/129,
> verified) — three weeks on, and ffmpeg's corpus has since grown to 314, so the gate now also
> spans a changed row set; tracked in [../TODO.md](../TODO.md).
>
> **Status note:** Tasks 2, 3, 4, 5, 7 done; **4B-v3 promoted** (ffmpeg+documents). Two items
> left open by design: Task 1 (dedicated held-out probe — substantively covered by the matched
> baseline + chain3-consistency) and Task 6 (preference tuning — only if data levers plateau).
> Reproducible procedure + outcomes are now canonicalized in [../FINE_TUNING.md](../FINE_TUNING.md).
> Remaining follow-ups tracked in [../TODO.md](../TODO.md): re-lock regression snapshots vs
> `qwen3-4b-v3`; CJK retrieval segmentation.

## Why this pass exists

Pass 2 produced one promising 1.7B candidate (`sft-v3-flat-q6`: ffmpeg hard `0.873 -> 0.927`
at `-0.3pt` full) and four documented dead ends (weighted SFT, tiny-set DPO, bulk synthetic
distill, ffmpeg-only scope). Pass 3 does **not** chase more of those. It fixes three
weaknesses in the pass-2 evidence and then tests the single lever pass 2 identified as
genuinely promising.

The three weaknesses:

1. **Selection bias.** `sft-v3-flat` was best-of-~8 candidates chosen on a **55-row** hard
   slice whose own stated noise floor is ~2 rows (3–4pt). Best-of-8 on n=55 inflates the
   apparent winner. The +5.4pt is *plausible but not confirmation-grade.*
2. **Single-point, Q6-only.** `sft-v3-flat` exists only at Q6, a single run. The quant pass
   (pass-2 Task 8) was never done and Q6 itself carries quant noise.
3. **Retrieval floor.** The audit found **129 utterances where the correct tool is never
   retrieved** (CJK tokenization, bitrate/volume/compress keywords). These are charged to the
   model in the metric but are not model-capacity failures.

## Baselines carried forward

| model | ffmpeg full | ffmpeg hard | ff chain3 | doc full | doc hard | size |
|---|---:|---:|---:|---:|---:|---:|
| 4B-Q4 untuned (production default) | 0.905 | 0.909 | — | 0.976 | 0.914 | 2.33 GB |
| 4B-Q4 union-v1 LoRA | 0.895 | 0.945 | — | 0.976 | 0.914 | 2.33 GB |
| 1.7B-ft-Q6 union-v1 (prior 1.7B) | 0.881 | 0.873 | — | 0.976 | 0.914 | 1.32 GB |
| **1.7B-sft-v3-flat-Q6 (pass-2 candidate)** | 0.878 | 0.927 | 0.938 | 0.970 | 0.914 | 1.32 GB |

On-disk training data is now the **v3** state (ffmpeg 396 rows, documents 334). Keep it.

## Experiment rules (inherited — do not relax)

1. Compare data/hyperparameters at **f16 or Q6, never Q4** (Q4 hard-slice noise ~±15pt).
2. **`success` verifier** is the promotion metric; cheap is smoke-test only.
3. Report **full and hard slices separately**; full is near a corpus ceiling.
4. **Inspect row-level flips** for every apparent win — a +2-row hard move is not a result.
5. Do **not** train on held-out hard rows verbatim.
6. Keep documents as an **anchor**, not a gradient sink.
7. **New this pass:** never promote on the same 55-row slice used for candidate selection —
   confirm on an independent probe (Task 1).

---

## Tasks

- [~] 1. Break the selection bias: independent confirmation probe (partly done via matched f16/Q6 baseline + chain3-consistency; dedicated held-out probe still open)
- [x] 2. Confirm `sft-v3-flat` at f16 + finish the quant sweep (done: f16/Q5/Q6/Q8 curve; Q6 = deployment pick; honest hard 0.891)
- [x] 3. Fix the retrieval floor before more model work (done — keywords applied: non-CJK misses 70→46, full ffmpeg +0.48pt, no regressions; CJK 44 left to segmentation)
- [x] 4. 4B-v3 retrain (the untested production-default lever) (done: passes gate, eliminates contamination, beats v1-ft)
- [x] 5. Planner-diversity experiment — **hypothesis NOT supported** (io transfers nothing to ffmpeg: hard +1.8pt noise, chain3 flat, full −0.7pt). Refines pass-2's cross-skill reading: the documents→ffmpeg benefit is not generic planner-shape diversity.
- [ ] 6. Preference tuning, done right (only if 4–5 plateau)
- [x] 7. Decide production lanes — **4B-v3 PROMOTED** for ffmpeg + documents (2026-07-02)

## Promotion (2026-07-02)

`qwen3-4b-v3` (= `models/qwen3-4b-sft-v3-flat-q4.gguf` — **renamed 2026-07-14 to
`models/knaif-qwen3-4b-v1-q4_k_m.gguf`**, see [PERFORMANCE.md §8](../PERFORMANCE.md)) is promoted as the runtime model for
**ffmpeg + documents** — the two skills it was trained on. Wiring:

- `models.yaml`: added the entry (n_ctx 8192, max_tokens 512, matching the validated eval
  config). Project-wide `default:` **stays `qwen3-4b`** (untuned instruct).
- `skills/ffmpeg/skill.yaml` and `skills/documents/skill.yaml`: `recommended_model:` points at
  it. *(The key was `qwen3-4b-v3` when written; **renamed to `knaif-qwen3-4b-v1` on 2026-07-20**
  — that is what `models.yaml` and both `skill.yaml` files carry today.)*
- `skills/io/skill.yaml`: **left on `qwen3-4b`** — io was not in the fine-tune's training
  data, so deploying the tuned model there is unvalidated. Conservative choice.

Verified: `knaif-cli run ffmpeg ... --backend auto` loads the new GGUF and produces a valid
plan; `python/core/tests/test_models.py` passes.

**Follow-up (not blocking):** the per-skill regression snapshots
(`skills/{ffmpeg,documents}/data/eval_snapshot.json`) are still locked against the old
untuned `qwen3-4b` (ffmpeg cheap/297, documents success/129 — stale corpus). `regression`
takes `--backends` explicitly so nothing auto-breaks, but the snapshots should be re-locked
against `knaif-qwen3-4b-v1` on the current corpus in a deliberate pass before relying on the gate.

## Pass-3 results so far (2026-07-02)

Runs under `evals/runs/2026-07-02_*`; details in `evals/INDEX.md`.

**The v3 data effect is real but smaller than pass-2 reported.** Pass-2's "+5.4pt hard" was
Q6 quant-inflated. Clean matched reads (845 corpus, `max_tokens:512`):

| model | full | hard | chain3 | note |
|---|---:|---:|---:|---|
| 1.7B v1-union f16 | 0.878 | 0.855 | 0.844 | baseline |
| 1.7B v3-flat f16 | 0.872 | 0.891 | 0.906 | **honest** data effect: hard +3.6, chain3 +6.2, full −0.6 |
| 1.7B v3-flat Q6 | 0.878 | 0.927 | 0.938 | **deployment pick** (1.32 GB, 241 ms); Q8==f16 proves Q6 hard is a favorable draw |
| 4B untuned Q4 | 0.905 | 0.909 | 0.938 | production default |
| 4B v1-ft Q4 | 0.895 | 0.945 | 0.938 | old tune — enum-bleed contamination, net −9 flips |
| **4B v3-flat Q4** | **0.898** | **0.945** | **0.969** | passes gate; **0 contamination**, net −6; documents held 0.976/0.914 |

Key robust signals (survive quantization): **chain3 +6.2pt (1.7B) / +3.1pt (4B)** from the 32
targeted chain rows, and the 4B's **contamination elimination** (v3 anti-contam rows removed
the `quality:"small"`/`convert_audio` bleed). Full corpus ≈ neutral; documents anchor held.

### 1. Break the selection bias: independent confirmation probe

**Status:** PARTLY DONE — **still genuinely open**, and the one real weakness left in the
promoted model's evidence. Substituted signals: the matched f16/Q6 baseline (which caught the
Q6 inflation) and chain3-consistency across quant levels. A *dedicated* held-out probe was
never built, so `sft-v3-flat`'s hard-slice margin has still never been confirmed on data
independent of the slice it was selected on.

The pass-2 winner was selected on the same 55-row hard slice it is reported on. Before any
promotion, build a confirmation signal that is independent of the selection target:

- Carve the held-out hard slice into a **selection half** and a **confirmation half**, or add
  a fresh held-out contrastive/chain probe (paraphrases + new files/params of the audited
  failure buckets, never verbatim eval rows).
- Re-score `1.7b-ft-Q6` (prior) and `sft-v3-flat-q6` on the confirmation probe.
- Optionally add a second training seed of `sft-v3-flat` to separate run-to-run variance from
  a real data effect.

**Gate:** the flat improvement reproduces on the confirmation probe (and/or a second seed)
and is not carried by 1–2 rows. If it does not reproduce, treat flat as within noise and keep
`1.7b-ft-Q6` as the 1.7B baseline; record the retraction.

### 2. Confirm `sft-v3-flat` at f16 + finish the quant sweep

**Status:** DONE — f16/Q5/Q6/Q8 curve run; **Q6** is the deployment pick; honest hard 0.891.

Remove quant noise from the pass-2 conclusion and find the 1.7B-v3 sweet spot.

- Evaluate `sft-v3-flat` at **f16** (noise-free read of the data effect).
- Quantize and eval **Q5_K_M, Q6_K, Q8_0** (Q8 diagnostic only).
- Report size, latency, ffmpeg full/hard/chain3, documents full/hard for each.

**Gate:** pick the smallest quant that holds the f16 hard-slice result within the confirmation
tolerance from Task 1. Do not promote Q8 (too close to 4B-Q4 footprint) unless it is a wide
diagnostic winner.

### 3. Fix the retrieval floor before more model work

**Status:** DONE — keyword fixes applied (non-CJK misses 70→46, full ffmpeg +0.48pt, no
regressions); the CJK 44 went to [retrieval-overhaul](2026-07-02-retrieval-overhaul.md).

129 audited utterances never retrieve the correct tool. Optimizing the model on these is
wasted gradient and inflates the failure count.

- Triage the 129 into **cheap keyword fixes** (bitrate/compress/volume/thumbnail terms) vs
  **CJK tokenization** (blocked at the time on segmentation work; since fixed by
  [retrieval-overhaul](2026-07-02-retrieval-overhaul.md), Task 2).
- Apply the keyword fixes; re-run the retrieval pass to confirm the miss count drops.
- **Exclude un-retrievable rows from all data-generation and promotion decisions** until
  retrieval is healthy — tag or filter them so the hard/full metrics stop charging them to
  the model.

**Gate:** retrieval-miss count on the ffmpeg eval corpus falls; the excluded set is explicit
and reproducible. Re-read the flat candidate's numbers with retrieval-miss rows separated.

### 4. 4B-v3 retrain (the untested production-default lever)

**Status:** DONE — passes the gate, eliminates the enum-bleed contamination, beats v1-ft.
**Promoted** (see Promotion above). Do not re-run.

The production default is still **4B-Q4 untuned** (best full ffmpeg, 0.905). The old 4B union
LoRA hit hard `0.945` but lost `-1pt` full ffmpeg from cross-skill enum bleed
(`quality:"small"`, hallucinated `convert_audio`). No 4B model was ever trained on the v3 data
or with a gentler recipe. This is the single most valuable untried experiment for the default
lane: a 4B-v3 that keeps full ffmpeg `>= 0.905` while lifting hard would be a real promotion.

- Train Qwen3-4B on the v3 union (r16, 3ep, lr2e-4 as the flat recipe; consider lr1e-4 to
  protect the near-ceiling full corpus).
- Eval at f16 first, then Q4 for the deployment read.
- Inspect the base->ft flip set for the enum-bleed failures the old 4B LoRA introduced; the
  v3 anti-contamination/reject rows should reduce them.

**Gate:** promote only if full ffmpeg holds within ~1pt of 0.905 **and** hard rises, with no
new cross-skill contamination in the flip set. Otherwise 4B-Q4 untuned stays the default.

### 5. Planner-diversity experiment (test the cross-skill hypothesis)

**Status:** DONE — **hypothesis NOT supported.** Adding `io` transferred nothing to ffmpeg
(hard +1.8pt noise, chain3 flat, full −0.7pt). This is the run that refutes pass 2's
Cross-Skill Training Conclusion; recorded as a dead end in
[FINE_TUNING.md §5](../FINE_TUNING.md).

Pass 2's most interesting conclusion: the union wins because documents **regularizes the
planner contract** (strict JSON, clarify/reject, chain composition), not because it carries
ffmpeg knowledge — the residual ffmpeg failures are planner errors, not media-knowledge
errors. If true, adding *structurally different* planner-shape diversity should lift ffmpeg
hard beyond mere preservation.

- Add either a small third skill with different wording/workflow shapes, or a targeted batch
  of clarify/reject/multi-step rows that exercise the same JSON-plan muscles **without**
  enum/tool-name overlap with ffmpeg (avoid the contamination that bit the 4B union LoRA).
- Keep a **held-out ffmpeg bucket probe** (from Task 1) to distinguish "ffmpeg improved" from
  "documents preserved."
- Compare against `sft-v3-flat` at matched quant.

**Gate:** ffmpeg hard rises on the held-out probe (not just full-corpus preservation) with no
full-ffmpeg or documents regression beyond tolerance. This confirms or refutes the hypothesis;
either outcome is a recorded result.

### 6. Preference tuning, done right (only if 4–5 plateau)

**Status:** Not started

Pass-2 DPO failed because it reused a **40-row eval-failure** set built from the selection
metric itself. Retry only if SFT/data levers plateau, and only with the fixes:

- Build a **larger** preference set from **non-eval** prompts (paraphrases of audited buckets),
  with per-row **bucket labels** (over-clarify, over-reject, wrong-enum, hallucinated-tool,
  bad-chain).
- chosen = validated expected plan; rejected = an actual bad plan from the SFT parent.
- Select on **held-out bucket probes**, not aggregate hard-slice noise.
- DPO/ORPO on top of the best SFT checkpoint.

**Gate:** beats its SFT parent on the labeled failure buckets, not just aggregate hard slice.

### 7. Decide production lanes

**Status:** DONE — **4B-v3 promoted** for ffmpeg + documents (2026-07-02); see Promotion
above and [FINE_TUNING.md §1](../FINE_TUNING.md).

Decision rules:

- **Default (full-quality) lane:** 4B-Q4 untuned unless Task 4's 4B-v3 beats it on full ffmpeg
  with no hidden regression.
- **Quality-per-byte lane:** the Task 1/2-confirmed 1.7B-v3 quant, if it stays >=1 GB smaller
  and its hard-slice gain survives the confirmation probe.
- **No separate ffmpeg-only lane** — closed by pass 2 (v1 and v3 both fail to beat the shared
  model at matched data).

**Gate:** update this plan, [../audits/2026-07-01-finetuning-study-findings.md](../audits/2026-07-01-finetuning-study-findings.md),
[../TODO.md](../TODO.md), and [../../evals/INDEX.md](../../evals/INDEX.md) with
the final decision and run folders.

---

## Explicitly out of scope (documented dead ends — do not repeat)

- More weighted / curriculum SFT (gentle/hard3/low-lr): moves hard but always costs full
  ffmpeg and documents. Diagnostic only.
- Tiny eval-derived DPO (see Task 6 for the corrected version).
- Bulk verifier-filtered synthetic distillation: schema-valid + dry-run-executable rows still
  diluted the ffmpeg routing distribution. Valid != helpful.
- Separate ffmpeg-only fine-tune: no measurable advantage at matched data.
- Gemma3 as a base: not competitive and 4x slower.

## Reproduction

See [../audits/2026-07-01-finetuning-study-findings.md](../audits/2026-07-01-finetuning-study-findings.md) Section 9 for the
train -> merge -> quantize -> eval pipeline. STOP on any `CUDA: illegal memory access`
(Blackwell+WSL2 TDR risk) — reboot, do not retry-spam.
