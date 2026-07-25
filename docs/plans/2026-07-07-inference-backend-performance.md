# Inference Backend Performance — Findings (2026-07-07)

**Status:** Done · **Created:** 2026-07-07 · **Completed:** —
**Owner:** core · **Ref:** [PERFORMANCE.md](../PERFORMANCE.md) §2 · [NATIVE.md](../NATIVE.md)

**Goal:** Research findings (no code changes) — record the measured, hardware-specific
inference-backend behavior (RTX 5080 backend ordering, Blackwell Vulkan collapse, first-run shader
tax) that underpins PERFORMANCE.md §2 and NATIVE.md's backend guidance.

> **Kept 2026-07-23** (S7 decision — research findings; the inspectable primary evidence behind
> [PERFORMANCE.md](../PERFORMANCE.md) §2 and [NATIVE.md](../NATIVE.md)'s backend guidance). It is
> cited only from those two *docs*, not from source — the fourth plan the prep plan's
> "cited-from-source" tier lists in error, after `cjk-retrieval-segmentation`,
> `training-subsystem` and `post-v1-ci`. Treat that tier as unverified wherever S7 has not reached.
>
> **Why keep a superseded findings doc:** every *conclusion* here is already in the shipping
> docs — the 5080 backend-ordering table, the Blackwell-specific Vulkan collapse, the first-run
> shader tax, the suspect-CUDA caveat (PERFORMANCE.md §2), and the `VkPipelineCache` dead end +
> daemon-helps-CUDA-not-Vulkan implications (NATIVE.md). What is *only* here is the **method
> behind the negative result**: the patched `ggml-vulkan.cpp` behind `GGML_VK_PIPELINE_CACHE`,
> `eCaptureStatisticsKHR` disabled, flushed at exit, yielding a populated 1.1 MB cache with zero
> speedup. "Ship prebuilt shaders" is the kind of idea that gets re-proposed, and its refutation
> is only trustworthy if the experiment that produced it is inspectable. Header already
> well-annotated (the supersession box below, the ✅-answered open question); only the stale
> `crates/` → `native/crates/` path was fixed.
>
> **⚠️ SUPERSEDED IN PART (2026-07-14). Everything below was measured on an RTX 5080
> (Blackwell / sm_120) and is correct *for that GPU only*.**
>
> 1. **The Vulkan question is answered: it was Blackwell-specific.** On an RTX 3070 Laptop
>    (Ampere / sm_86), Vulkan generates at **88.6 tok/s — within 3% of CUDA** and ~15× faster than
>    CPU, not the 5.7 tok/s CPU-tie seen here. **Do not carry finding #4 or implication #1
>    ("CUDA is required on NVIDIA") to non-Blackwell hardware.**
> 2. **The CUDA numbers in this doc are themselves suspect.** The 80 tok/s generation reported
>    below is *slower than the 3070 Laptop's 91 tok/s*, which is not physically credible for a
>    5080. Treat this table as Blackwell's **relative backend ordering**, not as absolute
>    throughput, and do not derive a cross-machine hardware ratio from it.
> 3. **The CPU column understates the CPU path**, which ran on llama.cpp's 4-thread default (since
>    fixed; the fix is worth ~2×).
>
> See **[PERFORMANCE.md](../PERFORMANCE.md)** for the current cross-hardware, cross-runtime,
> cross-backend scorecard.

Investigation into why the native `knaif run` felt slow. Measures the local
llama.cpp inference backends (CPU, Vulkan, CUDA) end-to-end and per-phase on real
hardware, and records a negative result for the Vulkan pipeline-cache hypothesis.

## TL;DR

- The cost of a `knaif run` is **inference compute**, not startup, model load, or
  shader compilation.
- On an RTX 5080, **CUDA is ~7× faster end-to-end than Vulkan** (~1.35 s vs ~9.7 s
  of inference) — driven by a **~4.5× faster prompt decode and a ~13× faster token
  generation**.
- **Vulkan barely accelerates this model on this GPU**: its token-generation speed
  (~5.7 tok/s) is essentially the same as pure CPU (~5.9 tok/s). It only beats CPU on
  prompt processing.
- The `VkPipelineCache` / "ship prebuilt shaders" idea is a **dead end** — proven by
  experiment (a fully populated 1.1 MB pipeline cache produced zero speedup).
- Actionable conclusion: **CUDA is required on NVIDIA hardware**; Vulkan is only a
  slow cross-vendor fallback; a persistent daemon helps the CUDA path, not Vulkan.

## Test environment

| | |
|---|---|
| CPU | 32 logical cores |
| GPU | NVIDIA GeForce RTX 5080 (Blackwell, compute capability 12.0 / sm_120), 16 GB |
| Also present | AMD Radeon iGPU (a 2nd Vulkan device; NVIDIA is auto-selected) |
| OS | Windows 11 |
| Model | `qwen3-4b-v1` (Qwen3 4B, q4_k_m GGUF, ~2.4 GB) |
| Prompt | ffmpeg skill planner prompt, **3938 tokens**, `n_ctx = n_batch = 8192` |
| Request | `compress clip.mp4 for email` (`run ffmpeg … --dry-run`) |
| llama.cpp | via `llama-cpp-2` / `llama-cpp-sys-2` 0.1.150 |
| GPU offload | `n_gpu_layers = 999` (all 37/37 layers) for GPU backends; `0` for CPU |
| Generation | greedy/argmax, capped at 32 tokens for the plan output |

Builds compiled in a VS Developer environment (MSVC + CUDA 13.3 + Vulkan SDK 1.4.350),
release profile, per-backend cargo features (`llama,cuda` / `llama,vulkan` / `llama`).

## Methodology

- **End-to-end** wall time measured around the process from PowerShell.
- **Per-phase** timing added inside `native/crates/knaif-llm/src/llama.rs` (behind
  `KNAIF_TIMING`): `model load_from_file`, `new_context`, `prompt_decode`,
  `generation`. This isolates where the seconds actually go, independent of process
  startup and run-to-run noise.
- Every invocation is a **fresh process** (cold), which is how the CLI is used today.
- The model file was warm in the OS page cache for all comparisons.

## Results — per-phase breakdown

Same prompt (3938 tokens), same model, same GPU. Representative of 2–3 runs each.

| Phase | CPU (ngl=0) | Vulkan | CUDA |
|---|---:|---:|---:|
| model load → memory/VRAM | ~0.45 s | ~1.1 s | ~1.25 s |
| new_context | ~0.17 s | ~0.20 s | ~0.02 s |
| **prompt decode** (3938 tok) | ~7.5 s | ~3.8 s | ~0.85 s |
| **generation** (32 tok) | ~5.4 s | ~5.6 s | ~0.4 s |
| **inference total** (`generate_plan`) | **~13.0 s** | **~9.7 s** | **~1.35 s** |

Derived throughput:

| | CPU | Vulkan | CUDA |
|---|---:|---:|---:|
| prompt decode | ~520 tok/s | ~1040 tok/s | ~4600 tok/s |
| generation | ~5.9 tok/s | ~5.7 tok/s | ~80 tok/s |

End-to-end wall time (process start → exit, includes backend init + teardown) was
**~3.0 s for CUDA** and **~13–18 s for Vulkan**, with Vulkan showing large run-to-run
variance (observed 12–35 s); CUDA was steady (~3.0–3.4 s).

## Findings

### 1. Backend / device selection
- The shipped installer is the **Vulkan** build (no CUDA runtime DLLs beside the exe;
  static llama.cpp + system `vulkan-1.dll`). It auto-selects the NVIDIA GPU (Vulkan0)
  over the AMD iGPU.
- A CUDA build (cargo `--features llama,cuda`, `CMAKE_CUDA_ARCHITECTURES=native`)
  embeds precompiled **sm_120 SASS** and selects `CUDA0` (the 5080). It needs the
  NVIDIA cudart/cublas redist DLLs on PATH / beside the exe (packaging already bundles
  these for `--kind=cuda`).

### 2. Model load is fine everywhere (not the bottleneck)
- ~0.45 s CPU (mmap into RAM), ~1.1–1.3 s for GPU backends (the extra ~0.7 s is the
  ~2.4 GB VRAM upload). Identical between Vulkan and CUDA. A one-time cost per process.

### 3. Prompt decode: Vulkan ~4.5× slower than CUDA
- 3938-token prompt: CUDA ~0.85 s, Vulkan ~3.8 s, CPU ~7.5 s. Vulkan does provide GPU
  acceleration here (beats CPU ~2×) but is far from CUDA.

### 4. Generation: Vulkan ~13× slower than CUDA — and no faster than CPU
- 32 tokens: CUDA ~0.4 s (~80 tok/s), Vulkan ~5.6 s (~5.7 tok/s), CPU ~5.4 s
  (~5.9 tok/s).
- **Vulkan token generation is statistically tied with the CPU** on this machine. A
  discrete GPU should be an order of magnitude faster than CPU for autoregressive
  decode. That it is not points to the Vulkan decode kernels (batch-1 matmuls,
  coopmat2 path) being unoptimized or falling back for the brand-new Blackwell /
  sm_120 target. This is the single dominant cost of a `run`.

### 5. Shader compilation / `VkPipelineCache` is NOT the cost (negative result)
Hypothesis tested: Vulkan is slow because it recompiles SPIR-V→GPU-ISA pipelines every
process, and a persistent `VkPipelineCache` (or the "capture-statistics defeats the
driver cache" theory) would recover CUDA-class speed. **Disproven:**
- Startup shader loading (`ggml_vk_load_shaders`) measured at **1 ms** — pipelines are
  compiled **lazily during the first decode**, not at init.
- A patched ggml-vulkan that provides a real `VkPipelineCache`, disables the
  `eCaptureStatisticsKHR` flag, and flushes the cache at process exit produced a
  **populated 1.1 MB cache** containing every compiled pipeline.
- Seeding that cache into warm runs gave **no speedup** (generation stayed ~5.6 s).
- Conclusion: the pipelines *are* cached correctly; caching does nothing because the
  bottleneck is compute execution, not compilation. The "ship prebuilt shaders" avenue
  cannot help. (Separately, a `VkPipelineCache` blob is GPU+driver-version-specific and
  could never be shipped in an installer regardless.)

### 6. Run-to-run variance is a Vulkan-only phenomenon
- CUDA was steady (~3.0–3.4 s wall). Vulkan varied 12–35 s wall for the same command.
  Per-phase `generate_plan` timing was comparatively stable (~9.7 s), so most variance
  lives in Vulkan backend init / driver / process teardown. This variance masked the
  real breakdown in early measurements (it briefly looked like "generation is free").

### 7. CPU is a usable last resort, not competitive
- ~13 s total, dominated by prompt decode. Faster model load (no VRAM upload) but slow
  compute. Fine as a no-GPU fallback; not something to prefer when a GPU exists.

## Root cause

The dominant cost of `knaif run` is **LLM inference compute** — specifically token
generation, then prompt decode. On NVIDIA hardware the Vulkan backend delivers only a
fraction of the GPU's capability for this model (generation no better than CPU),
whereas CUDA runs the same work ~7× faster end-to-end. Startup, model load, context
creation, and shader compilation are all minor and roughly equal across backends.

## Implications for the product

1. **CUDA on NVIDIA is required, not optional.** It is the only backend that makes
   `run` feel responsive (~1.4 s inference vs ~9.7 s). Ship a CUDA artifact for NVIDIA
   and select it when an NVIDIA GPU is present.
2. **Vulkan stays as the cross-vendor fallback** (AMD / Intel / no-CUDA), with the
   understanding that it is slow for LLM decode on at least this hardware.
3. **A persistent model daemon helps the CUDA path, not Vulkan.** A daemon only
   amortizes the one-time model load (~1.1–1.3 s); the slow Vulkan compute is paid on
   every inference regardless. On CUDA a daemon would turn ~1.35 s/call into near-
   instant repeat calls; on Vulkan it would only shave ~1 s off ~9.7 s.
4. **Do not invest in a Vulkan pipeline-cache patch** for performance — measured to
   have zero benefit.
5. Consider the smaller `qwen3-1.7b-v1` for the Vulkan/CPU fallback paths to partially
   offset their slower compute.

## Reproduction / instrumentation

- Phase timing: set `KNAIF_TIMING=1` (prints `[knaif-timing]` lines to stderr) — added
  to `native/crates/knaif-llm/src/llama.rs`.
- Force CPU: `KNAIF_N_GPU_LAYERS=0`. Force full offload: `KNAIF_N_GPU_LAYERS=999`.
- Build CUDA (VS Dev shell): `cargo build --release -p knaif-cli --features llama,cuda`
  with `CMAKE_CUDA_ARCHITECTURES=native`; run with `%CUDA_PATH%\bin` on PATH.
- Build Vulkan: `cargo build --release -p knaif-cli --features llama,vulkan` (needs the
  Vulkan SDK / `glslc`).
- The pipeline-cache experiment patched the vendored
  `ggml/src/ggml-vulkan/ggml-vulkan.cpp` in the cargo registry (gated behind
  `GGML_VK_PIPELINE_CACHE`). Not part of the repo; revert to restore a pristine crate.

## Open questions / follow-ups

- ✅ **ANSWERED 2026-07-14 — yes, it was Blackwell-specific.** Retested on an RTX 3070 Laptop
  (Ampere / sm_86): Vulkan generation **88.6 tok/s vs CUDA 90.9 tok/s** (within 3%), and 18×
  faster than that machine's CPU. The sm_120 / coopmat2 path was the culprit, exactly as
  suspected. Vulkan is a viable NVIDIA shipping target on pre-Blackwell hardware.
  One caveat found: the **first-ever** Vulkan run pays a ~36 s lazy shader-compilation cost
  (cached by the driver thereafter) — consistent with, not contradicted by, the negative
  `VkPipelineCache` result in finding #5. See [PERFORMANCE.md](../PERFORMANCE.md).
- Does `GGML_VK_DISABLE_COOPMAT2` (or forcing a different mul_mat path) change Vulkan
  generation speed here? Not yet tested; would not change the CUDA-on-NVIDIA decision.
- Packaging: installer NVIDIA detection to pick the CUDA artifact vs the Vulkan
  fallback; size/download implications of bundling CUDA redist DLLs.
