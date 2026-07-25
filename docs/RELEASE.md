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

**`cpu` is a build kind, not a release artifact.** It exists for a box with no Vulkan SDK and is
named `knaif-<ver>-<os>-<arch>-cpu.*` so it cannot overwrite the real one. Do not publish it: it
offers a user nothing the default lacks, at the cost of making them choose. (Before Option 3 the
backends were statically linked, so cpu/vulkan were different binaries and both had to ship — that
is why older docs list two. It is no longer true.)

**No CUDA artifact at v1** (C6 deferred) — see the CUDA section below.

**GGUF models are never attached to a release** — they live on Hugging Face
(`blackdeep/knaif`) and are pulled at runtime.

### CUDA payloads are NOT published at v1

The CUDA payload builds and is proven, but v1 ships **no `knaif backend install cuda` command**, so
nothing would fetch a published payload — and publishing it would bake org URLs into the release.
The **Windows installer must therefore offer no CUDA component** (an opt-in task with no command
behind it is worse than no offer). Payload publishing + the install surface land in the post-v1 plan.
The only CUDA route v1 documents is manual: build the payload and copy it into `~/.knaif/backends`.

**That manual route is LINUX-ONLY at v1.** `--kind=cuda` emits a real payload only on Linux; on
Windows it still produces the historical static-with-redist app, so a Windows user has nothing to
copy into `~/.knaif/backends` (the Windows loader would load one — there just isn't one to build).
Aligning Windows `cuda` onto Option 3 is post-v1. Neither OS publishes a CUDA asset at v1, so this
blocks nothing; just do not promise Windows users the manual route.

---

## 2. Build & package

### Linux (builds itself)

Needs `build-essential pkg-config cmake ninja-build patchelf libclang-dev`; Vulkan also
`libvulkan-dev glslc glslang-tools spirv-headers`; CUDA needs the toolkit (13.3 to match the
bundled redist). AppImage needs `libfuse2t64` + `appimagetool`.

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
  `NUM_JOBS`, **not** `CMAKE_BUILD_PARALLEL_LEVEL`.

---

## 3. CUDA arch range (what is built vs. what is tested)

Release arch list (never `native`, and it must be set via the **`CUDAARCHS` env var** — CMake
initialises `CMAKE_CUDA_ARCHITECTURES` from it and `llama-cpp-sys-2` offers no passthrough):

```
CUDAARCHS="75-real;80-real;86-real;89-real;90-virtual;120-real"
```

| Arch | GPU generation | Status |
|---|---|---|
| `sm_75` | Turing | **built, UNVERIFIED** (no card) |
| `sm_80` | Ampere (A100) | **built, UNVERIFIED** (no card) |
| `sm_86` | Ampere (RTX 30xx) | **built + runtime-verified** (RTX 3070) |
| `sm_89` | Ada (RTX 40xx) | **built, UNVERIFIED** (no card) |
| `compute_90` PTX | forward-compat | built — JITs forward to post-Blackwell |
| `sm_120` | Blackwell (RTX 50xx) | **built + runtime-verified** (RTX 5080) |

Running on 86 and 120 does **not** validate 75/80/89 — state exactly that; there is no interpolation
claim. **sm_75 is the hard floor:** CUDA Toolkit 13 removed offline compilation and library support
for Maxwell/Pascal/Volta. Those users fall back to Vulkan or CPU — they are not cut off.

**Driver floor: NVIDIA R580+** (CUDA 13). Presence of `nvcuda.dll` / `libcuda.so` is not sufficient.

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
7. **Delete** the release branch.

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

**Windows SmartScreen.** v1 ships **unsigned** (an OV certificate is out of scope for v1), so
Windows shows *"Windows protected your PC"*. Bypass: **More info → Run anyway**. Tell users to verify
the checksum first — that, not the absence of a warning, is what proves the download is intact.

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

**GPU.** The default artifact auto-selects Vulkan when a capable driver is present, else CPU — on
every vendor, so most users need nothing else. NVIDIA CUDA offload in v1 is manual **and Linux-only**:
build a matching `ggml-cuda` payload + NVIDIA redist (`installers/package.sh --kind=cuda`) and drop it
into `~/.knaif/backends` (needs an R580+ driver). The payload is ABI-coupled to the exe — use the one
built for your exact knaif version. **Windows has no CUDA route at v1**: `package.sh --kind=cuda`
still stages the historical static app there rather than cutting a payload, so there is nothing to
copy (post-v1, C6). Windows NVIDIA users get Vulkan, which is the default anyway.
