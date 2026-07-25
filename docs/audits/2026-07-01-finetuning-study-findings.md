# Fine-Tuning & Model-Selection Study — Findings and Handoff

> **What this doc is:** the **historical experiment log** for the fine-tuning study
> (passes 1–3) — the full narrative, every run's numbers, and the **retracted-claim lessons**
> (kept on purpose; they are the most instructive part). It is *not* the how-to.
> **If you want to run a fine-tune, start at [FINE_TUNING.md](../FINE_TUNING.md)** — the canonical
> procedure + rules + outcomes. This doc is the "why / full history" that FINE_TUNING.md
> distills. Keep it; do not delete.

**Compiled:** 2026-07-01 (pass-3 addendum §10: 2026-07-02) · **Status:** historical record
**Source plans:** [2026-06-27-fine-tuning.md](../plans/2026-06-27-fine-tuning.md) (Done),
[2026-06-30-best-skill-model.md](../plans/2026-06-30-best-skill-model.md) (Done),
[2026-07-01-qwen3-ffmpeg-max-results.md](../plans/2026-07-01-qwen3-ffmpeg-max-results.md),
[2026-07-02-qwen3-finetuning-pass3.md](../plans/2026-07-02-qwen3-finetuning-pass3.md)
**All raw results:** `evals/runs/` (folders named below); index in `evals/INDEX.md`

---

## 0. How to use this document

This is a **self-contained record** of a fine-tuning / model-selection investigation for the
`knaif` agent (natural language → validated JSON tool-plan). It exists so the same job can be
**handed to a different agent** and its results compared against this baseline. It contains:
the goal, the setup, every experiment + result, the conclusions (including **two I made and
then retracted** — keep those, they are the most informative for benchmarking another agent),
the open questions, and a reproduction guide.

**If you are an agent picking this up:** the highest-value open task is Section 8.1 (a
purpose-built ffmpeg training set to break parity). Do not trust a single run on a small
slice — see the **methodology warnings** in Section 3.

---

## 1. The system under test

- **knaif**: converts an utterance into `{"plan": [{"tool","args"}...]}`. A small local LLM
  proposes the plan; deterministic code validates/executes it. The model only needs to **route**
  to the right tool(s) and fill valid args.
- **Skills:** `ffmpeg` (video/audio ops, 13 model-visible tools) and `documents` (PDF/doc ops,
  15 tools). Each skill builds a prompt containing **only its own tools** (retrieved per
  utterance), so at inference the model never sees the other skill's tools.
- **Deployment constraint:** **one shared model serves every skill.** Therefore fine-tuning is
  on the **union** of all skills' training data — a per-skill fine-tune deployed as the shared
  model would catastrophically forget the other skill. (A single-skill *app* relaxes this — see
  Section 7.)
- **Two data files per skill:** `data/train.jsonl` (utterance→plan pairs the model **learns**
  from) and `data/eval.jsonl` (the **benchmark**, never trained on). They are distinct.

## 2. Hardware & tooling

- **GPU:** RTX 5080, 16 GB, **Blackwell (sm_120)**, driver 610.62, on **WSL2**.
  ⚠️ Blackwell+WSL2 is fragile: repeated CUDA faults can crash the Windows display driver (TDR).
  **On any `CUDA: illegal memory access`, stop — do not auto-retry.** A reboot clears it.
- **Training:** Unsloth, **bf16 LoRA** (`load_in_4bit=False`), in `python/training/.venv` (Python 3.12).
- **Quantize/convert:** llama.cpp at `~/tools/llama.cpp` (`convert_hf_to_gguf.py`, `llama-quantize`).
- **Scripts** (all in `python/training/` and `scripts/`):
  - `scripts/gen_train.py` — generates `train.jsonl` from live `tools.yaml`/`prompt.yaml`;
    validates every plan against `validate_plan` **and** enum/profile values.
  - `python/training/build_dataset.py --skills <s> --out <f>` — builds the chat dataset, reproducing the
    **exact inference prompt** per row (retrieval + skill header). Core venv (`uv run`).
  - `python/training/train_lora.py --base <hf> --data <f> --rank --epochs --lr --out` — bf16 LoRA,
    completion-only loss.
  - `python/training/merge_to_hf.py --adapter --out` — merge LoRA → bf16 HF (loads adapter dir so base
    always matches).
  - `python/core/tests/test_train_data_integrity.py` — guards `train.jsonl` against cross-skill leakage.
- **Eval:** `uv run python -m knaif.evalsuite run --skill <s>|--all-skills --verifier success
  --backends <name> --save evals/runs/<date>_<label>_success`. Backends in
  `eval_backends.yaml`.

## 3. Methodology & metric definitions (read before trusting numbers)

- **`success` verifier:** routes the model, **executes the plan for real** against fixtures, and
  grades the produced artifact. The honest metric. (`cheap` = routing only, no execution.)
- **`outcome` / full-corpus outcome:** fraction of utterances with the correct outcome
  (right tool/plan). ffmpeg full corpus ≈ **846 utterance-evals**, documents ≈ **164**.
- **`knaif` score:** partial-credit quality of the produced plan/artifact (0–1).
- **`hard` slice:** a **held-out** stratum of deliberately hard rows (ffmpeg **55** utt: 3-step
  chains, contrastive/ambiguous routing, clarify; documents **35** utt). **It is held out of all
  `train.jsonl`** so post-tune gains on it measure *generalization, not memorization*. **This is
  the differentiator** — the full corpus is near-saturated.
- ⚠️ **Two noise sources that bit us:**
  1. **Hard slice n is tiny (55 / 35).** A 3–4pt move ≈ 2 rows. Do not over-read single runs.
  2. **Q4 quantization adds ≈ ±15pt of hard-slice noise.** Config/data comparisons **must be done
     at f16** (or a high quant), not q4. (We learned this the hard way — Section 6.3.)

## 4. Models evaluated (naming key)

| name | what it is |
|---|---|
| `qwen3-4b` | **production** `Qwen3-4B-Q4_K_M.gguf` (prequantized download) |
| `qwen3-4b-base-{f16,q4}` | Qwen3-4B **instruct**, untuned, *our* HF→f16→Q4 build (≈ production) |
| `qwen3-1.7b-base-{f16,q4}` | Qwen3-1.7B instruct, untuned, our build |
| `qwen3-{4b,1.7b}-ft-{f16,q4,q5,q6,q8}` | + union LoRA (v1 data) |
| `qwen3-1.7b-ftv2*` | + union LoRA (v2 data — **rejected**) |
| `qwen3-1.7b-ffmpeg-q6` | single-skill ffmpeg-only LoRA, **v2 data** (confounded) |
| `qwen3-1.7b-ffmpegv1-q6` | single-skill ffmpeg-only LoRA, **v1 data** (clean) |
| `gemma3-4b` | `gemma-3-4b-it-Q4_K_M`, untuned |

**Note:** `Qwen/Qwen3-4B` (and 1.7B) are the **instruct** models, not pretrain bases — confirmed
(no `-Base` in HF cache; they score 0.9 zero-shot on structured JSON, which a raw base cannot).
"base" in our backend names means "untuned baseline," not Qwen3-Base.

## 5. Master results table (`success` verifier)

GGUF sizes (q4/q5/q6/q8 for 1.7B; q4 for 4B): 1.7B Q4 1.03 / Q5 1.17 / Q6 1.32 / Q8 1.71 GB;
4B-Q4 2.33 GB; 1.7B-f16 3.4; 4B-f16 8.0. Latency = ffmpeg median ms (RTX 5080 — **not** mobile).

> **⚠️ The `ff ms` column is RTX-5080-only.** The project moved to an RTX 3070 Laptop on
> 2026-07-14, which is **~3–4× slower** (4B: 350 → 1352 ms; 1.7B-Q6: 214 → 655 ms, both measured).
> **Quality columns are unaffected** — accuracy does not move with hardware. See
> [PERFORMANCE.md](../PERFORMANCE.md).

| model | size | ff full | ff hard | doc full | doc hard | ff ms |
|---|---|---|---|---|---|---|
| **qwen3-4b-base-q4 (incumbent)** | 2.33 | **0.905** | **0.909** | 0.976 | 0.914 | 368 |
| qwen3-4b (production) | 2.33 | 0.904 | 0.909 | — | — | ~370 |
| qwen3-4b-ft-q4 (union LoRA) | 2.33 | 0.895 | 0.945 | 0.976 | 0.914 | ~370 |
| qwen3-4b-base-f16 | 8.0 | 0.897 | 0.891 | 0.982 | 0.914 | — |
| qwen3-4b-ft-f16 | 8.0 | 0.902 | 0.927 | 0.976 | 0.914 | — |
| gemma3-4b (untuned) | 2.32 | 0.857 | 0.891 | 0.970 | 0.914 | **1515** |
| qwen3-1.7b-base-q4 (untuned) | 1.03 | 0.734 | 0.636 | 0.951 | 0.886 | ~200 |
| qwen3-1.7b-base-f16 | 3.4 | 0.798 | 0.764 | 0.951 | 0.914 | — |
| qwen3-1.7b-ft-q4 (union) | 1.03 | 0.869 | 0.691 | 0.957 | 0.914 | ~200 |
| qwen3-1.7b-ft-q5 (union) | 1.17 | 0.871 | 0.818 | 0.976 | 0.914 | ~200 |
| **qwen3-1.7b-ft-q6 (union)** | **1.32** | 0.881 | 0.873 | 0.976 | 0.914 | **198** |
| qwen3-1.7b-ft-q8 (union) | 1.71 | 0.881 | 0.800 | 0.988 | 0.943 | ~210 |
| qwen3-1.7b-ft-f16 (union) | 3.4 | 0.878 | 0.855 | 0.982 | 0.914 | — |
| qwen3-1.7b-ffmpegv1-q6 (ffmpeg-only) | 1.32 | 0.868 | 0.873 | n/a | n/a | ~198 |
| qwen3-1.7b-ffmpeg-q6 (ffmpeg-only, v2 — confounded) | 1.32 | 0.884 | 0.836 | n/a | n/a | ~198 |

Per-run scoreboards: `finetune-pre_success`, `finetune-post_success`, `instruct-vs-ft_success`,
`1.7b-quant-sweep_success`, `gemma3-baseline_success`, `datav2-config{,-f16}_success`,
`ffmpeg-only_success`, `ffmpeg-only-v1_success` (all dated under `evals/runs/`).

## 6. Validated conclusions

1. **Fine-tuning helps where there is headroom, and it generalizes.** The held-out hard slice
   rose on every ffmpeg cell after the union LoRA (base→ft, e.g. 1.7B-q4 hard 0.636→0.691,
   f16 0.764→0.855); 1.7B full ffmpeg +.08/+.135. Gain is generalization (slice held out).
2. **Diminishing returns with size.** The 1.7B (headroom) gained most; the **4B is at the
   instruct ceiling** — tuning it is net-neutral-to-slightly-negative on the full corpus, with a
   better hard slice. The ffmpeg full corpus sits ~0.90 for *both* tuned and untuned 4B → that's
   a **corpus ceiling, not a model ceiling**.
3. **Quantization, not size, is the 1.7B hard-slice bottleneck.** 1.7B-ft ffmpeg hard:
   Q4 0.691 → Q5 0.818 → **Q6 0.873** → Q8 0.800 (non-monotonic = n=55 noise; true ~0.82–0.87).
   The Q4→Q6 jump recovers most of the gap to the 4B (0.909). **Q6 (1.32 GB) is the sweet spot;
   Q8 (1.71 GB) adds nothing and is barely smaller than 4B-Q4.**
4. **Documents is saturated** (≈0.91–0.98 everywhere) — not a differentiator; all signal is ffmpeg.
5. **Gemma3-4B is not competitive** for these skills: worse than Qwen3-4B on ffmpeg (0.857) **and
   4× slower** (1515 ms). Do not fine-tune Gemma3.
6. **Mobile suitability:** 1.7B-Q6 (1.32 GB, ~½ the size, ~2× faster than 4B-Q4) is mobile-ready;
   4B-Q4 (2.3 GB) is high-end-mobile-only (RAM pressure / OS-kill risk / heat). Desktop latency
   does NOT transfer to mobile — needs an on-device benchmark.
7. **Single-skill vs union scoping has no measurable effect on ffmpeg quality (at matched data).**
   ffmpeg-only(v1) and union(v1) are **identical on the hard slice (0.873/0.901)** and a wash on
   the full corpus. No contamination penalty for the union, no transfer benefit either.

## 6b. Retracted / corrected claims (keep — useful for benchmarking)

- ❌ **"Union fine-tuning causes net-harmful cross-skill contamination on ffmpeg."** The union LoRA
  *did* produce a few documents-vocabulary errors (`quality:"small"` vs `small_file`; a
  hallucinated `convert_audio`), but **data v2 + ffmpeg-only experiments showed the effect is
  tiny** and not the deciding factor.
- ❌ **"Data v2 (anti-contamination rows) will improve the model."** It **hurt** the hard slice
  (f16: 0.855→0.800). The ~80 single-op rows diluted the chain/contrastive signal. **v1 data is
  better.** Rejected.
- ❌ **"ffmpeg-only fine-tune beats the union" (my first prediction).** The first ffmpeg-only run
  *looked* worse on the hard slice (0.836) — but that was **confounded** (it used v2 data).
- ❌ **"ffmpeg-only is worse — positive cross-skill transfer" (my second prediction).** Also wrong.
  The clean rerun on matched v1 data is **identical** to the union (0.873). The deficit was 100%
  the v2-data handicap. **Lesson: control the data version; don't conclude from a confounded run.**

## 7. Recommendations / decisions

- **Best general model right now:** **qwen3-4b-base-q4** (≈ production `Qwen3-4B-Q4_K_M`) — best
  full ffmpeg, strong hard slice, 2.3 GB. The safe default; nothing dethrones it on raw quality.
- **Quality-per-byte / mobile pick:** **qwen3-1.7b-ft-q6 (union, 1.32 GB)** — 1 GB smaller, ~2×
  faster, documents matched, ffmpeg within ~2.4pt full / ~3.6pt hard. Adopt when footprint/latency
  matter.
- **For a standalone single-skill ffmpeg app:** the **union 1.7B-Q6 is fine** (equal to an
  ffmpeg-only tune). No reason to ship a dedicated single-skill fine-tune **with current data** —
  but see 8.1.
- **Do not** ship Gemma3, fine-tune the 4B for production, or use data v2.

## 8. Open questions / untested hypotheses (the real next work)

1. **Purpose-built ffmpeg training set (highest value).** v1 got ffmpeg-only to *parity*, not
   superiority — but v1 was designed for *balance across two skills*. An ffmpeg-**optimized** set
   (heavier on 3–4-step chains, contrastive/ambiguous routing, fuller ffmpeg coverage, no
   documents-balance constraint) might push ffmpeg-only **above 0.873** toward the 4B (0.909). The
   one genuinely promising lever for a standalone ffmpeg app. **Untested.**
2. **On-device mobile benchmark.** All latency here is RTX 5080. Measure 1.7B-Q4 vs Q6 tokens/sec
   and memory on the actual target phone (llama.cpp Metal/Vulkan) before committing.
3. **Better union fine-tune recipe.** We only swept r16/3ep vs r8/2ep (the latter worse). A proper
   hyperparameter/data-curriculum search on the union could lift the hard slice without the
   full-corpus cost — uncertain payoff given the 4B is near ceiling.
4. **Corpus ceiling audit.** The ffmpeg full corpus tops out ~0.90 for every strong model. Is the
   residual ~10% real model error or ambiguous-row / criteria noise? An audit would tell whether
   there's hidden headroom at all.
5. **train.jsonl on disk is v2 (the rejected set);** the winning models used v1. If continuing,
   decide whether to revert `gen_train.py`/`train.jsonl` to v1 (`git show 005c0d1:...`).

## 9. Reproduction guide

```bash
# 1. (optional) regenerate training data
uv run python scripts/gen_train.py            # writes skills/*/data/train.jsonl
uv run python python/training/build_dataset.py --skills ffmpeg,documents --out python/training/union_chat.jsonl

# 2. train a LoRA (train venv)  — STOP on any CUDA illegal-memory-access; reboot; do not retry-spam
python/training/.venv/bin/python python/training/train_lora.py --base Qwen/Qwen3-1.7B \
  --data python/training/union_chat.jsonl --rank 16 --epochs 3 --lr 2e-4 --out python/training/adapters/<name>

# 3. merge -> f16 -> quantize
python/training/.venv/bin/python python/training/merge_to_hf.py --adapter python/training/adapters/<name> --out python/training/merged/<name>
python/training/.venv/bin/python ~/tools/llama.cpp/convert_hf_to_gguf.py python/training/merged/<name> --outtype f16 --outfile models/<name>-f16.gguf
~/tools/llama.cpp/build/bin/llama-quantize models/<name>-f16.gguf models/<name>-q6.gguf Q6_K

# 4. add a stanza to eval_backends.yaml, then eval (success = real execution)
uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success \
  --config eval_backends.yaml --backends <name>-q6 --save evals/runs/<date>_<label>_success

# 5. read full + HARD-slice numbers from the saved *_success.json (rows tagged "hard")
```

**Benchmark-an-agent framing:** give an agent Sections 1–4 + 9 and the goal *"find the best model
for these skills under a mobile size budget,"* withhold Sections 5–8, and compare its
conclusions/numbers to this record — especially whether it (a) avoids the q4-noise trap (3.ii),
(b) controls the data-version confound (6b), and (c) finds the purpose-built-ffmpeg lever (8.1).

---

## 10. Pass-3 addendum (2026-07-02)

Plan: [plans/2026-07-02-qwen3-finetuning-pass3.md](../plans/2026-07-02-qwen3-finetuning-pass3.md).
Runs under `evals/runs/2026-07-02_*`. Pass 3 confirmed/corrected the pass-2 candidate,
retrained the 4B, promoted it, and closed two experimental levers.

1. **The pass-2 "+5.4pt hard" headline was Q6 quant-inflation.** Clean matched reads (845
   corpus, `max_tokens:512`) put the honest v3-flat data effect at **hard +3.6pt / chain3
   +6.2pt** (f16), full −0.6pt. The tell: **Q8 scored identically to f16**, so Q6/Q5's higher
   hard numbers are favorable n=55 draws. **chain3 is the robust signal** — +6.2pt (1.7B) /
   +3.1pt (4B), identical across quant levels, from the 32 targeted chain rows. 1.7B deployment
   quant = **Q6**.
2. **4B-v3 retrain PASSES the promotion gate and was PROMOTED** for ffmpeg + documents
   (`models.yaml` `qwen3-4b-v3`; io stays on untuned `qwen3-4b`). vs untuned default: full
   0.905→0.898 (within tolerance; the retrieval fix below lifts it to 0.903), hard +3.6pt,
   chain3 +3.1pt, documents held. **The v3 anti-contamination rows eliminated the old 4B-ft
   enum-bleed** (0/38 regression flips carry a `quality:"small"`/`convert_audio` signature).
3. **Retrieval keyword fixes** (ffmpeg `tools.yaml`): non-CJK misses 70→46, full ffmpeg
   +0.48pt, no mis-routing. CJK (44) still blocked on segmentation. Audit:
   [audits/2026-07-02-ffmpeg-retrieval-miss-triage.md](2026-07-02-ffmpeg-retrieval-miss-triage.md).
4. **Planner-diversity hypothesis NOT supported.** A structurally-different 3rd skill (`io`,
   zero enum overlap, 40 rows) added to the union transferred **nothing** to ffmpeg (hard +1.8pt
   = noise, chain3 flat, full −0.7pt). So the documents→ffmpeg benefit is not generic
   planner-shape regularization; add skills for their own sake, not to boost ffmpeg.
5. **Retracted a would-be claim before stating it:** at f16 the v3 hard gain nearly vanished,
   which *looked* like "v3 is noise" — until the matched f16 baseline showed v1-union hard is
   0.855, so v3's 0.891 is a real +3.6pt. Always build the matched-quant baseline, not just the
   candidate.

**Still open:** Task 6 (preference tuning, only if data levers plateau); re-lock the stale
per-skill regression snapshots against `qwen3-4b-v3`; CJK retrieval segmentation.
