# Re-lock for 1.4.0 — 2026-10-28

Both shipped models (`knaif-qwen3-4b-v2`, eval key `qwen3-4b-sft-v4-flat-q4`;
`knaif-qwen3-1.7b-v2`, `qwen3-1.7b-sft-v9-flat-q6`), both skills, Python runtime, CUDA, the
`success` verifier, plus the whole safety corpus per model per skill. Corpus at `5fff335` (the
1.4.0 feature rows folded into `eval.jsonl`, kept rows `ffmpeg_204`/`208` fixed), prompts
generation 1 with the second reject example. `run_all.sh` reproduces every stage.

## Result

| Cell | All rows | Kept rows (10-18 → today) | Safety | Verdict |
|---|---|---|---|---|
| 4B ffmpeg | 999/1057 = 0.9451, knaif 0.9842 | 0.9422 → 0.9502 | 11/11 | ACCEPTED, locked |
| 4B documents | 269/277 = 0.9711, knaif 0.9874 | 0.9314 → 0.9657 | 9/9 | ACCEPTED, locked |
| 1.7B ffmpeg | 961/1057 = 0.9092, knaif 0.9759 | 0.9173 → 0.9264 | 11/11 | ACCEPTED (owner exception), locked |
| 1.7B documents | 252/277 = 0.9097, knaif 0.9928 | 0.9257 → 0.9434 | 9/9 | ACCEPTED (owner exception), locked |

"Kept rows" are the 883 + 175 utterances both this run and
`2026-10-18_relock-corpus-v2_success` measured; `comparison/` holds the row-by-row diff
(`a` = this run, `b` = 2026-10-18).

## The 1.4.0 feature rows

First measurement, so their floors were written from it (owner, 2026-10-28): at the 4B's score in
the skill's bar, at the 1.7B's own score under its model entry.

| Tag | ffmpeg 4B | ffmpeg 1.7B | documents 4B | documents 1.7B |
|---|---|---|---|---|
| `url_input` | 42/45 | 41/45 | 32/33 | 31/33 |
| `alerts_and_folders` | 29/30 | 19/30 | 17/18 | 10/18 |
| `many_files` | 92/102 | 84/102 | 53/54 | 48/54 |

The 1.7B mostly does the work and drops the "beep / notify me when done" part: neither shipped
model trained on these rows.

## Owner exception for the 1.7B (2026-10-28)

At the existing bars the 1.7B missed four thresholds, mostly because the new rows count in them:
documents aggregate 0.9097 (floor 0.91); documents `clarify` 6 misses (budget 4; the kept rows
miss the same 3 as on 10-18, the other 3 are 1.4.0 rows tagged `clarify`); documents `extract` 2
(budget 1; `documents_004` now asks instead of planning); ffmpeg `batch` 24/29 (floor 26/29;
`ffmpeg_229` asks instead of planning, and `ffmpeg_077`'s "convert *.mp4" with every file
already mp4 now stops with "Nothing to convert", the 1.4.0 rule against copying a file beside
itself). The owner lowered the 1.7B's own lines to these values; the 4B's bars are unchanged.

## Fixture integrity (documents re-run)

The first `run` regenerated the new documents folder fixtures; the documents generator writes
every documents fixture on each call, so it also rewrote the cached samples (new PDF and Office
timestamps) while `fixtures regen` recorded hashes only for the names it ran. `accept` refused
both documents boards for fixture integrity. Fixed in `fixtures regen` (bytes a regen command
wrote are recorded; test `test_a_sibling_rewritten_by_a_regenerating_command_gets_its_new_hash`),
documents fixtures force-regenerated, and the `documents` stage measured that arm again — same
grades on every row. The refused boards are in `superseded_fixture_integrity/`. The re-run's
`DIRTY_FILES.documents` lists the regen fix, uncommitted at the time.

## Known model gaps seen, not regressions

- `ffmpeg_202` "frame at the midpoint": the 4B guesses `at_time 00:00:25` on a 10 s clip and
  ffmpeg writes nothing; also failed on 10-18. The 1.7B says `middle` and passes.
- `ffmpeg_folder_003`: the 1.7B wrote `C:\Users\username\Desktop\…` itself; the sandbox refused
  it, as it should.

## After the lock

Release subsets re-derived: ffmpeg 232, documents 103 utterances.
