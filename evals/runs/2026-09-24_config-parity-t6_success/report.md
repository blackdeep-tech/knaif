# Config-parity T6 — full-corpus re-measure on the aligned llama.cpp config

Plan: `docs/plans/2026-09-23-inference-config-parity.md` (T6). Code `5ea3044` (notebook metadata the
only dirty file). Fixtures regenerated. `success` verifier, every utterance: ffmpeg 851, documents
164, plus both safety corpora. Same v2 weights (`knaif-qwen3-4b-v2`, Q4_K_M) in two configs:

| arm | backend | config |
|---|---|---|
| legacy | `qwen3-4b-sft-v4-flat-q4-legacycfg` | every snapshot to date: flash attention off, batch 512, KV prefix reused row to row |
| aligned | `qwen3-4b-sft-v4-flat-q4` | the contract since T4: flash attention auto(on), batch = n_ctx, ubatch 512, cold per call |

## Results

| | legacy | aligned | snapshot at the time |
|---|---|---|---|
| ffmpeg outcome / knaif | 0.93655 / 0.98545 | **0.93772 / 0.98373** | 0.93890 / 0.98012 |
| documents outcome / knaif | 0.98171 / 1.0 | **0.97561 / 0.99782** | 0.98171 / 1.0 |
| regression vs snapshot | clean, both skills | clean, both skills | |
| S2 | ffmpeg **NOT ACCEPTED** — `batch` 0.897 < 0.92 (n=29); documents ACCEPTED 36/36 | **ACCEPTED 39/39 + 36/36** | |
| safety corpora | 11/11, 9/9, 0 unsafe | 11/11, 9/9, 0 unsafe | |

legacy vs aligned, every utterance:

| | decision flips | outcome flips | net |
|---|---|---|---|
| ffmpeg (851) | 35 (4.11%) | **9 (1.06%)** | +0.12 pp |
| documents (164) | 3 (1.83%) | **1 (0.61%)** | −0.61 pp |

## Prediction, written before the run

- flips ~1–2%, net within ±1 pp — **held** (1.06% / 0.61%; the multilingual phrasings T2 never saw
  move at the same rate).
- safety 100% on both — **held**.
- both arms ACCEPTED — **failed**: legacy's `batch` slice is 26/29 against a floor that allows 2
  misses. `ffmpeg_077#1` flips with config (clarify on legacy, the correct plan on aligned);
  `ffmpeg_229#3/#4` now emit `output: "*.hevc"` — a codec used as an extension, which ffmpeg
  cannot mux — where at the 2026-09-16 lock they emitted `videos_hevc` / `hvc1`. Same config as
  the lock, so something else moved the model: most likely prompt change `be3f20f` (2026-09-22,
  "Unconfirmed by an eval arm"), which changed every ffmpeg prompt. Not ablated.

## Decision

The rule written into `run_all.sh`: re-lock on aligned only if flips stay small AND both arms are
ACCEPTED; otherwise stop and re-derive the v2 verdict before any publish. The second condition
failed, so nothing was re-locked here. The verdict was re-derived in
`../2026-09-24_v2-verdict-aligned_success/` — **v2 stands** — and both snapshots were re-locked
from this run's aligned arm after it.

## Follow-ups

- Engine: an `output` whose extension is a codec (`*.hevc`, `*.h264`) could be mapped to the
  container instead of handed to ffmpeg. Would have kept `batch` whole on both arms.
- `be3f20f`'s prompt line has now been measured only jointly with other changes; an ablation would
  isolate it.
