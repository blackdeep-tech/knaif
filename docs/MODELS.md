# Models

**The one place that answers "which model does knaif use, where do I get it, and why that
one?"** A living reference — released artifacts, hosting, selection rules, the evidence
behind each choice, and the rules for publishing a new one.

It is deliberately *not* a how-to. Fine-tuning procedure lives in
[FINE_TUNING.md](FINE_TUNING.md), backend setup in [INFERENCE.md](INFERENCE.md), latency in
[PERFORMANCE.md](PERFORMANCE.md), legal provenance in [PROVENANCE.md](PROVENANCE.md). This
doc links to them rather than restating them.

---

## 1. Released models

Both are knaif's own fine-tunes, published under Apache-2.0 in the single HuggingFace repo
**[`huggingface.co/blackdeep/knaif`](https://huggingface.co/blackdeep/knaif)**.

| Model | Base | Quant | Size | Serves | Status |
|---|---|---|---|---|---|
| **`knaif-qwen3-4b-v1`** | [`Qwen/Qwen3-4B`](https://huggingface.co/Qwen/Qwen3-4B) | Q4_K_M | 2.50 GB | `ffmpeg`, `documents` | **the default** — recommended for desktop + CLI |
| `knaif-qwen3-1.7b-v1` | [`Qwen/Qwen3-1.7B`](https://huggingface.co/Qwen/Qwen3-1.7B) | Q6_K | 1.32 GB | `ffmpeg`, `documents` | published; recommended for mobile, not deployed by default |

Third-party checkpoints knaif also knows how to run, but does not publish:

| Model | Role |
|---|---|
| `qwen3-4b` (stock `Qwen3-4B-Q4_K_M`) | project-wide `default:` — the safe fallback for skills the fine-tune was **not** trained on (e.g. `io`) |
| `gemma3-4b` | kept as an alternative-base control in the eval set. Not competitive (§4.1) |

**Naming.** `knaif-*` means a knaif fine-tune; an unprefixed key is a stock third-party
checkpoint. A model carries **one name** on disk, in the manifest, and on HuggingFace.
Public release versions (`v1`, `v2`, …) are contiguous and independent of knaif's own
version; internal fine-tune-cycle names (`sft-v3-flat`) stay in the manifest's
`training_run:` field and never leak into a public name.

Exact URLs, SHA-256 checksums, and byte sizes: [`contracts/models/model-manifest.yaml`](../contracts/models/model-manifest.yaml).

## 2. Getting a model

**No GGUF is bundled** — not in the wheel, not in the installer, and never attached to a
GitHub release. The runtime downloads on first use and verifies against the pinned SHA-256.

```console
$ knaif models pull knaif-qwen3-4b-v1     # ~2.5 GB, one time
$ knaif models list                       # what's installed
$ knaif models verify knaif-qwen3-4b-v1   # re-check the checksum
$ knaif models update | rm [<name>|--all]
```

Native runtime models live in `~/.knaif/models`; the Python runtime reads GGUFs from the
repo's `models/` directory (gitignored). The Windows installer offers the pull as an opt-in
post-install task. `knaif run` prompts for consent before any multi-GB download and falls
back to mock inference if declined — see [NATIVE.md §5-6](NATIVE.md).

## 3. How a model gets chosen

Three files mention models, with three different jobs. Confusing them is the usual source
of "why is it loading *that*?":

| File | Role | Read by |
|---|---|---|
| [`contracts/models/model-manifest.yaml`](../contracts/models/model-manifest.yaml) | **Bill of materials** — what this build ships, where to download it, checksum, per-surface recommendation | native `ModelStore`, installers |
| [`models.yaml`](../models.yaml) | **Runtime registry** — the one model a Python CLI/library call uses, plus its backend tuning (`n_ctx`, `max_tokens`, offload) | Python runtime |
| [`eval_backends.yaml`](../eval_backends.yaml) | **Benchmark set** — every backend the eval suite can run, including dead experiment stanzas kept for reproducibility | eval suite only |

Runtime resolution precedence (highest first):

1. `--model-path PATH` — raw GGUF, no tuning options applied
2. `--model NAME` — looked up in `models.yaml`
3. the skill's `recommended_model:` in `skills/<name>/skill.yaml`
4. `models.yaml`'s top-level `default:`
5. mock inference

Current pinning: `ffmpeg` and `documents` → `knaif-qwen3-4b-v1`; `io` → stock `qwen3-4b`.
**A skill is only pointed at a fine-tune if it was in that fine-tune's training union** —
otherwise it inherits a model that has been nudged away from its tools.

The manifest binding is **asymmetric**: publishing a new model requires a new knaif release
(each build carries its own manifest copy), but a knaif release does not require a new
model. Editing the manifest does not reach installed CLIs.

## 4. Why these models

### 4.1 Why Qwen3 as the base

A model here only has to **route** — pick the right tool(s) and fill valid args — so the
bar is instruction-following and schema discipline, not world knowledge. Three 4B-class
candidates were benchmarked on the real corpora with the `success` verifier:

| Base | ffmpeg full | ffmpeg hard | documents full | Median latency (`5080`) |
|---|---:|---:|---:|---:|
| **Qwen3-4B** (untuned) | **0.905** | **0.909** | 0.976 | **~350 ms** |
| Gemma3-4B-IT | 0.857 | 0.891 | 0.970 | 1515 ms (**4×** slower) |
| Phi-4-mini | 0.668 outcome | — | — | — |

Gemma3 lost on quality *and* speed — no reason to fine-tune it
([run](../evals/INDEX.md), 2026-06-30). Phi-4-mini was dropped for over-refusal: it rejected
47 legitimate plan rows to win the safety tags, which is not a usable planner. Qwen3 also
gives a clean `/no_think` switch (`thinking_enabled: false`), which matters because JSON
grammar-constraining plus suppressed thinking produces empty output on this prompt.

### 4.2 Why 4B and not the smaller 1.7B

The 1.7B is genuinely close, and it is *cheaper per byte* — but the deployable gap is
smaller than the size gap suggests, because the 1.7B's weakness is concentrated in the hard
slice and gets worse under aggressive quantization:

| Model | ffmpeg full | ffmpeg hard | documents full | Disk |
|---|---:|---:|---:|---:|
| 4B instruct (untuned ≈ prod) | 0.905 | 0.909 | 0.976 | 2.5 GB (Q4) |
| 1.7B fine-tuned, f16 | 0.878 | 0.855 | 0.982 | 3.4 GB |
| 1.7B fine-tuned, Q4 | 0.869 | **0.691** | 0.957 | 1.1 GB |

A small model is only worth shipping if it is *meaningfully* smaller **and** close on
quality. The 1.7B needs Q6 (1.32 GB) to hold up — a real ~1.2 GB saving, which is why it is
published for footprint-constrained surfaces (mobile recommendation) but is not the
default. See [plans/2026-06-30-best-skill-model.md](plans/2026-06-30-best-skill-model.md).

### 4.3 Why those quantizations

- **4B → Q4_K_M.** Best size↔quality at 4B; the model is near its instruct ceiling anyway.
- **1.7B → Q6_K.** Its hard-slice gap is mostly *quantization*, not size (f16 0.855 vs Q4
  0.691). Q6 recovers most of it at 1.32 GB. Q8_0 (~1.9 GB) is only ~0.6 GB under 4B-Q4 —
  not worth it.
- **Q8/f16 are diagnostics, not deployments.** Q4 hard-slice noise is ≈ ±15 pt on n=55, so
  a Q6/Q5 "gain" over an f16 read is usually a quant draw. Trust f16/Q8 for truth, ship Q4/Q6.

**Fine-tuning shrinks the quantization tax — it does not merely raise the score.** Measured
as a controlled f16-vs-Q4 pair at each size, the 1.7B's ffmpeg outcome tax fell from
**−.064 untuned to −.009 tuned**; the 4B's was ≈0 either way. A tuned small model holds up
under aggressive quantization far better than its untuned counterpart, which is what makes
a ~1 GB deployable 1.7B viable at all. Two consequences worth remembering:

- Do not read an untuned model's quant tax as a fixed property of the size. Re-measure it
  after tuning, or you will over-provision the quant level.
- The comparison is only meaningful when **every** GGUF in it — untuned and tuned — is
  built from the same weights through the same conversion pipeline. Reusing a
  differently-provenanced `models/*.gguf` as the untuned baseline mixes a conversion
  difference into the tax. (In practice that difference measured small — a
  differently-built production Q4 scored 0.904 against a purpose-built 0.905 — but it is
  not zero and it is free to avoid.)

### 4.4 Is a local 4B actually good enough

On eleven real-world ffmpeg requests, `knaif-qwen3-4b-v1` produced a correct,
`ffprobe`-verified artifact for all nine artifact requests — matching Claude Code
(`opus-4-8`), GitHub Copilot CLI (`sonnet-5`), and OpenAI Codex CLI (`gpt-5.5`) at 9/9 each
— at ~1.2 s per request instead of 11–16 s, at zero marginal cost. Over the full
846-utterance corpus the premium arm leads 0.989 vs 0.967 success, and that gap sits in the
hard, ambiguous, and multilingual tail, not in everyday work. Full write-up:
[experiments/2026-07-02-agent-vs-knaif-realworld.md](experiments/2026-07-02-agent-vs-knaif-realworld.md).

**Where the remaining gap actually is — routing, not ffmpeg.** Decomposing that same
comparison is more useful than the headline: outcome accuracy is 1.000 vs **0.905**, a
9.5-point gap, while the success score differs by only 2.2 points. The local model loses
mostly by *misrouting* — answering `clarify` or `reject` where a plan was expected, or
picking the wrong tool — not by generating worse ffmpeg. When it routes correctly it
generally produces a correct command. That is why routing quality, not command-rendering
capability, is where corpus and fine-tuning effort pays off, and it is the reason
`docs/CORPUS_AUTHORING_STEPS.md` says not to build `train.jsonl` until routing is healthy.

For scale: the premium arm cost roughly **$1.4 per 1000 generated lines** (a rough
estimate — see [BIG_LLM_HANDOFF.md](BIG_LLM_HANDOFF.md#cost-and-speed--what-is-and-isnt-comparable)
for why premium cost has no local counterpart) against zero marginal cost locally.

Latency, per machine and backend: [PERFORMANCE.md](PERFORMANCE.md) — never quote a speed
figure from this repo without naming the machine (`5080` numbers are 2.5–4× faster than
`3070L`).

## 5. How they were fine-tuned

One shared model serves every skill, so training is always on the **union** of the
participating skills' `data/train.jsonl` — a per-skill tune deployed as the shared model
catastrophically forgets the others. `data/eval.jsonl` is never trained on, and the
`hard` / `chain3` tagged rows are held out so gains there measure generalization.

**The recipe** (both v1 models, FT cycle `sft-v3-flat`): Unsloth bf16 LoRA, rank 16 /
alpha 16, 3 epochs, lr 2e-4, seed 3407, completion-only loss, `load_in_4bit=False`; union of
`ffmpeg` + `documents`; merge → f16 GGUF via llama.cpp `convert_hf_to_gguf.py` → quantize.
No weighting or curriculum — the "flat" recipe won.

**What it bought:**

| Model | ffmpeg full | ffmpeg hard | ffmpeg chain3 | documents |
|---|---|---|---|---|
| `knaif-qwen3-4b-v1` vs untuned 4B | 0.905 → 0.898 (within tolerance) | 0.909 → **0.945** | 0.938 → **0.969** | held at 0.976 / 0.914 |
| `knaif-qwen3-1.7b-v1` vs untuned | ≈ flat | **+3.6 pt** | **+6.2 pt** | held |

Zero cross-skill contamination in the regression flips — the v3 reject/near-pair rows
eliminated the earlier tune's enum bleed (ffmpeg emitting documents' `quality: "small"`, a
hallucinated `convert_audio`).

**Proven dead ends** — do not repeat without a materially different design: weighted /
curriculum SFT, tiny eval-derived DPO, bulk verifier-filtered synthetic distillation,
single-skill scope, and planner-diversity via a third skill.

Canonical procedure, methodology rules, and the promotion gate:
[FINE_TUNING.md](FINE_TUNING.md). Full experiment history including two retracted claims:
[audits/2026-07-01-finetuning-study-findings.md](audits/2026-07-01-finetuning-study-findings.md).
Per-run numbers: [evals/INDEX.md](../evals/INDEX.md).

## 6. Licensing and provenance

Both releases are **derivative works** of the Qwen3 family by Alibaba Cloud, used under
Apache-2.0 and redistributed under Apache-2.0. The manifest records this machine-readably
per entry (`base_model`, `base_model_license`, `license`) so provenance travels inside the
artifact. Training corpora were authored for knaif, reference synthetic filenames only, and
contain no personal or third-party content. Attribution notices live in
[`NOTICE`](../NOTICE); the engineering record is [PROVENANCE.md](PROVENANCE.md).

## 7. Publishing a new model

1. Pass the promotion gate in [FINE_TUNING.md §6](FINE_TUNING.md) — `success` full within
   ~1 pt of the incumbent, hard/chain3 up, no new contamination, anchor skill held.
2. Build the deployment quant (4B → Q4_K_M, 1.7B → Q6_K).
3. Upload to `blackdeep/knaif` and add a manifest entry with a **commit-SHA-pinned** resolve
   URL (never `main` — it moves under a fixed `sha256` and breaks `verify`), plus `sha256`,
   `size_bytes`, `base_model`, `base_model_license`, `skills`, and `training_run`.
   `scripts/publish_model.py` fills the checksum and size.
4. Add a `models.yaml` entry mirroring the validated eval config (`n_ctx 8192`,
   `max_tokens 512`, `thinking_enabled false`).
5. Point **only the trained skills'** `recommended_model:` at it; leave untrained skills and
   the project-wide `default:` alone.
6. Bump the public version by exactly one (`v1` → `v2`); keep the FT-cycle name in
   `training_run:` only.
7. **Cut a knaif release** — installed CLIs read their own bundled manifest, so an edit here
   reaches nobody until a release ships ([RELEASE.md](RELEASE.md)).
8. Re-lock each skill's `data/eval_snapshot.json` against the promoted model, in its own
   deliberate commit, and add a row to [evals/INDEX.md](../evals/INDEX.md).

A recommended model with `url: TODO` breaks first run for every user without `--model`;
`python/core/tests/test_model_manifest_release_ready.py` fails the build on that.

### Where models are hosted, and moving them

Models are hosted on **Hugging Face** (`blackdeep/knaif`), pulled anonymously with no token.
That choice was validated, not assumed: HF's limits are request-count caps per 5-minute
window, not a bandwidth throttle, and a single GGUF pull is nowhere near them — so slow
downloads were a *client* problem, fixed in the fetcher itself
([`fetcher.rs`](../native/crates/knaif-models/src/fetcher.rs): parallel byte-range chunks,
resume, 429/`Retry-After` backoff), not a hosting problem. A **token does not raise download
speed** — it raises the request-count ceiling, not per-connection bandwidth — so "give users a
token" is both ineffective here and contrary to the tokenless-download decision; the lever is
client parallelism.

If HF ever underperforms, **Cloudflare R2 is the pre-vetted migration target** ($0 egress,
native range/resume, tokenless public GET). The swap is deliberately cheap: models are
**content-addressed**, so migrating is *URL-only* — re-upload the identical bytes, change each
manifest entry's `url`, and the `sha256`/`size_bytes` stay valid so `verify` still passes. Keep
any new host's URLs immutable/version-pinned, the same discipline as HF's commit-SHA pinning.
Full cost/vendor analysis: [plans/2026-07-06-model-hosting-cdn-research.md](plans/2026-07-06-model-hosting-cdn-research.md).

## 8. See also

| Question | Doc |
|---|---|
| How do I set up a backend / GPU offload? | [INFERENCE.md](INFERENCE.md) |
| How fast is it on my hardware? | [PERFORMANCE.md](PERFORMANCE.md) |
| How do I run a fine-tune? | [FINE_TUNING.md](FINE_TUNING.md) |
| How is training data authored? | [TRAINING_DATA_GENERATION.md](TRAINING_DATA_GENERATION.md) |
| How is quality measured? | [EVAL_FRAMEWORK.md](EVAL_FRAMEWORK.md) |
| How does the model store work in the native runtime? | [NATIVE.md](NATIVE.md) |
| What license covers what? | [PROVENANCE.md](PROVENANCE.md) · [`NOTICE`](../NOTICE) |
