---
title: Promotion
description: The gate a candidate must pass, and what shipping a model actually involves.
sidebar:
  order: 5
---

## The gate

Promote only when **all four** hold:

- `success` full is within ~1pt of the incumbent,
- **hard and chain3 both rise**,
- **no new contamination** in the row-level flips, and
- the **anchor skill held**.

Any one failing means record the negative result and move on. That is a normal outcome —
[most of the ideas that have been tried here did not pass](/fine-tuning/outcomes/).

Note the shape of the gate: full is allowed to *dip slightly* while hard and chain3 must
rise. Full sits near a corpus ceiling, so demanding a gain there would reject real
improvements; demanding it not fall guards against a tune that trades broad competence for
a slice.

## Shipping it

1. **Build the deployment quant** — 4B → Q4_K_M, 1.7B → Q6_K.
2. **Add a `models.yaml` entry**, mirroring the validated eval config: `n_ctx 8192`,
   `max_tokens 512`, `thinking_enabled false`. A model served with different settings than
   it was measured with is not the model you measured.
3. **Point only the skills that were in training** at it, via each
   `skills/<skill>/skill.yaml` `recommended_model:`. Leave untrained skills and the
   project-wide `default:` on the untuned model.
4. **Verify it actually loads and plans:**
   ```bash
   uv run knaif-cli run <skill> "<utterance>" --backend auto --dry-run --show-plan
   ```
   Then run the full test suite.
5. **Record it** in `evals/INDEX.md` and in `docs/FINE_TUNING.md` §1.

## The step that is easy to forget

:::caution[Re-lock the per-skill snapshots afterwards]
`skills/<skill>/data/eval_snapshot.json` stays pinned to the **old** model until someone
re-locks it.

Leave it and every future regression check measures the new model against the previous
one's bar — which will look like a permanent, unexplained offset rather than a stale
reference.
:::

Do it as a deliberate pass, in its own commit, per
[locking a snapshot](/evaluate/snapshots/).

## Naming and versioning

Model versions keep their **own** line — `-v1`, `-v2` — and never inherit knaif's release
version. That is what makes upgrades free: the model store keys on the model's filename, so
a knaif upgrade with an unchanged recommendation re-downloads nothing.

The binding is asymmetric: publishing a new model requires a new knaif release, but a knaif
release does not require a new model. Many knaif versions may recommend the same one.

## Publishing

The public manifest is `contracts/models/model-manifest.yaml`, which records the URL,
SHA-256 and size for each published model. It is a **bill of materials, not a live
catalogue** — it ships *inside* the artifact, so each knaif release carries its own copy
and answers "what does this build require?", never "what is newest?".

Consequence worth knowing: **editing that file does not reach installed CLIs.** They read
their own bundled copy. Shipping a new model to users means cutting a release.
