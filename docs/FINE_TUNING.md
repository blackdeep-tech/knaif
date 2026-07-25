# FINE_TUNING.md — How to Fine-Tune a knaif Model (canonical)

**This is the entry point for anyone fine-tuning a `knaif` planner model.** It is the
reproducible procedure + the hard-won methodology rules + what we already know works and
doesn't. Read this before starting a fine-tune; follow it end to end.

- **What is released today, and why those models:** [MODELS.md](MODELS.md)
- **Data generation contract:** [TRAINING_DATA_GENERATION.md](TRAINING_DATA_GENERATION.md)
- **Full experiment history / numbers / retracted claims:** [audits/2026-07-01-finetuning-study-findings.md](audits/2026-07-01-finetuning-study-findings.md)
- **Every run's scores:** [../evals/INDEX.md](../evals/INDEX.md)
- **Latest pass write-up:** [plans/2026-07-02-qwen3-finetuning-pass3.md](plans/2026-07-02-qwen3-finetuning-pass3.md)

---

## 0. What "fine-tuning" means here

`knaif` turns an utterance into `{"plan": [{"tool","args"}...]}`. A small local LLM only
**routes** to the right tool(s) and fills valid args; deterministic code validates/executes.
Fine-tuning teaches that routing/composition behavior. One shared model serves multiple
skills, so we train on the **union** of skills' `train.jsonl` (never per-skill for a shared
deployment — it forgets the others).

Two files per skill, never confused: `data/train.jsonl` (learned from) vs `data/eval.jsonl`
(measured against, never trained on). The `hard` / `chain3` tagged eval rows are **held out**
of training — gains there measure generalization, not memorization.

## 1. Current production state (as of 2026-07-02)

| lane | model | serves | notes |
|---|---|---|---|
| **shared default** | `knaif-qwen3-4b-v1` = `models/knaif-qwen3-4b-v1-q4_k_m.gguf` | ffmpeg + documents | promoted; sft-v3 union LoRA, Q4, 2.5 GB (key renamed from `qwen3-4b-v3` 2026-07-20) |
| untuned fallback | `qwen3-4b` = `Qwen3-4B-Q4_K_M.gguf` | io + project default | skills not in training stay here |
| quality-per-byte | `models/knaif-qwen3-1.7b-v1-q6_k.gguf` (1.32 GB) | mobile / footprint | not deployed; ready if size matters |

Wiring: `models.yaml` (`default:` + named entries) and each skill's
`recommended_model:` in `skills/<skill>/skill.yaml`.

## 2. Hardware & environment

- **GPU:** RTX 5080, 16 GB, **Blackwell (sm_120)** on **WSL2**. ⚠️ Fragile: on any
  `CUDA: illegal memory access`, **STOP — do not auto-retry** (it can crash the Windows
  display driver / TDR). A reboot clears it. A 1.7B LoRA (3 epochs, ~730 rows) ≈ 9 min; a 4B
  ≈ 19 min.
- **Train venv:** `python/training/.venv/bin/python` (Unsloth, bf16 LoRA, `load_in_4bit=False`).
  After (re)building this venv, copy the Unsloth-cache guard into it so ad-hoc /
  REPL / notebook imports don't recreate `./unsloth_compiled_cache` in the repo root:
  `cp python/training/sitecustomize.py python/training/.venv/lib/python*/site-packages/`
- **Core venv:** `uv run ...` (knaif + skills; for data build + eval).
- **llama.cpp:** `~/tools/llama.cpp` (`convert_hf_to_gguf.py`, `build/bin/llama-quantize`).

**Training is strictly optional and physically isolated — keep it that way.** Someone who
runs `pip install knaif` must never acquire torch, Unsloth, or CUDA wheels; the library's
whole value is running locally without a training stack. Two properties hold this today and
neither is enforced by a test:

- `python/training/` sits **outside** the distributed package root. The wheel is built from
  `python/core/` with `include = ["knaif*"]`, so training cannot be packaged by accident —
  no exclude list is doing the work, the directory layout is.
- **Core never imports training.** The dependency runs one way: training reads skills'
  `data/train.jsonl` and writes `models/*.gguf`, and that is the entire interface.

So training deps belong in `python/training/requirements-train.lock` and its own venv, never
in `knaif`'s dependencies or extras. If a helper in `python/training/` ever looks worth
sharing with core, move the *logic* into core and import it from training — not the reverse.

## 3. The reproducible pipeline

```bash
# --- (a) DATA: regenerate skill train.jsonl, or hand-author targeted rows ---
uv run python scripts/gen_train.py          # writes skills/*/data/train.jsonl
uv run pytest python/core/tests/test_train_data_integrity.py   # guards leakage / invalid rows

# --- (b) BUILD the union chat dataset (reproduces the EXACT inference prompt per row) ---
uv run python python/training/build_dataset.py --skills ffmpeg,documents \
  --out python/training/union_chat.jsonl
#   optional weighting (diagnostic only — see §5): --weight-tags hard=3,ffmpeg:chain3=3
#   optional extra rows:                            --extra-jsonl ffmpeg=python/training/extra.jsonl

# --- (c) TRAIN a LoRA (STOP on any CUDA illegal-memory-access; reboot; do not retry-spam) ---
#   Defaults = the proven "flat" recipe: rank 16, alpha 16, 3 epochs, lr 2e-4, seed 3407.
python/training/.venv/bin/python python/training/train_lora.py \
  --base Qwen/Qwen3-1.7B \
  --data python/training/union_chat.jsonl \
  --out python/training/adapters/<name>
#   4B: --base Qwen/Qwen3-4B    (Unsloth resolves to unsloth/Qwen3-4B)

# --- (d) MERGE -> f16 GGUF -> quantize ---
python/training/.venv/bin/python python/training/merge_to_hf.py \
  --adapter python/training/adapters/<name> --out python/training/merged/<name>
python/training/.venv/bin/python ~/tools/llama.cpp/convert_hf_to_gguf.py \
  python/training/merged/<name> --outtype f16 --outfile models/<name>-f16.gguf
~/tools/llama.cpp/build/bin/llama-quantize \
  models/<name>-f16.gguf models/<name>-q6.gguf Q6_K
#   1.7B deployment quant = Q6_K (1.32 GB). 4B ships Q4_K_M. Q8_0 is a diagnostic only.

# --- (e) EVAL (success = real execution + criteria; the honest metric) ---
#   Add a stanza to eval_backends.yaml with max_tokens: 512, then:
uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success \
  --config eval_backends.yaml --backends <name>-q6 \
  --save evals/runs/<YYYY-MM-DD>_<label>_success
#   Read full + hard + chain3 from the saved *_success.json (rows tagged "hard"/"chain3").
#   Repeat --skill documents to check the anchor. Add a row to evals/INDEX.md.
```

## 4. Methodology rules — READ THESE (they are where every past mistake came from)

1. **Compare at f16 first, then a MATCHED-quant baseline. Never cross quant/corpus/config.**
   The single most expensive past error: a candidate at Q6 was compared against a baseline at
   a different quant + corpus, inflating a "+5.4pt" gain that was really +3.6pt. Build the
   baseline yourself at the same quant, same corpus, same `max_tokens`.
2. **Q4 hard-slice noise is ≈ ±15pt; even Q6/Q5 draw favorably on n=55.** If Q8 ≈ f16 but
   Q6/Q5 look better, the Q6/Q5 "gain" is a quant draw, not real. Trust f16/Q8 for truth.
3. **The hard slice is only 55 rows — a 2pt move ≈ 1 row.** Do not promote on it alone.
   **`chain3` is the more robust signal** (it moved identically across quant levels when the
   effect was real). Report **full + hard + chain3 separately**; full is near a corpus ceiling.
4. **A slice you selected on can no longer measure what you selected.** Picking the best of
   ~8 candidates *by* their hard-slice score and then reporting that same score is best-of-N
   inflation, not a measurement — with n=55 and a ~2-row noise floor, the winner is partly the
   luckiest draw. This is separate from rule 3's noise point and is not fixed by re-running.
   Confirm on something independent: a held-out half of the slice, a fresh probe built from the
   audited failure buckets (paraphrases, new files/params — never verbatim eval rows), or a
   second training seed to separate run-to-run variance from a real data effect. Cheap partial
   substitutes that have caught real problems: a **matched-precision re-read** (this is how the
   Q6-inflated "+5.4pt" was corrected to +3.6pt) and **chain3-consistency across quant levels**.
   Note honestly: the currently promoted model's hard-slice margin has *never* had a dedicated
   independent probe — that task is still open.
5. **Inspect row-level flips for every apparent win.** Count regressions vs fixes, and grep the
   regressions for **contamination signatures** (e.g. ffmpeg emitting documents' `quality:"small"`
   or a hallucinated `convert_audio`). A net-positive aggregate can hide new contamination.
6. **`success` verifier only** for promotion (executes the plan). `cheap` is smoke-test.
7. **Keep documents (or any saturated skill) as an anchor** in shared runs — it should prevent
   forgetting, not consume the gradient. Confirm the anchor held after every run.
8. **Never train on held-out `hard`/`chain3` eval rows verbatim** — use neighbors/paraphrases.
9. **The snapshot gate answers "may I promote this?", not "did training regress anything?"**
   `regression --all-skills` diffs against each skill's **committed** snapshot, which is the
   *deployed* model. An experimental build scoring below it can simply be a lineage gap —
   a study-artifact tune of a different base is expected to sit under the shipped model —
   and reading that FAIL as catastrophic forgetting is a mistake already made once here. For
   the forgetting question, baseline **your own pre-run** (same family, same pipeline);
   reserve the snapshot gate for the promotion decision in §6. Two setup traps come with it:
   the run folder must contain a scoreboard at **each** skill's snapshot verifier (ffmpeg's
   is `cheap`, documents' is `success`, so sweep at *both* into one folder — otherwise the
   unmeasured skill is silently skipped, not failed), and a gate that cannot fail is worse
   than none. See *The two ways an aggregate gate lies to you* in
   [EVAL_VERIFICATION_SOP.md](EVAL_VERIFICATION_SOP.md).
10. **Fix retrieval before blaming the model.** Run `uv run -m knaif.evalsuite retrieval`
   (recall@k / MRR, per script slice); rows where the expected tool isn't in the top-5 are
   *retrieval* failures, not model failures — no fine-tune can recover them. Keywords may be
   shared across tools (retrieval down-weights by document frequency). See the retrieval-miss
   audit and `docs/plans/2026-07-02-retrieval-overhaul.md`.

## 5. What we already know — outcomes (don't re-litigate these)

**Works:**
- **Small, targeted, hand-validated data.** ~30 contrastive rows aimed at audited failure
  buckets (3-step chains, clarify/reject boundaries, impossible-media rejects) gave the real
  gain: honest **hard +3.6pt / chain3 +6.2pt** (1.7B), full ≈ flat, quality up.
- **Anti-contamination rows.** The v3 reject/near-pair rows **eliminated** the old 4B tune's
  cross-skill enum-bleed (0 contamination in the regression flips vs the old tune's several).
- **The flat recipe** (r16 / 3ep / lr2e-4, no weighting). Fine-tuning helps most where there's
  headroom (1.7B > 4B); the 4B is near the instruct ceiling.
- **Q6 for 1.7B** (best size↔quality); **Q4 for 4B**.

**Does NOT work (proven dead ends — do not repeat without a materially different design):**
- **Weighted / curriculum SFT** (oversampling hard/chain) — moves hard but always costs full +
  documents. Diagnostic only.
- **Tiny eval-derived DPO** (40 pairs) — regressed ffmpeg. Preference tuning needs a larger,
  **non-eval**, bucket-labeled set with held-out bucket probes (unproven; Task 6, still open).
- **Bulk verifier-filtered synthetic distillation** — schema-valid + dry-run-executable rows
  still diluted routing. Valid ≠ helpful.
- **Single-skill scope** — ffmpeg-only never beat the shared union at matched data.
- **Bulk single-op rows aimed at an observed failure** ("data v2"): ~80 rows reinforcing the
  correct enum cut enum errors 3→2 but **lost the hard slice** (ffmpeg hard f16: v1 0.855 vs
  v2 0.800, lighter r8/2ep 0.782) by diluting the chain/contrastive signal and over-nudging
  `clarify`. Note the contrast with the anti-contamination rows that *did* work above: those
  were **contrastive near-pairs** (the wrong mapping shown against the right one), not bulk
  reinforcement of the right answer alone. Shape beats volume — adding rows to fix a small
  observed failure routinely costs more elsewhere than it gains, and only the full
  full/hard/chain3 read shows it.
- **Planner-diversity via a 3rd skill** — adding `io` (zero enum overlap) transferred **nothing**
  to ffmpeg. The documents→ffmpeg benefit is *not* generic planner-shape regularization.
  This one is worth stating carefully because the refuted theory is *persuasive* and keeps
  re-suggesting itself: that documents regularizes the model toward the shared planner
  contract (pick a tool, emit strict JSON, fill schema-valid args, clarify, reject, compose
  chains), so any structurally different skill should help. It was written up as the obvious
  next experiment, run, and it failed. What remains true is only the measurement — the union
  beats ffmpeg-only at matched data, at both v1 and v3 — while the **mechanism is unexplained**.
  Treat "add another skill to broaden the planner" as a dead end, not an untried idea.
- **Gemma3-4B** as a base — worse and 4× slower than Qwen3-4B.
- **"Fine-tune the instruct checkpoint instead of the base"** — a non-lever, already pulled.
  `Qwen/Qwen3-4B` **is** the post-trained instruct model; there is no separate `-Base` repo,
  and it behaves like one (0.905 zero-shot on structured JSON, which a raw pretrain base
  cannot do). Every tune recorded here is already a tune *of* the instruct model. That also
  explains why 4B gains are flat: tuning a strong instruct model on saturated routing is
  mostly downside risk — the LoRA damages rows the untuned model already got right.

## 6. Promotion procedure

Promote only when: `success` full within ~1pt of the incumbent **and** hard/chain3 rise **and**
no new contamination in the flips **and** the anchor skill held.

1. Build the deployment quant (1.7B→Q6, 4B→Q4).
2. Add a `models.yaml` entry (config mirroring the validated eval: `n_ctx 8192`,
   `max_tokens 512`, `thinking_enabled false`).
3. Point **only the skills that were in training** at it via their
   `skills/<skill>/skill.yaml` `recommended_model:`. Leave untrained skills and the
   project-wide `default:` on the untuned model.
4. Verify: `uv run knaif-cli run <skill> "<utterance>" --backend auto --dry-run --show-plan`
   loads the new GGUF and plans correctly; run the full test suite.
5. Record in `evals/INDEX.md` and this file's §1 table.
6. **Follow-up:** re-lock the per-skill regression snapshots
   (`skills/<skill>/data/eval_snapshot.json`) against the promoted model in a deliberate
   pass — they otherwise stay pinned to the old model.

## 7. Standard experiment loop (the repeatable method)

1. **Audit failures** → bucket real errors (chain / clarify / reject / enum / retrieval).
2. **Author ~30 targeted contrastive rows** against those buckets (not big template blocks).
3. **Fix retrieval keywords** for any retrieval-miss buckets first.
4. **Train the flat union**; eval at f16, then build the matched-quant baseline.
5. **Decide** on full + hard + chain3 + row-flip/contamination inspection, not aggregate noise.
6. **Promote** per §6 if the gate passes; otherwise record the negative result and move on.
