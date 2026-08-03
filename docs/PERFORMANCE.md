# Knaif Performance Scorecard

**What this doc is:** the one place that answers *"how fast is knaif — on which hardware, which
runtime, which backend, which model?"* A **living reference**, not a dated experiment log.

**What it is not:** a how-to ([EVAL_FRAMEWORK.md](EVAL_FRAMEWORK.md) for the eval suite,
[NATIVE.md](NATIVE.md) for the runtime) and not a quality study
([audits/2026-07-01-finetuning-study-findings.md](audits/2026-07-01-finetuning-study-findings.md)).
Per-run evidence: [evals/INDEX.md](../evals/INDEX.md). For *end-to-end* latency measured
against premium cloud agents on the same requests (~1.2 s local vs 11–16 s), see
[the 2026-07-02 agent comparison](experiments/2026-07-02-agent-vs-knaif-realworld.md) —
those are `3070L` numbers, and §6 explains why a cold CLI invocation costs ~2.6 s more than
the inference figure quoted there.

> **⚠️ Never quote a latency number from this repo without naming the machine.** Most pre-2026-07-14
> numbers were measured on an **RTX 5080** and don't say so. The project now runs on an **RTX 3070
> Laptop**, 2.5–4× slower. Worse, one *conclusion* in those docs — ["CUDA is required on
> NVIDIA"](#2-backend-cuda-vs-vulkan-vs-cpu) — turns out to be **true only on Blackwell**.

Last measured 2026-07-14 (Qwen3-4B q4_k_m, ffmpeg skill prompt of **3938 tokens**, 32-token
generation, `n_ctx = 8192`, fresh process, median of warm reps). **Linux CUDA payload added
2026-08-01** (§2, §6) — same model and prompt, measured on `3070L-WSL`.

---

## 1. Reference machines

| Tag | GPU | Arch | VRAM | CPU |
|---|---|---|---|---|
| **`5080`** | RTX 5080 (desktop) | Blackwell, sm_120 | 16 GB | 32 threads |
| **`3070L`** | RTX 3070 **Laptop** | Ampere, sm_86 | 8 GB | AMD, **8 physical / 16 logical** |
| **`3070L-WSL`** | the **same box** as `3070L`, under WSL2 | Ampere, sm_86 | 8 GB | same (driver 610.88) |
| **`M3P`** | Apple M3 Pro, integrated (Metal), 18-core GPU | Apple Silicon, arm64 | 18 GB unified (soft-capped via `iogpu.wired_limit_pct`) | Apple M3 Pro, 6 P + 6 E |

`M3P` — macOS 26.6 (build 25G72), Xcode 26.6 / CLT 26.6.0 (full Xcode installed; §7 of the
[2026-08-02 macOS support plan](plans/2026-08-02-macos-support.md) M3 still needs to test whether
CLT alone suffices). `MACOSX_DEPLOYMENT_TARGET=12.0` is the chosen floor for the shipped artifact
(D9) — this machine's own OS is far newer, so it proves nothing about the floor by itself; the E3
clean-room VM must run the floor OS.

`3070L-WSL` is the **Linux** artifact measured on the `3070L` hardware through WSL2, not a third
machine. Its GPU numbers land ~10–12% behind bare-metal `3070L` (§2), which is small enough to
trust for *shape* — but see the §6 warning: **one slice of its wall-clock budget is a WSL artifact
and must not be quoted as Linux.**

`3070L` also exposes an **AMD iGPU as Vulkan device 0**. llama.cpp correctly selects `Vulkan1`
(the NVIDIA) — but *verify it in any new Vulkan measurement*, or you will benchmark the iGPU.

### Hardware conversion factor (`5080` → `3070L`)

Paired per-utterance comparison on the ffmpeg corpus (same GGUF, same code, full GPU offload):

| Model | `5080` p50 | `3070L` p50 | Slowdown | Routing control |
|---|---:|---:|---:|---:|
| Qwen3-4B SFT-v3 Q4_K_M | 350 ms | **1352 ms** | **3.86×** | 311/313 identical (99.4%) |
| Qwen3-1.7B SFT-v3-flat Q6_K | 260 ms | **659 ms** | **2.53×** | 309/313 identical (98.7%) |

**Rule of thumb: a `5080` number × 2.5–4 ≈ the `3070L` number** (the bigger model degrades more).

**Why this is hardware, not drift:** the archived 5080 scoreboards keep per-row `latency_ms`, so
the *same utterances* are compared one-for-one, and only rows where both machines produced a plan
are counted. Routing came out ~99% identical on both arms and outcome accuracy did not drop
(4B: 0.903 → 0.914) — same model, same decisions, only slower. Full offload confirmed (37/37
layers); not a VRAM spill on the smaller card.

Evidence: the `runs/2026-07-14_rtx3070-speed_cheap` row in [evals/INDEX.md](../evals/INDEX.md),
which carries the paired per-utterance comparison. The run directory itself is generated scratch and
is not in the repository — `.gitignore` excludes `evals/**` bar the durable summaries, so INDEX.md is
the committed record for this and most other runs. Link to the row, never to a run directory.

> **Quality never moves with hardware — only speed does.** Every accuracy number in this repo
> survived the machine change. Greedy decoding on the same GGUF makes the same plan.

---

## 2. Backend: CUDA vs Vulkan vs CPU

**Headline: on Ampere, Vulkan is as fast as CUDA. On Blackwell, it collapses.** This reverses a
shipped decision, so it is the most load-bearing fact here.

### `3070L` (Ampere) — all six runtime × backend cells

| Runtime | Backend | prompt decode | generation | inference total |
|---|---|---:|---:|---:|
| **native** (Rust) | **CUDA** | **3522 tok/s** | **90.9 tok/s** | **1524 ms** |
| **native** | **Vulkan** | 3329 tok/s | 88.6 tok/s | 2143 ms |
| native | CPU (fixed, §3) | 68 tok/s | 6.1 tok/s | ~63 000 ms |
| **python** | **CUDA** | ~1800 tok/s | ~70 tok/s | ~2500 ms |
| **python** | **Vulkan** | 1879 tok/s | 72.9 tok/s | ~2520 ms |
| python | CPU (honest, §4) | 38 tok/s | 6.7 tok/s | ~110 000 ms |

### `5080` (Blackwell) — from [the 2026-07-07 investigation](plans/2026-07-07-inference-backend-performance.md), native only

| Backend | prompt decode | generation | inference total |
|---|---:|---:|---:|
| CUDA | ~4600 tok/s | ~80 tok/s ⚠️ | ~1350 ms |
| Vulkan | ~1040 tok/s | **~5.7 tok/s** | ~9700 ms |
| CPU | ~520 tok/s | ~5.9 tok/s | ~13 000 ms |

### `3070L-WSL` (Ampere) — the **Linux** artifact, 1.1.0 opt-in CUDA payload

Measured 2026-08-01 on the shipped `knaif-1.1.0-linux-x64.tar.gz` plus the
`knaif-1.1.0-linux-x64-cuda-backend` payload staged via `$KNAIF_BACKENDS_DIR`. Same prompt (3938
tok), same GGUF, 12 warm reps. The payload loaded, selected `CUDA0`, and offloaded 37/37 layers.

| Backend | prompt decode | generation | inference total | wall |
|---|---:|---:|---:|---:|
| **CUDA** (opt-in payload) | 1233 ms (**3194 tok/s**) | 398 ms (**80.4 tok/s**) | **1709 ms** | ~5.2 s |
| ngl=0, CUDA registered (**hybrid**, §4) | 7296 ms (540 tok/s) | 6460 ms (5.0 tok/s) | 14 274 ms | 19.0 s |
| true CPU (no GPU device present) | 76 382 ms (51.6 tok/s) | 5702 ms (5.6 tok/s) | 83 617 ms | ~81 s |

- **CUDA is ~49× faster than honest CPU on inference**, ~15.6× on wall clock. This is the
  headline number for the 1.1.0 opt-in payload.
- The middle row reproduces the §4 `op_offload` trap on Linux: `n_gpu_layers=0` still ran prompt
  matmuls on the GPU, giving **540 vs 51.6 tok/s** — a 10.5× gap between "CPU" and *actual* CPU.
- vs bare-metal `3070L` CUDA: prompt 3194 vs 3522 tok/s, generation 80.4 vs 90.9 tok/s, inference
  1709 vs 1524 ms. **WSL costs ~10–12% on compute**, consistently.

### ⚠️ A WSL "Vulkan" run is silently a **CPU** run

WSL exposes **no NVIDIA Vulkan ICD** (`/usr/share/vulkan/icd.d/` carries only Mesa's `lvp`,
`radeon`, `nouveau`, …). The default artifact's Vulkan backend loads fine and then reports:

```
ggml_vulkan: No devices found.
```

…after which llama.cpp falls back to CPU **without failing**. That is how the true-CPU row above was
obtained — it was collected as a "Vulkan" arm and had to be relabelled. **No Vulkan number in this
document may be measured under WSL.** Same failure mode as the `op_offload` trap in §4: a backend
that silently isn't the one you think you are benchmarking.

### What this means

- **Vulkan's catastrophic generation speed was Blackwell-specific.** On the `5080` Vulkan
  generated at 5.7 tok/s — *tied with pure CPU*, absurd for a discrete GPU. On Ampere it runs at
  **88.6 tok/s: ~15× faster than CPU and within 3% of CUDA.** This **closes the open question**
  that doc raised ("worth retesting on an older NVIDIA GPU"): the sm_120 / coopmat2 path was the
  culprit.
- **So "CUDA is required on NVIDIA" is too strong** — it is required *on Blackwell*. On Ampere the
  Vulkan artifact is a fine NVIDIA shipping target, and it is one build for all vendors with no
  CUDA redist DLLs.
- **CPU is not interactive on any machine** (a minute or more per request). Fallback only.

### ⚠️ Vulkan's first run pays a ~36 s shader-compilation tax

The **first-ever** Vulkan run compiled its pipelines during the first decode: **38.3 s** prompt
decode vs **2.1 s** on every run after (the driver caches compiled pipelines across processes).
The first launch after install will look hung. This does **not** contradict the 5080 doc's negative
`VkPipelineCache` result — that correctly showed caching can't fix Blackwell's slow *compute*; here
compute is fine and one-time *compilation* is the only cost. **Warm the cache at install time**, or
the user's first request eats it.

### ⚠️ The `5080` native CUDA numbers are suspect — do not derive a hardware ratio from them

The `5080` table reports **80 tok/s** generation. The `3070L` measures **91 tok/s** — a laptop
Ampere part beating a desktop Blackwell on memory-bound decode, which is not physically credible.
It is not a short-sample artifact (measured: extending generation 32 → 512 tokens moves throughput
only ~15%). The `5080`'s *Python*-path corpus numbers are self-consistent and give a believable
3.86× gap, so **use §1 for the hardware factor** and treat the `5080` native per-phase table as
Blackwell's *relative backend ordering* only. If that box returns, re-measure it.

---

## 3. Runtime: native (Rust) vs Python

On GPU, at matched prompt and model:

| Backend | native prompt | python prompt | native gen | python gen |
|---|---:|---:|---:|---:|
| CUDA | 3522 tok/s | ~1800 tok/s (**1.9×**) | 90.9 tok/s | ~70 tok/s (**1.3×**) |
| Vulkan | 3329 tok/s | 1879 tok/s (**1.8×**) | 88.6 tok/s | 72.9 tok/s (**1.2×**) |

**The native runtime is ~1.8–1.9× faster at prompt decode** on both GPU backends. Prompt decode is
where knaif spends its time (a ~4k-token skill prompt vs ~32 output tokens), so this is the
dominant runtime difference. It is **not** a Rust-vs-Python language effect — both are thin
bindings over the same llama.cpp; the timings come from llama.cpp's own C-level perf counters. The
remaining suspect is a llama.cpp version/build difference between the `llama-cpp-python` wheel and
`llama-cpp-sys-2`. **Unexplained; do not guess in this doc — measure it.**

> ❌ **`n_batch` is NOT the explanation** (a plausible theory, tested and refuted). Raising the
> Python `n_batch` from 512 → 8192 changed nothing: 1721/1772 tok/s vs 1737/1857 tok/s, i.e. noise.
> Compute is chunked by **`n_ubatch`** (physical batch, default 512), not by `n_batch` (logical) —
> both runtimes chunk at 512 regardless. Raising it only inflates the compute buffer. Left at the
> default deliberately in `orchestrator.py`.

### ✅ Fixed: the native CPU path ran on 4 threads

llama.cpp defaults **both** thread counts to `GGML_DEFAULT_N_THREADS = 4` no matter the machine,
and `knaif-llm` never set them. The two phases want *different* counts (8-core/16-thread CPU):

| threads | prompt decode | generation |
|---|---:|---:|
| **4** (llama.cpp default) | 24 tok/s | 5.5 tok/s |
| 8 (physical) | 54 tok/s | **6.0 tok/s** |
| 16 (logical/SMT) | **70 tok/s** | 5.2 tok/s |

Prompt decode is compute-bound and scales onto every logical thread; generation is memory-bound and
**regresses** under SMT contention. [`native/crates/knaif-llm/src/llama.rs`](../native/crates/knaif-llm/src/llama.rs)
now sets generation → **physical** cores and prompt decode → **all logical** threads (override with
`$KNAIF_N_THREADS` / `$KNAIF_N_THREADS_BATCH`). Native CPU inference: **126 s → 63 s (2×)**.
GPU builds are unaffected — the GPU does the work.

> ❌ **AVX2 was NOT missing** (another plausible theory, tested and refuted). The CMake cache shows
> `GGML_AVX2:BOOL=ON` in the stock build — ggml enables it by default on x86. Adding
> `-C target-feature=+avx2,+fma,+f16c` measured **zero** gain, so no `.cargo/config.toml` is
> needed (and forcing an AVX2 baseline onto shipped binaries would be a real cost for no benefit).

---

## 4. 🪤 The CPU-benchmark trap: `n_gpu_layers=0` is *not* CPU-only

With **any** GPU backend compiled in, llama.cpp offloads large batched matmuls to the GPU **even at
`n_gpu_layers=0`** (`op_offload`, default **on**). Measured, same wheel, prompt decode:

| config | prompt decode | what actually ran |
|---|---:|---|
| `n_gpu_layers=0` (default) | **426 tok/s** | ⚠️ **GPU** — a CUDA0 buffer is allocated |
| `n_gpu_layers=0, op_offload=False` | **38 tok/s** | the actual CPU |

**An 11× difference.** Any "CPU" benchmark taken with a GPU-enabled build is wrong unless it passes
`op_offload=False`. (This invalidated an earlier draft of this doc, which "found" the native CPU
build to be 13× slower than Python's — it was comparing honest CPU against secretly-GPU-assisted
CPU. At true parity native CPU is *faster*: 68 vs 38 tok/s.)

**It is also an opportunity.** Weights in system RAM + prompt matmuls on the GPU is a legitimate
**hybrid mode** — useful when a model doesn't fit in 8 GB of VRAM. It costs generation speed
(still CPU-bound) but makes prompt decode ~11× faster than true CPU. Not currently exposed by knaif.

---

## 5. Model scorecard (quality × size × speed)

Quality is machine-independent; speed is not. Distilled from
[the fine-tuning study](audits/2026-07-01-finetuning-study-findings.md) §5.

| Model | Size | ffmpeg full | ffmpeg hard | documents full | `5080` ms | `3070L` ms |
|---|---:|---:|---:|---:|---:|---:|
| **qwen3-4b-base-q4** (incumbent) | 2.33 GB | **0.905** | 0.909 | 0.976 | 368 | ~1350 |
| qwen3-4b-sft-v3-flat-q4 (**promoted**, = `knaif-qwen3-4b-v1`) | 2.33 GB | 0.903 | **0.945** | 0.976 | 350 | **1352** ✔measured |
| **qwen3-1.7b-sft-v3-flat-q6** (= `knaif-qwen3-1.7b-v1`) | **1.32 GB** | 0.878 | 0.927 | 0.970 | 260 | **659** ✔measured |
| gemma3-4b (untuned) | 2.32 GB | 0.857 | 0.891 | 0.970 | **1515** | ~5000 |

- **4B = the quality pick.** Comfortably interactive on the `5080` (~0.35 s); **~1.4 s/utterance**
  on the `3070L`, at the edge of sluggish.
- **1.7B-Q6 = the speed pick.** ~2.5 pt behind on full ffmpeg, 1 GB smaller, **~2× faster on both
  machines** (the ratio is hardware-invariant) — the better interactive default on the `3070L`.
- **Gemma3-4B is not competitive** — worse quality *and* ~4× slower. Qwen3 is the base; settled.

---

## 6. Where the wall-clock actually goes

A native CUDA `run` is **~5.2 s wall**, of which only ~1.6 s is model compute. Per-phase, timestamped
relative to process start on `3070L-WSL` (p50, warm):

| Slice | p50 | Notes |
|---|---:|---|
| process start | 6 ms | |
| knaif pipeline | 60 ms | registry + prompt build + validate + dispatch — measured with the mock backend |
| **CUDA context init** | **~1900 ms** | before the model load begins; ⚠️ **partly a WSL artifact — see below** |
| model load | 1318 ms | 2.5 GB GGUF → VRAM, warm page cache (**cold: 9395 ms**) |
| `new_context` | 66 ms | |
| prompt decode | 1233 ms | 3938 tokens |
| generation | 398 ms | 32 tokens |
| teardown | 240 ms | freeing ~4 GB VRAM at exit |
| **wall** | **~5180 ms** | |

> ❌ **The earlier "~2.6 s model load" was two costs conflated.** Loading the GGUF is ~1.3 s; a
> *separate* ~1.9 s goes to CUDA driver/context creation **before** the load starts. Both are paid on
> every CLI invocation, so the amortizable total is **~3.5 s, not ~2.6 s**.

> ❌ **`dlopen` is NOT the cost** (a plausible theory, tested and refuted). All four CUDA libraries
> load in **~120 ms** warm — including the 490 MB `libcublasLt.so.13` at ~52 ms. Shrinking the payload
> (e.g. `nvprune`) is a **download-size** lever, not a startup-latency one.

> ⚠️ **Do not design against the ~1.9 s CUDA init until it is re-measured on bare metal.** Bare-metal
> CUDA context creation is typically 100–300 ms; WSL's `/dev/dxg` paravirtualization is a known tax,
> and this is the one slice least trustworthy from WSL. Every other slice tracked bare-metal `3070L`
> within ~10%. **If bare metal is ~300 ms, the daemon's payoff drops from −3.5 s to ~−1.8 s and
> prompt-prefix KV reuse becomes the better first move.** Free to check; do it first.

**A persistent daemon plus prompt-prefix KV reuse is the biggest remaining win — and they are worth
~2× more together than separately.** The daemon amortizes CUDA init + model load + teardown; prefix
reuse removes the prompt decode. Measured proxy for the daemon alone: `plan --batch` over 10
utterances ran in **17.8 s = 1.78 s/utterance** vs ~5.2 s cold (2.9×) — but it still paid the **full
~1.6 s decode on every utterance**, because a fresh context is built per request and all 3938 prompt
tokens are re-decoded.

The skill prompt is a fixed ~3900-token prefix with only the utterance varying at the tail (batch
token counts ranged 3938–3943), so the prefix is reusable in principle. Nothing in `knaif-llm`
exploits it today — there is no KV-cache reuse or `state_seq` persistence. Together the two target
**~5.2 s → ~0.5 s**; the daemon alone stops at ~1.8 s.

Two caveats on that work:

- **Persisting the prefix KV to disk is not worth it** — ~580 MB f16 for a 3938-token Qwen3-4B
  prefix costs more to read than the 1.2 s of decode it saves. In-memory reuse only.
- **KV reuse needs parity validation.** Reusing a prefix changes how the decode is chunked, which can
  perturb floating-point accumulation and — under greedy argmax — flip a near-tie into a different
  plan. Gate it on `just eval-success` + `just parity`, not a smoke test. The same caveat applies to
  enabling flash attention, which is why neither is a free win.

---

## 7. Environment gotchas that cost real time

1. **`llama-cpp-python` won't load: `ggml-cuda.dll` dependency error.** The CUDA *runtime* DLLs must
   be on `PATH` — they ship in the venv via the `nvidia-*-cu12` pip packages, and are needed **even
   for `n_gpu_layers=0`** (the wheel is a CUDA build and cannot load without them):
   ```
   .venv/Lib/site-packages/nvidia/cublas/bin
   .venv/Lib/site-packages/nvidia/cuda_runtime/bin
   ```
   The **native** runtime is different: it *compiles* llama.cpp, so it needs a real CUDA toolkit
   (`nvcc`) and the Vulkan SDK (`glslc`). `just install-cuda` on Windows only pip-installs the
   runtime wheels — it does **not** give you a toolkit.

2. **Native Vulkan build fails: `C1083: Cannot open compiler generated file: ''`.** MSVC hitting
   `MAX_PATH`. The Vulkan build nests an ExternalProject deep inside the target dir — build into a
   **short** target dir (`cargo build … --target-dir C:\kv`).

3. **WSL has no NVIDIA Vulkan ICD — a "Vulkan" arm there is really CPU.** See §2. Benchmark Vulkan
   on bare metal or not at all. CUDA *does* work under WSL (via `/dev/dxg`), at a ~10–12% compute
   cost plus an inflated context-init time (§6).

4. **(Fixed) `UnicodeEncodeError` destroyed finished eval runs on Windows.** `print_scoreboard`
   wrote box-drawing rules (`═`, U+2550 — *not in cp1252*) to a default console and died **after**
   all inference and **before** `--save`. Now the writer degrades unencodable characters, and
   `cli.py` **saves before it prints**. A cosmetic failure can no longer destroy an hour of compute.

---

## 8. Model files on disk — one name per model

**Naming (unified 2026-07-14):** a model has **one name** on disk, in
`contracts/models/model-manifest.yaml`, and in the HuggingFace repo — the public `knaif-*` artifact
name. `models/` holds exactly:

| File | Public name | Is |
|---|---|---|
| `knaif-qwen3-4b-v1-q4_k_m.gguf` | `knaif-qwen3-4b-v1` | Q4_K_M 4B, FT cycle `sft-v3-flat` (promoted) |
| `knaif-qwen3-1.7b-v1-q6_k.gguf` | `knaif-qwen3-1.7b-v1` | Q6_K 1.7B, FT cycle `sft-v3-flat` |

FT-cycle names (`sft-v3-flat`, `ftv2`, …) survive **only** as `training_run:` provenance in the
manifest and as **eval backend keys**, which are deliberately frozen because they are baked into
every saved scoreboard filename in `evals/` — renaming a key would break its join with its
own history. **Key = experiment identity; file = artifact identity; `path:` maps one to the other.**

> **Trust `sha256`, not the filename.** Before the unification, `models/` held two byte-identical
> duplicates of these same models, and one of them — `qwen3-1.7b-ftv2-q4.gguf` — was **mislabeled**:
> a Q6_K copy of the 1.7B v1 model, not a data-v2 Q4. Its eval stanza would have silently
> benchmarked a different model *and quant* than its name claimed. Both duplicates are now deleted.
>
> The same trap cost real work here: `knaif-qwen3-1.7b-v1-q6_k.gguf` is **`sft-v3-flat-q6`**, *not*
> the v1-union `qwen3-1.7b-ft-q6`. An earlier draft of §1 paired it against the latter's
> scoreboard — two different fine-tunes — which depressed the routing control to 92.3% and inflated
> the slowdown to 3.06×. Paired correctly: control **98.7%**, slowdown **2.53×**.

**Most GGUFs referenced by `eval_backends.yaml` are absent** here — they were 5080-box study
artifacts. A missing GGUF fails **loudly** (every row errors, outcome ≈ 0.0); it will not silently
mock. An across-the-board ~0.0 means *the file is missing*, not *the model is bad*. Note
`models.yaml`'s `default: qwen3-4b` still points at an absent untuned GGUF.

---

## 9. Reproduction

```powershell
# Native, per-phase — from a Developer PowerShell (MSVC + CUDA toolkit + Vulkan SDK + LLVM)
cargo build --release -p knaif-cli --features llama,cuda   --target-dir target/bench-cuda
cargo build --release -p knaif-cli --features llama,vulkan --target-dir C:\kv   # short path!
cargo build --release -p knaif-cli --features llama        --target-dir C:\kc
$env:KNAIF_TIMING = '1'          # per-phase [knaif-timing] lines on stderr
$env:KNAIF_N_GPU_LAYERS = '0'    # force CPU
knaif.exe run ffmpeg "compress clip.mp4 for email" --model models\knaif-qwen3-4b-v1-q4_k_m.gguf --dry-run

# Python, per-phase: Llama(verbose=True) prints llama.cpp's own perf counters (prompt eval time /
# eval time) — the SAME counters the native timing wraps, so the two runtimes share one ruler.
# For an HONEST CPU number you MUST pass op_offload=False (see §4).
# Python+Vulkan needs a source build in an ISOLATED venv (it would overwrite the CUDA wheel):
#   CMAKE_ARGS="-DGGML_VULKAN=on -DGGML_CUDA=off" uv pip install --no-binary llama-cpp-python llama-cpp-python
```

```bash
# Linux CUDA payload, against the SHIPPED artifacts (no build needed) — §2 / §6.
# Stage the payload out-of-tree so ~/.knaif/backends is left alone:
tar xzf dist/knaif-1.1.0-linux-x64.tar.gz -C /tmp/kbench
cp dist/knaif-1.1.0-linux-x64-cuda-backend/* /tmp/kbench/backends/
KNAIF_TIMING=1 KNAIF_BACKENDS_DIR=/tmp/kbench/backends \
  /tmp/kbench/knaif-1.1.0-linux-x64/bin/knaif run ffmpeg "compress clip.mp4 for email" --dry-run

# Confirm the payload actually won device selection (not the bundled Vulkan backend):
#   --verbose | grep -E 'load_backend|prepare_model_devices'
# Isolate the non-inference wall-clock: KNAIF_LLM_BACKEND=mock  -> pipeline only, no llama.
```

## 10. Open items

- ⚠️ **Native is 1.8–1.9× faster than Python at prompt decode and we don't know why** (§3).
  `n_batch` refuted. Next suspect: llama.cpp version/build flags between the wheel and
  `llama-cpp-sys-2`.
- ⚠️ Vulkan's ~36 s first-run shader compile needs an install-time warm-up (§2).
- ❓ **Re-measure CUDA context init on bare-metal Linux** (§6). ~1.9 s under WSL, expected 100–300 ms
  bare metal. Free, and it decides the ordering of the two items below.
- 🚀 **Persistent daemon + prompt-prefix KV reuse** (§6) — the biggest remaining win, ~5.2 s → ~0.5 s.
  Do them together: the daemon alone stops at ~1.8 s because it still re-decodes the full prompt.
- 💡 Expose the CPU+GPU **hybrid** mode (§4) for models that don't fit in VRAM.
- 🧹 `models.yaml` `default:` points at a GGUF that isn't on this machine (§8).
- ❓ Re-measure the `5080` native backends if that box returns (§2).
