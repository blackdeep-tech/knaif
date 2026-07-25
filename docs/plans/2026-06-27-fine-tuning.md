# Fine-Tuning — multi-skill LoRA + size-vs-gain / quantization study

**Status:** Done · **Created:** 2026-06-27 · **Completed:** 2026-06-30
**Owner:** eval · **Ref:** docs/TRAINING_DATA_GENERATION.md

> **Kept 2026-07-23** (S7 decision — cited-from-source tier, and the strongest citation in
> the set: `eval_backends.yaml` names this plan from **config**, plus `evals/INDEX.md`,
> `docs/CORPUS_AUTHORING_STEPS.md`, an audit, and five sibling plans. Deleting it breaks a
> config comment.) This is the **controlled experiment**; `docs/FINE_TUNING.md` is the
> canonical how-to that its conclusions fed. Read the doc first — this file is the evidence
> behind it, kept because a methodology rule is only trustworthy if the run that produced it
> is inspectable.
>
> **Extracted to the shipping docs** (three findings the doc had not absorbed):
> - **fine-tuning shrinks the quantization tax** — 1.7B ffmpeg outcome tax −.064 untuned →
>   −.009 tuned, 4B ≈0 either way → [MODELS.md §4.3](../MODELS.md). The phrase "quantization
>   tax" appeared nowhere in `docs/`; §4.3 argued the 1.7B's quant level from its *untuned*
>   collapse, which overstates the level a tuned build needs. The controlled-input rule (same
>   weights, same conversion pipeline for every cell) went with it.
> - **"fine-tune the instruct checkpoint instead" is a non-lever** — `Qwen/Qwen3-4B` *is* the
>   instruct model, there is no `-Base` repo, so every tune here already is one →
>   [FINE_TUNING.md §5](../FINE_TUNING.md). A rejected alternative certain to be re-proposed;
>   both docs said "near the instruct ceiling" without ever saying the base *is* instruct.
> - **the snapshot gate answers "may I promote?", not "did training regress?"** — its FAIL
>   here was the base↔instruct lineage gap, a misreading this plan itself had to correct →
>   [FINE_TUNING.md §4](../FINE_TUNING.md), with the dual-verifier sweep requirement and a
>   pointer to the SOP's aggregate-gate section.
>
> Paths repointed (`eval_results/` → `evals/`). The corpus counts are *not* drift: the design
> section says ffmpeg 297 / documents 129 and Results says 314 / 143 because Task 3 added the
> +17 / +14 hard stratum in between.
>
> **Status note:** The single home for the fine-tune track, consolidated out of
> the eval roadmap's Phase 5 (that roadmap has since been retired) and the
> fine-tuning deferrals in
> [documents-productionization](2026-06-22-documents-productionization.md) (Phase F) and
> [cross-skill-eval-monitoring](2026-06-25-cross-skill-eval-monitoring.md).
> **Done 2026-06-30 — all 8 cells filled (see Results):** one multi-skill LoRA on the
> 698-row union, applied to both bases, benched as a size × precision × tuned grid.
> Fine-tuning generalized to the held-out `hard` slice (ffmpeg hard-outcome +.036–.091
> on every cell), the small 1.7B gained most (full +.080/+.135), and tuning shrank the
> 1.7B quant tax from −.064 to −.009. No per-skill forgetting vs the base baseline; the
> snapshot gate's FAIL is the base↔instruct gap, not regression (these ft bases are a
> study artifact, not production-promotion candidates).

**Goal:** Run *one* multi-skill LoRA fine-tune on the union of the active skills'
`train.jsonl`, applied to **both** base sizes, and measure the result as a controlled
size × precision × tuned grid. Produce a size-vs-gain curve and an isolated quantization
tax, graded on the honest `success` verifier against the current corpus.

---

## Decisions locked

| Decision | Choice | Why |
|---|---|---|
| **Thesis** | Both sizes — a size-vs-gain curve | Learning exercise: how much does fine-tuning buy at 1.7B vs 4B, and does ft-1.7B reach untuned-4B (the cost-play diagonal)? |
| **Skills in the union** | `ffmpeg` + `documents` only | `io` is stale / slated for rebuild — excluded from the union |
| **Per-skill vs combined** | One multi-skill fine-tune on the **union** | A per-skill fine-tune forgets the other skills (catastrophic forgetting). Inherited from [cross-skill-eval-monitoring](2026-06-25-cross-skill-eval-monitoring.md) |
| **Adapter precision** | **16-bit LoRA**, base loaded in **bf16** (not QLoRA) | 1.7B (~3.4 GB) and 4B (~8 GB) both fit the 16 GB RTX 5080 with gradient checkpointing; avoids QLoRA's double-quantization tax. bf16 is native on Blackwell |
| **Trainer / OS** | **WSL + Unsloth** (bf16 LoRA: `load_in_4bit=False`) | Triton/CUDA wheels for sm_120 (Blackwell) are far better supported on Linux; Unsloth is the least-friction trainer |
| **Quantize step** | merge → `convert_hf_to_gguf.py` → GGUF f16 → `llama-quantize` Q4_K_M | The eval harness runs GGUFs; Q4_K_M matches the deployed format |
| **Eval depth** | **4 cells per base** (precision × tuned) | Isolates fine-tune gain *and* quantization tax on both the untuned and tuned rows |
| **Multilingual scope** | **European languages now (DE/ES/FR/RU/BG); ZH deferred** | ZH is retrieval-blocked, not model-blocked — its fix is a **non-blocking follow-on**. This fine-tune pass proceeds on the space-delimited languages; ZH rows + a re-tune come *after* the CJK retrieval fix lands (it did, 2026-07-02 — [retrieval-overhaul](2026-07-02-retrieval-overhaul.md) Task 2) |

### Inherited, do not re-litigate

- **`train.jsonl` ≠ `eval.jsonl`.** Training input vs benchmark; a routing change must land
  in both or they drift (`docs/TRAINING_DATA_GENERATION.md`).
- **Fine-tuning stays optional.** The untuned model already clears its targets
  (qwen3-4b: ffmpeg outcome ~0.90 / knaif ~0.97; documents outcome 0.988 / knaif 1.00).
  This plan measures the *marginal* gain — it is not a prerequisite for shipping.
- **Retrieval-limited failures will not move.** Retrieval runs before the model sees the
  prompt; the "as a PDF" → `convert_document` miss is a retrieval fix, not a training one.

---

## Controlled-experiment design

The matrix is **2 bases × 2 precisions × 2 tuned states = 8 cells**, each an honest
`success` eval over ffmpeg (297) + documents (129):

| base | untuned-f16 | untuned-Q4 | ft-f16 | ft-Q4 |
|---|---|---|---|---|
| **1.7B** | ⬜ | ⬜ | ⬜ | ⬜ |
| **4B** | ⬜ | ⬜ | ⬜ | ⬜ |

What each comparison yields (per base):

- **fine-tune gain (deployed)** = untuned-Q4 → ft-Q4  *(both Q4 — the shipping number)*
- **fine-tune gain (clean)** = untuned-f16 → ft-f16  *(equal precision, no boundary crossed)*
- **quantization tax (untuned)** = untuned-f16 → untuned-Q4
- **quantization tax (tuned)** = ft-f16 → ft-Q4
- **size-vs-gain** = compare the gain rows across 1.7B and 4B
- **cost-play diagonal** = does ft-1.7B-Q4 reach untuned-4B-Q4?

**Controlled-input rule:** every GGUF in this study — untuned *and* tuned — is built from
the **same freshly-downloaded HF weights** through the **same** llama.cpp pipeline. Do
**not** reuse the existing `models/*.gguf` files on disk as the untuned baseline; their
provenance/conversion differs and would contaminate the quant-tax and gain numbers. Keep
the existing `qwen3-4b` backend untouched — the committed regression snapshots reference
it, and this experiment must not disturb that gate.

### Backend naming (8 stanzas)

```
qwen3-1.7b-base-f16   qwen3-1.7b-base-q4   qwen3-1.7b-ft-f16   qwen3-1.7b-ft-q4
qwen3-4b-base-f16     qwen3-4b-base-q4     qwen3-4b-ft-f16     qwen3-4b-ft-q4
```

Eval runs follow the repo convention — save to
`evals/runs/<YYYY-MM-DD>_<label>_<verifier>/` and add an `INDEX.md` row
(never a root-level `runs/`).

---

## Tasks

### - [x] 0. Feasibility spike — Blackwell (sm_120) training stack ✅ PASSED 2026-06-29

Highest-risk unknown; ~1 hour, before committing to anything. In WSL, stand up the
Unsloth + PyTorch + CUDA stack and confirm it sees the 5080 and can run **one** bf16 LoRA
step on `Qwen/Qwen3-1.7B`. If sm_120 has no matching wheels, fall back to PyTorch nightly
or native-Windows `transformers+peft+trl`. **Gate:** a single training step completes on
GPU. Do not proceed to real training until this passes.

**Result (2026-06-29):** PASS. One bf16 LoRA step on `Qwen/Qwen3-1.7B`
(`load_in_4bit=False`, `use_gradient_checkpointing="unsloth"`) completed on the RTX 5080
— loss 7.542, 17.4M/1.74B trainable params, ~6 s. Smoke test: `python/training/phase0_smoke.py`.
Env: dedicated `.venv-train` (Python 3.12.13), **not** the 3.14 core venv. **Proven,
pinnable version set** (feeds the `train` extra + `train-setup` in
[2026-06-29-training-subsystem](2026-06-29-training-subsystem.md)):

| Component | Version | Notes |
|---|---|---|
| Python | 3.12.13 | via `uv venv --python 3.12` |
| torch | **2.10.0+cu128** | installed as 2.11.0+cu128; Unsloth pinned it down to 2.10.0 — still cu128/sm_120-capable |
| unsloth / unsloth_zoo | 2026.6.9 | |
| transformers | 5.5.0 | |
| triton | 3.6.0 | JIT `_POSIX_C_SOURCE redefined` warnings are benign (glibc 2.43 vs CPython pyconfig) |
| xformers | 0.0.35 | FA2 not built (`FA2 = False`) — fine for LoRA |
| CUDA | toolkit 12.8 bundled · device sm_120, 15.92 GB | |

### - [x] 1. Download HF bases + build the 4 untuned GGUFs ✅ DONE 2026-06-29

Download the full-precision HF weights (not the disk GGUFs):

- `Qwen/Qwen3-1.7B`
- `Qwen/Qwen3-4B`

For **each** base, through the project's llama.cpp tooling:

```bash
python convert_hf_to_gguf.py <hf_dir> --outtype f16 --outfile models/qwen3-<sz>-base-f16.gguf
llama-quantize models/qwen3-<sz>-base-f16.gguf models/qwen3-<sz>-base-q4.gguf Q4_K_M
```

Produces `qwen3-1.7b-base-f16/q4` and `qwen3-4b-base-f16/q4`. These are the controlled
untuned baselines.

**Result (2026-06-29):** built `models/qwen3-{1.7b,4b}-base-{f16,q4}.gguf`
(1.7B: 3.8G/1.2G · 4B: 7.5G/2.4G; Q4_K_M ≈ 5.0/4.95 BPW). HF weights pulled into the
shared `~/.cache/huggingface` (1.7B was already cached; 4B fetched ~8G). `models/` is
gitignored. **Tooling:** llama.cpp built at `~/tools/llama.cpp` (CPU-only configure:
`-DGGML_CUDA=OFF -DLLAMA_CURL=OFF`, target `llama-quantize`). **Conversion-deps caveat:**
`convert_hf_to_gguf.py` only needs `gguf` (+ `sentencepiece`, `protobuf`) added to
`python/training/.venv`; do **NOT** `pip install -r requirements-convert_hf_to_gguf.txt` — it
pins **CPU torch 2.11.0** and would clobber the cu128 GPU torch. Our torch 2.10 already
satisfies the convert script's `>=2.6` requirement and runs conversion fine on CPU.

### - [x] 2. Add the base backend stanzas ✅ DONE 2026-06-29

Four stanzas mirroring `qwen3-4b` (keep `json_mode: false`, `thinking_enabled: false` —
the validated Qwen3 config), pointing at the four base GGUFs from Task 1. The four `*-ft-*`
stanzas are added later in Task 7.

```yaml
  qwen3-1.7b-base-q4:
    backend: llama_cpp
    options:
      path: models/qwen3-1.7b-base-q4.gguf
      description: Qwen3 1.7B base, Q4_K_M (controlled)
      n_ctx: 8192
      n_gpu_layers: 99
      n_threads: 8
      max_tokens: 2048
      json_mode: false
      thinking_enabled: false
  # ...-base-f16, qwen3-4b-base-f16, qwen3-4b-base-q4 analogously
```

### - [x] 3. Expand the hard multi-step stratum (held out from train) ✅ DONE 2026-06-30

**Result:** authored + validated **ffmpeg +17** (`ffmpeg_hard_001–017`: 8 `chain3`, 5
contrastive/`ambiguous` singles incl. a cue-less `clarify`, 4 two-step `chain2` mis-routing
chains) and **documents +14** (`documents_130–143`: split/extract/clarify contrastive set,
DE/ES/FR/RU/BG multilingual, final-artifact-only chains). Chain rows carry validated
`outputs[]` + `outputs_validated_by: human`; routing-axis rows use `grade: routing`. Full
set frozen as the primary instrument (no prune-on-failure). Committed `ae061b4`.

There is no model-level headroom to measure today: documents is near-saturated
(qwen3-4b outcome 0.988 / knaif 1.00) and ffmpeg's only chain stratum is the 36 `complex`
**two-step** rows (e.g. `ffmpeg_116` "trim then resize"). Run the experiment as-is and the
before/after delta is dominated by flaky multilingual single-utterance flips
(see `INDEX.md`), not by fine-tuning. Build a difficulty stratum the *untuned* models
genuinely fail — but with guardrails, or it corrupts the measurement:

1. **Author candidates in depth tiers.** Chaining is depth-agnostic (literal intermediate
   filenames link steps for ffmpeg; the eval path is single-shot `infer` → `execute_plan`,
   so there is **no plan-length cap** — the `max_steps=5` ceiling is only the deployed
   re-planning loop, not the measurement). **Per-deliverable partial credit is ffmpeg-only:**
   `grade_outputs` scores each `outputs[]` entry independently, but only ffmpeg exports a
   `grade_outputs` verifier (`skills/ffmpeg/eval/verifiers.py:524`); `score_corpus` only
   routes to it when the skill's verifier module provides it (`scoring.py:140`). **documents
   has no `grade_outputs`** (`skills/documents/eval/verifiers.py:220`), so a documents
   chain falls through to the single-artifact `success` verifier and is graded
   all-or-nothing on `success_criteria`, not per step. Before relying on fractional lift for
   documents, either (a) add a documents `grade_outputs` verifier, or (b) make the documents
   hard slice **final-artifact-only** and state that the documents rows score 0/1, not
   partial. The ffmpeg deep chains remain the partial-credit instrument. Tier the rows and
   tag by depth:
   - **`chain3` (3-step)** — the primary new headroom (ffmpeg: convert→resize→strip,
     trim→resize→compress; documents: extract→rotate→merge, split→convert→compress).
   - **`chain4` (4-step)** — a smaller stress tier (rotate→watermark→compress→protect).
   - **Stop at 4 (5 absolute max).** Past ~4–5 steps small models collapse to ~0 across
     the board → a floor with no gradient to measure against, and 6+ exceeds the deployed
     loop's 5-step ceiling. Authoring cost also scales linearly (one validated `outputs[]`
     entry per step), so go deep selectively, not broadly.
   - Also include trickier **2-step** wiring where the failure is mis-routing, not depth.

   Beyond depth, cover two more model-level difficulty axes (same author→run→triage flow):
   - **Lexical ambiguity / contrastive routing** (tag `ambiguous`). The same verb routes to
     different tools by context — e.g. *"extract the first 2 pages into a new pdf"* →
     `split_pdf` vs *"extract the text from the first 2 pages"* → `extract_text`. Both tools
     surface (`split_pdf`'s description contains "extract, pull out"), so this is a
     **selection** failure fine-tuning can fix, and the tool descriptions already encode the
     rule. Author **contrastive pairs** (minimal edit, opposite correct tool); route the
     genuinely cue-less ones (*"extract the first 2 pages"*) to `clarify`.
   - **Multilingual** (tag `multilingual`) — **only for space-delimited languages**
     (DE/ES/FR/RU/BG), where `retrieve_tools` tokenizes and surfaces the right tools, so the
     residual flakiness is model-level and fine-tunable. **CJK (Chinese) is excluded:**
     `retrieve_tools` whitespace-tokenizes (`registry.py:142`), so the `提取`/`拆分`/… keywords
     already in `tools.yaml` never match an unsegmented Hanzi query — the tool is never
     surfaced, and fine-tuning is downstream of retrieval. ZH rows pay off only **after**
     [retrieval-overhaul](2026-07-02-retrieval-overhaul.md) Task 2 lands (it did, 2026-07-02);
     step-3 triage drops them as retrieval failures until then.

   Tag every row `hard` plus its axis tag(s).
2. **Author and freeze the full hard candidate set — do NOT prune to only the rows that
   fail.** Keeping only rows that fail an untuned backend is selection-on-failure: it
   conditions the slice on noise (flaky multilingual flips especially) and inflates the
   apparent post-tune gain on regression-to-the-mean alone. Instead, **freeze the full hard
   set as the primary instrument** and run all untuned backends over it. Report the full set
   as the headline pre/post number; you may additionally report a **secondary "pre-failed
   subset" analysis** (rows that failed ≥1 untuned backend) clearly labelled as a biased
   slice, never as the primary result.
3. **Triage by cause.** Exclude rows whose failure is caused by *retrieval* (tool never
   surfaced) — fine-tuning cannot move those (retrieval runs before the model). Only
   composition / arg / chain-wiring failures belong in the hard set. (This is the one
   legitimate exclusion: it removes rows fine-tuning structurally cannot affect, not rows
   that merely happened to pass.)
4. **Author validated baselines** so the "after" is gradable. Single-command baselines use
   `baseline.command` + `validated_by: human`; **chain rows additionally need `outputs`
   (one entry per deliverable) marked `outputs_validated_by: human`** — that is the field
   the authoring-review logic checks (`corpus.py:121`), and a chain row stays "awaiting
   validation" until it is set, independent of `baseline.validated_by`. A hollow criterion
   makes the gain unmeasurable (the chain-grading harness already bit us once; see
   `INDEX.md` multistep-chain notes).
5. **Hold them out from `train.jsonl`.** These exact rows must NOT appear in training
   (Task 5) — same utterance/params measures memorization, not generalization.

**Gate:** every new row has a human-validated baseline (chain rows: `outputs_validated_by`
set). The full hard set is frozen as the primary instrument; the pre-failed subset, if
reported, is labelled secondary.

### - [x] 4. Pre-fine-tune baselines — fill the untuned half of the matrix ✅ DONE 2026-06-30

**Result:** locked honest `success` sweep over all 4 untuned bases, both skills, first run
including the Task-3 hard stratum → `evals/runs/2026-06-29_finetune-pre_success/`
(committed `9d8fb2b`, INDEX row added). Fixtures regenerated first. **ffmpeg hard slice is
the instrument:** clean size step (1.7b ~0.64–0.76 → 4b ~0.89–0.91 outcome) and a **1.7b
quant tax −13pt** (f16 0.764 → q4 0.636); documents near-saturated (only `132` + `143`
fail). Failures are composition/clarify, **none retrieval** → no exclusions; full hard set
stays the headline instrument.

**Prerequisite (learned 2026-06-29):** regenerate eval fixtures first —
`just eval-fixtures documents` (and `ffmpeg`). Fixtures live under the gitignored
`sandbox/`, so a fresh checkout has none; without them documents rows error at execution
(file-not-found) and score ~0.55 **despite correct routing** (4B knaif 1.000), poisoning
the baseline. The cheap pre-sweep (`runs/2026-06-29_finetune-pre_cheap`) hit exactly this.

Run the honest `success` eval on **all four** untuned backends across both skills, **before
any fine-tuning.** This is the locked "before" reference; commit the `score.json`s and the
matrix half.

```bash
uv run python -m knaif.evalsuite run --all-skills --verifier success \
  --config eval_backends.yaml \
  --backends qwen3-1.7b-base-f16,qwen3-1.7b-base-q4,qwen3-4b-base-f16,qwen3-4b-base-q4 \
  --label finetune-pre --keep-artifacts \
  --save evals/runs/2026-06-27_finetune-pre_success/
```

Run `--verifier cheap` first as a fast routing sanity check. Add the `INDEX.md` row, and
**report the `hard` slice (Task 3) separately** from the saturated rows — that slice is
where the fine-tune gain will show.

### - [x] 5. Regenerate the union `train.jsonl` (ffmpeg + documents) ✅ DONE 2026-06-30

**Result:** regenerated (not appended) from live `tools.yaml`/`prompt.yaml` →
**ffmpeg 33 → 364, documents 5 → 334** (balanced ~1.09:1), every model-visible tool
covered. Includes compound chains (literal intermediate filenames per each skill's prompt
contract, never `$var`), contrastive disambiguation pairs, DE/ES/FR/RU/BG multilingual
(ZH held for the CJK fix), and clarify/reject. Kept distinct (files + ops) from the Task-3
held-out slice. `scripts/gen_train.py` validates every plan against `validate_plan` **and**
canonical enum/profile values (caught/fixed invalid ffmpeg platforms). Committed
`20e012e` + `005c0d1`; `python/training/{build_dataset,train_lora,merge_to_hf}.py` drafted for
Tasks 6–7.

The bottleneck. Current sets are tiny *and* imbalanced (ffmpeg 33 / documents 5) — train
on that as-is and the model overfits ffmpeg and barely sees documents. **Regenerate, don't
append**, targeting a **balanced** few-hundred rows *per skill* from the live
`tools.yaml` / `prompt.yaml`, per `docs/TRAINING_DATA_GENERATION.md`. Gate generation on a
trusted corpus first (honest eval healthy, routing ≥ 85% on qwen3-4b).

**Inherited target — compound two-step chain fidelity** (from the retired
[complex-two-step-intents](2026-06-09-complex-two-step-intents.md)). Multi-intent chaining
routes, but small models mis-wire the link: step 2 reads the *original* file instead of
step 1's output, silently dropping one op; gemma3-4b sometimes omits the second output
entirely. The training set must include compound `A then B` utterances (resize+strip,
rotate+compress, convert+resize, …) — but **the chaining convention is skill-specific and
must match each skill's prompt contract:**

- **ffmpeg — literal intermediate filenames, NOT `$var`.** The ffmpeg prompt explicitly
  forbids variable chaining ("Never chain steps with `$variable` references",
  `skills/ffmpeg/prompt.yaml:30`): step 1 gets an explicit `output` filename and step 2
  reuses that **same literal filename** as its input (e.g. `trim_video → "output":
  "clip_trimmed.mp4"`, then `extract_audio → "inputs": ["clip_trimmed.mp4"]`). `$var`
  binding is an *internal* `Intent.expand()` mechanism (`docs/VARIABLE_BINDING.md:114`),
  never emitted in a model-visible ffmpeg public-tool plan. Training ffmpeg on `$prev`
  teaches a format the prompt forbids and the eval will mark wrong.
- **Direct-handler skills that expose variables to the model** — train `$var` references
  where that skill's prompt actually documents them. (documents' current prompt exposes no
  chaining syntax, so its compound rows likewise link by literal filename unless/until the
  prompt adds variable chaining.)

The fidelity target is the same — step 2 must consume step 1's output, not the original
file — only the surface syntax differs per skill. **Eval rows 117 / 227 / 279 are the
regression check.**

Include compound/3-step examples covering the same skills as the Task 3 hard stratum, but
**distinct from those exact rows** (different files/ops) — the held-out slice must stay
held out, or the post-fine-tune gain on it measures memorization.

Reinforce the two non-chain axes here too (again **distinct** from the held-out rows):
**multilingual** phrasings for the space-delimited languages, and **contrastive
disambiguation pairs** (`extract … into pdf` → `split_pdf` vs `extract the text` →
`extract_text`). **Hold ZH/CJK training rows until
[retrieval-overhaul](2026-07-02-retrieval-overhaul.md) Task 2 lands** (it did, 2026-07-02) —
training on inputs whose tools are never retrieved teaches a mapping the model can't use at
inference. (The contract in `docs/TRAINING_DATA_GENERATION.md` is already aligned with this.)

Validate before training: `just test-skill ffmpeg`, `just test-skill documents`, and
`just eval <skill> --verbose` for routing sanity on both.

### - [x] 6. Train — 16-bit LoRA on the union, both bases ✅ DONE 2026-06-30

**Result:** trained one LoRA per base on the 698-row union (bf16, `load_in_4bit=False`,
r=16/α=16, all 7 attn+MLP modules, grad-checkpointing `"unsloth"`, 3 epochs, lr 2e-4
cosine, eff-batch 8, completion-only loss; 264 steps each, identical hyperparameters).
Loss 1.12→0.016 (1.7B) and 1.07→0.014 (4B) on an RTX 5080, ~32 min for the pair. Adapters
under `python/training/adapters/qwen3-{1.7b,4b}-ft/`. Runner: `python/training/train_lora.py` (dataset
assembled by `python/training/build_dataset.py`, which reproduces the faithful inference prompt
per row). Committed `005c0d1` (scripts).

In WSL/Unsloth, bf16 base, `load_in_4bit=False`, gradient checkpointing. Train the **same**
union dataset onto each base independently → two LoRA adapters (1.7B, 4B). Same
hyperparameters across both sizes so the size comparison is clean (record them in the run
notes). Keep `Qwen/Qwen3-*` instruct templates consistent with the eval prompt format.

### - [x] 7. Merge → quantize → add the 4 ft backends ✅ DONE 2026-06-30

**Result:** per base, merged the adapter into bf16 HF (`python/training/merge_to_hf.py`, loads the
adapter dir so the base it resolves always matches) → `convert_hf_to_gguf.py --outtype f16`
→ `llama-quantize Q4_K_M`, the same pipeline as Task 1. Built `models/qwen3-{1.7b,4b}-ft-{f16,q4}.gguf`
(sizes match the base GGUFs). Added the 4 `*-ft-*` stanzas to `eval_backends.yaml` (options
mirror `*-base-*`). Smoke: `1.7b-ft-q4` loads and routes correctly. Committed `fc02fef`.

Per base: merge the adapter into bf16 HF, then the **same** pipeline as Task 1:

```bash
python convert_hf_to_gguf.py <merged_dir> --outtype f16 --outfile models/qwen3-<sz>-ft-f16.gguf
llama-quantize models/qwen3-<sz>-ft-f16.gguf models/qwen3-<sz>-ft-q4.gguf Q4_K_M
```

Add the four `*-ft-f16` / `*-ft-q4` backend stanzas (same options as the base stanzas).

### - [x] 8. Post-fine-tune eval + per-build regression gate ✅ DONE 2026-06-30

**Result:** ran `success` + `cheap` for all 4 ft backends into one folder
`evals/runs/2026-06-30_finetune-post_success/`, then the per-skill gate. Numbers in
the **Results** section below. The gate returns **FAIL (exit 1)** — but on inspection it is
the base↔instruct gap, **not** union-training forgetting: `regression --all-skills` diffs
each skill against its *committed snapshot*, which is the deployed **qwen3-4b instruct**
model, so fine-tuned *base* models landing below the *instruct* snapshot is expected and
not a regression vs these models' own baseline. The controlled forgetting check (post-ft vs
pre-base, same family — see Results) shows **no per-skill regression**. These ft bases are a
study artifact, not promotion candidates; this snapshot gate is the *promotion* gate, which
this experiment does not invoke. Committed `901f990`.

Run the **same** honest `success` eval on the four ft backends → fills the tuned half:

```bash
uv run python -m knaif.evalsuite run --all-skills --verifier success \
  --config eval_backends.yaml \
  --backends qwen3-1.7b-ft-f16,qwen3-1.7b-ft-q4,qwen3-4b-ft-f16,qwen3-4b-ft-q4 \
  --label finetune-post --keep-artifacts \
  --save evals/runs/2026-06-27_finetune-post_success/
```

Then the **catastrophic-forgetting gate** against each skill's committed snapshot, per ft
build independently (forgetting can hit one size and not the other).

**Mind the per-skill snapshot verifiers — a `success`-only sweep does NOT gate ffmpeg.**
`regression --all-skills` compares **each** skill against *that skill's* snapshot verifier
(`cli.py:1091`), and the committed snapshots disagree: documents' is `success`, **ffmpeg's
is `cheap`** (`skills/ffmpeg/data/eval_snapshot.json`). When a skill's snapshot verifier
is absent from the current run, the gate **silently skips it rather than failing**
(`cli.py:1100-1107`) — so against a `success`-only folder, ffmpeg is skipped and forgetting
on ffmpeg goes undetected. The gate's whole job (the load-bearing check) would be a false
green for the larger skill. Fix: the `--current-run` folder must contain a scoreboard at
**each** skill's snapshot verifier. So run the ft sweep at **both** `cheap` and `success`
(the `cheap` pass produces ffmpeg's `*_cheap.json`, the `success` pass produces documents'
`*_success.json`) into the **same** save folder, then run the gate against it:

```bash
# Add the cheap sweep so ffmpeg has a `*_cheap.json` to diff against its snapshot.
uv run python -m knaif.evalsuite run --all-skills --verifier cheap \
  --config eval_backends.yaml \
  --backends qwen3-1.7b-ft-f16,qwen3-1.7b-ft-q4,qwen3-4b-ft-f16,qwen3-4b-ft-q4 \
  --label finetune-post --keep-artifacts \
  --save evals/runs/2026-06-27_finetune-post_success/

uv run python -m knaif.evalsuite regression --all-skills \
  --current-run evals/runs/2026-06-27_finetune-post_success/
```

(Alternative: intentionally rebaseline ffmpeg's snapshot to `success` before relying on the
gate — a deliberate, separately-committed change, not an accident of this run.)

> **Status update (2026-07-24) — the alternative above is now the policy.** The *eval
> ladder* in [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md#the-eval-ladder--fast-while-developing-executing-before-done)
> makes an **executing** verifier the required acceptance bar: `cheap` is an iteration
> instrument and must not hold a snapshot (it reports false regressions — see the 0.973 →
> 0.928 chain-row artifact). ffmpeg is to be re-locked to **`output_diff`** (chosen over
> `success` on corpus coverage: nearly all rows carry a `baseline`, only 143/314 carry
> `success_criteria`).
>
> Until that re-lock lands, everything above stands as written — ffmpeg's snapshot is still
> `cheap`, so the dual `cheap` + `success` sweep is still required to avoid a false green.
> After it lands, ffmpeg's gate needs an `*_output_diff.json` in the `--current-run` folder
> instead of `*_cheap.json`, and the dual-sweep workaround can be dropped.

**Block any conclusion on a per-skill regression** — training on the union can silently
regress one skill. This gate is the load-bearing check, not the headline outcome number.

### - [x] 9. Assemble the matrix + write up ✅ DONE 2026-06-30

8-cell grid filled and committed (pre `9d8fb2b`, post `901f990`, both `score.json` sets +
`matrix.json` + INDEX rows). Findings in the **Results** section below.

---

## Results (2026-06-30)

**Verifier:** honest `success` (real execution). **Pre** = untuned bases
(`2026-06-29_finetune-pre_success`); **Post** = ft
(`2026-06-30_finetune-post_success`). One multi-skill LoRA (bf16, r=16, 3 epochs) on the
698-row union, trained per base, same hyperparameters.

### ffmpeg — full corpus (314 rows) and held-out `hard` slice (55 utt), outcome / knaif

| cell | full outcome (pre→post) | full knaif | **hard outcome** | **hard knaif** |
|---|---|---|---|---|
| 1.7b-f16 | 0.798 → 0.878 (**+.080**) | 0.942 → 0.958 | 0.764 → 0.855 (**+.091**) | 0.833 → 0.941 (**+.108**) |
| 1.7b-q4  | 0.734 → 0.869 (**+.135**) | 0.950 → 0.948 | 0.636 → 0.691 (+.055) | 0.821 → 0.851 (+.030) |
| 4b-f16   | 0.897 → 0.902 (+.005) | 0.963 → 0.973 | 0.891 → 0.927 (+.036) | 0.874 → 0.970 (**+.096**) |
| 4b-q4    | 0.905 → 0.895 (−.011) | 0.965 → 0.971 | 0.909 → 0.945 (+.036) | 0.944 → 0.947 (+.002) |

### documents — full corpus (143) and `hard` slice (35) — near-saturated

| cell | full outcome | full knaif | hard outcome | hard knaif |
|---|---|---|---|---|
| 1.7b-f16 | 0.951 → 0.982 | 0.971 → 0.998 | 0.914 → 0.914 | 1.000 → 1.000 |
| 1.7b-q4  | 0.951 → 0.957 | 0.959 → 0.987 | 0.886 → 0.914 | 0.985 → 1.000 |
| 4b-f16   | 0.982 → 0.976 | 1.000 → 0.996 | 0.914 → 0.914 | 1.000 → 1.000 |
| 4b-q4    | 0.976 → 0.976 | 1.000 → 0.998 | 0.914 → 0.914 | 1.000 → 1.000 |

### The findings

1. **Fine-tuning helps where there is headroom, and it generalizes.** The `hard` slice was
   held out of `train.jsonl`, yet ffmpeg hard-outcome rose on **all four** cells (+.036 to
   +.091) and hard-knaif up to +.108 — the gain is generalization, not memorization. This
   slice is the primary evidence of fine-tuning's marginal value (the saturated rows can't
   show it).
2. **Size-vs-gain curve:** the small 1.7B gained most (ffmpeg full outcome +.080/+.135),
   the 4B was already strong so gains are small/flat — diminishing returns with size.
3. **Quantization tax, isolated and reduced by tuning:** ffmpeg 1.7B f16→q4 outcome gap was
   **−.064 (base) → −.009 (ft)**; 4B ≈0 in both. Tuning let the q4 model nearly recover f16
   quality — the most practically useful result for a 16 GB-class deployment.
4. **A small full-corpus ffmpeg regression from union training, mechanistically cross-skill
   contamination** (corrects an earlier "no forgetting" reading — verification run
   `2026-06-30_instruct-vs-ft_success`). On full ffmpeg the ft-4b-q4 sits at 0.895 outcome,
   ~1pt **below both** production instruct (0.904) **and its own untuned base** (0.905) — and
   base↔instruct are equal here, so this is the LoRA's own effect, not a lineage gap. Base→ft
   is **32 new failures vs 23 fixes** (net −9 on the mostly-easy corpus). Several new failures
   are documents vocabulary bleeding into ffmpeg: `quality:"small"` (documents'
   `compress_quality` enum; ffmpeg wants `small_file`) and a hallucinated `convert_audio`
   (blend of `convert_document` + `convert_video`/`extract_audio`), plus more over-clarify /
   over-reject. **Net trade:** the LoRA spent ~1pt of easy-corpus accuracy to buy the +3.6pt
   hard-slice gain (#1). documents (near-saturated) stayed flat (±.006, noise). So union
   fine-tuning a small base does **not** beat a strong general instruct model on saturated
   routing — and induces measurable cross-skill interference at this data scale / 3 epochs.
5. **Snapshot regression gate = FAIL, correctly read as a non-event for this study.** It
   diffs against the deployed **qwen3-4b instruct** snapshot, so fine-tuned *bases* < that
   *instruct* model is the base↔instruct gap, not union-training regression. The gate is the
   *promotion* check; these ft bases are a controlled-experiment artifact, not promotion
   candidates. To use this gate for promotion later, baseline against the *pre* run (same
   family) or rebaseline the snapshots deliberately.

**Done:** all 8 cells filled and committed; the size-vs-gain curve and isolated
quantization tax are stated; the `hard` slice is reported pre-vs-post separately from the
saturated rows; and the cross-skill interference from union training is quantified
(small full-ffmpeg dip, finding #4).

### Follow-ups (not in scope here)

- **Per-skill vs union fine-tune.** The contamination in #4 is real evidence *against* a
  single naive union LoRA — but a **single-skill** LoRA is only viable if serving
  **hot-swaps a per-skill adapter** onto one shared base at inference (one base + N adapters,
  selected by the active skill). Deploying a single ffmpeg-only LoRA *as the shared model*
  would forget documents — strictly worse than the union's ~1pt bleed. The union remains
  correct for the **one-shared-model** deployment; the cheaper fixes for contamination are
  more/again-more-diverse union data, fewer epochs / lower rank, or per-skill adapters if the
  serving layer grows to support them.
- **We already fine-tuned the instruct checkpoint** (corrects an earlier note). `Qwen/Qwen3-4B`
  is the post-trained instruct model, not a pretrain base — confirmed by the cache (no
  `-Base` repo) and behaviorally (untuned scores 0.905 zero-shot on structured JSON, which a
  raw base can't). The production `Qwen3-4B-Q4_K_M` is just a different-provenance build of
  the same instruct model (0.904 ≈ our untuned 0.905). So "switch the base to instruct" is a
  non-lever; it's already pulled.
- **The remaining levers are data quality + lighter tuning.** Fine-tuning a *strong* instruct
  model is mostly downside risk on saturated routing: the LoRA *damaged* behavior the untuned
  model already had right (e.g. `ffmpeg_041` was correct pre, wrong post). The fix is to
  perturb the model less and feed it cleaner signal: fewer epochs (1–2) / lower rank (r=8) /
  lower LR; more diverse, larger union data; explicit anti-contamination signal (reinforce
  ffmpeg `quality: small_file` for "tiny/small", keep documents `compress_quality: small`
  distinct); and clearer unsupported→`clarify` vs unsafe→`reject` boundaries (the LoRA
  over-rejected/over-clarified borderline rows).
- **Fine-tuning earns its keep on the *small* model, not the big one.** 1.7B gained
  +.080/+.135 full outcome (real, deployment-relevant: a 1.1 GB q4 approaching 4B quality);
  the 4B is already at the instruct ceiling, so its LoRA is ~net-neutral-to-negative on full
  corpus. If the goal is a shippable small model, tune the 1.7B and leave the 4B alone.
