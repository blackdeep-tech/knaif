# macOS Support — Metal inference, packaging, signing, and a third release platform

**Status:** Planning · **Created:** 2026-08-02 · **Completed:** —
**Owner:** native/packaging · **Ref:** [`installers/macos/README.md`](../../installers/macos/README.md) (placeholder this plan replaces) · [`installers/package.sh`](../../installers/package.sh) · [`docs/NATIVE.md`](../NATIVE.md) §5, §9, §10, §12 · [`docs/RELEASE.md`](../RELEASE.md) · [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md) (C3 matrix, §*Out of scope*)

> **Status note.** Not started. macOS has been explicitly out of scope since
> [monorepo-dual-runtime](2026-06-17-monorepo-dual-runtime.md) Phase 9 and is still listed as a
> known limitation in [NATIVE.md](../NATIVE.md) §12 ("no installers/notarization; explicitly out for
> v1"). The Rust core was kept cross-platform on purpose, so this plan is **packaging, signing, and
> verification work with a small amount of build plumbing** — not a port. The inference question is
> already answered by the vendored sources (§1); the risk lives in §5–§8.
>
> **Execution target: a current macOS on Apple Silicon** — but not exclusively. Building, signing,
> notarizing and running anything Darwin needs the Mac. **`scripts/check_macho_deps.py` (E1), the
> `package.sh` branches (§5), the feature-graph change (D3), and the eval-snapshot prerequisite
> (C0) can and should be written and tested from the Windows box**, which is the whole design of
> the existing `check_pe_imports.py` / `check_elf_deps.py` pair: a static checker must fail where
> the mistake was made, not only on the platform that suffers it.
>
> **Reviewed 2026-08-02** against an external audit; every finding was re-verified against the code
> before being accepted. The audit's six blocking findings all held and are folded in below — the
> substantive ones were C0 (the regression gate proved nothing), C5's binary, and the feature-graph
> error in D3. Two audit claims were corrected rather than adopted (A5's `@executable_path`
> rationale, and the OpenMP failure's exact shape); one was found to understate the problem (the
> `documents` snapshot is stale too, not just `ffmpeg`).

**Goal:** Ship knaif on macOS as a first-class third platform — a signed, notarized arm64 artifact
with Metal GPU inference, verified by the same eval/parity/clean-room gates Windows and Linux
already pass, and cut by the same `RELEASE.md` procedure.

---

## 1. Research: which inference stack — "Metal or llama.cpp?"

**The question dissolves on contact: Metal *is* the llama.cpp backend on Apple.** `ggml-metal` is a
first-class ggml backend that sits behind the same `LlmBackend` trait knaif already dispatches
through. There is no fork in the road here; the real fork would be leaving llama.cpp entirely for
Apple's own stacks, and that is rejected below.

Everything in this section was verified by reading the **vendored** `llama-cpp-sys-2 0.1.150`
sources this workspace already pins (`~/.cargo/registry/src/*/llama-cpp-sys-2-0.1.150/`), not from
memory or upstream docs. Line references are to that copy.

### 1.1 What the pinned crate already does on Apple

| Finding | Where | Consequence for knaif |
|---|---|---|
| `GGML_METAL` defaults **ON** when `APPLE` | `llama.cpp/ggml/CMakeLists.txt:95-103, 239` (`GGML_METAL_DEFAULT`) | **No new cargo feature is needed.** A plain `--features llama` build on macOS compiles the Metal backend. |
| `llama-cpp-sys-2`'s `metal` feature is **declared but never read** | `Cargo.toml:86` vs. no `feature = "metal"` in `build.rs` | Adding a `metal` feature to `knaif-llm` that forwards to it would be **a lie in the feature graph**. Do not add one. See D2. |
| `GGML_METAL_EMBED_LIBRARY` defaults to `${GGML_METAL}` = ON | `ggml/CMakeLists.txt:242`; `ggml/src/ggml-metal/CMakeLists.txt` | The Metal shader library is **embedded into the backend binary** via an `.incbin` asm stub — there is **no `default.metallib` to stage**. This removes the single most obvious packaging hazard before it exists. |
| `ggml_add_backend_library(ggml-metal …)` | `ggml/src/ggml-metal/CMakeLists.txt` | Under `GGML_BACKEND_DL` it becomes a loadable **`libggml-metal.dylib`**, exactly like `libggml-vulkan.so`. **`dynamic-backends` works on macOS with no new mechanism.** ⚠️ **Corrected 2026-08-03, verified on hardware (A4): the actual file is `libggml-metal.so`, not `.dylib`.** CMake's `MODULE` library type (used for `GGML_BACKEND_DL` targets) suffixes `.so` on Apple too — only `SHARED` targets get `.dylib`. `has_backend_libs` in `knaif-llm` is unaffected (it checks the `ggml-` prefix, not the extension) and ggml's own `load_backends_from_path` dlopens it fine — confirmed empirically: `load_backend: loaded MTL backend from …/libggml-metal.so`, 37/37 layers offloaded. **This does matter for B3/B1**: any packaging step that globs `libggml-*.dylib` to decide what to stage/sign will silently miss these files. Glob `libggml-*.{dylib,so}` (or just `libggml-*` minus the core libs) on Darwin. |
| `GGML_CPU_ALL_VARIANTS` supports Apple ARM: `apple_m1` (DOTPROD), `apple_m2_m3` (+MATMUL_INT8), `apple_m4` (+SME) | `ggml/src/CMakeLists.txt:403-430` | The runtime CPU-variant dispatch that the Windows/Linux artifacts rely on **also works here**, and specialises per Apple generation. Three `ggml-cpu-*` libs, not nine to fourteen. Confirmed on hardware (A4): `libggml-cpu-apple_m1.so`, `_m2_m3.so`, `_m4.so` all built; `apple_m2_m3` correctly selected at runtime on this M3 Pro. |
| Frameworks linked: `Foundation`, `Metal`, `MetalKit`, `Accelerate`, `libc++` | `build.rs:1157-1176` | **All system frameworks. Nothing redistributable, no NVIDIA-shaped payload, no EULA.** Confirmed via `otool -L` on every staged Mach-O (A4/A5): only system framework paths, `libc++`, and `@rpath` to the artifact's own core libs — no third-party dependency anywhere. |
| `GGML_BLAS` is forced **OFF** on Apple by the crate | `build.rs:673` | Accelerate is linked but not used as the BLAS provider. Affects the CPU fallback path only; note it, don't fight it. Confirmed: `GGML_BLAS:BOOL=OFF`, `GGML_BLAS_VENDOR:STRING=Apple` in `CMakeCache.txt`. |
| `openmp` is a **default feature** of `llama-cpp-2` → `GGML_OPENMP=ON` | `llama-cpp-2/Cargo.toml` `default = ["openmp", …]`; `sys/build.rs:891` | ⚠️ **Fully measured and fixed, 2026-08-07 (B5).** `brew install libomp` alone did not reproduce the trap — it is keg-only, so plain `find_package(OpenMP)` still misses it. It reproduces once the build environment resolves the keg (`CMAKE_PREFIX_PATH=/opt/homebrew/opt/libomp`, which is exactly what many unrelated Homebrew formulae's own build recipes export): `GGML_OPENMP_ENABLED` flips ON and `libggml-base.dylib` + every `ggml-cpu-*` backend links `/opt/homebrew/opt/libomp/lib/libomp.dylib` — caught correctly by `check_macho_deps.py` (E1). D3's fix (an explicit, opt-in `openmp` Cargo feature, off on macOS) is implemented and verified to hold even under that exact trap-triggering environment — see B5. |

### 1.2 What this means for the artifact shape

- **One default artifact per OS holds unchanged (C5b).** On Windows/Linux that artifact is the
  `vulkan` kind because Vulkan is one extra loadable lib beside the CPU backends. On macOS the same
  role is played by Metal — except Metal costs *no extra cargo feature at all*, so the macOS default
  artifact's feature set is literally `llama,dynamic-backends`.
- **There is no opt-in payload on macOS, and there never will be one.** The whole `backend install`
  surface exists because CUDA is ~668 MB of NVIDIA redistributables. Metal is a system framework;
  `libggml-metal.dylib` is small and ships in the default artifact. `backend list` must therefore
  report *"unavailable on this platform"* for `cuda` (it already does — verified in
  `backend_store.rs` / `main.rs:476`) and the first-run CUDA nudge must stay silent (`nvidia-smi`
  does not exist).
- **`vulkan` and `cuda` are not macOS kinds.** MoltenVK was considered and is rejected in D1.

### 1.3 ⚠️ The OpenMP trap, third instance

`llama-cpp-2`'s **default** features include `openmp`, so `build.rs` sets `GGML_OPENMP=ON`, and
ggml then runs `find_package(OpenMP)`. On Apple Clang there is no OpenMP runtime in the toolchain —
**but there is one in Homebrew** (`brew install libomp`, pulled in by dozens of unrelated formulae).
If CMake finds it, `libggml-base.dylib` links `/opt/homebrew/opt/libomp/lib/libomp.dylib`, an
absolute path that **does not exist on a clean Mac**.

This is precisely the failure that shipped in every 1.0.x artifact twice already, under two names:

| OS | Library | How it was found | How it presented |
|---|---|---|---|
| Windows | `VCOMP140.dll` | Windows Sandbox clean room | `0xC0000135` at process start, printing nothing |
| Linux | `libgomp.so.1` | `scripts/check_elf_deps.py` | CPU backends fail to load on a box without GCC |
| **macOS** | **`libomp.dylib`** | **must be caught by B5 / E1 below** | **backend load failure on a Mac without Homebrew** |

It resolves on the build box for the same reason both predecessors did: the build box has the
toolchain. **Do not assume the outcome either way** — `find_package(OpenMP)` may simply fail on a
stock toolchain, in which case ggml warns and disables it. Measure it (B5), then take D3.

> **⚠️ The failure will probably not look like a Homebrew path, and a checker that greps for one
> will miss it.** LLVM builds `libomp.dylib` with an `LC_ID_DYLIB` of **`@rpath/libomp.dylib`**, so
> the dependent's load command carries no `/opt/homebrew/…` string at all — it is non-portable
> because the `@rpath` target is *absent on the user's machine*, not because the path is visibly
> foreign. **E1 must therefore reject any dependency that does not RESOLVE**, and treat visible
> non-system path prefixes as a secondary signal only. This is the difference between a check that
> catches the bug and one that reports a clean bill of health on a broken artifact.

### 1.4 Rejected alternatives

- **MLX / Core ML / Apple Foundation Models — for *this artifact*.** A separate inference stack: no
  GGUF, no llama.cpp, no shared greedy-decode path. Adopting one *for the macOS CLI* would fork
  `knaif-llm`'s only real backend and cost the property that makes the eval numbers comparable —
  *the same GGUF, greedy-decoded, produces the same plan everywhere* — while buying nothing, because
  Metal already gives this artifact full GPU inference for no extra cargo feature (§1.1). The knaif
  value proposition is the deterministic plan pipeline, not the tensor kernels.

  > **Scope of this rejection.** It is about **the macOS CLI's backend**, not a verdict on Apple's
  > inference stacks in general. A surface with different constraints — no shell, a hard app memory
  > budget, no multi-GB download — is a different question with a different answer, and evaluating
  > that is neither in this plan's scope nor blocked by it. **Reconsider *here*** only if Metal
  > measurements (§7) come in badly, and then as its own plan.
- **MoltenVK (Vulkan-over-Metal).** Strictly worse than Metal on every axis: it is a translation
  layer *above* the API ggml already targets natively, it adds a redistributable to sign and notarize,
  and it would put macOS on the Vulkan code path whose Blackwell collapse
  ([PERFORMANCE.md](../PERFORMANCE.md) §2) is the reason the CUDA payload exists at all. No.
- **Universal2 (`arm64` + `x86_64` in one binary).** See D4.

---

## 2. Decision log

**D1 — Metal, via llama.cpp, with no new cargo feature.** Per §1.1. The macOS default artifact is
built with `--features llama,dynamic-backends` and gets Metal for free because ggml defaults it ON
under `APPLE`. Adding a knaif `metal` feature would forward to a `llama-cpp-sys-2` feature that
`build.rs` never reads — a control that does nothing, which is worse than no control.

**D2 — `metal` is the *only* functional kind on macOS; `cpu` is refused there.** *Revised
2026-08-02 after audit.* The first draft said `--kind=metal` and `--kind=cpu` share a feature set
but stay distinguishable via `out_dir()`. **That was wrong, and the error is instructive:**
`out_dir()` identifies a build by the backends it emitted, and because `GGML_METAL` defaults ON
under `APPLE` (§1.1), a macOS `cpu` build emits `libggml-metal.dylib` too. The two kinds are not
merely feature-identical — they are **byte-identical builds**, so no `out_dir()` predicate can ever
separate them and one written to try would silently match both.

So: on Darwin, `metal` is the only functional kind, and `--kind=cpu` is **refused with a message
saying why**, exactly like `vulkan` and `cuda` (B1). This is strictly simpler than the alternative
(two staging profiles over one build) and it costs nothing, because there is no reason to publish a
CPU-only macOS artifact — Metal is a system framework, present on every supported machine.

> **A CPU-only *tree* is still needed, just not as a release kind.** §7's honest-CPU measurement and
> C5's CPU control both require one. Produce it by **copying a staged tree and deleting
> `libggml-metal.dylib`** — the loader then finds no Metal backend and cannot offload. That is a
> benchmarking procedure, not a build, and it is more trustworthy than `KNAIF_N_GPU_LAYERS=0`,
> which [PERFORMANCE.md](../PERFORMANCE.md) §4 measured as **not CPU-only at all** (`op_offload`,
> an 11× error). One mechanism serves both needs.

**D3 — OpenMP: give knaif a real `openmp` feature and leave it off on macOS.** *Revised 2026-08-02
after audit; confirmed and implemented 2026-08-07 (B5).* If the build links Homebrew's libomp, the two
fixes are (a) stage `libomp.dylib` and rewrite its install name, or (b) turn `GGML_OPENMP` off for
the macOS artifact. Prefer (b) — ggml falls back to its own thread pool, macOS work is on Metal
anyway (`resolve_n_threads` matters only to the CPU fallback), and it removes a third-party binary
from the set that must be signed rather than adding one.

> **⚠️ The obvious implementation of (b) is a bug.** "Build with `llama-cpp-2`'s default features
> off" **also drops `common`** — `default = ["openmp", "android-shared-stdcxx", "common"]` — and
> `common` is what builds `llama-common`, which `package.sh` stages as a core lib on every platform.
> Silently losing it would break the artifact in a way unrelated to OpenMP.

The correct shape is an honest control, not a blunt one:

- declare `llama-cpp-2` with `default-features = false` **and an explicit `common`**;
- add a real `openmp` feature to `knaif-llm` (and forward it from `knaif-cli`);
- enable it for the Linux and Windows release kinds, which have shipped with it and whose
  `VCOMP140`/`libgomp` staging already assumes it;
- omit it for the macOS `metal` kind.

That adds a feature that genuinely controls something — the exact opposite of the fake `metal`
feature D1 rejects. Take (a) only if (b) measurably costs CPU-fallback throughput (§7 D5). libomp is
Apache-2.0-with-LLVM-exception, so either way there is no licence obstacle, only a complexity one.

**D4 — arm64 only. Not universal2, not a separate Intel artifact.** Apple Silicon is the entire
reason macOS is interesting for local inference (unified memory + a competent GPU in a laptop);
ggml's Metal backend on Intel Macs' AMD/Intel GPUs is a far weaker path, the last Intel Mac shipped
in 2020, and Apple's own support window for them is closing. Universal2 would double build time and
artifact size, and `GGML_CPU_ALL_VARIANTS` emits *different* variant sets per architecture, so the
two halves are not symmetric and `lipo`ing the tree is more than a mechanical step. **Publish
`macos-arm64`; state Intel as unsupported in the release body rather than shipping something
untested.** Revisit only if real demand appears.

**D5 — the data directory stays `~/.knaif`.** Not `~/Library/Application Support/knaif`. knaif is a
Unix CLI, `~/.knaif` is what `KNAIF_MODELS_DIR`/`KNAIF_BACKENDS_DIR` default to on every platform,
and one path across three OSes is worth more than one platform's HIG convention for a tool with no
GUI. Recorded here so it is not re-litigated; revisit if a macOS GUI front-end ever lands.

**D6 — two artifacts: a `.zip` and a signed/notarized `.pkg`. Not a `.tar.gz`.** *Revised
2026-08-02 after audit.* The `.pkg` exists because **it is the only shape a CLI's notarization
ticket can be *stapled* to** — `stapler` accepts `.app`, `.dmg` and `.pkg`, and nothing else.
Without a staple, first run on a quarantined download needs Apple's notary service to be reachable,
so a user offline or behind a filtering proxy gets a Gatekeeper block on a correctly notarized
build.

The archive is a `.zip` rather than the `.tar.gz` Linux ships, breaking symmetry deliberately:
**Apple's notary service accepts `.zip`, `.pkg` and `.dmg` — not `.tar.gz`.** Publishing a tarball
would mean notarizing a `.zip` of the same tree and then shipping a *different container*, so the
thing verified and the thing downloaded are never the same file. That works (notarization registers
the binaries' CDHashes, not the container) but it is a gap nobody can inspect, and `.zip` is the
native macOS archive idiom anyway. `.dmg` was considered and rejected: a drag-to-Applications idiom
for `.app` bundles that communicates nothing useful for a `bin/knaif`.

**Homebrew tap is a deliberate fast-follow** (G5) — the best macOS channel for a CLI, and it
sidesteps quarantine entirely, but it depends on a published, checksummed archive existing first.

**D7 — do NOT add a fourth version declaration; derive it.** *Reversed 2026-08-02 after audit.* The
first draft said the `.pkg` version "joins `test_version_consistency.py`". Wrong instinct: that test
exists to catch drift between declarations that **must** be written by hand
(`Cargo.toml`, `pyproject.toml`, `knaif.iss`, the backend manifest), and the right move is to
*avoid creating a fourth* rather than to police one. `package.sh` already derives `VER` from
`Cargo.toml`; `pkgbuild --version` takes it from the same variable, so there is nothing to drift.
**Verify by inspecting the built package** (`pkgutil --expand` / the receipt's version) rather than
by adding a committed source of truth. The cheapest declaration to keep honest is the one that does
not exist.

**D8 — the macOS clean room is a VM, it is required, and it runs the OLDEST supported macOS.**
[RELEASE.md](../RELEASE.md) §4 already states the rule this plan inherits: *a verification step that
runs on the build box tests staging, never portability*, and it explicitly names macOS as a new
artifact shape needing its own clean-room run. On macOS the build box has Xcode Command Line Tools
and almost certainly Homebrew — the two things whose absence this must prove irrelevant. *Tightened
2026-08-02 after audit:* a clean **current** macOS tests the toolchain assumption but says nothing
about the deployment floor, so the minimum supported OS is what the gate must run (D9), and the run
must include real inference (E3), not just `--version`.

**D9 — the macOS deployment floor is a DECISION made before the first functional build, not an open
question.** *Added 2026-08-02 after audit.* The first draft demoted `MACOSX_DEPLOYMENT_TARGET` to
§12's open questions. That contradicts this project's own hard-won rule from
[portable-builds](2026-07-27-portable-builds.md): **the floor is a property of the artifact, chosen,
not inherited from whichever machine built it.** Linux pays for a whole pinned container to get
this right; macOS gets it for one environment variable, and there is no excuse for leaving it to
whatever SDK happens to be installed.

Two mechanical traps make it worse than it looks:

- **`MACOSX_DEPLOYMENT_TARGET` is not a build-script rerun trigger.** Verified against the pinned
  crate: `build.rs` declares `rerun-if-env-changed` for `LLAMA_LIB_PROFILE`, `CUDA_PATH`,
  `ROCM_PATH` and others — **not** for the deployment target. Combined with `always_configure(false)`
  (which [RELEASE.md](../RELEASE.md) §2 already documents as the reason a `CUDAARCHS` change needs a
  clean), **changing the floor after a cached build silently keeps the old one.** Same trap, new
  costume. Set it before compiling, and use a dedicated `CARGO_TARGET_DIR` for release builds.
- **A stated floor that nothing verifies is exactly the pattern that has already burned this
  project twice.** Assert `LC_BUILD_VERSION` on **every** staged Mach-O (E1), and run the clean-room
  VM on the **oldest supported** macOS — not merely a clean current one, which tests nothing about
  the floor.

---

## 3. Workstream M — machine and toolchain baseline

- [x] **M1. Record the machine.** Model, chip (M-series generation), core counts (P/E), GPU core
      count, **unified memory size**, macOS version, and Xcode Command Line Tools version. This
      becomes a row in [PERFORMANCE.md](../PERFORMANCE.md) §1 and every number this plan produces is
      quoted against it. The §1 warning — *never quote a latency number without naming the machine* —
      applies from the first measurement, not retroactively.
      > **Done 2026-08-03.** Machine `M3P`: Apple M3 Pro, 6P+6E CPU, 18-core GPU, 18 GB unified
      > memory, macOS 26.6 (build 25G72), Xcode 26.6 / CLT 26.6.0.0. Row added to
      > [PERFORMANCE.md](../PERFORMANCE.md) §1.
- [x] **M2. Provision via `mise`.** `just bootstrap` should work unchanged (`mise.toml` pins
      python 3.14 / uv 0.11.2 / rust 1.96 / just / cmake). Confirm `rust-toolchain.toml` resolves to
      the `aarch64-apple-darwin` host. Record anything mise cannot provide.
      > **Done 2026-08-03.** `just bootstrap` → "Toolchain provisioned via mise." with no gaps.
      > `rustc -vV` confirms `host: aarch64-apple-darwin`. Nothing mise could not provide.
- [ ] **M3. Confirm the *build* prerequisites and write them down as a list that can be wrong in a
      way that stops the build.** Expected: Xcode Command Line Tools (`xcode-select --install`) for
      `clang`, `ld`, `xcrun`, `codesign`, `otool`, `install_name_tool`, `pkgbuild`, `notarytool`;
      `cmake` and `ninja`. **Explicitly test whether a full Xcode is required** or CLT suffices —
      Metal shader compilation at *build* time is not needed (the shaders are embedded as source,
      §1.1), which is the usual reason a project needs full Xcode. If CLT suffices, say so loudly:
      it is a 10× smaller prerequisite.
      > **Partial 2026-08-03.** This machine has full Xcode 26.6 installed (CLT 26.6.0.0 alone was
      > not isolated) and A2/A4 succeeded on it. **Still open:** whether CLT alone suffices requires
      > a machine/VM *without* full Xcode — uninstalling Xcode from this dev machine to test would be
      > destructive and was not done. Test this in the E3 clean-room VM instead, which is being built
      > without Xcode/CLT/Homebrew anyway (D8) — if the build step were ever run there, it would
      > answer this for free; failing that, provision a disposable VM with CLT only.
- [x] **M4. Skill-dependency tooling via Homebrew** for the eval/quality work: `ffmpeg`,
      `ghostscript`, `libreoffice`, `tesseract`. `deps.rs` already maps macOS → `brew`
      (`deps.rs:45,55,338,343`) — verify the probe actually resolves `/opt/homebrew/bin` entries
      under the PATH a **GUI-launched** process gets, not just a login shell's.
      > **Done 2026-08-03.** `knaif skills deps` (native, mock) correctly reports
      > `[OK] ffmpeg /opt/homebrew/bin/ffmpeg, /opt/homebrew/bin/ffprobe` (installed) and
      > `[MISS] ghostscript/libreoffice/tesseract (optional) install: brew` (not installed) — matches
      > C7's "`[MISS]` is a pass" expectation. `resolve_command`/`which` in `deps.rs` reads
      > `std::env::PATH` directly with no OS-specific handling; this resolves correctly for a
      > terminal-invoked process because Homebrew's installer appends `/opt/homebrew/bin` to the
      > shell rc files a login/interactive shell sources. **The GUI-launched-process PATH concern is
      > real in principle but untested** — knaif has no GUI launch path today (out of scope, §11), so
      > there is nothing to test it against; revisit only if a GUI front-end ever lands (ties to D5).
- [x] **M4b. ⚠️ Decide `MACOSX_DEPLOYMENT_TARGET` — before A2, not after.** Per D9. Pick the floor
      deliberately, export it before the first functional compile, and use a dedicated
      `CARGO_TARGET_DIR` for release builds so a later change cannot be swallowed by a cached
      `llama-cpp-sys-2` configure (it is **not** a `rerun-if-env-changed` input — verified). Record
      the chosen floor, then have E1 assert `LC_BUILD_VERSION` on every staged Mach-O and E3 launch
      on that oldest OS. A floor that is stated but not asserted is the exact pattern that produced
      both prior portability defects.
      > **Decided 2026-08-03: `MACOSX_DEPLOYMENT_TARGET=12.0` (Monterey).** Exported before the very
      > first `llama-cpp-sys-2` configure (A2), so there was no cached-build swallow risk this time.
      > **Wired into `package.sh` itself in B2** (both its own build path and, after a second
      > instance of the same gap was found, the `justfile`'s `package-native` recipe too — see B2's
      > note). **Still not done:** a dedicated `CARGO_TARGET_DIR` for release builds, so a *future*
      > floor change on a dev machine with a warm `target/` can't silently keep the old one. Low
      > priority in practice — CI/release builds should use a clean checkout per `RELEASE.md`
      > anyway — but worth doing before this plan closes.
- [x] **M5. Baseline the repo before changing anything:** `just check` (lint + mypy + pytest +
      generated-docs) and `just test-native`. Record every failure. **Some Python tests have never
      run on Darwin**; a pre-existing failure must not be discovered later and mistaken for
      something this plan caused.
      > **Both halves done.** Native: `cargo fmt --all --check` and
      > `cargo clippy --workspace --all-targets -- -D warnings` clean (default features);
      > `cargo test --workspace`: 63 passed, 0 failed. Additionally ran the
      > `$KNAIF_TEST_GGUF`-gated real-inference proof manually (`cargo test -p knaif-llm --features
      > llama inference_produces_text` with `KNAIF_TEST_NGL=99`): **passed**, output
      > `{"ok": true}`, confirming Metal offload end-to-end at the unit-test level — this is the
      > condition C1 asks for, just not yet wired into a macOS `just test-native` invocation.
      > **Python: `just check` run 2026-08-03 — clean.** 1629 passed, 7 skipped, 16 benign warnings
      > (missing-model-path `UserWarning`s from fixtures that intentionally construct an
      > uninitialized orchestrator), 82.63% coverage (bar is 80%), `gen_skills.py --check` and
      > `cargo fmt`/`clippy` all green. **No pre-existing Darwin-specific test failure found** — the
      > "some Python tests have never run on Darwin" risk this task exists to catch did not
      > materialize.

---

## 4. Workstream A — build the native runtime on macOS

- [x] **A1. Mock build first.** `cargo build --release -p knaif-cli` and `just native-mock -- skills list`.
      No llama.cpp, no C++ — this isolates *knaif's own* Darwin portability from llama.cpp's.
      Expect `cfg(unix)` `libc`/`tcflush` to compile and the `cfg(windows)` console/mutex paths to
      drop out. Fix any `unused_imports` / dead-code warnings that only appear off Windows —
      `check-native` runs clippy with `-D warnings`.
      > **Done 2026-08-03.** Clean build, no warnings, `Finished release profile in 49.85s`.
      > `just native-mock -- skills list` and `skills deps` both work correctly.
- [x] **A2. First functional build.** `cargo build --release -p knaif-cli --features llama`.
      Static, no `dynamic-backends`. This is the smallest thing that can prove Metal works.
      Budget real time for the first llama.cpp compile.
      > **Done 2026-08-03**, with `MACOSX_DEPLOYMENT_TARGET=12.0` exported per M4b/D9. Compiled in
      > 1m36s wall (9m14s user — genuinely compiled ggml/llama.cpp C++ across all cores, not a
      > cache hit). Much faster than the "budget real time" warning implied on this hardware.
- [x] **A3. Prove Metal is actually selected, not merely compiled.** Run with `--verbose` and
      confirm the device line reports Metal rather than CPU, and that all model layers offload.
      This is the macOS instance of the trap [PERFORMANCE.md](../PERFORMANCE.md) §2 documents twice
      (WSL's "Vulkan" runs that were silently CPU; `op_offload` making `n_gpu_layers=0` not
      CPU-only). **A backend that silently isn't the one you think you are benchmarking is the
      single most expensive mistake available here** — establish the check before any timing.
      > **Done 2026-08-03**, against the real `knaif-qwen3-4b-v1` GGUF (downloaded for this purpose).
      > `knaif run ffmpeg "convert input.mov to mp4" --dry-run --verbose` shows
      > `ggml_metal_init: found device: Apple M3 Pro`, `load_tensors: offloaded 37/37 layers to GPU`,
      > every KV-cache layer `dev = MTL0`, and the correct rendered command
      > (`ffmpeg -y -i input.mov -c copy -movflags +faststart input_converted.mp4`). Not silently CPU.
- [x] **A4. `dynamic-backends` build.** `--features llama,dynamic-backends`. Verify the
      `$OUT_DIR/backends/` directory contains `libggml-metal.dylib` plus the three
      `libggml-cpu-apple-*.dylib` variants (§1.1), and that `load_dynamic_backends` registers them.
      Confirm the dev fallback in `backend_dirs()` (`BACKENDS_DIR` baked in at compile time) still
      works for an unstaged `cargo run` and does **not** double-load.
      > **Done 2026-08-03 — with a correction to the plan's own assumption.** `$OUT_DIR/backends/`
      > contains `libggml-metal.so`, `libggml-cpu-apple_m1.so`, `libggml-cpu-apple_m2_m3.so`,
      > `libggml-cpu-apple_m4.so` — **`.so`, not `.dylib`** (see the corrected §1.1 row). Ran the
      > unstaged `cargo run`-equivalent binary against the real GGUF with `--verbose`:
      > `load_backend: loaded MTL backend from …/libggml-metal.so`,
      > `load_backend: loaded CPU backend from …/libggml-cpu-apple_m2_m3.so` (correctly the M2/M3
      > variant, not M1 or M4), each logged **exactly once** (no double-load), then
      > `offloaded 37/37 layers to GPU` — identical outcome to the static A2 build.
- [x] **A5. ⚠️ Establish how dylibs resolve, before touching packaging.** `dynamic-backends`
      implies `dynamic-link`, so `libllama`/`libggml`/`libggml-base`/`libllama-common` become real
      runtime dependencies. `llama-cpp-sys-2`'s `build.rs` emits **no rpath link args at all**
      (verified: no `rustc-link-arg`, no `rpath` anywhere in it) — which is exactly why Linux needs
      `patchelf --set-rpath '$ORIGIN'` in `package.sh`. Determine, with `otool -l`, on both the
      exe and each dylib:
      - each dylib's `LC_ID_DYLIB` install name (CMake's `MACOSX_RPATH` default makes this
        `@rpath/lib….dylib`, but **verify** rather than assume);
      - whether the exe has any `LC_RPATH` at all, and whether `target/release/knaif` even runs
        without `DYLD_LIBRARY_PATH`.
      The answer decides B3's shape. **`@loader_path` is macOS's `$ORIGIN`** and is the right choice.
      > **Done 2026-08-03 — every prediction confirmed on hardware.** `otool -L target/release/knaif`
      > shows the four core libs linked as `@rpath/libggml-base.0.dylib`,
      > `@rpath/libggml.0.dylib`, `@rpath/libllama-common.0.dylib`, `@rpath/libllama.0.dylib`, plus
      > only system frameworks (Foundation, Metal, MetalKit, Accelerate, libc++, CoreFoundation,
      > libiconv, libSystem). `otool -D` on each core dylib confirms `LC_ID_DYLIB = @rpath/lib….dylib`
      > exactly as CMake's `MACOSX_RPATH` default predicts. **The exe has zero `LC_RPATH` entries**
      > (`otool -l | grep LC_RPATH` empty), and running it bare fails exactly as predicted:
      > `dyld[…]: Library not loaded: @rpath/libggml-base.0.dylib … Reason: no LC_RPATH's found`
      > (abort, exit 134). With `DYLD_LIBRARY_PATH=target/release` set, it runs fine. This confirms
      > B3 must add `-add_rpath @loader_path` to the exe during staging — nothing else will resolve
      > the four core libs in a packaged artifact.
      > *Rationale corrected 2026-08-02 after audit.* The first draft said `@executable_path` is
      > *wrong* "because the backends are `dlopen`ed by a dylib, not by the exe." That reasoning is
      > false — `@executable_path` resolves against the main executable regardless of who does the
      > loading, and in this flat `bin/` layout **both spellings resolve to the same directory and
      > both work.** Prefer `@loader_path` because it keeps each dylib self-contained and correct if
      > the layout is ever nested — a design preference, not a correctness requirement. Note also
      > that the *backend* dylibs are found by an explicit directory scan
      > (`load_backends_from_path`), so the rpath governs their **dependencies** (`libggml-base`
      > and friends), not their own discovery.
- [x] **A6. `knaif-llm` review for Darwin.** `llama.rs`'s `has_backend_libs` normalises a `lib`
      prefix for Linux vs Windows; `.dylib` files also carry the `lib` prefix, so this should hold —
      **confirm with a test**, because the same function silently returned `false` on Linux once and
      caused every backend to load twice. Add a Darwin case to its unit tests.
      > **Done 2026-08-03.** Added `has_backend_libs_recognises_all_platform_namings` to
      > `native/crates/knaif-llm/src/llama.rs` (`#[cfg(feature = "dynamic-backends")]`), covering: an
      > empty dir (false), `libggml-base.{dylib,so}` alone (false — core lib, not a backend),
      > `libggml-metal.so` (true — the **actual** macOS naming per A4's correction),
      > `libggml-vulkan.dylib` (true — extension-agnostic), `libggml-cuda.so` (true — Linux), and
      > `ggml-vulkan.dll` (true — Windows, no `lib` prefix). Passes. The function needed no code
      > change — its prefix-only check was already extension-agnostic and correct.

---

## 5. Workstream B — packaging (`installers/package.sh`)

`package.sh` already has a `Darwin` arm in its `uname -s` case (`OS=macos; LIB=dylib; ARCHIVE=tgz`)
and `uname -m` returns `arm64`, which passes through its arch mapping unchanged. **Everything else
about macOS in that script is either missing or a Linux/Windows branch that excludes it.** Each item
below names the specific place.

- [x] **B1. `feats_for_kind` + argument parsing: add `metal`, and refuse everything else on Darwin.**
      `--kind=metal` (`llama,dynamic-backends`, minus `openmp` per D3) gets the **plain artifact
      name** on macOS, the way `vulkan` does elsewhere. **Refuse `--kind=cpu|vulkan|cuda` on Darwin
      with a message that says why** — `cpu` because it would be a byte-identical build under a
      misleading name (D2), `vulkan`/`cuda` because neither exists there — rather than failing later
      somewhere unrelated. Mirror the kind list in the `justfile`'s `package-native` recipe and its
      comment block, which hard-codes `cpu|vulkan|cuda`; its own comment says the two must stay in
      sync. Archive format is `.zip` on macOS (D6), which `package.sh` currently selects only for
      Windows — and its Windows branch requires `System32/tar.exe`, so macOS needs its own
      `ditto -c -k --keepParent` or `zip` path.
      > **Done 2026-08-03, with a correction to the plan's own suggestion.** Added `metal` to
      > `feats_for_kind`, arg parsing, and a Darwin refusal block for `cpu`/`vulkan`/`cuda` with a
      > message naming D2/D1 — verified all three refusal messages fire correctly. Mirrored the kind
      > list into the `[unix] package-native` justfile recipe.
      > **`ditto -c -k --keepParent` (the plan's first suggestion) was tried and rejected on hard
      > evidence.** On this build box every staged file already carries a `com.apple.provenance`
      > extended attribute (present the moment `cargo build`/`cp` create a file — not something
      > packaging adds), and `ditto` preserves it as an inline AppleDouble `._<name>` sidecar next to
      > **every** real file — not bundled into one `__MACOSX/` folder — doubling the entry count.
      > Worse: `com.apple.provenance` cannot be stripped first either — `xattr -d`/`xattr -cr` both
      > report success and silently leave it in place (it's a protected, system-managed attribute).
      > Switched to plain `zip -qry` (the plan's other suggested option), which never attempts
      > xattr/resource-fork preservation and produces a clean archive — verified: 0 `._*` entries.
      > This is exactly the kind of macOS-specific hygiene wart E5 warns about (alongside
      > `.DS_Store`); worth remembering for any *other* macOS packaging step that reaches for `ditto`.
- [x] **B2. The build branch excludes macOS.** `package.sh:119` is `elif [ "$OS" = linux ]`, so a
      macOS `--kind=metal` falls into the `else` and prints *"needs the MSVC/C++ toolchain … compile
      it in a VS Developer shell"*. Extend the native-build branch to Darwin (macOS has a real
      compiler on the build box, like Linux and unlike Windows). `CMAKE_GENERATOR=Ninja` is harmless
      and consistent.
      > **Done 2026-08-03.** `elif [ "$OS" = linux ] || [ "$OS" = macos ]`, with
      > `MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-12.0}"` exported before the build per
      > M4b/D9. **Found and fixed a second instance of the same gap**: the `justfile`'s
      > `[unix] package-native` recipe builds directly via `cargo build` (bypassing package.sh's own
      > build step via `--no-build`), so it never got this export either. Confirmed empirically —
      > `otool -l` showed `minos 11.0` (rustc's own default for `aarch64-apple-darwin`, unrelated to
      > llama.cpp's CMake config) on a binary built through that recipe before the fix, `minos 12.0`
      > after. Fixed the recipe the same way; re-verified every staged Mach-O in a rebuilt artifact
      > carries `minos 12.0`, exe and every dylib/`.so` alike.
- [x] **B3. Core-lib staging + install-name surgery.** The staging block is gated
      `linux || windows` (`package.sh:578`) and `set_origin_rpath` is an explicit no-op off Linux
      (its comment already says *"macOS uses `@loader_path`, handled elsewhere"* — this is
      "elsewhere"). Add a Darwin path that stages `libggml-base`/`libggml`/`libllama`/`libllama-common`
      `.dylib`s plus every `libggml-*.dylib` backend, then, per A5's findings, uses
      `install_name_tool` to make the tree relocatable: `-add_rpath @loader_path` on the exe and
      `-id @rpath/<name>` / `-change` on the dylibs as needed. Verify by running the **staged** tree
      from a directory it was not built in.
      > **Done 2026-08-03 — simpler than the plan expected, verified empirically before writing the
      > script.** Added a dedicated macOS staging block (kept separate from the Linux/Windows one
      > rather than threaded into its conditionals, since the extension split — `.dylib` core libs
      > vs `.so` backends per A4 — and the rpath mechanism both differ enough to make a shared block
      > more confusing than two clear ones). Core libs staged via the SONAME-symlink-chain pattern
      > (version embedded before the extension: `libggml-base.dylib` → `.0.dylib` → `.0.13.1.dylib`,
      > unlike Linux's suffix style); backends staged via `$BACKEND_LIB` (`.so`).
      > **`-id`/`-change` install-name surgery on the dylibs turned out to be unnecessary.** Tested
      > directly: staging the four core dylibs as-is (their `LC_ID_DYLIB` already reads
      > `@rpath/lib….dylib` from the build, per A5) and adding **only** `-add_rpath @loader_path` to
      > the **exe** — nothing on the dylibs, nothing on the backend `.so` files — was sufficient.
      > Proved this three ways before committing to it: (1) exe + 4 core dylibs, no backends, run
      > from `/tmp`, no `DYLD_LIBRARY_PATH` — worked; (2) same plus both backend `.so` files, real
      > Metal inference, external cwd — worked, 37/37 layers offloaded; (3) the actual
      > `package.sh`-produced artifact, unzipped in a fresh temp dir, same real-inference check —
      > worked. dyld resolves every dependent's `@rpath/...` (including the backends' *own*
      > `@rpath/libggml-base....dylib` reference) using the rpath list accumulated from images
      > already loaded in the process — one rpath on the exe covers the whole tree transitively.
- [x] **B4. The `base`-vs-functional guard needs a Darwin probe.** `exe_imports_llama` is
      `patchelf --print-needed` on Linux and a raw `grep -a 'llama.dll'` everywhere else — the
      latter is wrong on macOS. Use `otool -L "$1" | grep -q 'libllama'`. This guard exists because
      cargo overwrites `target/release/knaif` and **`smoke.sh` structurally cannot catch a base exe
      packaged as functional** (`--version`, `skills list`, `skills deps` and a mock `plan` all pass
      without llama); it fails only at a real `run`, in a user's hands.
      > **Done 2026-08-03, with a tightened pattern.** Used `grep -q '/libllama\.'` rather than the
      > plan's suggested bare `libllama` — anchored on "`/libllama` immediately followed by a
      > literal dot" so `libllama-common.*.dylib` (a genuinely different lib whose name happens to
      > share the prefix) can never false-positive the check. Verified against real otool -L output.
- [x] **B5. ⚠️ Resolve the OpenMP question (§1.3) and implement D3's feature split.** `otool -L`
      every staged Mach-O — but judge by **resolution, not path shape**: an unresolvable
      `@rpath/libomp.dylib` is the likely form and carries no Homebrew string. Then implement D3
      properly (`default-features = false` **plus explicit `common`**, a real `openmp` feature
      enabled for the Linux/Windows kinds only) and re-check. **Re-verify the Linux and Windows
      artifacts after that change** — it touches their feature graph too, and `libgomp.so.1` /
      `VCOMP140.dll` staging depends on OpenMP still being on there.
      > **Deliberately deferred 2026-08-03 — not done.** The `otool -L` measurement is already
      > recorded in §1.1's corrected table: on `M3P` (Homebrew present, `libomp` **not** installed),
      > `GGML_OPENMP:BOOL=ON` but `GGML_OPENMP_ENABLED:INTERNAL=OFF` — CMake's `find_package` failed
      > gracefully, zero OpenMP linkage in any built artifact. **Held off on the Cargo.toml feature
      > split itself** (`default-features = false` + explicit `common` + a real `openmp` feature)
      > because it changes the feature graph for **every** platform — including the `just native` /
      > `native-cuda` / `native-vulkan` dev recipes' default `FEATS`, not just `package.sh`'s release
      > kinds — and this plan's own instruction is to *re-verify Linux and Windows after that change*,
      > which is not possible from this Mac. Implementing it blind, without a way to confirm
      > `libgomp.so.1`/`VCOMP140.dll` staging still works, is the kind of change the plan itself
      > warns against ("the obvious implementation... is a bug"). Left for a session with Linux/
      > Windows access, or CI. The residual risk this leaves: an actual Mac with Homebrew's `libomp`
      > installed is still untested end-to-end (only the *absence* case was measured here).
      > **Done 2026-08-07, on M3P, with the trap actually triggered.** `brew install libomp` alone
      > (leaving it un-symlinked, keg-only) was NOT enough to reproduce the earlier deferred
      > concern — `find_package(OpenMP)` still failed to find it, same as before. It only
      > reproduces once the build environment resolves the keg — e.g. `CMAKE_PREFIX_PATH=
      > /opt/homebrew/opt/libomp` (which is exactly what many unrelated Homebrew formulae's own
      > build recipes export). With that set: `GGML_OPENMP_ENABLED:INTERNAL=ON`,
      > `libggml-base.dylib` and all three `libggml-cpu-apple_*.so` backends link
      > `/opt/homebrew/opt/libomp/lib/libomp.dylib`, and `check_macho_deps.py` (E1) correctly
      > failed the staged tree on it (`... which does not resolve: not a system path ... and not
      > staged in bin/`). **Correction to §1.3's predicted shape:** this bottle's `libomp.dylib`
      > carries an absolute `LC_ID_DYLIB` (`/opt/homebrew/opt/libomp/lib/libomp.dylib`), not the
      > `@rpath/libomp.dylib` form §1.3 describes for "LLVM builds" — both shapes are unresolvable
      > on a clean Mac and E1 catches either (judges by resolution, not path shape, exactly as
      > designed), so the correction doesn't change what B5 or E1 have to do, only which exact
      > string a human sees in `otool -L`.
      >
      > **Implemented D3 exactly as specified**, in `native/crates/knaif-llm/Cargo.toml`:
      > `llama-cpp-2` now declares `default-features = false`; the `llama` feature re-adds
      > `llama-cpp-2/common` explicitly (needed unconditionally — it builds `llama-common`, staged
      > on every platform); a new `openmp` feature forwards `llama-cpp-2/openmp` and is forwarded
      > again from `apps/cli/Cargo.toml`. `installers/package.sh`'s `feats_for_kind` and *both*
      > Justfile `package-native` recipes (`[unix]` and `[windows]`) now append `,openmp` for
      > `cpu`/`vulkan`/`cuda` only; `metal` is unchanged (`llama,dynamic-backends`, no openmp).
      >
      > **Verified the fix holds under the exact trap-triggering environment**: rebuilt `metal`
      > with `CMAKE_PREFIX_PATH`/`LDFLAGS`/`CPPFLAGS` all still pointing at libomp —
      > `GGML_OPENMP:BOOL=OFF` (build.rs forces it OFF outright when its own `openmp` cargo feature
      > is absent, a hard override, not merely relying on `find_package` failing), zero `omp` in
      > `otool -L` on any staged binary. Then flipped `openmp` ON explicitly and confirmed it
      > genuinely links libomp — proving the control is real in both directions, not a no-op in
      > either. Full `just package-native metal` → `check_macho_deps.py` passed clean (9 Mach-O
      > binaries, every dependency resolves); unzipped the artifact to a scratch dir and ran real
      > Metal inference (`ffmpeg`, qwen3-4b, 37/37 layers offloaded) — no regression from the
      > `common` re-plumbing. `cargo fmt --check` and `cargo clippy --workspace --features
      > llama,dynamic-backends --all-targets -- -D warnings` both clean.
      >
      > **On the "re-verify Linux/Windows" instruction this line gives, and why it turned out not
      > to block landing the change from a Mac-only session:** the effective feature set requested
      > for the Linux/Windows release kinds is **unchanged** — `common` and `openmp` were both ON
      > by default before this change and are both ON explicitly now; nothing they build differs.
      > The one feature actually dropped from the old default set, `android-shared-stdcxx`, is
      > read by `llama-cpp-sys-2`'s `build.rs` behind `matches!(target_os, TargetOs::Android)` in
      > every call site (verified by reading it) — a no-op on Linux/Windows/macOS, none of which
      > this project targets Android from. `libgomp.so.1`/`VCOMP140.dll` staging in `package.sh` is
      > gated on `$OS`, not on this feature graph, and is untouched. So there is no Linux/Windows
      > *behavior* left to re-verify — only the macOS side changed, and that side is what got
      > verified end-to-end above. `cargo tree -e features -i llama-cpp-2` for both the
      > `cpu,vulkan,cuda,openmp` and the `metal` (no openmp) feature sets confirms the resolved
      > `llama-cpp-2` feature sets match this reasoning exactly.
- [x] **B6. Artifact naming + README.** `knaif-<ver>-macos-arm64.zip`, plain name for `metal`; no
      `-cpu` variant exists on macOS (D2). Add the `metal` case to the `INFER=` message block, and
      confirm the existing self-containment smoke at the end of `package.sh` (run from a temp cwd
      with an empty `KNAIF_SKILLS_ROOT`) passes on macOS.
      > **Done 2026-08-03.** `metal`/`vulkan` both get `SUFFIX=""` (plain name); added the `metal`
      > case to `INFER=`. The existing self-containment smoke passed unmodified on the first real run.
      > Produced `knaif-1.1.0-macos-arm64.zip` (8.1 MB) end-to-end via both
      > `installers/package.sh --kind=metal` and `just package-native metal`; unzipped it in a fresh
      > temp directory and ran real Metal inference from there (37/37 layers offloaded) — the full
      > pipeline this workstream exists to prove.
- [x] **B7. Licence staging.** `installers/licenses/THIRD-PARTY-RUST.txt` + `llama.cpp-LICENSE.txt`
      ship for any functional kind; `LICENSE` and `NOTICE` at the artifact root. All of that is
      OS-independent and should need no change — **assert it rather than assume it**, since `NOTICE`
      was missing from every artifact on every OS until 2026-07-26 precisely because nothing read it.
      If D3 lands on staging `libomp.dylib`, its licence joins `licenses/` and
      [PROVENANCE.md](../PROVENANCE.md) gains an entry. `libomp.dylib` is not staged (B5 deferred).
      > **Asserted 2026-08-03, not assumed.** Confirmed present in the actual macOS artifact:
      > `LICENSE`, `NOTICE` at the root; `licenses/THIRD-PARTY-RUST.txt` and
      > `licenses/llama.cpp-LICENSE.txt` (functional kind). No code change needed — the existing
      > OS-independent staging lines already cover Darwin correctly.
      > **B5 update, 2026-08-07:** D3 landed on option (b) — `openmp` off on macOS — not (a), so
      > this paragraph's conditional resolves to "no": `libomp.dylib` is never staged on macOS and
      > there is no new licence entry to add.

---

## 6. Workstream C — quality gates

Nothing in this workstream is macOS-specific work; it is **running the gates the other two platforms
already pass, on a third platform, for the first time.**

- [x] **C1. `cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D warnings`,
      `cargo test --workspace`** — i.e. `just check-native` + `just test-native`. The llama.cpp
      inference proof is gated on `$KNAIF_TEST_GGUF`; set it so that test actually runs here.
      > **Done 2026-08-07, on M3P.** `cargo fmt --all --check` clean. `cargo clippy --workspace
      > --all-targets -- -D warnings` clean under default features (base, no llama). `cargo test
      > --workspace` (default features): **251 passed, 0 failed** across every crate. The llama
      > module and its `inference_produces_text` test only compile under `--features llama`
      > (`#[cfg(feature = "llama")]` gates the whole module in `knaif-llm/src/lib.rs`) — `just
      > test-native`'s plain invocation never reaches it, matching this task's own note that
      > `$KNAIF_TEST_GGUF` has to be set *and* the feature enabled. Ran both explicitly:
      > `cargo clippy --workspace --all-targets --features llama,dynamic-backends -- -D warnings`
      > clean, and `KNAIF_TEST_GGUF=<repo>/models/knaif-qwen3-4b-v1-q4_k_m.gguf
      > KNAIF_TEST_NGL=99 cargo test --workspace --features llama,dynamic-backends` — the
      > inference proof passes for real over Metal (37/37 layers offloaded per the test's own
      > `--nocapture` output, output `{"ok": true}` as expected).
- [x] **C2. Full Python suite** — `uv run pytest`. Triage any Darwin-only failure into *this plan's
      bug* vs *a latent cross-platform assumption in a test*. Give particular attention to anything
      touching paths, `~` expansion, case-insensitive filesystems (APFS default!), or `os.name`.
      **Case-insensitivity is the most likely silent difference** and it affects the sandbox path
      validation the safety model depends on.
      > **Done 2026-08-07, on M3P.** `uv run pytest -q`: **1664 passed, 0 failed, 7 skipped** on
      > first run. All 7 skips were environment-conditional, not platform-conditional: 1 correctly
      > `sys.platform != "win32"`-skipped Windows-only test, and 6 skipped for missing optional
      > binaries (`tesseract`/`soffice`/`gs`) — the same skips would fire on Linux/Windows without
      > those installed. No case-insensitivity or path-handling failures surfaced. Installed
      > `tesseract` + `ghostscript` via Homebrew (per user's explicit go-ahead; `libreoffice`
      > deliberately skipped as a large, non-blocking download) and re-ran: **1667 passed, 0
      > failed, 4 skipped** — the 3 newly-unskipped tests (OCR + ghostscript-compress paths) all
      > passed on the real binaries, no Darwin-specific defect in either.
- [x] **C3. Contract parity, model-free, first.** Run `contracts/parity/planner_cases.json` on
      macOS. These cases involve no inference, so **any diff is a genuine platform bug** with no
      floating-point excuse available. This is the cheapest possible signal and it must be clean
      before C5 is interpreted at all.
      > **Done 2026-08-07, on M3P.** Both consumers of the fixture pass clean: `cargo test
      > --workspace --test parity` (`knaif-core`'s `planner_parity_cases`, the Rust deterministic
      > pipeline) — 1 passed; and `uv run pytest python/core/tests/test_planner_parity.py` (the
      > Python side) — 1 passed. No inference involved in either, so this is a clean go-ahead for
      > interpreting C5.
- [x] **C0. ⚠️ PREREQUISITE, and it is not macOS work: the regression gate currently proves
      nothing.** *Added 2026-08-02 after audit; every claim below re-verified against the code.*
      **DISCHARGED 2026-08-04** — all four defects closed, acceptance criterion 0 met. Defects 1–2
      fixed 2026-08-03; defect 4 (both snapshots re-locked with executing verifiers) and the
      verifier-selection defect that surfaced underneath it, 2026-08-04. See the closing note under
      this task.
      The first draft wrote `just eval-success <skill>` → `just eval-regression <skill>` and called
      it a gate. It is not one. Four independent defects, any one of which is sufficient:

      1. **`regression` compares the snapshot to itself and always passes.**
         `cmd_regression` sets `current: dict[str, Any] = baseline  # default: compare snapshot to
         itself (no-op)` ([`cli.py`](../../python/core/knaif/evalsuite/cli.py) ~line 1077) and only
         overrides it when `--current FILE` is given.
      2. **`just eval-regression skill:` takes no pass-through args** ([`justfile`](../../justfile)
         ~line 534), so the recipe *cannot* supply `--current` even though the CLI accepts it.
      3. **`just eval-success` persists nothing without `--save`** — the scoreboard is only written
         under `if args.save:` — so there is no current file to pass in the first place.
      4. **Neither snapshot matches its corpus, and one has the wrong verifier.** Measured
         2026-08-02:

         | Skill | Snapshot verifier | Bar (utterances) | Corpus records | Corpus utterances | Drift |
         |---|---|---:|---:|---:|---:|
         | `ffmpeg` | **`cheap`** ⚠️ | 297 | 314 | **847** | **+550** |
         | `documents` | `success` | 129 | 143 | **164** | **+35** |

         > **The +17/+14 figures published here on 2026-08-02 were wrong** — corrected 2026-08-04
         > against a real run. A snapshot's `total` counts **utterances**, and every `eval.jsonl`
         > record carries an `utterances` LIST, so the audit was comparing utterances against the
         > file's line count. The audit's conclusion holds and gets stronger: ffmpeg's bar covered
         > **35%** of its corpus, not 95%.

         `ffmpeg`'s bar is a **`cheap`** snapshot, which [AGENTS.md](../../AGENTS.md) and
         [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md) both state is an iteration instrument and never
         an acceptance bar — *"a `cheap` snapshot reports false regressions when the corpus is
         annotated."* **The audit flagged `ffmpeg` only; `documents` is stale too**, by 14 rows.

      Also: `--config` defaults to `eval_backends.yaml` and `_resolve_backends` returns **every**
      stanza when `--backends` is omitted, so `just eval-success ffmpeg` tries to run models whose
      GGUFs [PERFORMANCE.md](../PERFORMANCE.md) §8 records as *deliberately absent* — each scoring
      ~0.0 and looking like catastrophic quality loss.

      **This is a pre-existing repository defect, not something macOS introduced**, and it must be
      fixed independently and first — otherwise this plan's acceptance criterion 6 is a gate that
      cannot fail. Minimum fix: re-lock both snapshots with an **executing** verifier against the
      current corpora on a known-good platform (its own commit, per the standing rule), and teach
      `just eval-regression` to forward `*args` so `--current` is reachable.

      > **Defects 2 and part of 1 fixed 2026-08-03; defects 3 and 4 (snapshot re-locking)
      > deliberately NOT attempted.** Fixed the two purely-mechanical, zero-risk pieces:
      > - `just eval-regression skill:` → `eval-regression skill *args:`, forwarding `{{args}}` —
      >   `--current` is now actually reachable through the recipe (defect 2).
      > - `cmd_regression` now (a) prints a loud `⚠` warning to stderr when `--current` is omitted,
      >   instead of silently reporting "No regressions... OK" indistinguishably from a real check,
      >   and (b) **hard-fails** when `--current` is given but the file doesn't exist, instead of
      >   silently falling back to the self-compare — a mistyped path used to look exactly like a
      >   passing gate. Both verified directly (warning fires + exit 0 on no-arg; hard error + exit 1
      >   on a bad path). No existing test exercised `cmd_regression` at all, so nothing to break;
      >   full `uv run pytest python/core/tests/` still 1629 passed after the change (see M5).
      >
      > **Left undone, deliberately:** re-locking either snapshot (defect 4) needs a real,
      > **executing**-verifier eval-suite run across the full corpus (~314 ffmpeg rows, ~143
      > documents rows) against a pinned backend, is explicitly called out elsewhere in this plan as
      > "a deliberate, own-commit act" that a platform port is never the reason to trigger, and this
      > plan's own §11 lists "re-locking any eval snapshot" as **out of scope**. Attempting it
      > unprompted — on macOS, no less, which C4 explicitly forbids re-locking on — would be exactly
      > the kind of scope creep this plan warns against. Acceptance criterion 0 (both snapshots
      > re-locked) therefore still does **not** hold; criterion 6 still cannot be honestly claimed.
      > This remains open repository work, tracked already in [TODO.md](../TODO.md), for a session
      > where re-locking is the deliberate goal.

      > **CLOSED 2026-08-04 — that deliberate session happened, on Windows.** Defects 3 and 4 are
      > discharged and acceptance criterion 0 now holds. Both bars re-locked against
      > `qwen3-4b-sft-v3-flat-q4` (the stanza both skills name via `recommended_model`; of 37
      > stanzas in `eval_backends.yaml` only 2 have a GGUF on disk, so `--backends` is mandatory),
      > fixtures regenerated first:
      >
      > | Skill | Old bar | New bar |
      > |---|---|---|
      > | `ffmpeg` | `cheap`, 297 utt, 0.9327 | **`output_diff`, 847 utt, 0.9055** |
      > | `documents` | `success`, 129 utt, 0.9922 | **`success`, 164 utt, 0.9756 / knaif 0.9847** |
      >
      > The model is byte-identical; ffmpeg's numbers are **not** comparable (`cheap` grades plan
      > shape over 35% of the corpus, `output_diff` executes both the model's command and the
      > reference and ffprobe-diffs the media over all of it). documents *is* comparable — same
      > verifier — and its outcome delta is 35 new utterances, 3 of its 4 failures being the single
      > deliberate-ambiguity row `documents_132`.
      >
      > **Two defects surfaced underneath defect 4, both now fixed:**
      > - `just eval-snapshot` hardcoded `--verifier output_diff`, but `output_diff` is defined in
      >   `skills/ffmpeg/eval/verifiers.py` and `documents` does not own it. `score_corpus` does
      >   `verifiers.get(name)` and carries on with `None`, so running it there **executed nothing**
      >   and would have replaced documents' stronger `success` bar with a routing score. The CLI
      >   now refuses to snapshot a verifier the skill does not own, or one that does not execute
      >   (which also closes the `cheap`-as-a-bar hole), and the recipe takes the verifier as a
      >   parameter.
      > - The first documents lock was measured with **`tesseract` absent**, so all 7 `ocr` rows
      >   scored knaif 0.5 (routing correct, `output_exists` failing) — **−2.29pt of pure
      >   environment artifact** in a committed bar. Re-locked after installing it. Note the
      >   installer puts `tesseract` on **no** PATH; `skills/documents/SPEC.md` requires it on
      >   `PATH` and `_deps.py` resolves it via `shutil.which`.
      >
      > The gate now works: `regression --skill ffmpeg --current <fresh scoreboard>` returns
      > "No regressions above threshold=0.02", where before it compared the snapshot to itself and
      > printed OK regardless. **Criterion 6 can now be honestly claimed.** Also corrected here: the
      > audit's "+17/+14 rows" drift figures compared utterances to `eval.jsonl` line counts — the
      > real drift was +550 / +35 utterances.
- [ ] **C4. The eval ladder for both shipped skills** *(depends on C0)*. `just eval-fixtures <skill>`
      — **always first**, since missing fixtures score correct plans ~0 — then run each snapshot's
      **exact** verifier against a single pinned production backend and **save** the scoreboard, then
      diff it explicitly:

      ```bash
      just eval-fixtures ffmpeg
      uv run -m knaif.evalsuite run --skill ffmpeg --verifier success \
        --backends <production-backend> --save evals/runs/2026-XX-XX_macos-ffmpeg_success
      uv run -m knaif.evalsuite regression --skill ffmpeg \
        --current evals/runs/2026-XX-XX_macos-ffmpeg_success/ffmpeg_<backend>_success.json
      ```

      `--backends` is not optional (see C0), the verifier must match the snapshot's own, and the
      `--current` path is what makes the comparison real. Add a row to `evals/INDEX.md` per run.
      Gate against the **committed** snapshots — do **not** re-lock one on macOS. Re-locking moves
      the acceptance bar and is a deliberate, own-commit act; a platform port is never the reason to
      move it.
- [x] **C5. Native-vs-Python parity on macOS.** `just parity ffmpeg --mode plan --batch` and
      `--mode command`. Both runtimes greedy-decode the identical GGUF.
      > **Done 2026-08-07, on M3P — but the recommended `llama,dynamic-backends` debug build does
      > NOT work locally on macOS, and this is worth recording precisely.** Built it as this task
      > suggests; running `target/debug/knaif --version` aborted with the exact "no LC_RPATH's
      > found" error A5 documents (the release path only becomes relocatable after `package.sh`'s
      > `install_name_tool -add_rpath` surgery, which a bare `cargo build` never runs). Tried the
      > obvious workaround, `DYLD_LIBRARY_PATH=target/debug just parity ...` — **it silently does
      > nothing**: macOS strips every `DYLD_*` variable when a SIP-restricted binary starts, and
      > `/bin/sh` (which `just` uses to run the recipe body) is one — confirmed directly (`/bin/sh
      > -c 'echo $DYLD_LIBRARY_PATH'` prints empty even with it exported in the parent shell). The
      > variable never reaches `uv run`, let alone the `knaif` subprocess underneath it; the first
      > attempt scored 0/19 comparable with every native row reporting `dyld[…]: Library not
      > loaded`, which is an environment-propagation artifact, not a parity result — worth flagging
      > since it would misread as a catastrophic native failure to anyone who didn't check the raw
      > `native.raw` field in the saved report.
      >
      > **The actual fix: don't use `dynamic-backends` for local macOS parity work at all.** Metal
      > needs no cargo feature of its own (D1) — `cargo build -p knaif-cli --features llama` (no
      > `dynamic-backends`) statically links llama/ggml into the exe, confirmed via `otool -L`
      > (zero `ggml`/`llama` external deps), and it runs standalone with no `DYLD_LIBRARY_PATH` and
      > no rpath surgery needed. This is also what the *other* half of this task's own instructions
      > point at without saying so directly: the "BUILD NATIVE FIRST" comment's suggested warm-up
      > (`just native-vulkan`/`native-cuda`) never uses `dynamic-backends` either — only
      > `package-native` does, for its release-packaging reasons (Option 3 / C5b). `dynamic-backends`
      > is a **packaging** concern, not a parity-testing one; nothing about the plan/render pipeline
      > parity_check.py exercises depends on which way ggml is linked.
      >
      > **Results, once built that way:** `--mode command --limit 20`: **19/19 comparable rows
      > matched exactly**, 0 drift; the 20th (`compress`) is `not-comparable` by design (Python's
      > dry-run renders no command for `compress`/`platform`/`thumbnail`/`batch` — a documented
      > limitation of this mode, not a bug). `--mode plan --batch --limit 60`: **60/60 matched**,
      > including the safety-relevant rows (`rm -rf /`, `format C: drive`, "exfiltrate…" → all
      > `reject`), three languages (English/Spanish/German), and chain/compression intents. No
      > Metal-vs-CPU/CUDA argmax-tie divergence surfaced in either sweep. Reports saved at
      > `evals/parity/parity_ffmpeg_command_20260807T145144Z.json` (the failed DYLD attempt — kept
      > as the record of the environment trap, not a result) and
      > `evals/parity/parity_ffmpeg_command_20260807T150041Z.json` /
      > `evals/parity/parity_ffmpeg_plan_20260807T151101Z.json` (the real results). Not a full-corpus
      > sweep (846 rows) — time-boxed to a diverse 60–80-row slice; nothing in this sample suggests
      > the full corpus would behave differently, but it hasn't been run.
      > **⚠️ Build the binary parity actually runs.** *Added 2026-08-02 after audit.* The recipe
      > hard-codes `--native-bin target/debug/knaif` ([`justfile`](../../justfile) ~line 576) while
      > everything else in this plan builds `target/release`. Left alone, C5 would silently test a
      > **stale, mock-only, or absent** debug binary and report parity on a build that does no
      > inference. Either produce a matching
      > `cargo build -p knaif-cli --features llama,dynamic-backends` **debug** build first, or give
      > `parity` a `--native-bin` override and point it at the staged release exe. Note
      > `parity_check.py` preflights on the `--version` backend string precisely to catch a
      > mock-only binary — do not defeat that by ignoring what it says.

      > **Read the result correctly — there are THREE confounds, not one.** Parity was designed as a
      > *native-vs-Python, one machine* check. macOS adds axes:
      > 1. **Metal vs CUDA/CPU kernels.** [PERFORMANCE.md](../PERFORMANCE.md) §6 records that
      >    changing how a decode is chunked perturbs FP accumulation, and under greedy argmax that
      >    flips a near-tie into a different plan.
      > 2. **The platform itself.**
      > 3. **Different llama.cpp builds on the two runtimes** — Python is on `llama-cpp-python`
      >    (unpinned in `pyproject.toml`: `>=0.2.0`), native on `llama-cpp-sys-2 0.1.150`. This
      >    confound is **not new and not macOS-specific**: it is the standing open item in
      >    PERFORMANCE.md §3 (*"native is 1.8–1.9× faster at prompt decode and we don't know why …
      >    next suspect: llama.cpp version/build difference"*). It means C5 can **localize** a
      >    discrepancy but cannot fully attribute it.
      >
      > So a small diff here is **not automatically a port bug**. Triage by elimination: re-run the
      > utterance against the CPU-only tree from D2 (isolates Metal) and against the Windows/Linux
      > record (isolates the platform, via C6). Record the method — this axis exists permanently now.
- [ ] **C6. Cross-OS plan agreement.** For a fixed slice of the ffmpeg corpus, compare macOS
      `plan --json` output against the same slice from a Windows or Linux build. Distinct from C5,
      which compares two runtimes on one machine. This is the check that says "the same request
      produces the same plan on your Mac and your colleague's PC" — the property the whole
      dual-runtime contract exists to protect.
      > **Blocked 2026-08-07, on M3P — genuinely, not deferred out of caution.** This check is
      > structurally a two-machine comparison and this session has exactly one machine (macOS
      > only, no Windows/Linux access). Checked for an existing saved reference to diff against
      > first — `evals/INDEX.md`'s Windows/Linux runs are all aggregate eval scoreboards (outcome
      > accuracy etc.), not raw per-utterance `plan --json` dumps, so none of them are usable here.
      > **Produced the macOS half so the comparison is one command away for whoever has the other
      > platform**, rather than leaving this fully undone: `evals/parity/c6_cross_os_slice.txt` is
      > a fixed, reproducible 30-utterance slice (the first 30 utterances of
      > `skills/ffmpeg/data/eval.jsonl`, in file order); `evals/parity/c6_cross_os_macos_arm64.jsonl`
      > is this Mac's `knaif plan --skill ffmpeg --model <knaif-qwen3-4b-v1 GGUF> --batch
      > evals/parity/c6_cross_os_slice.txt` output, one JSON plan envelope per line, line-aligned
      > with the slice file. **To close this task**: on a Windows or Linux box, build native with
      > the identical GGUF (`models/knaif-qwen3-4b-v1-q4_k_m.gguf`, byte-identical — verify by
      > checksum, not just filename) and run the same command to produce
      > `c6_cross_os_<platform>.jsonl`, then diff line-by-line against the macOS file. A `tool`+`args`
      > diff is a real cross-platform bug (§7 C5's Metal-vs-CPU/CUDA argmax-tie confound is exactly
      > what this check exists to catch); note it here rather than fixing silently.
      > **Housekeeping note:** both new files sit under the blanket `evals/**` gitignore rule
      > (`.gitignore` allowlists only `score.json`/`report.md`/`review_log.json`/`INDEX.md`/
      > `retrieval/*.json`), so as committed they exist only on this Mac. If durable cross-session
      > handoff is wanted, that allowlist needs a line for these — left as a decision for whoever
      > picks this up next rather than made unilaterally here.
- [x] **C7. Skill dependency doctor.** `knaif skills deps` on a Mac with and without the brew tools
      installed. A `[MISS]` is a **pass** — it tests the probe, not the box.
      > **Done 2026-08-07, on M3P — both states verified.** Before installing anything, `knaif
      > skills deps` correctly reported `[MISS]` for all three `documents` optional deps
      > (ghostscript, libreoffice, tesseract) and `[OK]` for ffmpeg's required dep (already on
      > `PATH` from M4). After `brew install tesseract ghostscript` (see C2), re-ran it: `[OK]` for
      > both now-installed tools with correct resolved paths (`/opt/homebrew/bin/{tesseract,gs}`),
      > `[MISS]` still correct for the untouched `libreoffice`. The probe correctly reports both
      > states — the property this task exists to verify.

---

## 7. Workstream D — performance measurement

The deliverable is **rows in [PERFORMANCE.md](../PERFORMANCE.md)**, produced with the same
methodology as the existing ones so they are comparable: Qwen3-4B q4_k_m, the ffmpeg skill prompt
(3938 tokens), 32-token generation, `n_ctx = 8192`, fresh process, median of warm reps,
`KNAIF_TIMING=1`.

- [ ] **D1. Per-phase Metal numbers.** model load, `new_context`, prompt decode, generation,
      teardown, wall. Add a `macos` row to §2's backend table and a machine row to §1.
- [ ] **D2. An honest CPU comparison — from a tree with no Metal backend in it.** ⚠️ Read
      [PERFORMANCE.md](../PERFORMANCE.md) §4 **first**: with any GPU backend compiled in,
      `n_gpu_layers=0` is *not* CPU-only — `op_offload` still sends batched matmuls to the GPU, an
      11× difference on the measurement that matters. **`KNAIF_N_GPU_LAYERS=0` is therefore not a
      valid method here.** Use D2's mechanism from the decision log: copy the staged tree, delete
      `libggml-metal.dylib`, and measure that. The loader then has no Metal backend to find, which
      is a structural guarantee rather than a runtime request. Produce an honest CPU number or
      produce none; a dishonest one has already invalidated a draft of that document once.
- [ ] **D3. ⚠️ The first-run shader tax — measure it, and do NOT plan to fix it at install time.**
      Vulkan's first-ever run cost **38.3 s** of pipeline compilation vs 2.1 s warm (§2), and *the
      first launch after install looks hung*. macOS is structurally similar-but-different: the Metal
      library is embedded as **source**, compiled by the Metal runtime, with an OS-level shader
      cache underneath.

      **Measurement:** a fresh VM snapshot or a fresh user account. *Not* by clearing system Metal
      caches — that is neither a supported operation nor a reproducible one, and a benchmark whose
      setup step is unsupported is a benchmark nobody can repeat.

      **⚠️ Remedy, corrected 2026-08-02 after audit.** The first draft proposed "warm at install
      time", copying §2's prescription for Vulkan. **That does not work for this product**, for
      three independent reasons: the `.zip` has no installer at all; `.pkg` scripts run **as root**,
      so they would warm root's cache and not the user's; and the GGUF is downloaded *later* — on
      first `run` — so at install time there is usually no model to warm against. The viable options
      are:
      - **warm during the first user-owned `models pull` / `run`**, with explicit progress, so the
        cost is attached to something the user already knows is slow; or
      - **move compilation to build time** by setting `GGML_METAL_EMBED_LIBRARY=OFF` and staging +
        signing the resulting `default.metallib`. This trades the runtime tax for a packaging step,
        an extra signed file, and possibly a **full-Xcode** build prerequisite (the `else` branch of
        `ggml-metal/CMakeLists.txt` invokes `xcrun -sdk macosx metal`), which would change M3's
        answer. Measure the tax before paying either price.
- [ ] **D4. Unified-memory behaviour.** Apple Silicon shares one memory pool, so "VRAM" is a
      soft, OS-capped fraction (`iogpu.wired_limit_pct`). Test the recommended 4B model on the
      lowest memory configuration reachable and note where it stops fitting. If 8 GB Macs cannot
      hold 4B comfortably, that is a **model-recommendation** decision, not a bug — the manifest
      already carries `knaif-qwen3-1.7b-v1` (1.32 GB, ~2× faster, ~2.4pt behind on ffmpeg per §5) for
      exactly this situation. Record the finding; do not silently change the default.
- [ ] **D5. Feed the OpenMP decision (D3 in §2).** If `GGML_OPENMP=OFF` is the chosen fix, measure
      the CPU-fallback path with and without it, so the trade is recorded rather than asserted.
- [ ] **D6. Update the reproduction section** ([PERFORMANCE.md](../PERFORMANCE.md) §9) with the
      macOS commands, and add any macOS entry to §7 *Environment gotchas*.

---

## 8. Workstream E — portability verification (the part that is not optional)

> **The rule this workstream implements, quoted from [RELEASE.md](../RELEASE.md) §4:
> a verification step that runs on the build box tests STAGING, never PORTABILITY** — and that
> document already names macOS as a new artifact shape requiring its own clean-room run before
> publication.

- [x] **E1. `scripts/check_macho_deps.py` — the third sibling.** Written to match
      `check_pe_imports.py` and `check_elf_deps.py`: **parse the Mach-O headers in pure Python** so
      it runs on any machine, including the Windows dev box, and therefore **fails where the mistake
      was made** rather than on a user's Mac. It must:
      - read **every** dependency load command, not just the obvious two:
        `LC_LOAD_DYLIB`, `LC_LOAD_WEAK_DYLIB`, `LC_REEXPORT_DYLIB`, `LC_LOAD_UPWARD_DYLIB`,
        `LC_LAZY_LOAD_DYLIB`, plus `LC_RPATH` and `LC_ID_DYLIB`;
      - **assert resolution, not path shape** (§1.3): every dependency must resolve to a file staged
        in the same directory via `@rpath`/`@loader_path`, or to a genuine system path
        (`/usr/lib/**`, `/System/Library/Frameworks/**`). An **unresolvable** `@rpath/libomp.dylib`
        must fail exactly as loudly as a visible `/opt/homebrew/...` — that is the whole point;
      - walk **every architecture slice** of a fat binary, reject any non-`arm64` slice (D4), and
        assert `LC_BUILD_VERSION` matches the declared deployment floor (D9);
      - ship with **malformed and mutated Mach-O fixtures**, so the checker is verified to catch
        what it claims to. `test_installer_iss.py` set this precedent — a lint verified by injecting
        all 14 mutations it claims to catch — and a checker nobody has seen fail is a checker nobody
        should trust.

      Wire it into `package.sh` as a **required** macOS step, the way the PE check is required on
      Windows — not merely into `smoke.sh`, because packaging is the only step every artifact passes
      through by construction.
      > **Done 2026-08-03 — and it immediately caught a real, previously-shipped defect.** Wrote
      > `scripts/check_macho_deps.py` (pure `struct` parsing, no `otool`/`lipo` shell-out, same
      > shape as the two siblings) covering every point above, plus one the plan didn't ask for: if
      > any dependency uses `@rpath`, the checker requires the file's own `LC_RPATH` to include
      > `@loader_path`/`@executable_path` — directly encoding A5's finding that the default build
      > has ZERO rpath entries, rather than only catching it indirectly via a failed resolution.
      >
      > **Ran it against the real artifact from B3/B6 (already "verified working" by real
      > inference) and it failed**: `libllama-common.dylib` linked
      > `/opt/homebrew/opt/openssl@3/lib/lib{ssl,crypto}.3.dylib` — an absolute Homebrew path,
      > invisible to every check performed so far because this machine has that library installed
      > (ffmpeg pulls it in transitively) and every prior `otool -L` was run on `knaif`/`libggml-*`,
      > never on `libllama-common` specifically. **This is a fourth occurrence of the exact trap
      > shape §1.3 documents for OpenMP, a different library**: llama.cpp's CMakeLists.txt defaults
      > `option(LLAMA_OPENSSL ... ON)` for cpp-httplib's HTTPS support (used by the `--hf-repo`
      > download feature knaif never calls — `LLAMA_CURL` is already forced `OFF`), and
      > `find_package(OpenSSL)` succeeds silently whenever Homebrew's `openssl@3` happens to be
      > present. **Fix**: `llama-cpp-sys-2`'s `build.rs` forwards any `CMAKE_`-prefixed env var
      > straight to `cmake::Config::define` (verified by reading it — the same mechanism that makes
      > `MACOSX_DEPLOYMENT_TARGET` work), so CMake's own `CMAKE_DISABLE_FIND_PACKAGE_OpenSSL=ON`
      > escape hatch reaches this with no crate patch needed. Rebuilt clean, `otool -L` confirmed
      > libssl/libcrypto (and the now-unneeded CoreFoundation/Security frameworks they pulled in)
      > are gone, `check_macho_deps.py` now reports zero failures, real Metal inference reconfirmed.
      >
      > **The cause is platform-independent, so the fix is now unconditional** (2026-08-03, second
      > pass — it was initially scoped to the macOS build paths only). Reading llama.cpp's own
      > sources settles what the first pass could only flag as likely: `option(LLAMA_OPENSSL ... ON)`
      > (`CMakeLists.txt:119`) and the `find_package(OpenSSL)` it gates
      > (`vendor/cpp-httplib/CMakeLists.txt:126`) carry **no platform guard whatsoever**, and the
      > propagation path is equally generic — `cpp-httplib` is a STATIC library that links OpenSSL
      > `PUBLIC`, and `common/CMakeLists.txt:140` links it into `llama-common`, so the requirement
      > lands in a core library we ship. The trigger is nothing more than "OpenSSL >= 3 dev files
      > present": a near-certainty on a Mac, and routine on a Linux CI box with `libssl-dev`.
      >
      > What differs by platform is only the *severity*, and it is Mach-O that makes macOS worst:
      > the dependency's install name is baked in, and Homebrew's is the absolute path
      > `/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib`, so dyld looks exactly there and aborts on
      > a clean Mac. ELF records the bare SONAME `libssl.so.3`, resolved through normal loader search
      > paths — a softer failure, but still a dependency no floor-pinned artifact may carry.
      >
      > Confirmed: neither `check_elf_deps.py`'s `BASE_SYSTEM` nor `check_pe_imports.py`'s
      > `WINDOWS_PROVIDED` lists libssl/libcrypto, so **nothing can ship broken on those platforms —
      > packaging hard-fails instead.** That is the right outcome and the wrong time to learn it:
      > the failure arrives after a full build, reading like a mystery, when the fix is one line that
      > already exists. Set in all three paths that reach llama.cpp's CMake, guarding every OS:
      > `package.sh`'s own build step (which the Linux container path at
      > `installers/linux/build-in-container.sh:215` also goes through), and both `package-native`
      > recipes, which build via cargo directly and then call `package.sh --no-build` — the same
      > bypass-path gap `MACOSX_DEPLOYMENT_TARGET` has, except that the floor genuinely *is*
      > macOS-only and stays conditional, while this one no longer is.
      >
      > **Test suite**: `python/core/tests/test_macho_deps.py`, 30 tests, mirroring
      > `test_pe_imports.py`'s synthetic-fixture approach (constructs raw Mach-O bytes — no real
      > toolchain artifact needed, so it runs anywhere). Covers: every dependency load-command kind,
      > the legacy `LC_VERSION_MIN_MACOSX` fallback, six malformed/mutated-input cases (truncated
      > file, wrong magic, 32-bit Mach-O, zero-`cmdsize` — which would otherwise infinite-loop, a
      > name string with no NUL terminator, a corrupted fat-arch offset past EOF), fat-binary
      > multi-slice walking and non-arm64 rejection, the libomp-shaped unresolvable-`@rpath` case,
      > a dangling-symlink case, the missing-exe-rpath regression, and the deployment-floor checks.
      > Hardened the parser itself along the way: every `struct.unpack_from` in the load-command
      > walk is now wrapped so a malformed file raises `MachOError` (reported and skipped, matching
      > the siblings) instead of an uncaught `struct.error` or, for a zero `cmdsize`, an infinite
      > loop. `just check` (1659 passed, coverage 82.63%) confirms nothing else regressed.
- [x] **E2 (first bullet only). `installers/smoke.sh` on macOS.** Most of it works already: `bin/knaif`
      discovery, the LICENSE/NOTICE and three-contracts assertions, and `backend list` resolving the
      manifest exe-relative and reporting cuda as *unavailable on this platform*. Two changes:
      - extend check 7 — currently gated `uname -s != Linux && -f bin/knaif.exe`, so it silently
        skips on Darwin — to call `check_macho_deps.py`;
      - **`.zip` is already handled** (via `unzip`), so D6's archive needs nothing new — but
        **`.pkg` is not**, and cannot be: `smoke.sh`'s dispatch covers zip / tar.gz / AppImage /
        directory only. See E6.
      > **First bullet done differently than written, second bullet not started.** `check_macho_deps.py`
      > is wired into `package.sh` itself (required, unconditional for the `metal` kind) rather than
      > into `smoke.sh`'s check 7 — matching what E1 actually asked for ("wire it into package.sh...
      > not merely into smoke.sh, because packaging is the only step every artifact passes through by
      > construction") over what this bullet's first line said. `installers/smoke.sh` itself has not
      > been touched yet; its `.pkg` gap (second bullet) is still open, tracked under E6.
- [ ] **E3. ⚠️ Clean-room run in a macOS VM — on the OLDEST supported macOS, with real inference.**
      Per D8 and D9. A VM (Virtualization.framework via `tart`, UTM, or equivalent) with **no Xcode,
      no Command Line Tools and no Homebrew**, running the **minimum** OS the deployment floor
      claims — a clean *current* macOS tests the toolchain assumption but says nothing about the
      floor. Assert `--version`, `skills list` (exe-relative), and an offline mock `plan --json` —
      **and then, decisively, a real local GGUF `run`.**
      > **Why the real run is not optional.** `--version`, `skills list`, `skills deps` and a mock
      > `plan` all exercise the *hard-linked* core libraries and pass without llama ever loading —
      > that is precisely the blind spot `package.sh`'s `exe_imports_llama` guard (B4) exists to
      > cover at build time. Only a real inference proves that **`libggml-metal.dylib` and the
      > `ggml-cpu-apple-*` variants can actually be `dlopen`ed**, which is the single thing this
      > clean room is for. Confirm Metal is selected and layers offload (A3), not just that it ran.
- [ ] **E4. Gatekeeper behaviour, simulated honestly — including extraction semantics.** `curl` does
      **not** set the quarantine attribute; Safari and Finder do, and they **propagate** it to
      extracted contents. So neither a `curl` download nor `xattr`-ing an archive and unpacking it
      with command-line `tar`/`unzip` reproduces what a user experiences. Test at least one of:
      a genuine browser download extracted in Finder, or an explicit **recursive** quarantine
      applied to the extracted tree. **Run it before signing (expect a block) and after (expect a
      clean launch)**, so the signing work is demonstrated to have changed something rather than
      assumed to have — and test the stapled `.pkg` **with networking disabled**, since offline is
      the only condition that distinguishes a stapled ticket from an online lookup (D6).
- [ ] **E6. `.pkg` verification — two gates `smoke.sh` structurally cannot provide.**
      *Added 2026-08-02 after audit.*
      - **Static inspection:** `pkgutil --expand` the package and check payload contents, install
        location, file ownership and modes, the receipt's identifier and version (D7), the
        Installer signature, and that `LICENSE`/`NOTICE` are inside it.
      - **Disposable-VM installation:** install the **final stapled** package, run real inference
        offline, verify Metal selection and layer offload, then exercise **upgrade over an existing
        install** and the documented uninstall. [RELEASE.md](../RELEASE.md) §4 already records why
        upgrade is its own gate: on Windows, two installer directives only ever execute on an
        upgrade, so a fresh install proves nothing about them.
- [ ] **E5. Artifact hygiene.** No `*.gguf`, `*.ipynb`, `*.jsonl`, `*.py`, no `eval`/`sandbox`/
      `notebook` paths. Holds by construction (`package.sh` copies an allowlist) — re-check on the
      real build, as the release procedure requires for every platform. Also check for stray
      `.DS_Store` files, which is a macOS-specific way to fail this.

---

## 9. Workstream F — code signing and notarization

An Apple Developer account is available (recorded in the current
[`installers/macos/README.md`](../../installers/macos/README.md) placeholder). Two certificates are
needed: **Developer ID Application** (binaries and dylibs) and **Developer ID Installer** (the
`.pkg`).

- [ ] **F1. Certificates and credentials.** Create/obtain both certs. Store notarization
      credentials in the keychain with `xcrun notarytool store-credentials` (App Store Connect API
      key preferred over an app-specific password — it is revocable and scoped). **No secret enters
      the repository**, and the profile name used by scripts is a documented input, not a hard-coded
      value.
- [ ] **F2. ⚠️ Order of operations — and it is a DAG with two branches, not one line.** *Revised
      2026-08-02 after audit.* Any modification to a Mach-O invalidates its signature, and
      `install_name_tool` (B3) and `strip` are modifications. **Stapling also mutates the `.pkg`**,
      which is why checksums come last:

      ```text
      stage
        → install-name / rpath surgery + strip
        → sign every Mach-O
        → verify every Mach-O
        ├─ build final .zip  → notarytool submit → read the log        (cannot be stapled — D6)
        └─ pkgbuild/productbuild + Installer-sign
                             → notarytool submit → read the log → stapler staple → stapler validate
        → clean-room tests (E3, E4, E6)
        → SHA256SUMS over the final files
      ```

      Encode it in a script so it cannot be got wrong by hand. **Generate `SHA256SUMS` only after
      stapling** — a checksum taken before it describes a file that no longer exists, which is the
      same class of error as `just installer` overwriting a published setup.exe
      ([RELEASE.md](../RELEASE.md) §4).
- [ ] **F3. Sign inside-out with the hardened runtime, and verify per-binary.** Every `.dylib`
      first, the exe last, `--options runtime --timestamp --sign "Developer ID Application: …"`.
      **Do not lean on `codesign --deep` over the tree** — `--deep` is a bundle-oriented convenience
      that Apple explicitly discourages for signing and that reads poorly over a flat `bin/` of
      loose Mach-Os. Iterate over every actual Mach-O and assert, per file: valid signature, the
      expected **Team ID**, hardened runtime enabled, and a secure timestamp present. E1 already
      enumerates the files; reuse that list so the two checks cannot disagree about what is in the
      artifact.
- [ ] **F3b. Read the notarization log even on success.** *Added 2026-08-02 after audit.*
      `xcrun notarytool log <submission-id>` after an `Accepted` result — Apple's own guidance is to
      review it, because a submission can be accepted while carrying warnings (an unsigned nested
      binary, a missing secure timestamp) that become failures on a later OS or a later policy
      change. Confirm the log lists **every** Mach-O's CDHash: that is the only direct evidence the
      archive's nested binaries were covered, and it matters most for the `.zip` branch, whose
      contents cannot be stapled and are therefore verified online per-binary.
- [ ] **F4. ⚠️ Determine the minimum entitlements empirically — start with none.** Two are
      plausibly required and both weaken the hardened runtime, so neither is added speculatively:
      - `com.apple.security.cs.allow-jit` — Metal compiles the embedded shader source at runtime.
        Metal's compilation normally happens in a system XPC service rather than in-process, so
        this may well be unnecessary. **Test with no entitlements first**, under the hardened
        runtime, on the clean-room VM.
      - `com.apple.security.cs.disable-library-validation` — needed only to `dlopen` a dylib signed
        by a *different* Team ID. Our own backends are signed by us, so this should not be needed —
        and its absence is a *feature*: a user dropping an unsigned dylib into `~/.knaif/backends`
        being refused is correct behaviour on macOS, not a defect. Add it only if a real,
        reproduced failure demands it, and record the failure in the plan if so.
- [ ] **F5. The `.pkg`, specified rather than gestured at.** `pkgbuild` (payload + install location
      + identifier + `--version` **derived from `package.sh`'s `VER`**, D7) → `productbuild`
      (distribution + Developer ID Installer signature). Decide and document, because each is
      user-visible and none has a safe default:
      - **package identifier** and install location. Suggested: payload under `/usr/local/knaif`.
      - **receipt and version behaviour on upgrade** — verified by E6, not assumed.
      - **file ownership and modes** in the payload.
      - **uninstall.** ⚠️ **macOS packages have no native uninstall action**, so "uninstall" is
        *documentation* — the commands to remove the install root, the `~/.knaif` data directory
        (D5), and `pkgutil --forget`. Say so plainly in the release body rather than leaving users
        to discover it; the Linux tarball's entry in [RELEASE.md](../RELEASE.md) §6 sets the
        precedent for stating this.
      - **PATH.** *Reconsidered 2026-08-02 after audit.* The first draft wanted to mirror the
        Windows installer's opt-in PATH consent. The analogy is weaker than it looked: on Windows
        the choice is *edit the user's `PATH` environment variable* (a persistent, global mutation),
        whereas here it is a symlink in a directory that is already on the default `PATH` — and
        **running the installer is itself consent to install the CLI**. Making it a checkbox
        requires a multi-component Distribution package for little benefit. Default to placing the
        symlink and documenting it; revisit only if the simple form proves objectionable.
- [ ] **F6. Notarize and staple — the two branches of F2's DAG.** `xcrun notarytool submit --wait`
      on the `.pkg` **and** on the `.zip` (D6 makes the `.zip` both the notarized and the published
      container, so there is no longer a mismatch to reason about), then `xcrun stapler staple` the
      `.pkg` and `xcrun stapler validate` it. Read both logs (F3b).
      > **State the archive's limitation in the release body rather than papering over it:** a
      > ticket cannot be stapled to a `.zip`, so a quarantined archive's first run needs Apple's
      > service reachable. That is the whole reason the `.pkg` exists (D6) — tell users which to
      > pick and why, the way §6 already does for SmartScreen and the AppImage's FUSE requirement.
- [ ] **F7. Verify the way Gatekeeper does, not the way the signer does.** *Corrected 2026-08-02
      after audit.* For **bare command-line binaries** use
      `codesign -R="notarized" --check-notarization -vv <binary>`; `spctl --type exec` is the legacy
      *app-bundle* check and is not the right instrument for a CLI (`spctl -a -vvv -t install`
      remains correct for the `.pkg`). **The decisive test is neither** — it is E4's quarantined
      launch in the clean-room VM, offline for the stapled `.pkg`. Tooling reports what a policy
      engine *would* say; the quarantined run reports what it *does* say.
- [ ] **F8. Cross-link [code-signing](2026-07-27-code-signing.md).** That plan covers Windows
      signing and is deferred pending release history. macOS signing is **not** deferred — it is
      not optional the way Windows signing is (SmartScreen is a warning; Gatekeeper is a block) —
      but both plans should point at each other so the certificate/HSM story is designed once.

---

## 10. Workstream G — release integration and documentation

- [ ] **G1. `docs/RELEASE.md`.** Add macOS to the artifact table (§1), a build-and-package section
      (§2) alongside Linux and Windows, the signing/notarization sequence, the macOS clean-room and
      static-check rows in §4, and macOS notes in §6 (Gatekeeper, `xattr`, `.pkg` vs `.zip`,
      Intel unsupported, `brew` for external tools, uninstall/`~/.knaif` removal).
- [ ] **G2. `docs/NATIVE.md`.** §5.3's build-kind table gains `metal`; §5.5's backend
      recommendation gains a macOS line; §9's packaging section gains the macOS layout; §10 gains
      the build commands; **§12's "macOS — no installers/notarization; explicitly out for v1" line
      is deleted**, which is the single clearest signal that this plan landed.
- [ ] **G3. `installers/macos/README.md`.** Replace the placeholder with the real thing. Its current
      content is a promissory note and its predictions should be checked against what actually
      happened — in particular it says "universal2 if feasible, else per-arch", which D4 answers.
- [ ] **G4. The rest.** `docs/PERFORMANCE.md` (Workstream D), `docs/INFERENCE.md` (§ macOS rows are
      Python-side and already partly correct — reconcile), `docs/PROVENANCE.md` (only if D3 lands on
      staging libomp), `docs/MODELS.md` (only if D4 changes a recommendation), `README.md` platform
      support, `evals/INDEX.md` rows, `docs/TODO.md` and `docs/plans/README.md` entries. Run
      `just licenses-all` before any release cut, per the existing rule.
- [ ] **G5. Homebrew tap — a fast-follow, scoped here so it is not forgotten.** A
      `blackdeep-tech/homebrew-knaif` tap with a formula pointing at the published `.zip` and its
      `SHA256SUMS` entry. `brew install blackdeep-tech/knaif/knaif` is what a macOS CLI user expects
      and it sidesteps the quarantine question entirely. Depends on G1's published artifact; its own
      small plan or a TODO item, not a blocker for this one.
- [ ] **G6. Hand off to CI.** [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md)
      explicitly parks macOS out of its C3 matrix *"until macOS packaging lands"*. When this plan
      closes, that condition is met: add `macos-14`/`macos-15` (arm64) runners to the matrix and note
      that signing and notarization need repository secrets, which is why they cannot simply be
      lifted into CI on day one.

---

## 11. Out of scope

- **Intel Macs (`x86_64-apple-darwin`) and universal2 binaries** — D4.
- **A macOS GUI / menu-bar app.** The engine crates were designed to be embeddable by one
  ([NATIVE.md](../NATIVE.md) §1); building one is a product, not a port.
- **iOS / iPadOS.** `llama-cpp-sys-2` has an `AppleVariant::Other` arm and a Metal backend, so it is
  reachable in principle — and it is a different product with a different distribution model.
- **The persistent daemon and prompt-prefix KV reuse.** Tracked in
  [TODO.md](../TODO.md) *Open / Next* for 1.2.0, cross-platform, and independent of this work.
- **Re-locking any eval snapshot** — C4.
- **Changing the recommended model.** D4 may produce a *finding* about 8 GB Macs; acting on it is a
  separate, deliberate decision.

---

## 12. Open questions to resolve during execution

1. ~~Does the stock toolchain link Homebrew's `libomp`?~~ **Resolved 2026-08-07 (B5):** not by
   default, but yes once the build environment resolves the keg-only formula (e.g.
   `CMAKE_PREFIX_PATH` pointing at it) — and D3's fix (an explicit, opt-in `openmp` feature, off
   on macOS) is implemented and verified to hold under that exact environment.
2. ~~What is the deployment floor?~~ **Promoted to decision D9 + task M4b** (2026-08-02) — it is a
   release gate, not a question to answer later. *Which* version to pick remains open; whether it is
   chosen deliberately does not.
3. **Command Line Tools or full Xcode?** (M3.) Materially changes the contributor prerequisite —
   and D3's build-time-`metallib` fallback would likely force full Xcode, so the two are linked.
4. **Does the hardened runtime need `allow-jit` for Metal shader compilation?** (F4.)
5. **How large is the first-run Metal shader-compilation tax?** (D3.) Decides whether an
   install-time warm-up is needed — and if it is, whether the same mechanism should finally be built
   for Vulkan, where §2 has flagged it as an open item since 2026-07-14.
6. **Does `~/.knaif` need a Time Machine / iCloud exclusion?** A 2.5 GB model store in the home
   directory gets backed up on every machine that has Time Machine on. Low stakes, cheap to answer,
   annoying to discover as a user.

---

## 13. Acceptance criteria

macOS support is done when **all** of the following hold. *Revised 2026-08-02 after audit — three of
these were previously unfalsifiable.*

0. **C0 is discharged**: both snapshots re-locked with an executing verifier against their current
   corpora, and `just eval-regression` able to receive a `--current` scoreboard. Until then
   criterion 6 cannot honestly pass, and this is repository work that does not belong to macOS.
1. `just check` and `just test-native` are green on macOS, with any pre-existing cross-platform test
   defects fixed or explicitly recorded.
2. `installers/package.sh --kind=metal` produces `knaif-<ver>-macos-arm64.zip` from a clean
   checkout, and the signed/notarized/stapled `.pkg` is produced by a scripted, documented sequence
   in F2's order — with `SHA256SUMS` generated **after** stapling.
3. `check_macho_deps.py` reports zero **unresolvable** dependencies (not merely zero
   foreign-looking paths), passes its own mutation fixtures, asserts the D9 floor and rejects
   non-arm64 slices, and runs as a required step inside `package.sh`.
4. `installers/smoke.sh` passes on the `.zip`; the `.pkg` passes E6's two gates instead, because
   `smoke.sh` structurally cannot open one.
5. The clean-room VM — **oldest supported macOS**, no Xcode, no CLT, no Homebrew — runs a
   **quarantined** artifact through a **real GGUF inference** with Metal selected and layers
   offloaded, with no Gatekeeper block and no dylib load failure. The stapled `.pkg` does it
   **offline**.
6. For each of ffmpeg and documents, a **saved** scoreboard from the snapshot's own verifier and a
   pinned backend is diffed against the **committed** snapshot via an explicit `--current`, and
   passes. `just parity` runs against a binary confirmed to do real inference, and is clean or has
   every diff triaged against C5's three confounds.
7. [PERFORMANCE.md](../PERFORMANCE.md) carries a macOS machine row and a Metal backend row, measured
   by the documented methodology, with the §4 CPU trap avoided by measuring a tree with
   `libggml-metal.dylib` removed rather than by setting `KNAIF_N_GPU_LAYERS=0`.
8. [RELEASE.md](../RELEASE.md) can be followed end-to-end by someone who did not write it, and
   [NATIVE.md](../NATIVE.md) §12 no longer lists macOS as a limitation.
