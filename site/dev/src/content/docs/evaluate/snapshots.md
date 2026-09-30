---
title: Locking a snapshot
description: Committing an acceptance bar, gating changes against it, and the comparability rule that stops false regressions.
sidebar:
  order: 4
---

A snapshot is your skill's **committed acceptance bar**: `data/eval_snapshot.json`, checked
in beside the corpus.

It is the artefact that turns "it seemed fine when I ran it" into something the project can
enforce. It is also what promotes your skill from `preview` to `stable` on
[knaif.org](https://knaif.org/skills/) — advertising a skill and locking its bar are
deliberately the same act.

## Locking

```bash
just eval-fixtures <skill>     # never skip this
just eval-success <skill>      # confirm the numbers are what you expect
just eval-snapshot <skill>     # write the bar
```

:::caution[Lock in its own commit]
Re-locking **moves the acceptance bar**. Do it deliberately, in a commit that does nothing
else, and only when adopting a measured improvement.

A snapshot bump buried in a feature commit is indistinguishable from silently lowering the
bar to make your change pass.
:::

## Gating

```bash
just eval-regression <skill> <current>   # exits non-zero if any metric dropped past threshold; `current` must be
                                          # a freshly saved scoreboard with the snapshot's verifier and row count
```

This is what protects every *other* skill from your change. One shared fine-tuned model
serves all of them, so a training run tuned for your skill can quietly degrade someone
else's — the union of committed snapshots is the only thing that catches it.

## When two runs are comparable

:::danger[Only compare runs sharing both the same verifier and the same corpus revision]
Different verifiers measure different things — `cheap` checks routing, `success` checks the
artifact. Comparing across them is meaningless.

A **grown corpus** changes the mix. Accuracy can fall purely because the added rows are
harder, with nothing regressed at all.
:::

A run differing in either dimension is a **fresh baseline, not a data point in a trend**.
Label it that way in `evals/INDEX.md` and archive the superseded run rather than deleting
it.

Per-tag and per-row comparison stays valid across corpus growth wherever the rows
themselves are unchanged — and that is usually what you actually wanted to know.

## Saving runs

```bash
uv run -m knaif.evalsuite run --skill <skill> \
  --config eval_backends.yaml --backends qwen3-4b --verifier success \
  --save evals/runs/2026-01-01_my-arm_success
```

All runs go under `evals/`, never a root-level `runs/`. The naming convention is
`<YYYY-MM-DD>_<label>_<verifier>` — the verifier is in the filename precisely because
comparing across verifiers is invalid, so the mistake is visible at a glance.

Add a row to `evals/INDEX.md` for every saved run.

## Keep the failures

Runs that failed their gate stay in the index. They are not clutter — they are the record
that lets the project say *no* to a plausible-sounding change with evidence rather than
opinion.

Real examples from this repo's own history: a 47% rendered-prompt reduction came out
accuracy-neutral on a clean A/B, so three planned optimisations were dropped. DPO over the
SFT parent lost ground and was not promoted. Hard-weighted oversampling over-rotated.

Each of those is an afternoon someone else does not have to spend. That only works because
the runs were kept.

## What good looks like

For reference, the committed bars for knaif 1.2.0 — **as measured in the Python lane**, on the
`success` verifier. Every figure here is read straight from the skill's snapshot:
`data/eval_snapshot.json` for the default model (`knaif-qwen3-4b-v2`), and
`data/eval_snapshot.knaif-qwen3-1.7b-v2.json` for the 1.7B, which has its own bar:

| Skill | Model | Corpus | Full | Hard slice | 3-step chains |
|---|---|---:|---:|---:|---:|
| `ffmpeg` | 4B v2 | 861 utterances | 0.943 | 0.964 | 0.969 |
| `ffmpeg` | 1.7B v2 | 861 utterances | 0.920 | 0.929 | 0.969 |
| `documents` | 4B v2 | 164 utterances | 0.976 | 0.886 | — |
| `documents` | 1.7B v2 | 164 utterances | 0.963 | 0.914 | — |

Note that ffmpeg's *full* score is lower than its hard slice — the aggregate includes
clarify and reject rows, which are harder to get right than they look.

### Which lane a number came from

**These are authoring-lane numbers.** They come from the Python runtime — the `knaif`
package, and what the SDK gives you. The native CLI is a separate measurement (L4: the
shipped binary, executing for real, one fresh process per request), and it is the number that
backs "the CLI works". For knaif 1.2.0 it was measured on every backend the release covers:

| ffmpeg `outcome_accuracy`, 4B v2 | |
|---|---:|
| Python lane, committed snapshot | **0.943** |
| Native CLI, Windows · CUDA | **0.943** |
| Native CLI, Windows · Vulkan | **0.941** |
| Native CLI, Windows · CPU | **0.942** |
| Native CLI, Linux · CUDA | **0.945** |

Every one of these cells is accepted against the bar (the model's floor and its Python score
minus 0.02, every required capability slice, safety at 100% on the binary); the per-backend
table for both models and skills is in the model card and the release notes. So quote the
Python figure for the library and the SDK, quote the native figure for the CLI, and never quote
either as "knaif's accuracy" without saying which runtime produced it. `cheap` numbers are never
published at all: a snapshot can only be locked from an executing run.
