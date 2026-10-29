# Compress result: rows first (2026-10-28)

**What:** the compress fix (audio never raised, a compress that is not smaller is not delivered,
the result line), the `stated_args` gate (an invented `target_size_mb` is left out) and the Python
preflight fix, measured on the three new `compress_result` rows plus every row whose plan uses
`compress_video` (`corpus.jsonl`: 41 rows, 122 utterances), before any re-lock.

**How:** both shipped models (`qwen3-4b-sft-v4-flat-q4`, `qwen3-1.7b-sft-v9-flat-q6`), Python
runtime, CUDA, executing verifier (`success`). `run_all.sh run`, then `compare` (row-diff against
`2026-10-28_relock-1.4.0_success`).

| Model | Kept rows vs the 1.4.0 re-lock | `compress_result` (9 utterances) |
|---|---|---|
| 4B | 113 / 113 same grade, same plan (70 same command: audio now copied) | 8 / 9 |
| 1.7B | 113 / 113 same grade, same plan (80 same command) | 7 / 9 |

The misses are the model's: 4B `ffmpeg_size_003#0` put `container` on `compress_video` (an
unsupported arg, so a clarify); 1.7B `ffmpeg_size_003#0`/`#1` planned the convert and no compress.

**Superseded, kept for the record:**
- `superseded_mixed_code/`: the 4B arm ran before the `stated_args` gate was written, the 1.7B
  after it; not comparable.
- `superseded_format_rule/`: the first rule for a chain intermediate of another format wrote
  nothing; the 1.7B's `ffmpeg_batch_001[1]` (`*.mov` → `converted.mkv` → compress) lost one file's
  conversion (0.75). The rule now delivers the encode; this run measures it (1.0).

**Not done here:** the re-lock. The corpus grew by 3 rows (1057 → 1066 utterances), so the
snapshots, the `compress_result` floor and the release subset wait for a full ffmpeg run.
