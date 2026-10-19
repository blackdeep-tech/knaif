# Result store, first real run — 2026-10-19

Plan: [2026-10-14-faster-release-evidence](../../../docs/plans/2026-10-14-faster-release-evidence.md),
T2c (E1/E2 as amended 2026-10-19). Script: [`run_all.sh`](run_all.sh), criteria written in it
before any result.

**Question.** Planned through the result store, a cell asks the model only for the prompts the
store does not hold, recording each raw reply under the sha256 of its exact prompt, and executes
every row by replaying those replies (`KNAIF_LLM_BACKEND=replay`). Planned again from what it
recorded, does the cell ask the model nothing and grade every row the same?

**What was measured.** A developer CUDA build of this checkout (`target/release-cuda/knaif.exe`,
lane `native-cli`, unpackaged: a check, not L4 evidence), `knaif-qwen3-4b-v2`, ffmpeg's committed
release subset (194 utterances), in process, RTX 5080. The store was this folder's own (ignored;
a developer build's namespace is no use to a release). A 30-row cold-start sample
(`shards --subset --rows 30`) ran live in both stages.

## Result: the store works as designed

| | `record` (empty store) | `reuse` (from `record`'s store) |
|---|---:|---:|
| Replies measured / reused | 194 / 0 | **0 / 194** |
| Model planning | 96.6 s | **none** (`plan_batch_s` null) |
| Row execution (incl. 30 live cold-start rows) | 191.8 s | 192.9 s |
| Cold start: live plan = stored plan | 30 / 30 | 30 / 30 |
| Live rows without a plan | 0 | 0 |
| Outcome accuracy / knaif score | 0.9536 / 0.9865 | 0.9536 / 0.9865 |
| Coverage | 1.0 | 1.0 |

`evalsuite row-diff` of the two boards: **194 / 194 same grade, same plan, same command**
([`comparison.json`](comparison.json)).

The outcome accuracy, 0.9536, is also the accepted Python run's on these 194 rows (the 4B
snapshot's `source_run`, measured 2026-10-18), so the subset cell on this build regresses no
row-level aggregate.

`record` wrote a few more replies than utterances: the repair retry's second model call has its
own prompt and so its own reply, which `reuse` served as designed.

## What it means for the plan

- The binary half (record / replay by prompt) and the lane half (`native --store`) work together
  on a real model: a second pass plans nothing and grades identically.
- On a GPU subset cell the saving is the planning (here ~97 s of a ~5 min cell); execution and
  the live cold-start rows remain. On the CPU, where planning is ~13 s a row, the saving is most of
  the cell (Part I, I10).
- The board's entry-point text still described mock replay in store mode; corrected after this run
  (`LaneConfig.store_entry_point`).
