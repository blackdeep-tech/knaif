---
title: Fine-tuning
description: When training a model is worth it, what it can and cannot fix, and the pipeline that produces a promotable candidate.
sidebar:
  order: 1
---

knaif's model does one job: turn an utterance into `{"plan": [{"tool", "args"}]}`. It
routes to the right tool and fills valid arguments. Everything after that is deterministic
code.

So fine-tuning here teaches **routing and composition** — not knowledge, not style, not
capability. If your skill cannot do something, training will not teach it to; that is a
handler you have not written.

## One model serves every skill

Training happens on the **union** of every skill's `data/train.jsonl`, never per-skill for
a shared deployment — a single-skill tune forgets the others.

Two consequences worth internalising before you start:

- **Your training data affects other people's skills.** Every other skill's committed
  snapshot is a regression gate on your run.
- **Someone else's training run can degrade yours.** Which is why locking your
  [acceptance bar](/evaluate/snapshots/) is not bureaucracy — it is the only thing that
  makes your skill visible to that gate.

## Two files, never confused

| File | Role |
|---|---|
| `data/train.jsonl` | Learned from |
| `data/eval.jsonl` | Measured against — **never trained on** |

Rows tagged `hard` and `chain3` are **held out** of training entirely, so gains there
measure generalisation rather than memorisation. Train on them verbatim and your numbers
become meaningless in a way no later check will catch.

## Is it worth it at all?

Often not, and the honest answer is measured rather than assumed.

**Fine-tuning helps most where there is headroom.** The 1.7B gains meaningfully; the 4B
sits near the instruct ceiling, so tuning it is mostly downside risk — the LoRA damages
rows the untuned model already got right.

**Fix retrieval first.** Rows where the expected tool is not in the top-5 retrieved are
*retrieval* failures, and no amount of training recovers them:

```bash
uv run -m knaif.evalsuite retrieval    # recall@k / MRR, per script slice
```

A keyword fix is an afternoon. A training cycle is days, and it cannot fix this.

## Current production state

| Lane | Model | Serves |
|---|---|---|
| Shared default | `knaif-qwen3-4b-v1` (Q4_K_M, 2.5 GB) | ffmpeg + documents |
| Untuned fallback | `qwen3-4b` | Skills not in training |
| Quality-per-byte | `knaif-qwen3-1.7b-v1` (Q6_K, 1.32 GB) | Not deployed; ready if size matters |

Skills that were not part of a training run stay on the untuned model. That is deliberate —
a tune is only ever pointed at the skills it was trained on.

Both `knaif-*` rows are published on HuggingFace and can be pulled without training
anything — see [Released models](/models/) for sizes, checksums and what the tune bought.

## The pipeline

```bash
# (a) data — regenerate or hand-author targeted rows
# (b) build the union chat dataset (reproduces the exact inference prompt per row)
# (c) train a LoRA — the flat recipe: rank 16, alpha 16, 3 epochs, lr 2e-4
# (d) merge -> f16 GGUF -> quantize   (4B ships Q4_K_M; 1.7B ships Q6_K)
# (e) eval with `success` against EVERY active skill, not just yours
```

Training code lives in `python/training/`.

## The loop that actually works

1. **Audit failures** and bucket them — chain, clarify, reject, enum, retrieval.
2. **Fix retrieval keywords** for any retrieval-miss bucket first.
3. **Author ~30 targeted contrastive rows** against the remaining buckets — not big
   template blocks. [Writing training data](/fine-tuning/data/).
4. **Train the flat union.** Evaluate at f16, then build a matched-quant baseline yourself.
5. **Decide** on full + hard + chain3 plus row-level flip inspection — never an aggregate
   alone. [Methodology](/fine-tuning/methodology/).
6. **Promote, or record the negative result** and move on.

Step 6 is not a formality. [Known outcomes](/fine-tuning/outcomes/) exists because several
persuasive ideas have already been tried and failed here, and re-running one is the most
common way to lose a week.

## Where to go next

| | |
|---|---|
| [Writing training data](/fine-tuning/data/) | Shape beats volume, and why contrastive pairs work where bulk does not |
| [Methodology](/fine-tuning/methodology/) | The rules that stop you fooling yourself |
| [Known outcomes](/fine-tuning/outcomes/) | What works, and the proven dead ends |
| [Promotion](/fine-tuning/promotion/) | The gate, and what shipping a model actually involves |
