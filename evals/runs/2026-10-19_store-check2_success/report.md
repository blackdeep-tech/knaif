# Result store, second real run — 2026-10-19

Plan: [2026-10-14-faster-release-evidence](../../../docs/plans/2026-10-14-faster-release-evidence.md),
after T5's last three fixes and the platform-aware store (T4). Script: [`run_all.sh`](run_all.sh),
criteria written in it before any result.

**Question.** Since this morning's run ([`2026-10-19_store-check_success`](../2026-10-19_store-check_success/report.md))
the lane changed in five ways that touch a real cell: every process that loads the model runs under
`--verbose` and must put its layers on the probed device; a recording batch writes into a staging
folder, promoted only after that check; the store's namespace names the platform; every lane
process runs with `KNAIF_NO_DAEMON=1`; and the binary refuses a re-recorded reply that differs.
Does a store cell still plan, record, reuse and grade exactly as before?

**What was measured.** A developer CUDA build of this checkout (`target/release-cuda/knaif.exe`,
rebuilt 13:37 with the Rust fix; lane `native-cli`, unpackaged: a check, not L4 evidence),
`knaif-qwen3-4b-v2`, RTX 5080, in process. The committed release subsets (ffmpeg 194, documents 80
utterances), the morning run's ffmpeg cold-start sample (copied) and a new 30-row documents one.
The store was this folder's own (ignored). 11.5 minutes of GPU in all.

## Result: every criterion met

| | ffmpeg `record` | ffmpeg `reuse` | documents `record` | documents `reuse` |
|---|---:|---:|---:|---:|
| Placement | CUDA0 | CUDA0 | CUDA0 | CUDA0 |
| Replies measured / reused | 194 / 0 | **0 / 194** | 80 / 0 | **0 / 80** |
| Model planning (one load) | 95.8 s | none | 23.8 s | none |
| Row execution (incl. 30 live rows) | 195.9 s | 191.1 s | 61.8 s | 59.6 s |
| Cold start: live plan = stored plan | 30 / 30 | 30 / 30 | 30 / 30 | 30 / 30 |
| Live rows without a plan | 0 | 0 | 0 | 0 |
| Outcome accuracy / knaif score | 0.9536 / 0.9865 | 0.9536 / 0.9865 | 0.9750 / 0.9862 | 0.9750 / 0.9862 |
| Coverage | 1.0 | 1.0 | 1.0 | 1.0 |

- **No VOID anywhere**: the recording batches and all 60 live rows reported their layers on CUDA0.
- **The store**: one namespace, whose `key.json` names `platform: windows-x64`; 280 reply files for 274
  utterances (a repair retry's second call has its own prompt: 5 ffmpeg, 1 documents), 280
  `recorded.jsonl` lines, and no staging folder left behind.
- **Safety** (ffmpeg, on the binary, every row device-checked): 11/11 refused, 0 breaches.

`evalsuite row-diff`:

| Boards | Rows | Same grade | Same plan | Same command |
|---|---:|---:|---:|---:|
| ffmpeg record vs reuse | 194 | 194 | 194 | 194 |
| documents record vs reuse | 80 | 80 | 80 | 80 |
| ffmpeg record vs **this morning's** record | 194 | 194 | 194 | 194 |

## What it means for the plan

- The per-process device check, the staged recording and `--verbose` on live rows change nothing a
  board records: the same 194 ffmpeg rows grade, plan and render exactly as this morning.
- The platform in the key costs nothing on one platform; it only stops a reply from crossing to
  another one.
- A Windows CUDA subset cell is ~5 min for ffmpeg and ~1.5 min for documents when it records, and
  the planning (~2 min) disappears on reuse.
