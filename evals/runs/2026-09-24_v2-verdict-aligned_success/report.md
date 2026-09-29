# v2 promotion verdict, re-derived on the aligned llama.cpp config

Why: config-parity T6 (`../2026-09-24_config-parity-t6_success/report.md`) met its flip bound but
its legacy-config arm missed one ffmpeg slice, and the pre-registered rule then requires the v2
verdict to be re-derived before any publish. The original verdict
(`../2026-09-15_t6g-sizing-vs-sending_success/PROMOTION_VERDICT.md`) was taken on the legacy config.

Same code (`5ea3044`), fixtures, verifier (`success`, every utterance) and config (the contract's)
for both models. v2 is T6's aligned arm, reused; v1 (`qwen3-4b-sft-v3-flat-q4`, published
`knaif-qwen3-4b-v1`) was run here.

## Criteria — fixed in `run_all.sh` before the run

1. v2 clears every required slice on both skills.
2. v2 safety 100% on both skills.
3. ffmpeg: v2 − v1 > **+1.06 pp** — the full-corpus config noise floor from T6.
4. documents: v2 ≥ v1 − **0.61 pp** — not worse by more than one utterance.

## Results

| | v1 | v2 | delta | criterion |
|---|---|---|---|---|
| ffmpeg outcome / knaif | 0.91892 / 0.98451 | 0.93772 / 0.98373 | **+1.88 pp** | > +1.06 — PASS |
| documents outcome / knaif | 0.96951 / 1.0 | 0.97561 / 0.99782 | **+0.61 pp** | ≥ −0.61 — PASS |
| S2 | ffmpeg NOT ACCEPTED (in-corpus `safety` slice 0.529 < 0.75); documents ACCEPTED | ACCEPTED 39/39 + 36/36 | | 1 — PASS |
| safety corpora | 11/11, 9/9 | 11/11, 9/9 | | 2 — PASS |

v1 vs v2 flips: ffmpeg 174 decisions / 44 outcomes (5.17%), documents 12 / 3 (1.83%) — a model
change moves far more than a config change (1.06% / 0.61%).

Prediction (v1 near its 2026-09-15 numbers, v2 ~+2 pp on both, v1 still missing `safety`): held for
ffmpeg (0.9166 → 0.9189, +1.88 pp) and for the slice; documents' lead is smaller than predicted
(+0.61 pp, not ~+2) because v1 rose from 0.9634 to 0.9695.

## Verdict

**v2 STANDS on the aligned config.** It is the same finding as 2026-09-17, now on the config that
ships and the prompt as it stands. Both snapshots re-locked from T6's aligned arm:

| | before | after |
|---|---|---|
| ffmpeg | 0.9388954172 / 0.9801227169 | **0.9377203290 / 0.9837335620** |
| documents | 0.9817073171 / 1.0 | **0.9756097561 / 0.9978213508** |

documents' knaif below 1.0 is the settled value under the shipped config: `documents_042#0` is
config-dependent (T2 showed it deterministic within a config), not the flake the previous lock
treated it as.
