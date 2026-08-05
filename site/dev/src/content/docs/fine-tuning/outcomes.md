---
title: Known outcomes
description: What has already been tried here — including the persuasive ideas that were run, failed, and keep re-suggesting themselves.
sidebar:
  order: 4
---

Read this before designing an experiment. Several of the entries below are ideas that sound
obviously correct, were run, and did not work — re-running one is the most common way to
lose a week here.

## What works

**Small, targeted, hand-validated contrastive data.** ~30 rows aimed at audited failure
buckets gave **hard +3.6pt / chain3 +6.2pt** on the 1.7B, with full roughly flat and
quality up.

**Anti-contamination near-pairs.** Reject and near-pair rows eliminated the previous 4B
tune's cross-skill enum bleed entirely — **zero contamination** in the regression flips,
against several before.

**The flat recipe.** Rank 16, alpha 16, 3 epochs, lr 2e-4, no weighting.

**Quantisation by size.** Q6 for the 1.7B (best size-to-quality); Q4 for the 4B. Q8 is a
diagnostic, not a deployment.

**Fine-tuning where there is headroom.** 1.7B gains meaningfully. The 4B is near the
instruct ceiling.

## Proven dead ends

Do not repeat these without a materially different design.

### Weighted or curriculum SFT

Oversampling `hard` and `chain` rows moves the hard slice but **always costs full and
documents**. The weighting flags remain in the tooling as a diagnostic only.

### Tiny eval-derived DPO

40 preference pairs regressed ffmpeg. Preference tuning plausibly needs a larger,
**non-eval**, bucket-labelled set with held-out bucket probes — that remains unproven and
untried, not recommended.

### Bulk verifier-filtered synthetic distillation

Rows that were schema-valid *and* dry-run executable still diluted routing.

> Valid is not the same as helpful.

### Bulk single-op reinforcement

~80 rows reinforcing a correct enum cut enum errors 3→2 and **lost the hard slice**
(ffmpeg hard f16: 0.855 → 0.800). Contrast with the anti-contamination rows that worked:
those were contrastive near-pairs showing the wrong mapping against the right one, not bulk
repetition of the right answer. [Shape beats volume](/fine-tuning/data/#shape-beats-volume).

### Single-skill scope

ffmpeg-only never beat the shared union at matched data — at either data revision.

### Planner diversity via a third skill

:::caution[The refuted theory is persuasive and keeps re-suggesting itself]
The idea: `documents` regularises the model toward the shared planner contract — pick a
tool, emit strict JSON, fill schema-valid args, clarify, reject, compose chains — so *any*
structurally different skill should help.

It was written up as the obvious next experiment, run with `io` (zero enum overlap), and
transferred **nothing** to ffmpeg.

What remains true is only the measurement: the union beats ffmpeg-only at matched data. The
**mechanism is unexplained**. Treat "add another skill to broaden the planner" as a dead
end, not an untried idea.
:::

### Gemma3-4B as a base

Worse quality *and* roughly 4× slower than Qwen3-4B. Qwen3 is the base; settled.

### "Fine-tune the instruct checkpoint instead of the base"

A non-lever that has already been pulled. `Qwen/Qwen3-4B` **is** the post-trained instruct
model — there is no separate `-Base` repo, and it behaves like one (0.905 zero-shot on
structured JSON, which a raw pretrain base cannot do).

Every tune recorded here is already a tune *of* the instruct model. That also explains why
4B gains are flat: tuning a strong instruct model on saturated routing is mostly downside
risk, because the LoRA damages rows the untuned model already got right.

## Why the failures are written down

Each entry above is an afternoon — sometimes a week — that nobody else has to spend. They
survive because the runs that failed their gate were kept in `evals/INDEX.md` rather than
deleted.

An eval suite whose only output is good news cannot tell you to stop.
