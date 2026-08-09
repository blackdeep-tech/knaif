# S2 — native before and after Workstream Q

The eval-parity measurement the plan was written to make: the same utterances through the native
runtime **before** and **after** the retrieval + example-selection port, with the pre-fix side
preserved by P2b rather than reconstructed.

## Design

| | |
|---|---|
| subset | **60 utterances** — all 41 chain-tagged, plus 19 sampled at seed 20260809 |
| model | `knaif-qwen3-4b-v1-q4_k_m.gguf`, pinned by path on every lane |
| pre-fix binary | `dist/staging/knaif-1.1.0-windows-x64` — planner-identical to this branch's pre-P0 state |
| post-fix binary | `target/debug/knaif` on `feat/native-parity` (Q1–Q5) |
| verifier | routing-only on plan envelopes, **every lane passed through `validate_plan`** so all fail at the same stage |

**Why a subset.** The Vulkan build fails on this machine — `vulkan-shaders-gen` dies inside a
nested CMake ExternalProject with `RC2136` from the Windows SDK's `rc.exe` — so inference is
CPU-bound at roughly 60 s/utterance. The full 847 would take ~14 hours; 60 takes one.

**Why both native lanes ran on CPU.** The P2b baseline was Vulkan. Comparing a Vulkan "before"
against a CPU "after" would conflate the backend with the change, since a floating-point argmax
tie can flip a greedy decode. Re-running the pre-fix binary on CPU costs an hour and makes the
delta attributable. It turned out not to matter — see below — but that was not knowable in advance.

## Results

| lane | outcome | tool present | invalid |
|---|---|---|---|
| native pre-fix (Vulkan) | 0.950 | 0.900 | 2 |
| native pre-fix (CPU) | 0.950 | 0.900 | 2 |
| **native post-fix (CPU)** | **0.950** | **0.900** | **2** |
| python (CUDA) | 0.950 | 0.900 | 2 |

| agreement (identical tool sequence) | |
|---|---|
| backend effect alone — pre-fix Vulkan vs pre-fix CPU | **60/60 (100%)** |
| Q effect, backend held constant — pre-fix CPU vs post-fix CPU | 56/60 (93.3%) |
| with Python, **before** Q | 56/60 (93.3%) |
| with Python, **after** Q | **59/60 (98.3%)** |

**Q moved no aggregate quality metric and raised per-row agreement with Python from 93.3% to
98.3%.** That is the expected and correct shape: P2c had already refuted the premise that native
planned worse, so there was no quality gap left for Q to close. What Q closes is *divergence* — and
per-row parity is the acceptance criterion this plan settled on, precisely because an aggregate can
hide offsetting errors.

**Backend is not a confound.** Vulkan and CPU produced identical plans on all 60 utterances. Worth
recording beyond this run: it means the 847-utterance Vulkan baseline is directly comparable to CPU
runs, so a future S run need not re-measure the "before".

### The four rows Q changed

Three moved toward Python; the fourth is the one row still disagreeing.

| row | before | after | |
|---|---|---|---|
| `ffmpeg_hard_017` | **invalid plan** | `adjust_speed → strip_audio` | now matches Python |
| `ffmpeg_hard_014` | `convert_video → strip_audio` | `strip_audio → convert_video` | now matches Python |
| `ffmpeg_hard_005` | `resize → compress → strip_audio` | **invalid plan** | matches Python (also invalid) |
| `ffmpeg_hard_005` | `resize → strip_audio` | `resize → compress` | **still differs** — Python emits `resize → compress → strip_audio` |

Q repaired one previously invalid plan and introduced another, so the invalid count stays at 2 on
different rows. The single residual disagreement is a dropped third step on a `chain3` row, not a
routing error.

## An operational note that cost an hour

The first pre-fix run died silently after 2 of 60 utterances. The cause was not the runtime: a
`git commit` run while the job was in flight, whose pre-commit hooks **stash unstaged files**,
pulled the output file out from under a process that was writing to it. Do not commit while a
background job writes into the working tree.
