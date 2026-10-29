# Documents after the compress fix — 2026-10-29

`compress_pdf` now reports a size line and the documents result view changed (the compress fix,
`2b3b626`..`3a98558`), on code newer than the documents snapshots, which were locked on 2026-10-28
from `evals/runs/2026-10-28_relock-1.4.0_success`. Not a re-lock: the full documents corpus on both
shipped models, Python runtime, CUDA, the `success` verifier, plus the safety corpus, compared row
by row with the boards the snapshots name. Code at `30b4b39`. `run_all.sh` reproduces it.

## Result

| Model | All rows | vs the 10-28 boards | Safety | Bar |
|---|---|---|---|---|
| 4B (`qwen3-4b-sft-v4-flat-q4`) | 0.971, knaif 0.987 | 277/277 same grade, plan and command | 9/9 | ACCEPTED (41 thresholds) |
| 1.7B (`qwen3-1.7b-sft-v9-flat-q6`) | 0.910, knaif 0.993 | 277/277 same grade, plan and command | 9/9 | ACCEPTED (41 thresholds) |

Nothing moved, so the documents snapshots and release subset stay as locked on 2026-10-28.
