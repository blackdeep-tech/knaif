# Config-parity flip rate — does the llama.cpp config alone change eval answers?

Plan: `docs/plans/2026-09-23-inference-config-parity.md` (T2). Code: `b467d0b` (tree clean but for
the notebook's kernel metadata). Model: `knaif-qwen3-4b-v2` (Q4_K_M) in every arm. Greedy
decoding throughout.

| Arm | Config |
|---|---|
| A | promotion arm as every snapshot was measured: llama-cpp-python defaults, KV prefix reused row to row |
| A2 | A again |
| B | A + fresh cache per call |
| C | native's config: flash attention on, `n_batch = n_ctx`, fresh cache per call |
| D | native `plan --batch`, `target/release-cuda` |

**Coverage caveat.** The Python arms ran with the `cheap` verifier, which runs only the FIRST
utterance of each row (`runner.py:154` runs every utterance only for executing verifiers). The
shared population is therefore 469 utterances — 326 ffmpeg + 143 documents — English first
phrasings only; the 546 other phrasings (most of the multilingual slice) are unmeasured here.

## Results

| Pair | ffmpeg decision / full / outcome flips (n=326) | documents (n=143) |
|---|---|---|
| A vs A2 | 0 / 0 / 0 | 0 / 0 / 0 |
| A vs B | 7 (2.15%) / 10 / 1 (0.31%) | 2 (1.40%) / 2 / 1 (0.70%) |
| A vs C | 11 (3.37%) / 13 / 4 (1.23%) | 2 (1.40%) / 3 / 0 |
| A vs D | 14 (4.29%) / 23 / — | 2 (1.40%) / 3 / — |
| C vs D | **3 (0.92%)** / 10 / — | **0** / 0 / — |

*decision* = tools + non-file args (fair across lanes); *full* = whole plan; *outcome* = graded,
Python arms only.

Net accuracy change, A → B: ffmpeg +0.31 pp, documents −0.70 pp. **A → C: 0.00 pp on both** —
ffmpeg's 4 outcome flips split 2 up (`ffmpeg_148` clarify→reject, `ffmpeg_166` reject→clarify),
2 down (`ffmpeg_161`, `ffmpeg_300`: under C the model adds `end == start` beside `frames: 1`,
which the engine refuses as a frame count combined with a range).

Safety corpora, separately, every Python arm: **ffmpeg 11/11, documents 9/9, 0 unsafe** — A, B
and C alike.

## Prediction, written before the run

- A vs A2 = 0 — **held.** Same order, same cache path, same arithmetic: the lane is deterministic.
  The TODO's `documents_042#0` flip is not run-to-run noise; it flips between A and C (config).
- A vs B small and nonzero — **held** (9 decisions over 469).
- C vs D far below A vs D — **held**: ffmpeg 14 → 3, documents 2 → 0. Aligning the config removes
  ~80% of the decision-level divergence between the lanes; the rest is llama.cpp version.

## Decision rule (pre-registered in the plan)

Largest outcome flip rate measured: **1.23%** (ffmpeg, A vs C). Smallest margin behind an
existing decision: **1.83 pp** (documents, v2 over v1 in `PROMOTION_VERDICT.md`; ffmpeg's is
2.00 pp, the tightest acceptance-floor margin is documents `avg_knaif_score` at 2.0 pp). **Below
it: the existing evaluations and the v2 verdict stand**, and the net shift from moving to
native's config is 0.00 pp. The plan proceeds to T3–T5 (align the lanes) without a re-lock
forced by this measurement.

Stated limits: the margin is only ~1.5× the flip rate, not an order of magnitude; the population
is first phrasings only; and several acceptance *slices* sit 1–2 utterances above their floors,
where a single flip can cross a bar. T6's re-measure — full corpus, `success` verifier, aligned
config — is where those are settled.

## Follow-ups

- The union of decision flips is the fragile-row list for plan T8 (training): ffmpeg 038, 045,
  100, 127, 134, 138, 148, 161, 166, 201, 202, 233, 244, 259, 260, 268, 296, 300; documents 038,
  042, 105.
- Engine: `frames` alongside a zero-length range that agrees with it (`end == start`,
  `frames: 1`) could be accepted instead of refused — it is the only way C scored lower than A.
