# Daemon mode and prompt-prefix reuse

**Status:** Active · **Created:** 2026-10-01 · **Completed:** —
**Owner:** native CLI · **Ref:** [TODO.md](../TODO.md) (*Daemon mode*; *Inference latency: daemon +
prompt-prefix KV reuse*) · [PERFORMANCE.md §6](../PERFORMANCE.md) ·
[release-1.2.1](2026-09-30-release-1.2.1.md) (*Deferred*)
**Release:** 1.3.0 · release index: [release-1.3.0](2026-09-30-release-1.3.0.md)

**Goal:** Cut the time of a `knaif run` by keeping the model loaded between runs (daemon mode)
and by not re-processing the fixed part of the prompt on every request (prefix reuse), without
changing a single plan the model produces.

In **1.3.0** (owner, 2026-10-05; proposed 2026-10-01). Too big for the 1.2.1 patch: it
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

- **No plan may change through the daemon.** Revised 2026-10-06 (owner asked what requires L3/L4
  with the daemon on — nothing does): the daemon carries only `(system, user) -> raw text`; prompt,
  retrieval, validation, repair, gates and execution stay in the CLI, and the fingerprint refuses a
  daemon whose model/build/backends/settings differ. So the check is a **plan-equality diff**:
  `plan --batch` over both corpora in-process and through the daemon, outputs byte-identical. The
  release's own L3/L4 run on the default (daemon off) path, as for any minor.
- The full L4 matrix and the clean room with the installer stopping and upgrading over a running
  daemon. These are the minor release's gates, which is why this cannot be a patch.
- The timing table above re-run with the same request, as the before/after evidence.

## Tasks

### - [x] D0 — Owner decisions

Surface (start/stop and their names), idle timeout, whether the daemon is on by default.

- 2026-10-05 (owner): **opt-in, started explicitly** — off by default; a `start`/`stop`/`status`
  surface; `run` uses a running daemon and otherwise loads the model itself; it shuts down after an
  idle timeout. Still open: the exact names and the timeout value.
- 2026-10-05 (owner): the daemon can also be started **from a run**: `knaif run <skill> "…" --daemon`
  starts it if none is running and serves that request through it; later runs use it without the
  flag. `--daemon` with a daemon already running just uses it.

- 2026-10-06 (decided while building, no owner answer was needed to proceed): the surface is
  `knaif daemon start | stop | status` plus `run --daemon`; the idle timeout is **10 minutes**
  (`--idle-minutes`); it is off by default.
- 2026-10-07 (owner): **signed off** — the names and the 10-minute timeout stand.

### - [x] D1 — Daemon

`apps/cli/src/daemon.rs`. Only inference crosses the process boundary: the daemon holds one
`LlmBackend` and answers `generate` over a loopback TCP socket; the CLI keeps building the prompt and
validating, repairing, gating and executing the plan, so a plan through the daemon is the in-process
plan by construction. Access: `127.0.0.1`, OS-chosen port, mutual proof of a random 256-bit secret in
`~/.knaif/daemon.json` (owner-only on Unix, the profile ACL on Windows) — never sent, server proves first. One request at a time (the backlog is the queue).
Handshake: a daemon is borrowed only if its model, build id (version + exe size + mtime) match and the
client has no generation override (`KNAIF_MAX_TOKENS`/`N_CTX`/`N_GPU_LAYERS`/`N_THREADS*`) and no
`KNAIF_NO_DAEMON`. Any mismatch, stale record, refused connection or mid-request death makes the CLI
load the model itself, including a daemon that dies mid-request. 20 unit tests (mock backend):
answers, no-token client, impostor on the port (never sees token or prompt), model/build/settings mismatch,
idle shutdown, strangers do not keep it alive, stalled and oversized connections, stale and replaced
records, start lock, fingerprint, proofs, fallback after the daemon dies.

*Codex audit, 2026-10-06 (read-only, 11 findings):* all taken except two, recorded here. Taken: token no
longer sent before the server proves itself; settings fingerprint (prefix reuse, model file, backends
folder); start lock + instance-checked record removal + kill a half-started child; bounded and
deadlined reads, control calls timed; per-process exclusive temp file, private state dir, profile-only
location; mid-request fallback; record removed only after the model is freed and `stop` waits for the
process to exit; saved/restored handle flags; OCR now runs the tesseract that was found; the prefix-cache
`unsafe` borrow now rests on an `Arc`. **Not taken:** binding an installation identity into the record
(uninstalling install A also stops a daemon started from install B — two installs of one user are not a
supported shape); Windows ACL hardening of the state folder beyond the profile's own (documented instead).

*Found while building it:* on Windows a child inherits the parent's inheritable stdout/stderr pipe
whatever its own stdio is set to, so `knaif run … --daemon | tail` never saw end-of-output while the
daemon lived. `spawn_detached` clears the inherit flag on the parent's std handles around the spawn.

### - [x] D2 — Prefix reuse (implemented, **off by default: it fails the gate**)

`LlamaCppBackend::with_prefix_reuse` / `KNAIF_PREFIX_REUSE=1` keeps the context and drops the KV cache
only after the longest common token prefix with the previous prompt. Measured 2026-10-06 on this box
(RTX 5080, CUDA, `knaif-qwen3-4b-v2`, `plan --batch` over the whole eval corpus, in-process):

| | reuse off | reuse on | plans that differ |
|---|---:|---:|---:|
| ffmpeg, 861 utterances | 6 m 48 s | 4 m 19 s (-37%) | **61** |
| documents, 164 utterances | 45.8 s | 34.9 s (-24%) | **4** |

The differences are real plan changes (`compress_video` loses `target_size_mb`, `pages: "-1"` becomes
`"all"`, an `output` is dropped, clarify wording), not noise in whitespace. Splitting the prompt into
different batches shifts floating-point results enough to flip near-ties under greedy decoding on a
Q4 model. **The gate for this plan is "no plan may change", so it stays off** and the daemon does not
enable it. It would also help less than the table suggests: the system prompt's tool list and examples
are chosen per request, so the shared prefix is the header, except on the repair retry (identical
system turn). Not planned further unless the owner wants to trade plan stability for speed.

### - [x] D3 — Installer and lifecycle

The daemon does **not** hold `knaif-cli-running` (a resident holder would make setup refuse to start
for as long as the model stays loaded). The Windows installer runs `knaif daemon stop` from
`PrepareToInstall` (before files are replaced) and from the uninstall step. Linux tarball/AppImage
have no installer step; stopping it first is documented (NATIVE.md §5.7). **Not yet run:** the clean
room with setup over a live daemon (a minor-lane gate, below) and macOS.

### - [ ] D4 — Gates and the before/after timing

Timing, same request as the table above (`convert clip.mov to mp4 --dry-run`, CUDA, this box):
in-process **2.64 s**; first run with `--daemon` 2.05 s (it loads the model once); later runs
**0.70 s**, same plan.

- [x] Plan-equality gate — **passed** 2026-10-06: `plan --batch` over both corpora (861 ffmpeg + 164
  documents utterances), in-process vs through the daemon, CUDA, 4B v2: **byte-identical** on both
  skills. Evidence: [evals/parity/2026-10-06_daemon-plan-equality](../../evals/parity/2026-10-06_daemon-plan-equality/README.md).
- [x] Linux functional check — **done** (owner, 2026-10-06). What remains is the clean room with setup
  run over a live daemon (a release gate, run with 1.3.0's clean room), so D4 stays unticked.

Linux, 2026-10-06 — WSL Ubuntu 24.04 on the same box, **CPU build** (`build_native_kind.sh cpu`),
`knaif-qwen3-1.7b-v2`, same request. Everything works: same plan, `daemon.json` 0600, the daemon
detached (own session, no tty), `stop` in 0.1 s with the process gone, `kill -9` then a run falls back
in-process and the stale record is removed, a world-writable state folder is refused. **But on CPU it
saves nothing measurable:** in-process 7.8 s vs 6.2–8.0 s through the daemon. `KNAIF_TIMING` shows why
— the model load is **129 ms** (an mmap from the page cache; nothing to upload to a GPU), context
creation ~0.4 s, and prompt decoding **5.6 s** of 2,445 tokens dominates in both paths. The daemon
removes the load, and on a CPU the load is not the cost; prompt processing is (D2's case, which fails
its gate). Linux with a GPU backend (where the load includes the upload) is not measured. Still to do: `eval-success` / L3 with the daemon on (no plan may change — by
construction it cannot, but the gate asks for the measurement), the full L4 matrix and the clean room
with setup over a running daemon.
