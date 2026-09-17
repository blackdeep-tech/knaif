# Improve the 4B planner — handoff to a separate agent and branch

**Status:** Planning — handoff, not started · **Created:** 2026-09-17 · **Completed:** —
**Owner:** whoever picks this up · **Ref:** continues S3/S4 of
[2026-09-10-skill-quality-lifecycle.md](2026-09-10-skill-quality-lifecycle.md); the model it
starts from was produced by [2026-09-11-reject-clarify-taxonomy.md](2026-09-11-reject-clarify-taxonomy.md)

**Goal:** Raise the 4B planner's measured quality above the currently accepted bar, on evidence
that can be compared to it. Then hand the result back to be published — **you do not publish.**

---

## The one thing to understand before starting

**A model is only "better" against a control arm you ran yourself.** The numbers below were
measured on a specific instrument — this corpus, this harness, `scoring_policy: 2`, matched
quant, `max_tokens: 512`. Any of those moving makes a comparison meaningless, and the project
has already paid for that lesson twice (see `docs/FINE_TUNING.md` §4 rule 1, and the four
control re-runs recorded as `2026-09-13_t6a-control-v{2,3,4}`).

So: **never compare your candidate to the committed snapshot.** Run the incumbent yourself, in
the same sweep, on the same code state, and compare arm to arm.

## Where you start

Branch from `feat/reject-clarify-taxonomy` (rename pending — take whatever that branch is called
when you read this; it is the one whose HEAD commit is `model(4b): stage knaif-qwen3-4b-v2…`).
**Do not branch from `main`** — it lacks the corpus relabelling, the harness fixes, the scoring
policy bump and both re-locked snapshots, so nothing you measure there will compare.

| | |
|---|---|
| Incumbent model | `models/knaif-qwen3-4b-v2-q4_k_m.gguf` (internal FT cycle `sft-v4-flat`) |
| Eval backend key | `qwen3-4b-sft-v4-flat-q4` — **do not rename it**, it is baked into every saved scoreboard filename in `evals/` |
| Adapter / merged | `python/training/adapters/qwen3-4b-sft-v4-flat/`, `python/training/merged/qwen3-4b-sft-v4-flat/` |
| Training data | `skills/*/data/train.jsonl` → `python/training/build_dataset.py` → `union_chat.jsonl` (742 rows) |
| Recipe | flat: r16 / alpha 16 / 3 epochs / lr 2e-4 / seed 3407, `--base Qwen/Qwen3-4B` |
| Naming | your cycle is **`sft-v5`**. The public number (`v3`) is only assigned if it ships — two namespaces, never crossed |

## The bar you must beat

Both skills, `success` verifier, coverage 1.0. These are the accepted, frozen values:

| | ffmpeg | documents |
|---|---:|---:|
| `outcome_accuracy` | **0.93772** (n=851) | **0.98171** (n=164) |
| `avg_knaif_score` | 0.97794 | 1.00000 |
| S2 acceptance | ACCEPTED 31/31 | ACCEPTED 28/28 |
| safety corpus | **11/11**, 0 breaches | **9/9**, 0 breaches |

**Acceptance is not "the aggregate went up".** A candidate is promotable only if, on your own
control-vs-candidate sweep:

1. both skills pass `just eval-accept` (aggregate floors **and** every required slice), and
2. safety is **100% on both** — no tolerance, no curve, it is the one bar that is not a trade, and
3. no required slice regresses below its floor, and
4. `documents` does not regress. It is the anchor skill (`FINE_TUNING.md` §4 rule 7): one union
   model serves both, so a gain on ffmpeg paid for with documents is not a gain.

## What is actually still failing — your target list

53 failing utterances across 40 rows, from the accepted run
`evals/runs/2026-09-16_trim-range-fix_success`. The shape of the failures:

| expected → actual | count | reading |
|---|---:|---|
| `plan` → `clarify` | 23 | **over-asking** — the largest single bucket |
| `clarify` → `plan` | 12 | under-asking |
| `plan` → `error` | 9 | routed right, produced something broken |
| `clarify` → `reject` | 6 | over-refusing — the taxonomy, still leaking |

Worst rows by utterance count:

```
ffmpeg_114 x4  plan->clarify   change the mp3 to aac format
ffmpeg_202 x4  plan->error     extract frame at the midpoint of clip.mp4
ffmpeg_113 x3  plan->clarify   convert mp3 to flac lossless
ffmpeg_090 x2  plan->clarify   generate a poster image from clip.mp4
ffmpeg_156 x2  clarify->plan   convert clip.mp4 to the best format
ffmpeg_166 x2  clarify->reject resize clip.mp4 to 0x0 pixels
ffmpeg_227 x2  plan->clarify   downscale mov to 480p and remove sound track
ffmpeg_299 x2  clarify->reject Generate the ffmpeg command for this and run it.
```

Plus two the last snapshot commit (`517bac8`) explicitly named as **training-data candidates
rather than code**: `ffmpeg_092` ×4 ("remove the last 3 seconds" extracts them instead of
trimming them away) and `ffmpeg_268#4` (emits no end bound at all).

**Classify before you train.** Roughly half of these may not be model judgement — `plan → error`
in particular is usually a renderer or arg-schema defect, and a retrain that "fixes" it would
take credit for a bug it did not cause and bake the harness's mistake into the training signal.
That exact argument is made at length in `docs/plans/2026-09-08-model-output-improvement.md` (on
branch `bugfix/ffmpeg-audio-format`) — **read it for the method, ignore its numbers**, which are
from v1 on the old corpus and several of whose targets are already fixed.

## Rules that will bite you

From `docs/FINE_TUNING.md` §4 — read it, but these are the ones that have actually cost time:

- **Matched quant, corpus and config, or the comparison is fiction.** The most expensive past
  error was a Q6 candidate vs a differently-quantised baseline: a claimed +5.4pt was really +3.6.
- **Q4 hard-slice noise is ±15pt.** The hard slice is 56 rows; a 2pt move is one row. `chain3` is
  the more robust signal. Report full + hard + chain3 **separately**; never promote on hard alone.
- **A slice you selected on can no longer measure what you selected.** Picking the best of N
  candidates by their hard-slice score and then quoting that score is best-of-N inflation.
- **Never train on eval rows verbatim** — paraphrase. A test enforces the safety corpus is held
  out; the eval corpus deserves the same discipline.
- **Run `uv run -m knaif.evalsuite retrieval` first.** A tool missing from the top-5 is a
  retrieval failure no fine-tune can recover.
- **Inspect row-level flips, not just the aggregate.** Count fixes vs regressions and grep the
  regressions for contamination signatures (ffmpeg emitting a documents tool, or vice versa). A
  net-positive aggregate can hide new contamination.

## Do not touch

- **The snapshots** (`skills/*/data/eval_snapshot.json`). Re-locking is how the bar moves, and it
  is a deliberate act taken here, in its own commit, only when adopting a measured improvement.
- **Publishing.** No HF upload, no manifest `recommendations` edit, no `recommended_model:` move.
  The upload needs a token that is not yours and a release that is not yours to cut.
- **Shared prompt or contract files, unless you measure BOTH models.** `skills/*/prompt.yaml` and
  `contracts/parity/*` are shared. On 2026-09-16 a documents prompt fix gained the 1.7B 3.7pt and
  silently cost the accepted 4B two rows; it was caught only because both were measured, and it
  was then reverted. If you change a prompt, the 4B control arm is mandatory.
- **The eval backend key** `qwen3-4b-sft-v4-flat-q4`, and the `evals/` history.
- **The 1.7B.** It is deprioritised. `models/qwen3-1.7b-sft-v4-flat-q6.gguf` exists and is
  measured (`evals/runs/2026-09-16_1.7b-v4-pair_success`) — leave it alone.

## The loop

```bash
# 0. classify first — is this row the model, or the renderer?
uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose \
  --config eval_backends.yaml --backends qwen3-4b-sft-v4-flat-q4 --limit 40

# a focused probe beats a 30-minute run while iterating: build a small corpus and pass --corpus
uv run python -m knaif.evalsuite run --skill ffmpeg --corpus /tmp/probe.jsonl \
  --verifier success --verbose --config eval_backends.yaml --backends qwen3-4b-sft-v4-flat-q4

# 1. data -> union dataset  (rebuild it; the on-disk copy bakes in the prompt at build time)
uv run python scripts/gen_train.py
uv run pytest python/core/tests/test_train_data_integrity.py
uv run python python/training/build_dataset.py --skills ffmpeg,documents \
  --out python/training/union_chat.jsonl

# 2. train -> merge -> gguf -> quantize   (~25 min for the LoRA on a 5080)
python/training/.venv/Scripts/python.exe python/training/train_lora.py \
  --base Qwen/Qwen3-4B --data python/training/union_chat.jsonl \
  --out python/training/adapters/qwen3-4b-sft-v5-flat
python/training/.venv/Scripts/python.exe python/training/merge_to_hf.py \
  --adapter python/training/adapters/qwen3-4b-sft-v5-flat \
  --out python/training/merged/qwen3-4b-sft-v5-flat
PYTHONPATH=~/tools/llama.cpp/gguf-py python/training/.venv/Scripts/python.exe \
  ~/tools/llama.cpp/convert_hf_to_gguf.py python/training/merged/qwen3-4b-sft-v5-flat \
  --outtype f16 --outfile models/qwen3-4b-sft-v5-flat-f16.gguf
~/tools/llama.cpp/build/bin/llama-quantize.exe models/qwen3-4b-sft-v5-flat-f16.gguf \
  models/qwen3-4b-sft-v5-flat-q4.gguf Q4_K_M       # 4B ships Q4_K_M; 1.7B would be Q6_K

# 3. add a matched stanza to eval_backends.yaml, then sweep BOTH arms, BOTH skills, in one run
#    (copy evals/runs/2026-09-16_1.7b-v4-pair_success/run_all.sh — it is exactly this shape)

# 4. verdict
uv run python -m knaif.evalsuite accept --skill ffmpeg   --current <sb>.json --safety <safety>.json
uv run python -m knaif.evalsuite accept --skill documents --current <sb>.json --safety <safety>.json
```

Merging with Unsloth copies base weights from cache, so **verify the LoRA actually landed** —
compare one tensor against the base checkpoint rather than trusting the file's timestamp. A
merged file whose mtime predates the merge is normal; a merged file identical to the base is not.

## What to hand back

A branch containing, and nothing else:

1. `evals/runs/<date>_<label>_success/` with **both arms**, both skills, both safety corpora, a
   `run_all.sh` that reproduces it, and a `report.md` carrying the verdict — including what got
   **worse**, named by row. The reports from `2026-09-15_t6g-sizing-vs-sending_success` and
   `2026-09-16_1.7b-v4-pair_success` are the format to match.
2. A row in `evals/INDEX.md` per saved run.
3. The `train.jsonl` / prompt / code diffs that produced the gain, each one measured.
4. The GGUF on disk (it is gitignored) and its adapter.
5. An explicit statement of **what you did not isolate.** If several changes rode in one build,
   say so: report, do not attribute.

We will re-lock the snapshots and publish from the integration branch, after re-running L3/L4 —
both of which your model invalidates, and neither of which is your job.
