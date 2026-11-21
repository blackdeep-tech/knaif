# ffmpeg re-lock for the satisfied container fix — 2026-10-30

Both shipped models (`knaif-qwen3-4b-v2`, eval key `qwen3-4b-sft-v4-flat-q4`;
`knaif-qwen3-1.7b-v2`, `qwen3-1.7b-sft-v9-flat-q6`), ffmpeg only, Python runtime, CUDA, the
`success` verifier, plus the whole safety corpus per model. Code at `c3bdd01` with a clean tree
(`DIRTY_FILES.run`): `compress_video` accepts `container: mp4` (`satisfied_args`, not in the
prompt), and a compress that names its format but is not smaller delivers the stream copy
convert_video would write. The corpus did not change (378 rows, 1066 utterances). Documents is not
re-run: it has no compress_video and its grading did not change. Rows first:
`2026-10-29_satisfied-container_success`. `run_all.sh` reproduces every stage.

## Result

| Cell | All rows | Safety | Verdict |
|---|---|---|---|
| 4B ffmpeg | 1008/1066 = 0.9456, knaif 0.9844 | 11/11 | ACCEPTED (45 thresholds), locked |
| 1.7B ffmpeg | 970/1066 = 0.9099, knaif 0.9749 | 11/11 | ACCEPTED (45 thresholds), locked |

Against `2026-10-29_relock-compress-result_success` (`comparison/`, `a` = this run):

| Model | Same grade | Same plan | Same command | Changed |
|---|---|---|---|---|
| 4B | 1065/1066 | 1066/1066 | 1066/1066 | `ffmpeg_size_003` #0: clarify → plan, knaif 1.0 |
| 1.7B | 1066/1066 | 1066/1066 | 1066/1066 | none |

This is what the rows-first run measured and what the locked boards predicted: across both models'
recorded ffmpeg replies and both safety boards, that utterance was the only compress_video step
with a `container`. `compress_result` outcome is now 9/9 on both models (the 4B was 8/9), knaif
1.0 on the 4B; the 1.7B's two half-scores (a single convert at `small_file`) stand.

## Afterwards

- `skills/ffmpeg/data/eval_snapshot.json` and `eval_snapshot.knaif-qwen3-1.7b-v2.json` locked
  from the boards here (`source_run`).
- `skills/ffmpeg/data/release_subset.json` re-derived: 234 utterances, unchanged.
- `pyproject.toml` was edited during the run (a pyright `exclude` list, not part of this work); it
  shows in `DIRTY_FILES.compare`, `.lock` and `.subset`, not in `.run`, and touches no eval input.
- `evalsuite gate` still reports L1–L4 stale for both skills: that is the native release
  evidence, which is measured at the freeze, not by this re-lock.
