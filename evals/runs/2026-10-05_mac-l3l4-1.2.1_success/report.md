# macOS L3 and L4 on the merged 1.2.1 tree — the same decisions as 1.2.0

Run 2026-10-05 10:46–18:57 on the M1 Pro, macOS 27.2 Beta 2 (build `26B5091g`). Merging `main` (release
1.2.1) into `feat/macos-support` (`bdd01b5`) changed native code, so every macOS L3/L4 record read as
stale. This run re-measures them on that tree with the same procedure and decision rules as
`evals/runs/2026-10-03_mac-l4_success` (`run_all.sh`, `run_l3.sh`; `run_stages.sh` runs them in order).

Build: `just package-native metal` from a fresh checkout of `bdd01b5` outside `~` gave
`knaif-1.2.1-macos-arm64.zip`; its `knaif` is a new binary (sha256 `c67fcb99…`, 1.2.0's was
`d3e91b27…`). The GGUFs were checked against their published hashes.

## L4

| Cell | Requests | Outcome | Knaif score | Safety | Verdict |
|---|---:|---|---|---|---|
| 4B Metal ffmpeg | 861 | 0.9384 | 0.9857 | 11/11 | **ACCEPTED** |
| 4B Metal documents | 164 | 0.9756 | 0.9851 | 9/9 | **ACCEPTED** |
| 1.7B Metal ffmpeg | 861 | 0.9187 | 0.9780 | 11/11 | **NOT ACCEPTED** (`batch` 25/29 < 0.896) |
| 1.7B Metal documents | 164 | 0.9634 | 0.9945 | 9/9 | **ACCEPTED** |
| 4B CPU sample | 115 + 35 | 0.9130 / 1.0000 | 0.9789 / 0.9926 | — | not graded |
| 1.7B CPU sample | 115 + 35 | 0.9304 / 0.9429 | 0.9694 / 0.9905 | — | not graded |

Against the 1.2.0 run (`comparison_vs_1.2.0.txt`): **all 2,350 requests have the same plan and the
same grade**, so the verdicts are unchanged, the 1.7B `batch` miss included. The four
`<model>|macos|mtl` cells in `evals/acceptance/*.json` now name this run, and their tree fingerprints
match the merged tree.

Latency, p50 time to artifact: Metal 4B ffmpeg 7979 → 8006 ms (+0.3%), 4B documents 3363 → 3550 ms
(+5.6%), 1.7B ffmpeg 4052 → 4275 ms (+5.5%), 1.7B documents 1675 → 1759 ms (+5.0%); CPU +5% to +13%.
The same morning, the 1.2.0 binary on Beta 2 ran at Beta 1's speed
(`evals/runs/2026-10-05_mac-beta2-probe_success`, not committed), so the extra time most likely comes
from the 1.2.1 binary, not from the OS. Recorded, not gated.

## L3

Native: the packaged 1.2.1 binary. Python: `llama-cpp-python` 0.3.36 on Metal. Command mode, full
corpora, 1.2.0's bounds (`run_l3.sh`). Reports: `evals/parity/2026-10-05_mac-l3-1.2.1-<model>-<skill>/`.

| L3 | equivalent / gated | port bugs | plan disagreement (bound) | verdict |
|---|---|---|---|---|
| 4B ffmpeg | 298 / 298 (30 not comparable) | 0 | 0.00% (4.11%) | PASS |
| 4B documents | 141 / 143 | **1** (`documents_105`) | 0.70% (1.83%) | **FAIL** |
| 1.7B ffmpeg | 298 / 298 (30 not comparable) | 0 | 0.00% (4.11%) | PASS |
| 1.7B documents | 142 / 143 | 0 | 0.70% (1.83%) | PASS |

The same as on 1.2.0. `documents_105` is still native's dry run accepting a `reorder_pages` order its
execution rejects; it reproduces on 1.2.1 with the mock backend, so 1.2.1 did not touch it. The 0.70% is
`documents_057`, as before.

During the 1.7B ffmpeg L3 cell the run was paused for about 10 seconds (SIGSTOP/SIGCONT, at the
contributor's request) and resumed; the request in flight completed normally and the cell passed.

## Records

The boards, logs and safety results stay on the Mac (utterances and local paths), as in 1.2.0.
