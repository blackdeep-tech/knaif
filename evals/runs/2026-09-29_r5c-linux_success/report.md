# R5c Linux half — T14 (Linux CUDA cells) on RC `71884fd`: all four ACCEPTED

Run 2026-09-29 12:46–14:02 in WSL Ubuntu 24.04 on the same RTX 5080 as the Windows half: the
packaged `knaif-1.2.0-linux-x64.tar.gz` (`4fbba4a9…`, built from `71884fd` in the container),
unpacked fresh, the frozen Linux CUDA payload installed at T13 (`backend verify cuda`: ok), the
GGUFs on the Linux filesystem (hashes checked by the stage). One fresh process per request,
executing for real, `success` verifier, 8 threads; safety on the same binary. Rules and
pre-registration: `run_all.sh` (`d7704cf`, hardened after a Codex audit in `05d7dd0` before launch).

| Cell | Skill | Outcome (floor / Python) | Avg score (floor / Python) | Safety | Verdict |
|---|---|---|---|---|---|
| 4B Linux CUDA | ffmpeg | 0.9454 (0.9231 / 0.9431) | 0.9808 (0.9641 / 0.9841) | 11/11 | ACCEPTED |
| 4B Linux CUDA | documents | 0.9817 (0.9556 / 0.9756) | 0.9803 (0.9800 / 0.9818) | 9/9 | ACCEPTED |
| 1.7B Linux CUDA | ffmpeg | 0.9222 (0.8999 / 0.9199) | 0.9768 (0.9585 / 0.9785) | 11/11 | ACCEPTED |
| 1.7B Linux CUDA | documents | 0.9634 (0.9500 / 0.9634) | 0.9945 (0.9745 / 0.9945) | 9/9 | ACCEPTED |

Prediction (written before the run): 4B accepted on both skills; ~30% risk that the 1.7B misses a
thin ffmpeg slice. It did not: the 1.7B holds every slice on Linux CUDA, as on Windows CUDA.

**Windows CUDA vs Linux CUDA: 0 decision flips**, both models, both skills
(`<model>/flips_<skill>_windows_vs_linux_cuda.txt`). Same GPU, llama.cpp and model; the OS, driver
path and compiler differ, and the model decides identically. The small score differences come from
executing the plans, not from planning: only `ffmpeg_202` is graded differently (4B rows 0 and 3,
1.7B row 2). The model asks for a thumbnail at an invented time (10 s, 25 s) past the end of a
short clip instead of its midpoint, a wrong plan on both OSes; Windows' ffmpeg exits with an error,
Ubuntu's ffmpeg 6.1.1 exits 0 without a usable frame (outcome counted, artifact scored 0).

**Launch incident, before the run (no result affected).** The first launch passed `bash -lc 'cd
~/knaif && …'` through `Start-Process`, which split it at `&&`: the stage ran for ~40 s in the
Windows checkout over `/mnt/c`, replaced that checkout's unpacked Windows artifact (re-unpacked by
every Windows stage) and let `uv` strip files from the Windows `.venv`. Killed at once; the `.venv`
was reinstalled from the unchanged `uv.lock` (`uv sync --reinstall`; the core suite passes), the
stray files deleted, and the script now refuses to run from `/mnt` (`1316637`). Relaunched with
`wsl.exe --cd ~/knaif`.

## T15 — Linux CPU cells by reuse: NOT confirmed for either model; full Linux CPU runs follow

Run 2026-09-29 14:20–15:33, the Linux binary on the CPU (GPU hidden), one process per request,
over the samples drawn and committed before the run (`t15_sample_*.json`: 115 ffmpeg + 35
documents per model, seed 20260929). `t15_confirm.py` compared the plans with the Windows CPU plans
at the decision level (4B: the 2026-09-25 CPU plans, which T9a confirmed for the release binary;
1.7B: the T10 board).

| Model | ffmpeg (115) | documents (35) | Verdict |
|---|---|---|---|
| 4B | 4 decision flips | 0 | NOT confirmed |
| 1.7B | 6 decision flips | 3 | NOT confirmed |

Flips: 4B `ffmpeg_246#2`, `271#3`, `284#0`, `hard_016#1`; 1.7B `ffmpeg_094#1`, `099#1`, `115#0`,
`121#2`, `138#0`, `229#1`, `documents_073#0`, `119#0`, `129#0` (`<model>/cpu/<skill>_verdict.txt`).
By the pre-registered rule neither Windows CPU cell stands for Linux: both Linux CPU cells were
recorded as failing ("sample did not confirm", not waivable) and each model's full Linux CPU cell
runs instead (`run_all.sh t15full <model>`, pre-registered in `a4d1a7a` before any full run).
Prediction (written before the run): 0 flips for both models. Wrong.

The contrast with T14 is the finding: on CUDA the two OSes plan identically (0 decision flips over
1,025 requests per model), on the CPU they break ~3-5% of near-ties differently. The CPU backend is
the one component each OS builds separately (a different compiler, and the ggml CPU variants each
artifact carries), so its floating-point accumulation can differ where the GPU kernels do not.
