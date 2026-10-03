# macOS L4 — the metal .zip on Apple Silicon: 4B accepted on both skills, 1.7B ffmpeg misses one slice

Run 2026-10-03 04:20–09:35 on an M1 Pro (8P + 2E, 16-core GPU, 16 GB, macOS 27.2), the Mac's list
step 5 and step 6 of [the macOS support plan](../../../docs/plans/2026-08-02-macos-support.md) §0
(C4, D14). The packaged `knaif-1.2.0-macos-arm64.zip` (`b07bbf5a…`), built by `just package-native
metal` from `880a576` (the `mac/build-fixes` tree) in a checkout outside `~`, unpacked fresh by
every stage; the GGUFs checked against their published hashes. One fresh process per request,
executing for real, `success` verifier; safety on the same binary. Decision rules and procedure:
`run_all.sh`, written before any stage ran.

## Metal (step 5): full L4 cells

| Cell | Skill | Outcome (floor / Python) | Avg score (floor / Python) | Safety | Verdict |
|---|---|---|---|---|---|
| 4B macOS Metal | ffmpeg | 0.9384 (0.9231 / 0.9431) | 0.9857 (0.9641 / 0.9841) | 11/11 | ACCEPTED |
| 4B macOS Metal | documents | 0.9756 (0.9556 / 0.9756) | 0.9851 (0.9800 / 0.9818) | 9/9 | ACCEPTED |
| 1.7B macOS Metal | ffmpeg | 0.9187 (0.8999 / 0.9199) | 0.9780 (0.9585 / 0.9785) | 11/11 | **NOT ACCEPTED** |
| 1.7B macOS Metal | documents | 0.9634 (0.9500 / 0.9634) | 0.9945 (0.9745 / 0.9945) | 9/9 | ACCEPTED |

Every board ran on `MTL0` (861 + 164 requests per model, coverage 1.0). Verdicts:
`<model>/metal/<skill>_verdict.txt`; the records are in `evals/acceptance/<skill>.json` under
`<model>|macos|mtl`.

**The 1.7B ffmpeg miss is one slice by one row:** `batch` 25/29 (0.862) against a floor of 0.896,
which needs 26. The aggregates clear their floors. Three of the four failed `batch` rows
(`ffmpeg_077#1`, `206#1`, `229#0`: a `plan` answered with a `clarify`) fail on every 1.2.0 platform
too. The fourth, `ffmpeg_229#4`, passes on Windows and Linux CUDA and fails on Windows Vulkan and
Windows CPU as well as here. The same 1.7B scores `batch` 26/29 on CUDA, 25/29 on Windows Vulkan,
24/29 on Windows CPU — and 1.2.0 recorded Windows Vulkan 1.7B as NOT ACCEPTED on exactly this
threshold (`0.862 < 0.896`, n=29). This is the 1.7B's known thin slice meeting a near-tie that only
CUDA breaks the right way, not a macOS defect. Per the pre-registered rule it is recorded and goes
to the owner; no bar was moved.

Time to artifact on Metal (one process per request, model load included, plan rows, warmup
excluded): 4B p50 7.98 s ffmpeg / 3.36 s documents; 1.7B p50 4.05 s / 1.67 s. These include the
executed ffmpeg/document work and are not the PERFORMANCE.md methodology (step 8).

## Row flips against Windows and Linux (step 6)

`scripts/l4_rows.py compare evals/parity/1.2.0-l4-rows/<ref>.json <mac board> --list`, saved as
`<model>/flips_<ref>_vs_mac_<backend>.json`. Information, not a verdict.

| Model | Skill | Reference | Decision flips | both right | only ref right | only Mac right | both wrong | Grades differ |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 4B | ffmpeg (861) | Windows CUDA = Linux CUDA | 31 | 23 | 5 | 2 | 1 | 7 |
| 4B | documents (164) | Windows CUDA = Linux CUDA | 2 | 0 | 0 | 1 | 1 | 1 |
| 1.7B | ffmpeg (861) | Windows CUDA = Linux CUDA | 10 | 3 | 4 | 2 | 1 | 6 |
| 1.7B | documents (164) | Windows CUDA = Linux CUDA | 0 | 0 | 0 | 0 | 0 | 0 |
| 4B | ffmpeg CPU sample (115) | Linux CPU sample | 2 | 1 | 1 | 0 | 0 | 1 |
| 4B | documents CPU sample (35) | Linux CPU sample | 0 | 0 | 0 | 0 | 0 | 0 |
| 1.7B | ffmpeg CPU sample (115) | Linux CPU sample | 5 | 2 | 1 | 0 | 2 | 1 |
| 1.7B | documents CPU sample (35) | Linux CPU sample | 2 | 2 | 0 | 0 | 0 | 0 |

Windows CUDA and Linux CUDA agree with each other (1.2.0 found 0 flips between them), so the Mac
compares identically against both. Metal against CUDA flips 3.6% of the 4B's ffmpeg decisions and
1.2% of the 1.7B's — mostly between two plans that are both graded correct — where Windows CUDA
against Linux CUDA flipped none: the GPU kernels differ (C5's first confound), and greedy decoding
turns their different accumulation into a different choice at near-ties. Of the flips that change
correctness, CUDA is right more often (4B 5 vs 2, 1.7B 4 vs 2); the aggregate cost is what the
outcome column shows (4B 0.9384 vs Linux CUDA 0.9454). The CPU samples flip at most 5 of 115
against Linux CPU, the same order as 1.2.0's Linux-vs-Windows CPU sample (4–6 of 115).

## CPU sample (D14)

The 1.2.0 Linux sample rows (`t15_sample_<model>_<skill>.json`, 115 ffmpeg + 35 documents) on the
same tree with `libggml-metal.so` removed (D2; never `KNAIF_N_GPU_LAYERS=0`); every board ran on
`CPU`. Outcome: 4B 0.913 ffmpeg / 1.000 documents; 1.7B 0.930 / 0.943. How a macOS CPU cell is
composed or accepted from these is the owner's call, as it was for Linux (T15s); nothing was composed
and no CPU verdict was recorded.

## Not committed

The boards, per-request logs and safety results stay on the Mac: they carry utterances and local
paths (the reason `evals/parity/1.2.0-l4-rows/` exists). The acceptance records name them by path.
