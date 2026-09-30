---
title: Writing training data
description: Why thirty contrastive rows beat eighty reinforcing ones, and how to target a real failure bucket.
sidebar:
  order: 2
---

`data/train.jsonl` pairs an utterance with the plan the model should have produced. It is
learned from; `data/eval.jsonl` is measured against. Never the same rows.

## Shape beats volume

This is the single most useful thing this project has learned about training data, and it
is counter-intuitive enough to state with the numbers attached.

**What worked:** ~30 hand-validated **contrastive** rows aimed at audited failure buckets —
3-step chains, clarify/reject boundaries, impossible-media rejects. Result: **hard +3.6pt,
chain3 +6.2pt** on the 1.7B, full roughly flat.

**What did not:** ~80 rows reinforcing a correct enum after an observed enum error. It cut
enum errors from 3 to 2, and **lost the hard slice** — ffmpeg hard at f16 fell from 0.855
to 0.800. It diluted the chain and contrastive signal, and over-nudged `clarify`.

The difference is not size. It is that the first set showed the model **the wrong mapping
against the right one**, and the second only repeated the right answer.

:::caution[Adding rows to fix a small observed failure usually costs more than it gains]
And only the full read — full **and** hard **and** chain3 — reveals it. The enum experiment
looked like a win on the metric it targeted.
:::

## Contrastive near-pairs

The technique that works: put the confusable cases side by side, so the boundary is what is
being taught rather than the answer.

The clearest success is anti-contamination. When a shared model mixed enums across skills —
ffmpeg emitting documents' `quality: "small"`, or a hallucinated `convert_audio` — the fix
was reject/near-pair rows showing each skill's vocabulary against its neighbour's.
Result: **zero contamination** in the regression flips, against several in the previous
tune.

## Start from an audit, not from intuition

1. Run the eval and **bucket the real failures**: chain, clarify, reject, enum, retrieval.
2. Take the retrieval bucket out — [that is a keyword fix](/fine-tuning/#is-it-worth-it-at-all),
   not a training problem.
3. Write rows against the remaining buckets, weighted by how many rows each actually
   contains.

Writing rows for a bucket you assumed rather than measured is how the 80-row experiment
happened.

## Never train on held-out eval rows

Rows tagged `hard` and `chain3` are held out precisely so that gains there measure
generalisation. Training on them verbatim inflates exactly the numbers you will use to
decide, and nothing downstream will catch it.

Use **neighbours and paraphrases** instead — same failure bucket, different files, different
parameters, different phrasing.

## Building the union dataset

```bash
# reproduces the EXACT inference prompt per row, so training and serving match
uv run python -m training.build_union
```

That fidelity matters: a model trained against a prompt shape it will not see at inference
learns the wrong conditioning.

Weighting flags exist (`--weight-tags hard=3,ffmpeg:chain3=3`) and are **diagnostic only** —
see [known outcomes](/fine-tuning/outcomes/#weighted-or-curriculum-sft).

## Keep a saturated skill in the mix

`documents` scores near its ceiling, so it contributes little gradient — and that is the
point. It sits in the union as an **anchor** against forgetting. Confirm it held after every
run; if the anchor moved, the run did something you did not intend.
