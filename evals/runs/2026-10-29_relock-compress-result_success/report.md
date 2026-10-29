# ffmpeg re-lock for the compress fix — 2026-10-29

Both shipped models (`knaif-qwen3-4b-v2`, eval key `qwen3-4b-sft-v4-flat-q4`;
`knaif-qwen3-1.7b-v2`, `qwen3-1.7b-sft-v9-flat-q6`), ffmpeg only, Python runtime, CUDA, the
`success` verifier, plus the whole safety corpus per model. Code and corpus at `3a98558` with a
clean tree: the compress fix (the audio is never raised; a compress that is not smaller says so and
leaves things as they were), the `stated_args` gate, and the 3 `compress_result` rows (378 rows,
1066 utterances). Documents is not re-run: its corpus did not change. `run_all.sh` reproduces
every stage.

## Result

| Cell | All rows | Kept rows (10-28 → today) | Safety | Verdict |
|---|---|---|---|---|
| 4B ffmpeg | 1007/1066 = 0.9447, knaif 0.9844 | 999/1057 → 999/1057 | 11/11 | ACCEPTED (45 thresholds), locked |
| 1.7B ffmpeg | 970/1066 = 0.9099, knaif 0.9749 | 961/1057 → 961/1057 | 11/11 | ACCEPTED (45 thresholds), locked |

Against `2026-10-28_relock-1.4.0_success` (`comparison/`, `a` = this run): **1057/1057 same grade
and same plan on both models.** The commands differ on 43 (4B) and 35 (1.7B) utterances, every
one a plan with `compress_video`: the source's audio is copied where it already sits under the
profile's bitrate, as the fix intends.

## The `compress_result` rows

First measurement, so the floor is written from it: 9 utterances, under `min_rate_rows`, so a row
budget, `compress_result: { max_failures: 1 }`, which holds both models without a 1.7B entry.

| Row | 4B | 1.7B |
|---|---|---|
| `ffmpeg_size_001` (mov → smaller mp4, audio not raised) | 3/3 | 3/3 |
| `ffmpeg_size_002` (already starved: nothing written) | 3/3 | 3/3 |
| `ffmpeg_size_003` (convert + compress a starved mov) | 2/3 | 3/3, knaif 0.5 on #0 and #1 |

The 4B's miss (#0) asks instead of planning: it put `container` on `compress_video`. The 1.7B's
two half-scores plan one `convert_video` at `quality: small_file`: the outcome is right (a plan),
but it writes `clip_tiny.mp4`, not the checked `clip_tiny_compressed.mp4`, and nothing checks the
result against the source size. All three are model choices that the same code passed on the other
utterances; the rows-first run (`2026-10-28_compress-result_success`) produced the same plans on
all 9 utterances per model (its "1.7B 7/9" counts full knaif scores, not outcomes).

## Afterwards

- `skills/ffmpeg/data/eval_snapshot.json` and `eval_snapshot.knaif-qwen3-1.7b-v2.json` locked
  from the boards here (`source_run`).
- `skills/ffmpeg/data/release_subset.json` re-derived: 234 utterances (232 before).
- Full pytest suite after the lock: 4782 passed, 20 skipped.
