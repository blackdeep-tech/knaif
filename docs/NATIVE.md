# NATIVE.md — knaif Native Runtime

Authoritative reference for the **native (Rust) implementation** of knaif: the CLI, the
engine crates it is built from, the local llama.cpp inference stack (CPU / Vulkan /
CUDA), model management, skill bundles, and packaging. For the language-neutral
execution model see [ARCHITECTURE.md](ARCHITECTURE.md); for skill authoring see
[TOOL_SCHEMA.md](TOOL_SCHEMA.md).

## 1. What the native runtime is

knaif ships in two runtimes that share the same declarative contract (skill YAML,
prompt format, plan-envelope schema, safety model):

- **Python** (`python/core`) — the operator / evaluation / fine-tuning path.
- **Native** (this document) — a self-contained Rust binary for end users, with local
  GGUF inference and no Python dependency. Desktop and mobile front-ends are intended
  to consume the same engine crates.

The model only *proposes* a plan; deterministic Rust code validates, expands, confirms,
and executes it. Skills' declarative data (YAML) is shared verbatim with Python; only
the handlers are reimplemented natively.

Design record: [docs/plans/2026-06-17-monorepo-dual-runtime.md](plans/2026-06-17-monorepo-dual-runtime.md).

## 2. Tech stack

| Area | Choice |
|---|---|
| Language / edition | Rust 2021, `rust-version = 1.96` |
| CLI parsing | `clap` v4 (derive) |
| Serialization | `serde`, `serde_yaml`, `serde_json` (**`preserve_order`** — prompt example JSON must stay in trained key order) |
| Inference | `llama-cpp-2` / `llama-cpp-sys-2` 0.1.150 (bundles llama.cpp) |
| GPU backends | CPU (static), **Vulkan** (cross-vendor), **CUDA** (NVIDIA) — compile-time cargo features |
| Hashing | `sha2` (model checksum verification) |
| HTTP | `ureq` v2 (blocking, rustls; model downloads — no tokio) |
| Progress UI | `indicatif` (download bars) |
| PDF / Office (documents skill) | `lopdf`, `regex`, `zip`, `quick-xml`, `calamine`; optional `pdfium-render` + `image` (feature `pdfium`) |
| Text normalization | `unicode-normalization` |

All third-party deps are Apache-2.0-compatible per the dependency-license policy (no
GPL/AGPL bundling). GPU SDKs (CUDA, Vulkan) and heavyweight external tools (ffmpeg,
LibreOffice, Ghostscript, Tesseract) are **never bundled** — see §8.

## 3. Workspace layout

A Cargo workspace of reusable engine crates plus the CLI app:

| Crate | Responsibility |
|---|---|
| `native/crates/knaif-core` | Deterministic contract: plan parse/normalize/validate, registry, prompt build, retrieval, safety gates, clarify gate, JSON extraction, skill discovery, deps doctor. **No inference deps.** |
| `native/crates/knaif-models` | Shared model store + manifest (`ModelStore`, `Manifest`, `HttpFetcher`). **No inference deps** — a model-management UI can embed it without linking llama.cpp. |
| `native/crates/knaif-llm` | Inference backends behind the `LlmBackend` trait: `MockBackend` and `LlamaCppBackend`. Depends on `knaif-models` to locate files. No Ollama. |
| `native/crates/knaif-skill-api` | The native skill contract (`HandlerContext`, `Step`/`Intent` equivalents, sandbox helpers) — mirrors Python `handler_api` / `tool`. |
| `skills/ffmpeg/native` | Native ffmpeg skill (expand → dry-run preview / subprocess execution). |
| `skills/documents/native` | Native documents skill (PDF/Office read + structural write ops). |
| `apps/cli` | The `knaif` binary — arg parsing + output formatting only; all logic lives in the engine crates. |

## 4. Execution pipeline (native `run` / `plan`)

`knaif run <skill> "<request>"` mirrors the Python pipeline:

```
request
  -> pre-inference safety gate (skill unsafe-phrase list)      reject early, never reaches model
  -> dependency preflight (execute mode only)                  fail fast if a REQUIRED tool is missing
  -> PlanSession: load registry (skill tools.yaml ∪ core control tools) + prompt overrides + backend
  -> build_prompt() (skill prompt.yaml header/examples + tool list)
  -> backend.generate_plan()  (mock, or llama.cpp with --model)
  -> extract_json -> parse_plan -> normalize_plan -> apply_defaults -> validate_plan
  -> [repair] on parse/validate failure, retry once with validator feedback (real model only)
  -> apply_clarify_gate (chain-intermediate linking + hallucinated-filename downgrade)
  -> core control tools short-circuit (clarify / reject)
  -> skill dispatch: dry-run preview OR confirmed execution
```

- `--dry-run` previews commands/output paths with no side effects (stubs missing
  inputs). Execution requires explicit consent (`--yes`, or an interactive `y`);
  non-interactive execution without `--yes` errors with the preview.
- `plan --skill <name> [--json] [--batch FILE]` emits the validated plan envelope only.
  `--batch` loads the model once and streams one plan per input line (avoids per-line
  model reload).
- Without `--model`, the recommended model is auto-selected (§5.6); the **mock backend**
  (empty plan + first-run guidance) is the fallback when none is available.

Windows path handling: utterances have `\` normalized to `/` before prompting so a
model echoing `.\clip.mov` cannot emit an illegal `\c` JSON escape. This is done at the
prompt, not by repairing escapes in the model's output — an escape-repair pass would also
paper over *genuine* malformed JSON, whereas forward slashes are lossless for the file-path
domain (accepted by ffmpeg and `std::path` on Windows). Root cause is the training data:
skills train only on bare filenames, never a slashed path, so the model has no learned
escaping behavior and copies the utterance substring verbatim.

## 5. Inference

### 5.1 Backend abstraction

`LlmBackend::generate_plan(system, user) -> String` keeps the runtime
backend-agnostic. The backend owns chat formatting because framing is model-specific.

- **`MockBackend`** — deterministic, no model. Returns `$KNAIF_LLM_MOCK_RESPONSE` or an
  empty plan. Used by tests, offline dev, and eval/parity harnesses.
- **`LlamaCppBackend`** (feature `llama`) — real local inference via `llama-cpp-2`.

Selection: a `--model <name|path>` builds `LlamaCppBackend`; otherwise the recommended model is
auto-selected when available, else the mock runs (§5.6 has the full precedence, including the
`$KNAIF_LLM_BACKEND` = `mock` | `llama` opt-out). A `--model` name resolves through the
`ModelStore`; a path is used directly.

### 5.2 llama.cpp integration

- One build bundles the compiled-in backends; **GPU offload is a runtime choice** via
  `n_gpu_layers` (which *backends* are compiled in is a build-time feature choice).
- Uses the **GGUF's built-in chat template** when present (so a fine-tuned model sees
  its trained framing); falls back to plain ChatML. `/no_think` is appended to disable
  Qwen3 thinking (the promoted eval config); a leaked `<think>` block is stripped
  downstream.
- **Greedy decode** (argmax) — deterministic JSON-plan task, minimal sampler surface.
- `n_ctx = n_batch` (default 8192) so the large planner prompt decodes in one pass.
  A fresh context is created per utterance; the model is loaded once per process.

### 5.3 Backends: CPU / Vulkan / CUDA

| Build kind | Cargo features | Notes |
|---|---|---|
| `base` | *(none)* | No inference backend — plumbing/dev artifact; `run` cannot infer. |
| `cpu` | `llama` | CPU inference, statically linked (single exe, no extra DLLs). |
| `vulkan` | `llama,vulkan` | Cross-vendor GPU. Needs a Vulkan-capable driver (`vulkan-1.dll`). No bundled DLLs. |
| `cuda` | `llama,cuda` | NVIDIA GPU. Bundles NVIDIA cudart/cublas **redist** DLLs beside the exe (users supply only their driver). |

Device selection is automatic (e.g. the Vulkan build picks the discrete NVIDIA GPU over
an integrated one; the CUDA build picks `CUDA0`).

**Loadable backends (`dynamic-backends`) — THIS IS THE SHIPPING MODEL (locked 2026-07-16).**
The table above describes *static* builds, still available for dev. Release artifacts instead add the
`dynamic-backends` feature, which builds llama.cpp with `GGML_BACKEND_DL=ON` +
`GGML_CPU_ALL_VARIANTS=ON`, so every backend becomes a `ggml-*` shared lib the exe `dlopen`s at
runtime. That is what lets CUDA ship as an **opt-in download** rather than ~½ GB in every artifact.
Proven end-to-end on **Windows and Linux** — default artifact runs on CPU/Vulkan, dropping the CUDA
payload in enables GPU offload next run, and a CUDA-present-but-no-usable-GPU box falls back cleanly.

- **Scan order** — **`$KNAIF_BACKENDS_DIR` (else `~/.knaif/backends`) FIRST, then the exe's
  directory.** Both are always scanned, so an opt-in payload *adds to* the artifact's own backends;
  a missing directory is skipped silently, which is *how* a user without the CUDA payload simply
  never loads it. The order matters because when two backends drive the **same physical GPU**,
  llama.cpp dedupes by PCI id and the **first-registered wins** — with the exe dir first, the
  bundled Vulkan backend won and an installed CUDA payload was inert. Ordering picks the *device*;
  staleness is a separate check, below.
- **Stale payloads are refused at load time.** `~/.knaif/backends` is deliberately outside the
  install dir — that is what makes `backend install` elevation-free and keeps it working when the
  install is read-only — so it **survives an app upgrade** and is scanned first. Without a check, an
  upgraded `knaif` would load the previous release's `ggml-cuda`, whose ABI no longer matches. So
  `BackendStore` writes a receipt (`.knaif-backends.yaml`) stamped with the installing release, and
  the loader skips the whole directory with a message naming the fix when the stamp does not match
  the running binary, or when a previous install did not finish. Install-time pinning alone cannot
  cover this: the mismatch exists *before* any `backend install` could run.
  A directory with **no receipt** still loads — that is the documented manual route (build a payload
  and drop it in), which is how `backend install` itself gets debugged.

**The `backend` command and its manifest.** `knaif backend list|install|verify|remove` manages the
opt-in payloads. What each carries is declared in
[`contracts/backends/backend-manifest.yaml`](../contracts/backends/backend-manifest.yaml): per
platform, a list of files with a per-file SHA-256 and the release tag each rides. It ships in the
**product** artifact beside `core_tools.yaml` and `model-manifest.yaml` — it is read by an
already-installed `knaif` deciding what to download, so it cannot live in the payload it describes.

It is a bill of materials and is **stricter than the model manifest**, which is forgiving by design
(a knaif upgrade with an unchanged model recommendation re-downloads nothing). A backend inverts
that: the ABI-coupled lib *must* be replaced on upgrade, an older payload has to be refused, and an
unverified file is refused rather than installed — `ModelStore` tolerates a checksumless pull,
`BackendStore` does not, because unverified backend bytes fail inside ggml and read as a driver bug.

Installing is **stage-all → verify-all → swap**, not a per-file `rename` loop: four atomic
operations are not one atomic operation, and an interrupted install would otherwise leave a
directory holding a mix of two payloads. Payload assets are published as **loose per-file assets**
rather than archives, so nothing on the install path extracts anything.
- **`GGML_CPU_ALL_VARIANTS`** emits ~9–14 `ggml-cpu-*` libs (`sse42` … `alderlake`, ~8 MB total;
  count varies by target) and dispatches on the host CPU at runtime — strictly better than one
  static CPU baseline.
- **The exe stops being self-contained**: `dynamic-backends` implies `dynamic-link`, so
  `llama`/`ggml`/`ggml-base`/`llama-common` shared libs must be staged beside it. On Linux the
  staged ELFs also need an **`$ORIGIN` RPATH** (`package.sh` applies it with patchelf) so the
  unpacked folder relocates.
- **Vulkan needs `CMAKE_GENERATOR=Ninja`** — on Windows from a VS Developer shell; on Linux
  `package.sh` sets it. See §10.

### 5.4 Performance findings (RTX 5080, knaif-qwen3-4b-v1) — **the decisive result**

Measured 2026-07-07. Full investigation and methodology:
[docs/plans/2026-07-07-inference-backend-performance.md](plans/2026-07-07-inference-backend-performance.md).
Same 3938-token prompt, per-phase (`KNAIF_TIMING=1`):

| Phase | CPU (ngl=0) | Vulkan | CUDA |
|---|---:|---:|---:|
| model load → memory/VRAM | ~0.45 s | ~1.1 s | ~1.25 s |
| new_context | ~0.17 s | ~0.20 s | ~0.02 s |
| prompt decode (3938 tok) | ~7.5 s | ~3.8 s | ~0.85 s |
| generation (32 tok) | ~5.4 s | ~5.6 s | ~0.4 s |
| **inference total** | **~13.0 s** | **~9.7 s** | **~1.35 s** |

Conclusions (all evidence-backed):

1. **The cost is inference compute, not startup, model load, or shader compilation.**
2. **CUDA is ~7× faster end-to-end than Vulkan** on NVIDIA (prompt decode ~4.5×,
   generation ~13×). CUDA generates ~80 tok/s vs Vulkan ~5.7 tok/s.
3. **Vulkan barely accelerates generation on this GPU** — its ~5.7 tok/s is essentially
   the same as pure CPU (~5.9 tok/s). Likely unoptimized Vulkan kernels for the new
   Blackwell / sm_120 target (coopmat2). Tracked upstream as ggml-org/llama.cpp#16230.
4. **`VkPipelineCache` / "prebuilt shaders" is a dead end** — a fully populated 1.1 MB
   pipeline cache produced zero speedup (proven by experiment). Shaders aren't the cost,
   and such a blob is GPU+driver-version-specific and un-shippable anyway.
5. **A persistent daemon helps the CUDA path, not Vulkan.** It only amortizes the
   ~1.1–1.3 s model load; Vulkan pays ~8.5 s of slow compute on every call regardless.

### 5.5 Backend recommendation (product)

- **CUDA is required on NVIDIA hardware** — the only backend that makes `run`
  responsive. Ship a CUDA artifact and select it when an NVIDIA GPU is present.
- **Vulkan is the cross-vendor fallback** (AMD / Intel / no-CUDA), accepting it is slow
  for LLM decode.
- **CPU** is the no-GPU last resort.
- Consider `knaif-qwen3-1.7b-v1` for the Vulkan/CPU fallback paths to offset slower compute.
- Do **not** invest in a Vulkan pipeline-cache patch for speed.

**The first-run CUDA offer.** The default artifact ships CPU+Vulkan; CUDA is an opt-in payload, so
something has to tell an NVIDIA user it exists — *before* their first slow run, not after. A Blackwell
user who runs first and reads later gets one CPU-speed request and may reasonably conclude the
product is broken.

The offer has **two strengths**, because the two populations are genuinely different:

| Population | Message | Why |
|---|---|---|
| Compute cap in `nudge.vulkan_inadequate_compute_caps` (today: `12.0`, Blackwell) | prominent, stated as *correctness* | Vulkan generates at ~CPU speed there (§5.4 / PERFORMANCE.md §2) — the payload is what makes the product work |
| Any other NVIDIA GPU | quiet, stated as *optional* | CUDA is faster, Vulkan is perfectly usable |
| Driver below `requires.min_driver` | update hint, **no offer** | the payload would download and then fail to load, which reaches the user as "CUDA didn't work" |
| No NVIDIA GPU, or already installed | nothing at all | an unsolicited GPU message on an AMD laptop is noise |

Three properties worth keeping:

- **The architecture list is data, not code** (`contracts/backends/backend-manifest.yaml`). The
  defect behind the strong case lives in a llama.cpp/driver code path and may be fixed upstream, at
  which point a list baked into the source would start lying in the other direction. Re-measure each
  supported architecture when the llama.cpp pin moves, record it in `PERFORMANCE.md`, edit the list.
- **The probe is `nvidia-smi`, not ggml.** It must work on a machine where CUDA is *not* installed,
  which is the whole point. `LlamaBackendDevice` carries no compute capability, and the
  `compute capability 8.6` line people remember is a CUDA-backend init log that by definition does
  not exist yet. One `nvidia-smi --query-gpu=driver_version,compute_cap` returns both fields the
  gate needs, so the architecture half costs nothing beyond the driver check.
- **The soft message quotes no speed figure.** It used to say "~3%", which was the *generation*
  column; knaif's workload is prompt-decode-dominated. No replacement number is quotable until
  `PERFORMANCE.md` §2 is reconciled — its per-phase rates and its stated totals do not add up.

`$KNAIF_NO_CUDA_NUDGE` suppresses it. Failure is silent throughout: an unreadable manifest or a
missing `nvidia-smi` must never disturb a run that was going to work.

### 5.6 Default model auto-select

`run` is usable without `--model`: when none is given, the manifest's `recommendations.cli`
(else `default`) model is selected automatically. Precedence, in order:

1. **`--model <name|path>`** — authoritative. Honored even in a build without the llama.cpp
   backend, where it raises "rebuild with `--features llama`" rather than silently degrading.
2. **`$KNAIF_LLM_BACKEND=mock`** — explicit opt-out; the mock wins over auto-select (offline/eval).
3. **Recommended model already installed** → used silently, no prompt.
4. **Otherwise** → the download-consent flow below; declining falls back to the mock.

**Consent / download.** `run` asks `Download recommended model <name> (~2.5 GB)? [y/N]` (size read
from the manifest's `size_bytes`) and pulls it with a progress bar on `y`. `--yes` skips the
question and downloads. When stdin is not a tty and `--yes` was not passed, the run falls back to
the mock with first-run guidance — a multi-GB download **never** happens without consent, so CI and
piped runs never block. Prompts and the progress bar go to **stderr**; stdout stays clean.

`plan` is **non-prompting**: it auto-selects an installed model (3) but downloads a missing one only
under `--yes`. `plan --batch` / `--json` never prompt and never download — batch loads the model once
up front, and neither may risk a prompt interleaving with streamed output.

A build without the llama.cpp backend never auto-selects: the mock is the only backend that can run,
so selecting a model could only turn a working mock run into a load error.

Model selection happens **after** request parsing, the safety gate, and dependency preflight, so a
rejected request never triggers a 2.5 GB download.

## 6. Model management

- **Manifest** (`contracts/models/model-manifest.yaml`) — the catalog the `ModelStore`
  reads: per-model `file` / `url` (commit-SHA-pinned HF resolve URL) / `sha256` /
  `size_bytes` / `skills`, plus per-surface `recommendations`. GGUFs are **not** bundled;
  first run downloads them.
- **Store** — models live in `~/.knaif/models`. `ModelStore` resolves names → paths,
  verifies checksums, and downloads via `HttpFetcher` (`ureq`).
- **Hosting** — fine-tuned GGUFs live in the single HF repo `blackdeep/knaif`. Public
  release naming (`v1`, `v2`…) is separate from the internal fine-tune-cycle naming; see
  the model-naming standard.
- **Commands**: `models list | verify <name> | pull <name> | update | rm [<name>|--all]`.

## 7. Skills in the native runtime

- A skill is a self-contained bundle at `skills/<name>/`: declarative YAML/data at the
  top (shared with Python), handlers in a native crate. The binary reads YAML straight
  from the bundle.
- `skill.yaml` declares `runtimes.native.status` (e.g. `supported`) and
  `runtimes.native.crate` — consumed by `knaif skills list` and the CLI. For how to author a skill
  as Python-only, native-only, or both, see [TOOL_SCHEMA.md](TOOL_SCHEMA.md) → *Runtimes*.
- Native v1 ships **ffmpeg** and **documents** (`io` is stale, excluded).
- **Deps doctor**: `knaif skills deps [<name>]` reports each skill's declared external
  tools and whether they resolve on PATH — detection only, never modifies PATH. It is
  the same probe that drives the installer component tree and the `run` preflight.
- **Evaluating a native skill**: `just native-mock` is the fast authoring loop (no
  llama.cpp); `just parity <skill>` is the final phase — it pins both runtimes to the same
  GGUF and diffs the rendered output. Both are rungs on the eval ladder in
  [EVAL_FRAMEWORK.md](EVAL_FRAMEWORK.md#the-eval-ladder--fast-while-developing-executing-before-done).

## 8. Resource resolution

The binary resolves its data relative to itself so a packaged install runs from any
directory:

- `resolve_skills_root()` — `$KNAIF_SKILLS_ROOT`, else walk up for `skills/` (dev
  checkout), else exe-relative (`<install>/skills`).
- `resolve_repo_file()` — for `contracts/runtime/core_tools.yaml`, the model manifest, and the
  backend manifest: walk up from cwd (checkout), else beside/one-level-up from the exe (the
  `<install>/bin/knaif` + `<install>/contracts/...` layout). `$KNAIF_MODEL_MANIFEST` /
  `$KNAIF_BACKEND_MANIFEST` override their respective lookups.

## 9. Packaging & distribution

Staged by `installers/package.sh` into a portable layout:

```
bin/knaif[.exe]                     the CLI
bin/*.so | *.dll                    (functional kinds) core llama/ggml libs + loadable ggml-* backends
skills/<name>/                      RUNTIME DATA ONLY (skill.yaml, tools.yaml, prompt.yaml, vocab.yaml, profiles/)
contracts/runtime/core_tools.yaml      core control-tool registry
contracts/models/model-manifest.yaml   model catalog
contracts/backends/backend-manifest.yaml  opt-in GPU payloads `knaif backend install` fetches
LICENSE, README.txt, licenses/      license notices (Rust deps always; llama.cpp for inference builds; NVIDIA EULA for cuda)
```

`package.sh` names each contract file individually rather than copying `contracts/` wholesale, so a
new contract reaches the installed tree only when it is added there — `installers/smoke.sh` asserts
all three are present, and that `backend list` can actually read the last one from an unrelated cwd.

`package.sh --kind=base|cpu|vulkan|cuda`:

| Kind | Produces |
|---|---|
| `base` | plumbing artifact, no inference. Builds itself anywhere (no C++ toolchain). |
| `vulkan` | **THE RELEASE ARTIFACT** — CPU **and** Vulkan backends in one tree. Gets the plain name. |
| `cpu` | build kind only (a box with no Vulkan SDK): core libs + `ggml-cpu-*` variants, `-cpu` suffix. |
| `cuda` | **Linux:** opt-in payload, not an app — `ggml-cuda` + NVIDIA redist for `~/.knaif/backends`. **Windows:** still the historical static-with-redist app (post-v1, C6). |

**The CUDA payload needs an R580+ driver (CUDA 13).** This is a hard floor, and the failure it
produces is misleading: on an older driver the payload copies in cleanly, the loader finds it,
and the load then fails in a way that reads like a driver bug rather than a version mismatch.
The presence of `nvcuda.dll` is **not** a sufficient check — it says a driver exists, not that it
is new enough. Until `knaif backend install cuda` ships with a driver-aware gate
([post-v1-ci-and-cuda-opt-in](plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md), U2–U3), the CUDA
payload is a manual copy into `~/.knaif/backends` and this documentation is the only thing
standing between an old-driver user and that failure. Probe the actual driver version
(`nvidia-smi`) before installing; if it is below R580, update the driver rather than trying the
payload. Vulkan is the supported path on any NVIDIA card whose driver is older.

**One default artifact per OS** (C5b), never one per backend: `vulkan` is a strict superset of `cpu`
(the Vulkan backend is one extra loadable lib, and it loses device selection where there is no usable
GPU), so `cpu` is **not** a release artifact — publishing both only makes users choose.

**Linux** builds every kind itself (gcc + cmake + ninja + patchelf), stages the dynamic libs beside
the exe with an `$ORIGIN` RPATH, and collects the CUDA redist as `.so.13` from
`$CUDA_PATH/targets/x86_64-linux/lib`. **Windows** functional kinds must be compiled first in a VS
Developer shell, then packaged `--no-build`; `package.sh` stages the same core libs + loadable
backends beside the exe (no RPATH needed — Windows searches the loading module's own directory).

**Linux artifacts:** the default `.tar.gz`, plus an **AppImage**
(`installers/linux/build-appimage.sh <staged-dir|tarball>`) that mirrors the same exe-relative layout
(exe+libs at `usr/bin`, `skills/`+`contracts/` at `usr/`). One AppImage carries the default CPU+Vulkan
backends; the opt-in `ggml-cuda.so` loads from `~/.knaif/backends`, outside the read-only mount.

**Windows installer** (`installers/windows/knaif.iss`, Inno Setup, per-user, no admin):

- Component tree mirrors the product model: **core** is mandatory; each **skill** is an
  optional component (currently ~0.1 MB each — runtime data only; the real footprint is
  the external tools + model, installed separately).
- **Supporting tools** are installed post-install via **winget** as opt-in tasks
  (FFmpeg default-on; Ghostscript / LibreOffice / Tesseract opt-in) — only when winget
  exists and the tool isn't already on PATH. Third-party tools are never bundled.
- **Model**: an opt-in task runs `knaif models pull <default>` (~2.5 GB, one-time),
  skipped when the GGUF is already in `~/.knaif/models`.
- **PATH**: appended only with explicit consent (the `addtopath` task), removed on
  uninstall.

Component-model rationale: see the installer-component-model decision record.

## 10. Building from source

```bash
# base (no inference) — no MSVC/C++ toolchain needed
cargo build --release -p knaif-cli
installers/package.sh --kind=base
```

**Linux — `package.sh` builds and packages in one step** (gcc + cmake + ninja + patchelf; Vulkan also
needs `libvulkan-dev`/`glslc`/`spirv-headers`; CUDA needs the toolkit). It selects the features and
stages the dynamic libs for you:

```bash
installers/package.sh --kind=cpu                                  # llama,dynamic-backends
installers/package.sh --kind=vulkan                               # + vulkan  (the default artifact)
CUDAARCHS="75-real;80-real;86-real;89-real;90-virtual;120-real" \
  installers/package.sh --kind=cuda                               # opt-in payload
installers/linux/build-appimage.sh dist/knaif-<ver>-linux-x64-vulkan.tar.gz
```

**Windows — compile first in a "Developer PowerShell for VS"**, then package `--no-build`:

```bash
cargo build --release -p knaif-cli --features llama,dynamic-backends            # cpu
CMAKE_GENERATOR=Ninja \
  cargo build --release -p knaif-cli --features llama,dynamic-backends,vulkan   # vulkan
CUDAARCHS="75-real;80-real;86-real;89-real;90-virtual;120-real" \
  cargo build --release -p knaif-cli --features llama,dynamic-backends,cuda     # cuda payload
installers/package.sh --no-build --kind=<cpu|vulkan|cuda>
```

Release artifacts use **`dynamic-backends`** (§5.3). Drop it for a static single-exe dev build
(`--features llama[,vulkan|,cuda]`); `package.sh` will then have no backend libs to stage.

**Vulkan requires `CMAKE_GENERATOR=Ninja`.** The default Visual Studio/MSBuild generator breaks on
llama.cpp's `vulkan-shaders-gen` ExternalProject with `cannot find the batch label specified -
VCEnd`. Ninja drives `cl.exe` directly, so run it from a **Developer PowerShell for VS** (which
supplies both the VS-bundled Ninja and the MSVC `INCLUDE`/`LIB` env). `just native-vulkan` forces
this for you.

**CUDA arch list: use `CUDAARCHS`, and never `native`.** CMake initialises
`CMAKE_CUDA_ARCHITECTURES` from the **`CUDAARCHS`** environment variable — setting
`CMAKE_CUDA_ARCHITECTURES` in the environment does nothing, and `llama-cpp-sys-2`'s build script
offers no passthrough. Without `CUDAARCHS`, ggml's default list emits **no `sm_120` SASS at all**
(no Blackwell support).

**Forward-compat PTX must come from a non-`12X` virtual arch.** ggml rewrites every `12X` → `12Xa`
(`ggml-cuda/CMakeLists.txt`), so `120-virtual` yields `sm_120a` PTX — architecture-specific, and
therefore *not* forward-compatible. `90-virtual` escapes that rewrite and its `compute_90` PTX JITs
forward to any later GPU, which is why the arch list carries it (and why `120-real` alone is fine
for Blackwell SASS).

Verify what a build actually produced — a flag typo silently drops an arch:

```bash
"$CUDA_PATH/bin/cuobjdump" --list-elf <lib> | grep -oE 'sm_[0-9]+' | sort -u
```

**Changing `CUDAARCHS` or the generator needs a clean first.** The build script calls
`always_configure(false)`, so cmake will not reconfigure and an incremental build silently keeps the
old settings: `cargo clean -p llama-cpp-sys-2` (or `just clean-vulkan-build`).

Run a CUDA build with `%CUDA_PATH%\bin` on PATH (or the bundled redist DLLs beside the
exe). Tests: `cargo test` (the llama.cpp inference proof is gated on `$KNAIF_TEST_GGUF`).

## 11. Environment variables

| Variable | Effect | Default |
|---|---|---|
| `KNAIF_SKILLS_ROOT` | Override the skills directory | (resolved) |
| `KNAIF_MODEL_MANIFEST` | Override the model-manifest path | (resolved) |
| `KNAIF_BACKEND_MANIFEST` | Override the backend-manifest path | (resolved) |
| `KNAIF_MODELS_DIR` | Override the GGUF store directory | `~/.knaif/models` |
| `KNAIF_BACKENDS_DIR` | Override where loadable `ggml-*` backends are scanned for (`dynamic-backends` builds; the exe's own directory is always scanned too) | `~/.knaif/backends` |
| `KNAIF_NO_CUDA_NUDGE` | Suppress the first-run CUDA offer (§5.5) | off |
| `KNAIF_LLM_BACKEND` | `mock` opts out of default-model auto-select (§5.6); `llama` needs a model | auto-select, else `mock` |
| `KNAIF_LLM_MOCK_RESPONSE` | Canned mock plan (offline dev/eval) | empty plan |
| `KNAIF_N_GPU_LAYERS` | GPU offload layer count (`0` = CPU) | `999` |
| `KNAIF_N_CTX` | Context / batch size | `8192` |
| `KNAIF_MAX_TOKENS` | Generation cap | `512` |
| `KNAIF_TIMING` | Print `[knaif-timing]` per-phase inference timing to stderr | off |
| `KNAIF_DEBUG` | Dump raw model output on a parse/validate failure | off |

## 12. Known limitations & roadmap

- **CUDA distribution — mechanism DONE, install surface DEFERRED to post-v1.** The default download
  carries CPU+Vulkan loadable backends (§5.3); CUDA is an **opt-in payload** (~½ GB of NVIDIA redist
  that non-NVIDIA users never pay for) loaded from `~/.knaif/backends`. The **loader** is proven on
  Windows and Linux (C5a), and the multi-arch fatbin per §5.3/§10 is built; payload split +
  GH-Releases hosting are decided.
  **v1 ships no `knaif backend install cuda` command and no CUDA component in the installer** — the
  only documented v1 route is copying the payload into `~/.knaif/backends` by hand. The install
  surface, plus its **driver gate** (CUDA 13 needs R580+; `nvcuda.dll`/`libcuda.so` presence alone is
  not sufficient), lands in
  [post-v1-ci-and-cuda-opt-in](plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md).
  - **That manual route is LINUX-ONLY in practice at v1.** `package.sh --kind=cuda` emits a real
    payload only on Linux (D4: built, and C6b verified CUDA then wins device selection). On Windows
    the same flag still produces the **historical static-with-redist app**, not a payload, so there is
    nothing a Windows user could copy into `~/.knaif/backends` — even though the Windows loader would
    happily pick it up (the default Windows artifact is a `dynamic-backends` build). Aligning Windows
    `cuda` onto Option 3 is part of the same post-v1 plan. Neither OS publishes a CUDA asset at v1, so
    this blocks nothing — but do not tell a Windows user the manual route is available to them.
- **CI** — there is no `.github/workflows/` yet; v1 gates on **local** green. CI + `release.yml` +
  eval-parity are tracked in the same post-v1 plan (they need the final org, so they land after the
  transfer).
- **macOS** — no installers/notarization; explicitly out for v1.
- **Linux CPU floor** — the CPU artifact is glibc-linked; a static-musl floor build is a possible
  fast-follow (CUDA/Vulkan need glibc + the vendor driver regardless).
- **Persistent daemon** — keep the model resident to make repeat CUDA calls near-instant
  (low value for Vulkan; see §5.4/5.5).
- **Vulkan decode speed** — investigate whether it is Blackwell/sm_120/coopmat2-specific;
  revisit after llama.cpp updates.
- **Execution breadth** — native `run` supports ffmpeg + documents, including image watermark
  (documents; image-XObject with soft-mask alpha, covered by `overlay.rs` tests).
- **Logging facility** — diagnostics are currently ad-hoc `eprintln!` gated by env vars
  (`KNAIF_TIMING`, `KNAIF_DEBUG`). Establishing a first-class logging system (and routing
  timing through it) is deferred to its own plan.
