# T0 (a)–(c) on the maintainer's machine — 2026-10-17

Plan: [2026-10-14-faster-release-evidence](../../../docs/plans/2026-10-14-faster-release-evidence.md),
Part B (B1) and Tasks (T0, T3). Script: [`run_all.sh`](run_all.sh), one stage at a time, nothing
in parallel.

**Question.** If every row of an L4 cell is planned in one batch (one model load) and each row
then runs through the shipped `knaif run` with the mock backend replaying its plan, are the
per-row grades those of the cell measured the 1.3.0 way (one `knaif run` per row, model
reloaded each time)? And what does it save?

**What was measured.** The frozen 1.3.0 Windows artifact (`dist/knaif-1.3.0-windows-x64.zip`,
sha256 `7c289f3e…`, the zip 1.3.0's Windows cells measured), RTX 5080 box, `KNAIF_N_THREADS=8`,
in process. Each board is compared row by row (`evalsuite row-diff`: outcome, correctness, knaif
score; plan; rendered command) with the same cell of
[`2026-10-13_r130-windows_success`](../2026-10-13_r130-windows_success/). Skills, corpora and
verifiers are unchanged since the freeze commit `8c94cbe` (the script checks it). The row diffs
are committed as `<stage>/<skill>/comparison.json` (4B) and `1.7b/<stage>/<skill>/comparison.json`.

- `exec`: execution alone, no GPU and no model: the plans are 1.3.0's own `plan --batch` output
  from its daemon check (`evals/parity/2026-10-13_r130-win-daemon-plan-equality/4b/`).
- `cuda`, `vulkan`: the whole cell, batch-planned fresh on that backend, plus a 30-row cold-start
  sample (`evalsuite shards --rows 30`, evenly spaced) run live with the model, whose plans are
  compared with the batch's.
- `cpu`: 100 rows per skill (`evalsuite shards --rows 100`, evenly spaced), batch-planned on the
  CPU (GPU hidden), compared with those rows of 1.3.0's full CPU cell.

## Result: identical on every row

| Model | Stage | Skill | Rows | Same grade | Same plan | Same command | Cold start (live = batch) |
|---|---|---|---:|---:|---:|---:|---|
| 4B | exec | ffmpeg | 861 | 861 | 861 | 861 | — |
| 4B | exec | documents | 164 | 164 | 164 | 164 | — |
| 4B | cuda | ffmpeg | 861 | 861 | 861 | 861 | 30 / 30 |
| 4B | cuda | documents | 164 | 164 | 164 | 164 | 29 / 29 (1 screened) |
| 4B | vulkan | ffmpeg | 861 | 861 | 861 | 861 | 30 / 30 |
| 4B | vulkan | documents | 164 | 164 | 164 | 164 | 29 / 29 (1 screened) |
| 4B | cpu | ffmpeg | 100 | 100 | 100 | 100 | — |
| 4B | cpu | documents | 100 | 100 | 100 | 100 | — |
| 1.7B | cuda | ffmpeg | 861 | 861 | 861 | 861 | 30 / 30 |
| 1.7B | cuda | documents | 164 | 164 | 164 | 164 | 29 / 29 (1 screened) |
| 1.7B | vulkan | ffmpeg | 861 | 861 | 861 | 861 | 30 / 30 |
| 1.7B | vulkan | documents | 164 | 164 | 164 | 164 | 29 / 29 (1 screened) |
| 1.7B | cpu | ffmpeg | 100 | 100 | 100 | 100 | — |
| 1.7B | cpu | documents | 100 | 100 | 100 | 100 | — |

"Screened": a live row the unsafe-phrase screen rejected before inference, so it has no plan to
compare (`plan --batch` does not screen; `run` does, in both modes).

Every backend also reproduced its **own** 1.3.0 plans exactly, four days and a fresh process
later: batch planning on CUDA equals `run` on CUDA, Vulkan equals Vulkan, CPU equals CPU. Across
backends the plans do differ (4B, CUDA vs Vulkan today: 63 of 861 ffmpeg plans, 5 of 164
documents), as 1.3.0's flip reports said.

## What it saves (wall clock on this box; not speed figures)

| Model | Cell | 1.3.0, `run` per row | Batch planning | Execution | Batch-planned total |
|---|---|---:|---:|---:|---:|
| 4B | CUDA ffmpeg | 43.7 min | 6.6 min | 15.6 min | **22.2 min (−49%)** |
| 4B | CUDA documents | 5.6 min | 0.7 min | 1.2 min | **1.9 min (−66%)** |
| 4B | Vulkan ffmpeg | 50.4 min | 11.3 min | 15.8 min | **27.2 min (−46%)** |
| 4B | Vulkan documents | 7.1 min | 1.6 min | 1.3 min | **2.9 min (−59%)** |
| 4B | CPU ffmpeg | 17.2 s/row | 12.5 s/row | 0.7 s/row | **13.2 s/row (−23%)** |
| 4B | CPU documents | 8.2 s/row | 5.3 s/row | 0.1 s/row | **5.4 s/row (−34%)** |
| 1.7B | CUDA ffmpeg | 35.4 min | 4.1 min | 13.3 min | **17.3 min (−51%)** |
| 1.7B | CUDA documents | 4.3 min | 0.5 min | 1.0 min | **1.4 min (−67%)** |
| 1.7B | Vulkan ffmpeg | 41.2 min | 8.0 min | 13.3 min | **21.3 min (−48%)** |
| 1.7B | Vulkan documents | 5.2 min | 1.2 min | 1.1 min | **2.2 min (−58%)** |
| 1.7B | CPU ffmpeg | 12.3 s/row | 10.1 s/row | 0.5 s/row | **10.7 s/row (−13%)** |
| 1.7B | CPU documents | 5.1 s/row | 4.1 s/row | 0.1 s/row | **4.3 s/row (−16%)** |

1.3.0's times are the sums of its boards' per-row latencies. "Execution" includes the 30 live
cold-start rows, which still load the model. Execution alone, from recorded plans with no model
at all (`exec`): 13.7 min for 861 ffmpeg rows, 23 s for 164 documents rows.

## What it means for the plan

- **B1 holds** (T0 a–c): batch planning plus replayed execution gives the same grade, plan and
  command as `run` on every row measured, both models, every backend, and the cold-start rows
  plan the same live. On a GPU it halves a cell.
- **On the CPU, planning is the cost, not the model load** (≈95% of a CPU row is planning even in
  one batch: prompt processing at ~2,400 tokens a request dominates). B1 saves 13–34% there,
  and B2 (confirm another backend by re-running only the rows whose plan differs) saves nothing
  on the CPU, because finding those rows means planning them all. The plan's B2 estimate (4B
  CPU cell 4.3 h → 30–40 min) assumed execution was the cost and does not hold. What does cut
  the CPU cells: **the store** (Part E: a release that does not change planning reuses every
  plan, and re-executing 861 rows takes ~15 min) and **the subset** (Part D: fewer rows to plan
  when planning did change). Prompt-prefix reuse, which would cut the prompt cost, was measured
  on 2026-10-06 and changes 61 of 861 plans (NATIVE.md §5.7), so it stays off.
- **The execution half is the shipped path**: the replay goes through `knaif run` itself, so the
  unsafe-phrase screen, validation, expansion, the clarify gate and execution are what users run.
  No product code was changed for it (`KNAIF_LLM_BACKEND=mock` + `KNAIF_LLM_MOCK_RESPONSE` already
  existed).

## Incidents

- The first `cuda` attempt (14:21) wrote no board: `batch_plan` passed the binary a relative path
  to its batch file while the binary ran inside the batch folder. Fixed (absolute path, with a
  regression test, `test_the_batch_file_is_found_from_the_folder_the_binary_runs_in`), checked on
  3 rows on CUDA, and `cuda`, `vulkan`, `cpu` re-run from the start. The failed attempts measured
  nothing.
- The chain script logged `exit 0` for those failed stages (`$(date)` reset `$?`); fixed in the
  chain, which is not part of this folder. `COMPLETE` keeps both attempts.
