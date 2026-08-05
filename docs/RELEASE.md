# RELEASE.md — cutting a knaif release

How to build, package, verify, and publish a knaif release. **v1.0.0 is cut by hand** — there is no
`.github/workflows/` yet (CI + `release.yml` are a post-v1 follow-on, see
[plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md](plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md)),
so a release gates on **local** green, not CI.

Background: [NATIVE.md](NATIVE.md) §5.3 (loadable backends), §9 (packaging), §10 (building).

---

## 0. Version bump

Three declarations must agree, or `python/core/tests/test_version_consistency.py` fails:

| Surface | File |
|---|---|
| Rust workspace | `Cargo.toml` → `[workspace.package] version` |
| Python package | `python/core/pyproject.toml` → `version` |
| Windows installer | `installers/windows/knaif.iss` → `AppVersion` |

Tags are semver (`v*.*.*`) — use `v1.0.0`, never a bare `v1`. Per-skill `skills/*/skill.yaml`
versions are a **separate namespace** and are deliberately not bumped in step.

The model manifest is a **bill of materials, not a live catalog**: it ships inside the artifact and
binds the release to the models it was tested with. `python/core/tests/test_model_manifest_release_ready.py`
fails the build if a recommended model still has `url: TODO` (since default-model auto-select, the
recommended model is the *default* path — a placeholder URL would break first run for everyone).

---

## 1. Artifact set

Release builds use the **`dynamic-backends`** loadable-backend model (NATIVE.md §5.3).

**One default artifact per OS** (C5b), never one per backend. That artifact is built from the
`vulkan` kind, which under Option 3 means **CPU *and* Vulkan**: the Vulkan backend is one extra
loadable lib beside the same CPU backends, and it loses device selection on a box with no usable
GPU. It is a strict superset of `cpu` and runs everywhere `cpu` does, so it gets the plain name.

| Artifact | Kind | Contents |
|---|---|---|
| `knaif-<ver>-windows-x64.zip` | `vulkan` | portable tree — CPU + Vulkan |
| `knaif-<ver>-windows-x64-setup.exe` | `vulkan` | Inno installer (per-user, no admin), same tree |
| `knaif-<ver>-linux-x64.tar.gz` | `vulkan` | portable tree — CPU + Vulkan |
| `knaif-<ver>-linux-x86_64.AppImage` | `vulkan` | the same tree as a single file |
| `SHA256SUMS` | — | one line per published artifact |

The **support matrix** these artifacts imply — supported OSes, the measured runtime
floors, GPU backends, and the external-tool caveats — is declared once in
[`contracts/release/platforms.yaml`](../contracts/release/platforms.yaml) and read by the
website. State a floor there, not in prose here, so the two cannot disagree.

**`cpu` is a build kind, not a release artifact.** It exists for a box with no Vulkan SDK and is
named `knaif-<ver>-<os>-<arch>-cpu.*` so it cannot overwrite the real one. Do not publish it: it
offers a user nothing the default lacks, at the cost of making them choose. (Before Option 3 the
backends were statically linked, so cpu/vulkan were different binaries and both had to ship — that
is why older docs list two. It is no longer true.)

**GGUF models are never attached to a release** — they live on Hugging Face
(`blackdeep/knaif`) and are pulled at runtime.

### CUDA payload assets (from 1.1.0)

The CUDA payload is **not an artifact in the table above** — it is not a downloadable app and no user
should be choosing between it and the default. It is fetched by `knaif backend install cuda`, and it
is published as **loose per-file assets split across two tags**:

| Files | Tag | Why |
|---|---|---|
| `ggml-cuda.dll` / `libggml-cuda.so` (~150 MB), plus the four MSVC runtime DLLs on Windows | the **product release** (`v<ver>`) | ABI-coupled to that release's exe; a tag-scoped URL structurally cannot serve a newer lib to an older binary |
| `cudart*` / `cublas*` / `cublasLt*` (~517 MB), `NVIDIA-CUDA-EULA.txt` | **`redist-cuda-13.3`** (pre-release, never deleted) | keyed to the CUDA toolkit, byte-identical across knaif releases, so it is uploaded once |

**Sizes are the measured Windows 1.1.0 payload**, staged 2026-07-30: **668 MB** total across ten
files — `cublasLt64_13.dll` 464 MB, `ggml-cuda.dll` 150 MB (six real archs), `cublas64_13.dll` 53 MB,
and ~1 MB of CRT, `cudart`, and licence text. The figure `backend list` shows is computed from the
manifest, so it is right by construction; prose is not, and the earlier "~618 MB" predated the CRT
and licence files joining the payload. Re-measure when the arch list or toolkit moves, and take the
Linux numbers from that payload's own fragment rather than assuming these.

**Loose files, not archives** (decided 2026-07-29). Each asset carries its own `sha256` in the
manifest, so nothing on the install path extracts an archive — `BackendStore` is fetch → hash →
stage → swap. It also keeps a Microsoft CRT security fix to ~1 MB of individually pinned files rather
than a republished ~150 MB `ggml-cuda.dll`, which is the servicing argument that ruled out static
linking in the first place.

**The licence files ship inside the payload**, landing in the user's backends directory alongside the
libraries. Under loose-file publishing the release *page* is not what reaches a user's disk, and
NVIDIA's EULA permits redistribution only *with* the licence text. `package.sh` hard-fails if
`installers/licenses/NVIDIA-CUDA-EULA.txt` is absent.

**Both OSes emit a real payload from 1.1.0.** Through 1.0.x, `--kind=cuda` produced a genuine payload
only on Linux; on Windows it produced the historical static-with-redist *app*, so a Windows user had
nothing to install and the installer's opt-in task had nothing to place. That shape is now behind
`--legacy-windows-cuda-app` and is not publishable.

Nothing is published until every `url`/`sha256` in
[`contracts/backends/backend-manifest.yaml`](../contracts/backends/backend-manifest.yaml) is real and
its `status:` is flipped to `published` — `test_backend_manifest_release_ready.py` fails the build
on a payload that claims to be published while carrying a placeholder. See §7.

---

## 2. Build & package

### Linux — PUBLISHED artifacts are built in the container

```bash
just package-linux                  # builds HEAD's commit -> tar.gz + AppImage
just package-linux --rev=v1.1.0     # builds that tag
```

**This is the only supported way to build a Linux artifact that gets published**, and it is the
only step in the whole repo that needs Docker. It fixes the artifact's runtime floor instead of
inheriting it from whoever happened to run the build — a maintainer on Arch (glibc 2.42) or Fedora
42 (2.41) building natively ships a binary that starts on almost nothing, and **nothing warns
them**. See [`installers/linux/Dockerfile`](../installers/linux/Dockerfile) for what is pinned.

It checks out a **commit** into a clean tree inside the container rather than mounting the working
copy, so uncommitted dirt cannot reach a published artifact, file modes come from git rather than
from a 9p mount, and `.gitattributes` line endings are correct. `--dev` mounts the worktree instead,
for iterating on packaging; never publish what it produces.

### Linux — native build (local artifacts only)

`installers/package.sh --kind=vulkan` still works natively on any distro. **The floor is then your
own distro's**, which is fine for a local build and wrong for a release.

Needs `build-essential pkg-config cmake ninja-build patchelf libclang-dev`; Vulkan also
`libvulkan-dev glslc glslang-tools spirv-headers`; CUDA needs the toolkit (13.3 to match the
bundled redist). AppImage needs `libfuse2`/`libfuse2t64` + `appimagetool`.

> **That package list is right for 24.04 and wrong for anything older**, which is exactly why the
> container exists. On Ubuntu 22.04: `glslc` **is not packaged by Ubuntu at all**; `libfuse2t64` is
> **`libfuse2`** (the `t64` suffix arrived with 24.04's 64-bit `time_t` transition); and — the one
> no amount of reading would have found — **22.04's Vulkan headers are 1.3.204 and llama.cpp's
> Vulkan backend does not compile against them**, needing `VkPhysicalDeviceCooperativeMatrixFeaturesKHR`,
> `LayerSettingsCreateInfoEXT` and `vk::DriverId::eMesaDozen`. The Dockerfile takes Vulkan headers,
> loader, `spirv-headers`, `glslang-tools` and `shaderc` from **LunarG**, not from Ubuntu.
>
> A prose dependency list cannot be wrong in a way that stops the build. An executable one cannot be
> wrong in a way that doesn't.

- **A cached `llama-cpp-sys-2` build hides missing `-dev` packages.** cmake configure and bindgen
  run once and are then cached, so a box that built successfully can later lose `libclang-dev` or the
  Vulkan `-dev` packages and still **`--no-build` package fine** — the failure only appears when
  something invalidates the crate's fingerprint (a changed env var, a `cargo build` that errored, a
  wiped `target/release/build/llama-cpp-sys-2-*`) and forces a fresh compile. Two ways this bites:
  - **`libclang-dev`** — bindgen `dlopen`s `libclang.so`; absent → *"Unable to find libclang"*.
  - **Vulkan `-dev`** (`libvulkan-dev glslc glslang-tools spirv-headers`) — cmake's `find_package`
    → *"Could NOT find Vulkan (missing: Vulkan_LIBRARY Vulkan_INCLUDE_DIR glslc)"*. The runtime
    loader `libvulkan.so.1` surviving is **not** enough; the build needs `libvulkan.so` + headers.

  Verify the toolchain with a **clean** build (wipe the `llama-cpp-sys-2-*` dir first), never a cached
  one, or "the build environment is set up" is an untested claim.
- **`appimagetool`** is a single self-contained AppImage from AppImageKit `continuous` — a download,
  not an apt package. Put it on `PATH` or pass `APPIMAGETOOL=/path/to/appimagetool`.

```bash
installers/package.sh --kind=vulkan                                   # -> dist/knaif-<ver>-linux-x64.tar.gz
installers/linux/build-appimage.sh dist/staging/knaif-<ver>-linux-x64
```

`package.sh` picks the features, stages the core libs + loadable backends beside the exe, and sets an
**`$ORIGIN` RPATH** (patchelf) so the unpacked folder relocates.

### Windows (compile first in a "Developer PowerShell for VS")

```bash
CMAKE_GENERATOR=Ninja cargo build --release -p knaif-cli --features llama,dynamic-backends,vulkan
installers/package.sh --no-build --kind=vulkan                        # -> dist/knaif-<ver>-windows-x64.zip
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installers\windows\knaif.iss
```

`just package-native vulkan` + `just installer` wrap the same steps.

**Vulkan requires `CMAKE_GENERATOR=Ninja`** — the default MSBuild generator dies in
`vulkan-shaders-gen` with `cannot find the batch label specified - VCEnd`.

**Package each kind immediately after its own build.** Several feature sets coexist under
`target/release/build/llama-cpp-sys-2-*/`, and cargo does not re-run a cached build script, so mtime
does not identify which build is current. `out_dir()` resolves the right one by the backends it
emitted — but only ever package the kind you just built, and never assume a rebuilt-but-cached kind
refreshed anything.

**Package from that same Developer shell, not just build from it.** `package.sh` stages the four
VC++ runtime DLLs from `$VCToolsRedistDir`, which a Developer shell exports pointing at the redist
tree for the *very toolset that compiled the binaries*. Run packaging from a plain Git Bash and the
variable is unset, so the script falls back to scanning the filesystem and says so:

```text
NOTE: VCToolsRedistDir unset or unusable — falling back to a filesystem scan.
```

The scan picks the newest redist tree it finds, which on a single-VS box is normally the right
answer — verified 2026-07-30, where scanned and exported resolution produced byte-identical
payloads. It stops being the right answer when you pin an older toolset (`vcvarsall.bat x64
-vcvars_ver=<ver>`) or have several Visual Studios installed: the scan's answer and the compiler's
diverge, and a mismatched CRT loads fine and fails later. Treat the NOTE as "this release was
packaged on inference" and re-run rather than shipping it.

If you must package outside a Developer shell, export the variable by hand — the script accepts a
Windows path and a trailing backslash:

```bash
export VCToolsRedistDir='C:\Program Files\Microsoft Visual Studio\18\Community\VC\Redist\MSVC\<ver>\'
```

`<ver>` is whatever `VC/Auxiliary/Build/Microsoft.VCRedistVersion.default.txt` contains.

**Do not "fix" a missing redist tree with `vc_redist.x64.exe`.** That installer deploys the CRT into
`System32`; it does not create the `VC\Redist\MSVC\<ver>\x64\Microsoft.VC*\` tree `package.sh` copies
from, and it is the wrong licence path besides — the Distributable List grant the packaging relies on
is scoped to files inside a Visual Studio installation's `VC\redist` (see `docs/PROVENANCE.md`). The
tree comes from the MSVC v14x **build tools** component in the VS Installer, nothing else.

### Build traps (both OSes)

- **Changing `CUDAARCHS` or the generator needs a clean.** `always_configure(false)` means cmake will
  not reconfigure and an incremental build silently keeps the old settings. `cargo clean -p
  llama-cpp-sys-2` is the documented step, but it does **not** reliably remove the directory — wipe
  `target/release/build/llama-cpp-sys-2-*` directly to be sure.
- **Stale lib copies break `build.rs`.** If `target/release/lib{ggml,llama}*.so*` survive from a
  previous feature set (possibly as dangling symlinks), build.rs's hard-link step panics with
  `AlreadyExists`. Delete them before switching kinds.
- **Memory.** llama.cpp's Vulkan `mul_mm` shader and nvcc are memory-hungry; on a ~7 GB box, 16
  parallel jobs OOM-kill `cc1plus`. Cap with `CARGO_BUILD_JOBS=<n>` — cmake-rs reads cargo's
  `NUM_JOBS`, **not** `CMAKE_BUILD_PARALLEL_LEVEL`. On a 15 GB box the same default (16 jobs) does
  not OOM outright; it *pages*, which is worse to diagnose because it produces no error at all.

### A Windows CUDA build takes about an hour, and shows nothing while it does

Measured 2026-07-30: **54 minutes**, on 16 CPUs / 15.4 GB with `CARGO_BUILD_JOBS=4` and the default
MSBuild generator. State this plainly because the failure mode is misreading it as a hang — cargo
prints one `Compiling llama-cpp-sys-2` line and then goes silent for the whole build, since the
per-file progress belongs to a build script and never reaches the terminal.

The cost is structural: ggml-cuda has **183 translation units** (64 top-level plus 119 generated
template instances), and each is compiled once per real architecture. Six real archs means roughly
six times the nvcc work of a single-arch build.

To tell a slow build from a stuck one, watch objects rather than stdout:

```bash
find target/release/build/llama-cpp-sys-2-*/out -path '*cuda*' -name '*.obj' | wc -l   # of 183
```

Two things worth knowing:

- **Don't build six archs to test packaging.** `KNAIF_CUDA_DEV_ARCHS=86-real` (or whatever card you
  have) verifies against that shorter list and names the payload `-DEVARCH` so it cannot be
  published by accident. Roughly a sixth of the work.
- **The MSBuild generator serialises most of it.** Object paths under `*.dir/Release/` confirm it is
  in use, and it parallelises across projects rather than within a target — so `CARGO_BUILD_JOBS=4`
  still yields one compiler at a time for long stretches. `CMAKE_GENERATOR=Ninja` very likely fixes
  that (it is what `package.sh` uses on Linux for every functional kind), but **it is unverified for
  CUDA on Windows** and switching forces a full reconfigure, discarding the build cache. Try it on a
  build you can afford to lose.

---

## 3. CUDA arch range (what is built vs. what is tested)

Release arch list (never `native`, and it must be set via the **`CUDAARCHS` env var** — CMake
initialises `CMAKE_CUDA_ARCHITECTURES` from it and `llama-cpp-sys-2` offers no passthrough):

```
CUDAARCHS="75-real;80-real;86-real;89-real;90-real;90-virtual;120-real"
```

| Arch | GPU generation | Status |
|---|---|---|
| `sm_75` | Turing | **built, UNVERIFIED** (no card) |
| `sm_80` | Ampere (A100) | **built, UNVERIFIED** (no card) |
| `sm_86` | Ampere (RTX 30xx) | **built + runtime-verified** (RTX 3070) |
| `sm_89` | Ada (RTX 40xx) | **built, UNVERIFIED** (no card) |
| `sm_90` | Hopper (H100) | **built, UNVERIFIED** (no card) |
| `compute_90` PTX | forward-compat | built — JITs forward to post-Blackwell |
| `sm_120` | Blackwell (RTX 50xx) | **built + runtime-verified** (RTX 5080) |

Running on 86 and 120 does **not** validate 75/80/89/90 — state exactly that; there is no
interpolation claim. **sm_75 is the hard floor:** CUDA Toolkit 13 removed offline compilation and
library support for Maxwell/Pascal/Volta. Those users fall back to Vulkan or CPU — they are not cut
off.

**Driver floor: NVIDIA R580+** (CUDA 13). Presence of `nvcuda.dll` / `libcuda.so` is not sufficient.
Declared once, in
[`contracts/backends/backend-manifest.yaml`](../contracts/backends/backend-manifest.yaml)
(`requires.min_driver`), so the payload and the gate that offers it cannot disagree.

**`90-real` is built as well as `90-virtual`** *(added 2026-07-29)*. `90-virtual` alone leaves Hopper
on PTX JIT, and PTX JIT is the documented **exception** to CUDA's minor-version driver compatibility
— i.e. precisely the case the R580 floor does *not* cover. A few MB of fatbin removes the caveat.
Deliberately **not** doing the larger version of this fix (a per-architecture driver floor): sm_90 is
a data-center part that will not run this CLI, and that is complexity bought for nobody.

**Forward-compat PTX must come from a non-`12X` virtual arch.** ggml rewrites every `12X` → `12Xa`,
so `120-virtual` yields architecture-specific `sm_120a` PTX that cannot JIT forward. `90-virtual`
escapes that rewrite; its `compute_90` PTX JITs to any later GPU, at PTX size only and no extra SASS.

Verify a build actually produced every arch — a flag typo silently drops one:

```bash
"$CUDA_PATH/bin/cuobjdump" --list-elf <ggml-cuda lib> | grep -oE 'sm_[0-9]+[a-z]*' | sort -u
"$CUDA_PATH/bin/cuobjdump" --list-ptx <ggml-cuda lib> | grep -oE 'sm_[0-9]+[a-z]*' | sort -u
```

Match `sm_[0-9]+[a-z]*`, not `sm_[0-9]+` — the latter silently truncates `sm_120a` to `sm_120`. Use
`--list-ptx`, never `--dump-ptx` (dumping ~125 MB of PTX text times out).

---

## 4. Verify every artifact

```bash
installers/smoke.sh dist/knaif-<ver>-<os>-<arch>[-<kind>].{zip,tar.gz}
```

Unpacks outside the checkout with an empty `KNAIF_SKILLS_ROOT` and asserts: `--version` matches the
version `Cargo.toml` claims; `skills list` finds ffmpeg + documents via exe-relative resolution;
`skills deps` probes (a `[MISS]` is a **pass** — it tests the probe, not the box); one offline mock
`plan --json` emits a JSON envelope. It never downloads.

`smoke.sh` also asserts **`LICENSE` and `NOTICE` are both in the artifact** — Apache-2.0 §4(a) and
§4(d) respectively, and `NOTICE` carries the Qwen3 derivation attribution for the models knaif
downloads. `NOTICE` was absent from every artifact on every OS through 1.0.1 precisely because no
check read it.

### Testing the Windows installer without damaging a real install

The wizard cannot be verified silently — `/VERYSILENT` never builds the task tree, which is how
pre-checked tasks shipped in 1.0.1 unnoticed. So installer changes need a GUI run, and that run must
be isolated:

Use the recipe rather than calling `ISCC` by hand — it is the isolation, not a shortcut to it:

```bash
just package-native vulkan     # stage an artifact first, as `just installer` needs
just installer-test            # -> dist/test-installer/knaif-<ver>-windows-x64-setup.exe
```

It encodes three separations, each guarding a different failure:

- **Its own output dir.** Compiling to the default output overwrites
  `dist/knaif-<ver>-windows-x64-setup.exe`, a **published artifact with a row in `SHA256SUMS`**; a
  stray rebuild silently invalidates the published checksum.
- **A throwaway `AppId`** (`/DAppIdGuid=`). Inno treats two builds sharing an `AppId` as the **same
  application** and derives the same `{AppId}_is1` uninstall key from it, so without this a scratch
  install registers against the production key — and removing it afterwards destroys the real
  install's Add/Remove Programs registration and its upgrade detection. An overridden build labels
  itself *"(TEST BUILD)"* in Add/Remove Programs so the two cannot be confused.
- **Its own install directory** (`/DTestInstall`). The throwaway `AppId` separates the uninstall
  *key* but not the *tree*: a test build landing in the production default is one the real
  installer does not recognise as a prior version, so it installs over the same directory and
  leaves **two Add/Remove Programs rows sharing one**, where uninstalling either breaks the other.

Extra `ISPP` defines pass through, which is how branches gated on hardware get exercised without
it — `just installer-test /DMinNvidiaDriver=9999` puts the driver floor above any real driver, so
the CUDA task must not render.

Two rules the recipe cannot enforce:

- **Never delete the production `{AppId}_is1` key as cleanup.** Tear down the throwaway build's key
  only.
- **Never run the uninstaller with `/SUPPRESSMSGBOXES`** unless you mean it. It answers the
  data-directory prompt with that prompt's default — which keeps `~/.knaif` from 1.1.0 onward, but
  a 1.0.1-or-earlier uninstaller still on disk will delete a 2.5 GB model store.

**Uninstall every test build when done**, before building release artifacts.

### Exercise the upgrade path — every release, not once

**Two of the installer's directives only ever run on an upgrade, so a fresh install proves nothing
about them:**

- **`[InstallDelete]`** is a no-op on a fresh install, because none of the four target directories
  exist yet. It is a destructive section, and an upgrade is the only time it actually deletes.
- **`AppMutex`** is never triggered without a running `knaif.exe`. If its string and
  `hold_app_mutex` in `apps/cli/src/main.rs` ever drift apart, the directive silently does nothing
  and an upgrade over a running CLI goes back to being deferred to reboot with no signal.

Do this under a throwaway `AppId`, against a scratch directory, with **two** builds:

```bash
# 1. baseline
ISCC /DAppVersion=<prev> /DAppIdGuid=<throwaway-guid> /O<scratch> installers/windows/knaif.iss
# install it, then leave `knaif.exe` running in a terminal

# 2. the upgrade
ISCC /DAppVersion=<new> /DAppIdGuid=<throwaway-guid> /O<scratch> installers/windows/knaif.iss
```

Assert, in order: setup **refuses to proceed while the CLI is running** (`AppMutex`); after closing
it, **no *"folder already exists"* warning**; the directory is reused from `InstallLocation`; the
Add/Remove row's `DisplayVersion` advances to the new version rather than adding a second row; and
`{app}\bin` contains no library left over from the previous build. Then uninstall the test build.

Each `ISCC` run needs its `dist\staging\knaif-<ver>-windows-x64` to exist — copy the staged tree to
the second version's name rather than rebuilding, since the payload is irrelevant to this check.

### Clean-room verification — REQUIRED, both OSes

> **The rule these findings earned, which applies to every artifact shape added later:
> a verification step that runs on the build box tests STAGING, never PORTABILITY.**
> `installers/smoke.sh` executes the artifact on the machine that built it, so it can only ever
> prove that machine can run it — and that machine has the full toolchain. Every 1.0.x Windows
> binary imported `VCRUNTIME140`/`MSVCP140`/`VCOMP140` with none of them staged, and every Linux
> binary needed `libgomp.so.1` unstaged, precisely because nothing ran anywhere else. A new
> artifact shape (a second Linux format, macOS, a container image) needs its own clean-room run
> before it is published.

Two layers, and they are not redundant. The **static** checks read what a binary *requires* and run
on any machine, so they fail where the mistake was made. The **clean-room** runs prove what a real
loader *does*, and catch requirements nobody thought to look for. Each has caught something the
other missed.

```bash
# Static — no VM, no container, runs anywhere. Both must exit 0.
python scripts/check_pe_imports.py dist/staging/knaif-<ver>-windows-x64/bin
python3 scripts/check_elf_deps.py  dist/staging/knaif-<ver>-linux-x64/bin

# Dynamic — Linux, both artifacts, BOTH directions
installers/linux/check-floor.sh dist/knaif-<ver>-linux-x64.tar.gz
installers/linux/check-floor.sh dist/knaif-<ver>-linux-x86_64.AppImage
```

`check_elf_deps.py` prints the **measured** floor. Read it rather than assuming: the artifact
requires `GLIBC_2.34`, **below** the 2.35 base image, and the binding constraint is
`GLIBCXX_3.4.30` / `CXXABI_1.3.13` from `libstdc++` — *not* glibc. Any support table quoting a
glibc number alone is measuring the wrong thing.

`check-floor.sh` must **pass at the floor and fail below it**. One direction proves nothing about
where support ends, and the failure must be a symbol-version error — the script rejects any other
failure as inconclusive rather than counting it as success.

**Windows: run the unpacked zip in Windows Sandbox** (`Containers-DisposableClientVM`) — a
disposable VM with no developer tooling, which is the machine that reproduces a missing runtime.
Drive it from a `.wsb` with the artifact mapped read-only, a writable folder for results, and a
`LogonCommand`. Assert `knaif.exe skills list` exits 0; a missing runtime exits **-1073741515**
(`0xC0000135`) printing nothing at all.

Then, per artifact set:

- **Artifact hygiene** — no `*.gguf`, `*.ipynb`, `*.jsonl`, `*.py`, no `eval`/`sandbox`/`notebook`
  paths. Holds by construction (`package.sh` copies an allowlist), but re-check on the real build.
- **Clean-env install** — install the Windows installer and the Linux AppImage/tarball on a machine
  outside any checkout and run for real. `run` must work **without `--model`** (auto-select) and land
  on CPU/Vulkan with no fault on a box with no NVIDIA card. Assert the installer offers **no CUDA
  component**.
- **Checksums**

```bash
cd dist && sha256sum knaif-<ver>-* > SHA256SUMS      # Linux
# Windows: Get-FileHash -Algorithm SHA256 <file>
```

---

## 5. Publish (strict order)

The tag and every release URL must be **born in the final org** — never redirected into it. The
repository home is `blackdeep-tech/knaif`, created **fresh** rather than transferred, so no release
URL has ever depended on an org redirect.

### Rehearse the publish flow before the first real cut

**Everything below is irreversible, and none of it has ever been executed.** No GitHub Release has
existed, so step 5 is untested procedure — and each of its outputs is permanent: the `release-tags`
ruleset means a pushed tag cannot be moved, a published Release URL is public the moment it exists,
and a PyPI version can **never** be reused. There is no revision to a first release, only a second
one that looks like an apology.

So run the whole path once against throwaway outputs before running it for real:

- **A draft GitHub Release** — create it, upload the artifacts and `SHA256SUMS`, check the rendered
  body and the asset names, then **delete it without publishing**. Drafts are invisible to everyone
  but the repo's maintainers, so this exercises upload, size limits, and the body's markdown with no
  public trace.
- **TestPyPI** for the wheel (§5a) — it needs its own token in a `[testpypi]` section, which is
  precisely the guard against fat-fingering the real index.
- **Do not push a throwaway tag.** A draft Release can be attached to an existing tag or created
  against a branch; a tag is the one artifact here with no undo, so it stays for the real cut.

Cheap, and it converts "the publish procedure is written down" into "the publish procedure has been
run". Skip it only on a release whose flow is already proven — which, until one has shipped, is
none of them.

1. Open the PR; review; ensure **local suites green** (not "CI green" — there is no CI).
2. **Merge to `dev`.** *The branch is closed here; everything below is release mechanics.*
3. **v1.0.0 only — OSS prep.** Run the OSS-prep pass to completion, ending
   with the flattened tree pushed to `blackdeep-tech/knaif` and the repo public. **Hard gate on
   step 4.** Later releases skip this step entirely.
4. **Tag and push `v1.0.0`.** For v1.0.0 the tag goes on the **flatten commit** — the post-scrub
   initial commit in the new repo — *not* the `dev` merge commit, which predates the scrub and would
   ship pre-prep source. From v1.0.1 on, tag the release commit as normal.
5. **Publish** the GitHub Release on that tag: upload the artifacts + `SHA256SUMS`, draft → publish,
   public. Artifacts staged *before* the transfer publish *after* it **without a rebuild** — safe only
   because no v1 artifact names the org. Re-run `installers/smoke.sh` on the staged set first anyway;
   it takes seconds and is the last chance to catch a stale artifact. Confirm that no artifact bakes
   a GitHub org URL.
6. **Verify** a fresh download installs and runs, independent of the build box.
7. **Refresh the website's download data — `just release-data`**, then commit
   `site/data/release.json`. The knaif.org download buttons are built from that snapshot
   and **nothing else updates it**. Skipping this leaves the site advertising the previous
   release; there is no CI to catch it (see
   [plans/2026-08-04-website-split.md](plans/2026-08-04-website-split.md) §6, which folds
   this into `release.yml` when that lands). The URLs are deliberately *not* derived from
   `Cargo.toml`: step 2 bumps the version before step 5 publishes the assets, so a derived
   link would 404 in between. `uv run pytest python/core/tests/test_release_data.py`
   verifies the snapshot's shape offline.
8. **Delete** the release branch.

---

## 5a. Publish the Python package to PyPI

Separate from the GitHub release above: the native artifacts and the `knaif` wheel are
independent deliverables. The **name is claimed by an upload**, not a reservation.

**Credentials.** PyPI account with 2FA, and an API token in `~/.pypirc` — username is
literally `__token__`, password is the whole token including the `pypi-` prefix. Never put
`.pypirc` in the repo (it is not gitignored). After the first upload, replace the
account-wide token with a project-scoped one and revoke the broad one.

```bash
just sync-license                        # LICENSE/NOTICE mirror; drift guard fails otherwise
rm -rf python/core/dist
uv run --with build python -m build python/core/
uv run --with twine twine check python/core/dist/*
```

Before uploading — the artifact is **immutable**, and a version number can never be reused:

```bash
# LICENSE + NOTICE must be inside both artifacts (Apache-2.0 §4); a README link is not enough
python -c "import zipfile,glob;z=zipfile.ZipFile(glob.glob('python/core/dist/*.whl')[0]);print([n for n in z.namelist() if 'LICENSE' in n or 'NOTICE' in n])"
# the runtime contract must ship
python -c "import zipfile,glob;z=zipfile.ZipFile(glob.glob('python/core/dist/*.whl')[0]);print([n for n in z.namelist() if n.endswith('.yaml')])"
```

Optional dry run on TestPyPI — needs its **own** token in a `[testpypi]` section; the PyPI
token does not work there:

```bash
uv run --with twine twine upload --repository testpypi python/core/dist/*
```

Then upload and verify from a clean environment:

```bash
uv run --with twine twine upload python/core/dist/*
uv run --with knaif --no-project python -c "import knaif; print(knaif.__name__)"
python -m knaif.examples.clock "list timezones in europe"    # after `pip install knaif[clock]`
```

Notes. Run these in Git Bash — PowerShell does not expand `dist/*` for external commands.
The `[project.urls]` links 404 until step 3 makes the repo public; they resolve at view
time, so no re-upload is needed. Skill bundles are deliberately excluded from the wheel, so
`list_skills()` is empty on a bare install — that is expected, not a packaging fault.

---

## 6. Notes for users (put these in the release body)

**Windows SmartScreen.** knaif ships **unsigned**, so Windows shows *"Windows protected your PC"*.
Bypass: **More info → Run anyway**. Tell users to verify the checksum first — that, not the absence
of a warning, is what proves the download is intact.

Signing is tracked in [`plans/2026-07-27-code-signing.md`](plans/2026-07-27-code-signing.md). Note
that no certificate would make this prompt disappear on day one: Microsoft removed EV's
instant-SmartScreen privilege in 2024, so every certificate type now accrues reputation per file
hash through download volume. Signing is worth doing for integrity and enterprise allow-listing —
not for the first-run prompt.

**Checksum verification.**

```bash
sha256sum -c SHA256SUMS --ignore-missing                        # Linux
(Get-FileHash -Algorithm SHA256 <file>).Hash                    # Windows — compare to SHA256SUMS
```

**AppImage.** `chmod +x knaif-<ver>-linux-x86_64.AppImage && ./knaif-<ver>-linux-x86_64.AppImage`.
Needs FUSE2 (`libfuse2t64` on Ubuntu 24.04); otherwise run with `--appimage-extract-and-run`.

**First run.** No `--model` needed — knaif offers to download the recommended GGUF (~2.5 GB) into
`~/.knaif/models`. Upgrading knaif re-downloads nothing while the recommendation is unchanged.
External tools (ffmpeg, LibreOffice, Ghostscript, Tesseract) install separately — `knaif skills deps`
reports what each skill needs.

**Uninstall / removing data.** `~/.knaif` (the model store + the opt-in `backends/` payload) lives
outside the install dir so it survives an upgrade. The **Windows uninstaller asks** whether to delete
it too and **deletes by default** — answer No only if you plan to reinstall and want to keep the
~2.5 GB model. Upgrades never prompt (Inno installs over an existing install without uninstalling);
a `/SILENT` uninstall deletes without asking. The **Linux tarball and AppImage have no uninstaller** —
delete the unpacked folder (or the `.AppImage`) and, to reclaim the model, `rm -rf ~/.knaif`.

**Unattended install — pass `/TYPE=full`.** The interactive installer defaults to the `full` type
(all skills), but a `/VERYSILENT` install *without* `/TYPE` or `/COMPONENTS` reuses whatever component
selection a previous install remembered — which can silently install **only some skills** (observed:
ffmpeg but not documents on a re-install). For deterministic scripted/CI installs pass the type
explicitly, e.g.
`knaif-<ver>-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /TYPE=full /DIR="<path>"`
(add `/TASKS=""` to skip PATH, winget deps, and the model download).

**GPU.** The default artifact auto-selects Vulkan when a capable driver is present, else CPU. That
covers every vendor, and it is enough for most users — **but not for NVIDIA users on the newest
cards.** On Blackwell (RTX 50xx, sm_120) the Vulkan path generates at roughly CPU speed: ~5.7 tok/s
against the CPU's ~5.9, measured on knaif's real workload ([PERFORMANCE.md](PERFORMANCE.md) §2).
That is not a slower option, it is a product that reads as broken, so say so plainly in the release
body rather than letting "Vulkan works everywhere" stand.

NVIDIA users install the CUDA backend with one command:

```
knaif backend install cuda
```

~668 MB, needs an R580+ driver, and it takes effect on the next run. `knaif backend remove cuda`
undoes it. On the newest cards it is what makes the product usable; on older NVIDIA cards it is
faster and genuinely optional. knaif offers it on first run when it detects an eligible GPU, and the
Windows installer offers it as a task that is checked by default — the task renders only on a machine
whose GPU and driver already qualify and that has no payload yet, so it is never shown to a user it
cannot help. Setup blocks on the download, which the task description states.

The payload is ABI-coupled to the exe, so it is re-installed per knaif release — the loader detects a
payload from another release and ignores it with a message rather than loading it. Copying the files
into `~/.knaif/backends` by hand still works and stays documented as the fallback, but it is not the
route to put in a release body.

> **This paragraph was wrong through 1.0.x** and is the text a release body would have been written
> from. It told every NVIDIA user that Vulkan was enough. The measurement that contradicts it was
> taken 2026-07-14 and simply never reached this file.

---

## 7. Publish the CUDA payload assets

Only when cutting a release whose payload changed, or the first time a toolkit version is used.
Ordering matters: the manifest cannot be filled in until the assets exist, and the product artifact
cannot be built until the manifest is filled in — so the redist half goes first and the product half
is a two-pass build.

**1. Build the payloads.** Windows in a VS Developer shell on the maintainer's box, Linux in the CUDA
container (`installers/linux/Dockerfile.cuda`). Set the arch list explicitly — changing `CUDAARCHS`
needs a *clean* build, see §3:

```bash
CUDAARCHS="75-real;80-real;86-real;89-real;90-real;90-virtual;120-real" \
CARGO_BUILD_JOBS=4 \
  cargo build --release -p knaif-cli --features llama,dynamic-backends,cuda   # ~1 hour, silent
installers/package.sh --no-build --kind=cuda
```

Budget an hour and don't mistake the silence for a hang — see §2's build traps, which also cover the
job cap and the fast single-arch loop for packaging work.

This writes `dist/staging/knaif-<ver>-<os>-<arch>-cuda-backend/` as **loose files** plus
`dist/knaif-<ver>-<os>-<arch>-cuda-backend.manifest-fragment.yaml` carrying every file's real
`sha256` and size. `package.sh` verifies the fatbin actually contains every requested arch
(`cuobjdump`) and hard-fails if one was silently dropped.

**2a. Rehearse the install before uploading anything.**

```bash
installers/rehearse-backend-install.sh          # add --platform linux-x64 when packaging there
```

Serves the staged payload over `127.0.0.1`, splices the fragment's real checksums into a copy of the
real manifest, and drives `list → install → verify → remove` plus the two failure paths (a corrupted
`sha256` must land nothing and must not damage an existing install) and the stale-receipt refusal.
Roughly fifteen seconds, no uploads, and it fails on the two things unit tests structurally cannot
see: a fragment whose checksums have drifted from the staged bytes, and a payload file `package.sh`
stages but never declares. What it does *not* cover is the three backend cases — those need the GPU,
and on Windows the third one wants `CUDA_VISIBLE_DEVICES="-1"`, since an empty string reads as
*unset* there and the case then passes without testing anything.

**The fragment is the upload list, not the directory listing.** The staging directory holds one more
file than the fragment does: a generated `README.txt` orienting whoever opens the folder locally.
`write_manifest_fragment` skips it deliberately and the manifest must not declare it — it is not
payload, and `backend install` would fetch it into `~/.knaif/backends` if it were. Upload exactly the
files the fragment names, and read each one's `tag:` to decide which of the next two steps it belongs
to.

**2. Upload the redist half once, to `redist-cuda-13.3`.** Everything the fragment tags
`redist-cuda-13.3`: NVIDIA's three libraries plus `NVIDIA-CUDA-EULA.txt`. A pre-release tag, and one
that is **never deleted** — installed manifests point at it forever. Skip this entirely if the tag
already carries this toolkit's files; they are byte-identical across knaif releases, which is the
whole reason for the split.

**3. Upload the product half to the release tag**, alongside the normal artifacts. Everything the
fragment tags `product`: the ggml CUDA lib, `llama.cpp-LICENSE.txt`, and on Windows the four VC++
runtime DLLs.

Both licence texts are payload files that land on the user's disk, not release-page attachments —
under loose-file publishing the page is not what reaches a user. Omitting the EULA breaks the
condition NVIDIA's redistribution grant is subject to;
`python/core/tests/test_backend_payload_manifest.py` guards the manifest side of that.

**4. Fill in `contracts/backends/backend-manifest.yaml`** from the fragments: paste each platform's
`files:` block and replace every `url: TODO` with the asset's download URL. Write URLs against
`blackdeep-tech/knaif`. Then set `status: published`.

**5. Rebuild and re-verify the product artifacts.** The manifest ships *inside* them, so the tree
built in step 1 carries the pre-publication copy. This is unavoidable — the manifest describes assets
that do not exist until they are uploaded.

**6. Only now generate `SHA256SUMS`**, over the complete final set, and publish.

Two rules worth restating because getting them wrong is silent:

- **Never resolve `latest`.** A tag-scoped URL is what structurally prevents a newer lib reaching an
  older exe. `test_backend_manifest_release_ready.py` asserts this.
- **Asset names must be unique within a tag** — so a shared name must mean shared bytes. The
  binaries collide on nothing (`.so` vs `.dll`), but the two licence texts are staged into *both*
  payloads and therefore upload **once each**, shared: `NVIDIA-CUDA-EULA.txt` on `redist-cuda-13.3`
  and `llama.cpp-LICENSE.txt` on the product tag. That is only safe because
  `installers/licenses/**` is pinned to LF in `.gitattributes` — without the pin a Windows checkout
  stages CRLF and the Linux container LF, giving one file two sha256 values and two platforms one
  asset name, so at most one platform's `backend install` could pass its checksum.
  `test_backend_payload_manifest.py` asserts the pin and
  `test_backend_manifest_release_ready.py` asserts the consequence. If a future platform produces a
  genuinely different file under a name already taken, suffix the *asset* name and keep the
  manifest's `name:` as the on-disk name — the two are separate fields precisely so they can differ.
  Note `gh release upload` takes the asset name from the file's basename, so a suffixed asset means
  renaming the file before upload, not a flag.
