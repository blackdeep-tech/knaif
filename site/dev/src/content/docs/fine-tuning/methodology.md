---
title: Methodology
description: The rules that stop a training experiment from fooling you — every one of them written after it already had.
sidebar:
  order: 3
---

Every rule here exists because it was learned the expensive way. They are about
measurement, not training, because measurement is where fine-tuning experiments actually go
wrong.

## Never compare across quant, corpus, or config

Compare at **f16 first**, then against a baseline you built yourself at the **same quant,
same corpus, same `max_tokens`**.

The most expensive mistake in this project's history: a candidate at Q6 was compared
against a baseline at a different quant and corpus. It reported **+5.4pt**. The real,
matched-precision figure was **+3.6pt**. A third of the headline was an artefact of the
comparison.

## Quantisation noise is larger than most effects you are chasing

**Q4 hard-slice noise is roughly ±15pt.** Even Q6 and Q5 draw favourably on a 55-row slice.

So if Q8 ≈ f16 but Q6 or Q5 look better, the Q6/Q5 "gain" is a lucky draw, not a result.
Trust f16 and Q8 for truth; treat deployment quants as deployment, not measurement.

## The hard slice is 55 rows — two points is one row

Do not promote on the hard slice alone. **`chain3` is the more robust signal**: when an
effect was real, it moved identically across quant levels.

Report **full, hard, and chain3 separately**, always. Full sits near a corpus ceiling and
will look flat even when something real happened underneath.

## A slice you selected on can no longer measure what you selected

:::danger[Best-of-N inflation]
Picking the best of ~8 candidates *by* their hard-slice score and then reporting that same
score is not a measurement. With n=55 and a two-row noise floor, the winner is partly just
the luckiest draw.

**Re-running does not fix this.** It is a different problem from ordinary noise.
:::

Confirm on something genuinely independent:

- a held-out half of the slice,
- a fresh probe built from the audited failure buckets — paraphrases and new
  files/parameters, never verbatim eval rows, or
- a **second training seed**, which separates run-to-run variance from a real data effect.

Two cheaper partial substitutes have caught real problems: a **matched-precision re-read**
(this is how +5.4pt became +3.6pt) and **chain3 consistency across quant levels**.

:::note[Stated honestly]
The currently promoted model's hard-slice margin has never had a dedicated independent
probe. That task is open. It is recorded here rather than quietly omitted, because a
methodology page that only lists rules other people broke is not much use.
:::

## Inspect row-level flips for every apparent win

Count regressions against fixes, and grep the regressions for **contamination signatures** —
ffmpeg emitting documents' `quality: "small"`, or a hallucinated `convert_audio`.

A net-positive aggregate can hide new contamination. The aggregate is the thing that moved;
the flips are what actually happened.

## `success` only, for promotion

`cheap` is a smoke test. Promotion decisions execute the plan. See
[the eval ladder](/evaluate/ladder/).

## Keep an anchor skill in every shared run

A saturated skill — `documents` — should **prevent forgetting, not consume the gradient**.
Confirm the anchor held after every run.

## The snapshot gate answers "may I promote?", not "did I regress?"

:::caution[This distinction has already been got wrong once]
`regression --all-skills` diffs against each skill's **committed** snapshot — which is the
*deployed* model. An experimental build scoring below it can simply be a lineage gap: a
study-artefact tune of a different base is *expected* to sit under the shipped model.
Reading that FAIL as catastrophic forgetting is a mistake already made here.
:::

For the forgetting question, baseline **your own pre-run** — same family, same pipeline.
Reserve the snapshot gate for the promotion decision.

Two setup traps come with it:

- **Sweep at every skill's own snapshot verifier into one folder.** ffmpeg's is `cheap`,
  documents' is `success`. Miss one and that skill is *silently skipped*, not failed.
- **A gate that cannot fail is worse than no gate**, because it manufactures confidence.

## Fix retrieval before blaming the model

```bash
uv run -m knaif.evalsuite retrieval
```

Rows where the expected tool is not in the top-5 are **retrieval** failures. No fine-tune
recovers them, and time spent training against them is time spent not fixing a keyword.
