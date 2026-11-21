# Release evidence in CI: the CPU cells and the Linux daemon check

**Status:** Draft · **Created:** 2026-11-03 · **Completed:** —
**Owner:** evals / release · **Ref:** [faster-release-evidence](2026-10-14-faster-release-evidence.md)
(Part C, *C5*) · [release-1.4.0](2026-10-26-release-1.4.0.md) · [RELEASE.md §4](../RELEASE.md)
**Release:** main

> **Status note:** asked for by the owner on 2026-11-03, during 1.4.0's second candidate: the
> release's evidence is faster than 1.3.0's but still takes a lot of time; can the checks move to CI?
> Decided the same day: plan it for **after 1.4.0**. Nothing here changes 1.4.0, whose rules were
> registered before its runs. A release adopts this only through a Part I amendment written before
> its freeze (T6).

**Goal:** the CPU L4 cells (Windows and Linux) and the Linux daemon check run on GitHub's runners,
sharded, in about an hour of wall time, leaving the release box with the GPU cells and L3 only.

## Why — where 1.4.0's evidence time went

Machine time of the second candidate's stages (`COMPLETE` of each run, void stages left out):

| Stage | Windows | Linux (WSL, same box) |
|---|---|---|
| GPU cells (CUDA, Vulkan; Linux CUDA) | ~35 min | ~20 min |
| CPU cells | ~1 h 25 min | ~2 h 20 min (the store held no Linux CPU reply) |
| Live re-runs (I5) | ~15 min | ~8 min |
| L3 (CUDA) | ~50 min | — |
| Daemon plan equality | ~10 min (CUDA) | ~3 h (CPU, as I7 says) |

About 3 h on Windows and 6 h on Linux, run one after the other on one machine. The CPU work
(cells and the Linux daemon check, ~7 h) is two thirds of it and is exactly what GitHub's runners
can do. The Windows run's wall time was ~23 h; the rest was pauses, two void stages and decisions,
which no runner shortens.

## What T0 already measured (faster-release-evidence, C5, 2026-10-17)

`evidence-cell.yml`, the published 1.3.0 files, 4B ffmpeg, 100 rows: on Windows and Linux runners
(4 cores) **100 / 100 rows the same grade** as this box, **93 / 100 the same plan** (7 differ in
argument detail, some between two runners as well), **~3.1× slower per row** (~50 s). macOS runners
(M1, 3 cores) timed out on 16 of 20 rows: Metal and the Mac stay on the Mac.

## What moves and what stays

| Check | Where | Why |
|---|---|---|
| L1 / L2 contracts | CI, every PR (already) | — |
| CPU L4 cells, Windows and Linux | **CI, sharded** | CPU-bound; free runners for a public repo |
| Linux daemon plan equality | **CI, sharded**, or this box on CUDA (decision D2) | 3 h on this box's CPU |
| CUDA / Vulkan L4 cells | this box | no GPU on standard runners |
| L3 | this box, CUDA | 50 min; on CPU runners it is hours and a different measurement |
| Windows daemon (CUDA), macOS (Metal, CPU, L3, daemon) | this box, the Mac | as today |

## Decisions for the owner (before T1)

- **D1. How the frozen Windows and Linux files reach the runners.** They are built and signed on the
  release box, not in CI. Options: a draft GitHub release holding the freeze's files (downloadable
  with the workflow's token, never public until published), or an upload to the evidence workflow
  run itself. Either way each job checks the sha256 the run script names.
- **D2. Linux daemon check:** sharded on CI CPU (keeps I7's "Linux CPU"), or on this box's CUDA in
  WSL (~10 min, an I7 change, no CI work).
- **D3. The result store on CI.** Runners' CPUs differ from each other (C5), and the store key names
  the platform, not the CPU. Proposed: CI cells neither read nor write `evals/store`; every CI row
  is planned fresh, which sharding makes cheap.
- **D4. The measurement condition (C3).** A CPU cell measured on a GitHub runner stands for that OS's
  CPU cell; the runner's CPU model, cores and image are recorded next to the board. About 7% of plans
  may differ from this box's, so per-row decisions like 1.4.0's can still arise; each goes to the
  owner with both plans and commands, as I3 already says.

## Tasks

- [ ] **T1. The frozen files as input.** `evidence-cell.yml` takes the frozen build (D1) and the
  sha256 from the run script, not only a published release's files.
- [ ] **T2. Shards that keep I5.** Each shard batch-plans its rows in one model load and runs its
  share of the committed cold-start sample live; a mismatch marks the cell for a live re-run, as on
  the box.
- [ ] **T3. The join job.** Compose the shards into one board, run safety on the binary over the
  whole safety corpus (never sharded), run `accept-native --population subset`, upload the board,
  the verdict and every runner's machine record. Nothing is written back: the acceptance record is
  written from the downloaded files, by a person, as today.
- [ ] **T4. Daemon plan equality on CI** (if D2 says CI): sharded, byte-identical comparison per
  shard, joined like T3.
- [ ] **T5. The run script.** A stage that dispatches the workflow for the freeze and one that
  fetches and checks its artifacts into the run folder (`ci` and `ci-fetch`), keeping one stage per
  invocation and the `COMPLETE` log.
- [ ] **T6. Part I amendment.** I1 (device), I7 (if D2 moves it) and I8 (D3), worded for the owner,
  carried into the run-script template; `test_release_evidence_template.py` keeps them equal.
- [ ] **T7. Trial against 1.4.0, before any release relies on it.** Run all eight CPU cells on CI
  against the published 1.4.0 files and compare row by row with this box's 1.4.0 CPU boards. Rule
  written before the run: every row the box passed passes on CI, or the difference is shown to the
  owner with both plans; wall time and runner minutes recorded.
- [ ] **T8. Docs.** RELEASE.md §4 and the TODO item this replaces.

## Not in scope

GPU runners (paid), rented machines (C6 priced them as the fallback), and macOS on GitHub's runners.
