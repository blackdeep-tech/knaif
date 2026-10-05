# Daemon mode and prompt-prefix reuse

**Status:** Draft · **Created:** 2026-10-01 · **Completed:** —
**Owner:** native CLI · **Ref:** [TODO.md](../TODO.md) (*Daemon mode*; *Inference latency: daemon +
prompt-prefix KV reuse*) · [PERFORMANCE.md §6](../PERFORMANCE.md) ·
[release-1.2.1](2026-09-30-release-1.2.1.md) (*Deferred*)
**Release:** 1.3.0 · release index: [release-1.3.0](2026-09-30-release-1.3.0.md)

**Goal:** Cut the time of a `knaif run` by keeping the model loaded between runs (daemon mode)
and by not re-processing the fixed part of the prompt on every request (prefix reuse), without
changing a single plan the model produces.

Proposed for **1.3.0** (owner, 2026-10-01). Not assigned yet: 1.3.0's scope is decided in its own
session, and this plan goes into that index's scope only then. Too big for the 1.2.1 patch: it
adds CLI surface, a long-running process, installer changes, and needs the minor release's gates.

---

## Where the time goes today — measured 2026-10-01

One native `knaif run ffmpeg "convert clip.mov to mp4" --dry-run` (plan only, no ffmpeg run), this
box: Windows 11, RTX 5080, native Windows (not WSL), 1.2.1 dev build, `knaif-qwen3-4b-v2`. One
warm-up, then 5 runs (CPU: 2); medians, all runs within ±3%. Phases from `KNAIF_TIMING=1`; start-up,
the post-inference stretch and shutdown from timestamping each output line.

| ms | CUDA | Vulkan | CPU |
|---|---:|---:|---:|
| Start-up: process, GPU backend start, device probe, skill load (process + skills alone: ~12 ms) | 152 | 203 | ~200 |
| Model load (`load_from_file`) | 1053 | 1170 | 1741 |
| Context creation | 17 | 220 | 89 |
| **Prompt processing** (2,445 tokens) | 236 | 255 | **6,415** |
| **Generation** (32 tokens) | 157 | 212 | 2,251 |
| After inference, before the result prints (most likely freeing the model from GPU memory — `build_plan` drops the session first; ffprobe alone is 29 ms) | 339 | 405 | ~300 |
| Shutdown | 48 | 32 | — |
| **Whole run** | **2,004** | **2,465** | **10,953** |

CPU start-up/after-inference are split from the 524 ms remainder by the GPU builds' proportions,
not measured separately.

### What each change would save on this box

| | CUDA | Vulkan | CPU |
|---|---|---|---|
| **Daemon** (no start-up, load, context, model free, shutdown per run) | ~1.6 s of 2.0 → **~0.4 s** | ~2.0 s of 2.5 → **~0.5 s** | ~2.3 s of 11.0 → ~8.7 s |
| **Prefix reuse** (process only the request at the end of the prompt) | ~0.23 s | ~0.25 s | **~6.3 s** |
| Both | ~0.2 s | ~0.25 s | ~2.4 s |

**The ordering question the TODO asked is answered here, and it depends on the backend:**
- **GPU:** the daemon is almost all of the win (1.6 of 2.0 s on CUDA). The TODO's 1.9 s "CUDA context
  init" was a WSL figure; on native Windows the whole start-up is 152 ms. The cost a daemon removes
  is the model load (and freeing it), not CUDA start-up.
- **CPU:** prefix reuse is the bigger win by far (6.3 of 11 s). A user with no GPU gains most from it.
- So the two are worth doing together, as the TODO said; if one must come first, it is the daemon
  for GPU users and prefix reuse for CPU users.

The prompt is 2,445 tokens here (the TODO's 3,938 was an older, longer prompt). Linux bare metal is
not measured yet; the daemon's design should not depend on it, but quote no Linux number without it.

## What has to be designed

- **Surface:** how the daemon starts (on first `run`, explicitly, or at login), how it is stopped,
  how a user sees it is running. New subcommands or flags; their names are a user-facing decision.
- **Who may talk to it:** a local socket (Unix) or named pipe (Windows) that any process of another
  user could reach would let it plan and run commands. Access must be limited to the user who
  started it, on each OS.
- **Lifecycle:** idle shutdown (it holds ~3 GB of GPU memory while idle), a stale daemon after an
  upgrade (version handshake), a crash mid-request (the CLI falls back to loading the model itself),
  two CLIs at once (one request at a time, or a queue).
- **Installer:** setup refuses while `knaif-cli-running` is held (AppMutex, tested in the 1.2.1
  clean room). A resident daemon would hold it forever, so install, upgrade and uninstall must stop
  the daemon first, on Windows and Linux. macOS joins in 1.3.0 too.
- **No leak between requests:** the model's context (KV cache) must be fully reset before each
  request, or one request changes the next one's plan.
- **Prefix reuse correctness:** reusing the processed prefix changes how decoding is split, which can
  shift floating-point results; under greedy decoding a near-tie can flip to a different plan.

## Gates

- `eval-success` on both skills and `just parity` (L3) with the daemon and prefix reuse on, against
  the current snapshots: **no plan may change**. Any change is a finding to explain, not noise.
- The full L4 matrix and the clean room with the installer stopping and upgrading over a running
  daemon. These are the minor release's gates, which is why this cannot be a patch.
- The timing table above re-run with the same request, as the before/after evidence.

## Tasks

### - [ ] D0 — Owner decisions

Surface (start/stop and their names), idle timeout, whether the daemon is on by default.

### - [ ] D1 — Daemon

### - [ ] D2 — Prefix reuse

### - [ ] D3 — Installer and lifecycle

### - [ ] D4 — Gates and the before/after timing
