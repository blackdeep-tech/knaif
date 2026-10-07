---
title: Released models
description: The Qwen3 fine-tunes knaif publishes on HuggingFace — what they are, how to get one, and which to point your app at.
sidebar:
  order: 1
---

knaif publishes **fine-tunes of its own**, all Apache-2.0, all in a single
HuggingFace repo: **[huggingface.co/blackdeep/knaif](https://huggingface.co/blackdeep/knaif)**.

They are trained to do one job — turn an utterance into
`{"plan": [{"tool", "args"}]}` — so what they learned is **routing and argument
extraction**, not knowledge or style.

| Model | Base | Quant | Size | Status |
|---|---|---|---|---|
| **`knaif-qwen3-4b-v2`** | [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B) | Q4_K_M | 2.50 GB | **The default from knaif 1.2.0.** Recommended for desktop and CLI |
| `knaif-qwen3-1.7b-v2` | [Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B) | Q6_K | 1.32 GB | knaif 1.2.0's model for footprint-constrained surfaces; not deployed by default |
| `knaif-qwen3-4b-v1` | [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B) | Q4_K_M | 2.50 GB | The default of knaif 1.0.1 and 1.1.0; kept so pinned installs keep working |
| `knaif-qwen3-1.7b-v1` | [Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B) | Q6_K | 1.32 GB | knaif 1.0.1 and 1.1.0's footprint model; kept |

Which knaif release uses which model, and each model's numbers per backend from the shipped
binary, are on the [model card](https://huggingface.co/blackdeep/knaif).

Exact URLs, SHA-256 checksums and byte sizes live in
[`contracts/models/model-manifest.yaml`](https://github.com/blackdeep-tech/knaif/blob/main/contracts/models/model-manifest.yaml).

`knaif-*` means a knaif fine-tune. An unprefixed key like `qwen3-4b` is a stock
third-party checkpoint that knaif knows how to run but does not publish.

## Getting one

**No GGUF is bundled** — not in the wheel, not in the installer, never attached to a GitHub
release. The runtime downloads on first use and verifies against the pinned checksum.

```console
$ knaif models pull knaif-qwen3-4b-v2     # ~2.5 GB, one time
$ knaif models list                       # what's installed
$ knaif models verify knaif-qwen3-4b-v2   # re-check the checksum
```

The native runtime keeps models in `~/.knaif/models`; the Python runtime reads GGUFs from
the repo's gitignored `models/` directory.

## Pointing your own app at one

For an SDK app, a released fine-tune is just a GGUF path:

```python
from knaif.orchestrator import InferenceOrchestrator

orch = InferenceOrchestrator(
    backend="llama_cpp",
    model_path="models/knaif-qwen3-4b-v2-q4_k_m.gguf",
)
```

See [Connecting a model](/sdk/inference/) for the Ollama path and the reasoning-model
defaults that hang if you build the orchestrator by hand.

:::caution[A fine-tune is tuned for *its* skills]
Both models were trained on the union of `ffmpeg` and `documents` training data. That is an
advantage if your commands look like media or document work, and a liability if they do not
— training nudges routing toward the tools it saw.

The same rule governs knaif's own skills: a skill is only pointed at a fine-tune **if it was
in that fine-tune's training union**. `io` was not, so it runs on the stock `qwen3-4b`
instead. If your vocabulary is far from media work, measure a stock instruct model against
the tune before assuming the tune wins — see [Evaluate a skill](/evaluate/).
:::

## How the runtime picks a model

Three files mention models and do different jobs, which is the usual source of "why is it
loading *that*?":

| File | Role |
|---|---|
| `contracts/models/model-manifest.yaml` | Bill of materials — what a build ships, where to download it, checksum |
| `models.yaml` | Runtime registry — the model a Python CLI/library call uses, plus backend tuning |
| `eval_backends.yaml` | Benchmark set — every backend the eval suite can run |

Resolution precedence, highest first:

1. `--model-path PATH` — a raw GGUF, with no tuning options applied
2. `--model NAME` — looked up in `models.yaml`
3. the skill's `recommended_model:` in `skills/<name>/skill.yaml`
4. `models.yaml`'s top-level `default:`
5. mock inference

## Why Qwen3, and why 4B

A planner only has to route and fill arguments, so the bar is instruction-following and
schema discipline rather than world knowledge. Three 4B-class bases were benchmarked on the
real corpora with the `success` verifier, in the **Python lane** — these compare bases
against each other, not what the native CLI delivers:

| Base | ffmpeg full | ffmpeg hard | documents full |
|---|---:|---:|---:|
| **Qwen3-4B** (untuned) | **0.905** | **0.909** | 0.976 |
| Gemma3-4B-IT | 0.857 | 0.891 | 0.970 |
| Phi-4-mini | 0.668 outcome | — | — |

Gemma3 lost on quality *and* ran ~4× slower on the same machine. Phi-4-mini was dropped for
over-refusal — it rejected 47 legitimate plan rows to win the safety tags, which is not a
usable planner. Qwen3 also gives a clean `/no_think` switch, which matters because
constraining JSON while suppressing thinking produces empty output on this prompt.

The 1.7B is genuinely close but its weakness concentrates in the hard slice and worsens
under aggressive quantization (ffmpeg hard: **0.855 at f16, 0.691 at Q4**). Q6_K recovers
most of it at 1.32 GB, which is why it ships at Q6 and stays a footprint option rather than
the default.

## What the fine-tune bought

Both v1 models come from one recipe (FT cycle `sft-v3-flat`): Unsloth bf16 LoRA, rank 16 /
alpha 16, 3 epochs, lr 2e-4, completion-only loss, trained on the union of `ffmpeg` and
`documents`. No weighting, no curriculum — the flat recipe won.

| Model | Effect |
|---|---|
| `knaif-qwen3-4b-v1` | ffmpeg hard **0.909 → 0.945**, chain3 **0.938 → 0.969**; full corpus and documents held |
| `knaif-qwen3-1.7b-v1` | ffmpeg hard **+3.6 pt**, chain3 **+6.2 pt**; documents held |

The gains sit in the hard and multi-step slices, which is what you would expect from
training that teaches composition rather than capability. These are the **fine-tune
experiment's** figures, at that run's quant; the committed baseline the regression gate
uses is separate and slightly lower on the hard slice (0.929 at the shipped Q4) — see
[snapshots](/evaluate/snapshots/). Rows tagged `hard` and `chain3`
are held out of training entirely, so those numbers measure generalisation.

**v2 (knaif 1.2.0)** keeps the flat recipe on a larger union: `reject` now means only "unsafe",
`clarify` covers everything a skill cannot do or needs more detail for, and terse phrasings got
their own rows. The 4B v2 (FT cycle `sft-v4-flat`) lifts ffmpeg from 0.921 to **0.943** and
documents from 0.970 to **0.976** under the same grader; the 1.7B v2 (`sft-v9-flat`) lifts ffmpeg
from 0.878 to **0.920** and passes the safety set it missed by one request as v1. Both are
measured on the shipped binary per backend before release; the 1.7B has its own acceptance bar and
misses a few thin ffmpeg slices on Vulkan and CPU, which ships by owner decision and is listed on
the model card.

Fine-tuning also **shrinks the quantization tax** rather than merely raising the score: the
1.7B's ffmpeg outcome tax fell from −.064 untuned to −.009 tuned. Do not read an untuned
model's quant tax as a property of the size — re-measure after tuning, or you will
over-provision the quant level.

## Is a local 4B actually good enough

Measured with v2 (2026-10-01): on eleven real-world ffmpeg requests, three runs each,
`knaif-qwen3-4b-v2` (native, CUDA) produced a correct, `ffprobe`-verified file for all nine
file requests in every run — 27/27, matching Claude Code (`opus-5.5`, `sonnet-5.5`), OpenAI
Codex CLI (`gpt-6-astra`, `gpt-6.1-sol`) and GitHub Copilot CLI (`gpt-5.6-terra`) at 27/27
each — at zero marginal cost and about a tenth of the time. The full table is on
[knaif.org/vs](https://knaif.org/vs/); the first measurement, with v1 on 2026-07-02, showed
the same picture.

Across the full 846-utterance corpus — measured with v1 against `opus-4-8` in July, and not
re-measured since — the premium arm does lead — **0.989 vs 0.967** success — and it is worth
knowing exactly where. Decomposed, outcome accuracy is 1.000 vs **0.905**,
a 9.5-point gap, against only 2.2 points of success. The local model loses mostly by
**misrouting** — answering `clarify` or `reject` where a plan was expected, or picking the
wrong tool — not by generating worse commands. When it routes correctly it generally
produces a correct one.

That is the single most useful thing to know before you invest: routing quality is where
corpus and fine-tuning effort pays off, and it is why you should not build `train.jsonl`
until routing is already healthy.

:::note[Speed figures need a machine attached]
Latency here is hardware-bound and varies several-fold across GPUs, so no number in this
project means anything without naming the box it came from. Per-machine, per-backend
figures live in
[`docs/PERFORMANCE.md`](https://github.com/blackdeep-tech/knaif/blob/main/docs/PERFORMANCE.md).
:::

## Publishing your own

Training a model is a separate track from consuming one — the pipeline, the methodology
rules, and the proven dead ends are under [Fine-tuning](/fine-tuning/). Promotion is the
last step: a candidate becomes a released model only after it is evaluated against **every**
active skill's committed snapshot, not just the one you were working on.
