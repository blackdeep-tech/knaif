# `installers/linux/` — Linux packaging

**Status:** implemented (Phase 9, 2026-07-16). Option 3 (loadable `ggml-*` backends) on Linux.

## Artifacts

**One default artifact per OS** (C5b), never one per backend.

- **Tarballs** — built by [`../package.sh`](../package.sh):
  - `package.sh --kind=vulkan` → **THE RELEASE ARTIFACT**: `knaif-<ver>-linux-x64.tar.gz` (the plain
    name). CPU **and** Vulkan — `libggml-vulkan.so` beside the same `ggml-cpu-*` variants, so it is a
    strict superset of `cpu` and falls back to CPU where there is no usable GPU.
  - `package.sh --kind=cpu` → build kind only, for a box with no Vulkan SDK. Carries a `-cpu` suffix
    so it cannot overwrite the release artifact. **Do not publish it** — it offers a user nothing the
    default lacks, at the cost of making them choose.
  - `package.sh --kind=cuda` → **opt-in CUDA payload** (`libggml-cuda.so` + NVIDIA `.so.13` redist),
    dropped into `~/.knaif/backends/` (`$KNAIF_BACKENDS_DIR`) on an existing install — not a
    standalone app. **Linux is the only OS that can build this payload** (Windows `cuda` is still the
    historical static app — post-v1), and **v1 publishes no CUDA asset**.
- **AppImage** — [`build-appimage.sh`](build-appimage.sh) `<staged-dir|tarball>` wraps a full
  artifact — feed it the default (vulkan) tree. It carries the same CPU+Vulkan backends; the
  opt-in `ggml-cuda.so` loads from `~/.knaif/backends/`, outside the read-only mount. Set
  `APPIMAGETOOL=/path/to/appimagetool-x86_64.AppImage` if it is not on `PATH`.

The binary is dynamically linked (`dynamic-backends`); `package.sh` stages the core libs beside the
exe and sets an `$ORIGIN` RPATH so the unpacked folder relocates. GGUF models are never bundled.
CUDA/Vulkan need glibc + the vendor driver; a static-musl CPU floor is a possible fast-follow.

**`libgomp.so.1` is staged too.** The GNU OpenMP runtime is *not* part of a base Linux system — it
ships with GCC's runtime libraries — and `libggml-base` plus every `ggml-cpu-*` variant links it.
Without it the CPU backends fail to load on any machine that merely lacks a compiler. It is the
exact counterpart of `VCOMP140.dll` on Windows and was missed for the same reason: it resolves on
the build box, so running the artifact there could never fail. Licence-wise it is GPLv3 **with the
GCC Runtime Library Exception**, which exists to permit exactly this, so nothing propagates to
knaif and no extra notice file is required.

## Building a published artifact

```bash
just package-linux                  # HEAD's commit -> tar.gz + AppImage
just package-linux --rev=v1.1.0     # a specific tag
just package-linux --dev            # mount the worktree; never publish the result
```

Published Linux artifacts are built inside [`Dockerfile`](Dockerfile), which fixes the runtime floor
rather than inheriting it from whoever ran the build. A native `package.sh` run still works for a
**local** artifact — the floor is then your own distro's. Full rationale in
[`docs/RELEASE.md`](../../docs/RELEASE.md) §2 and
[`docs/plans/2026-07-27-portable-builds.md`](../../docs/plans/2026-07-27-portable-builds.md).

### The CUDA payload

```bash
just package-linux --kind=cuda      # opt-in ggml-cuda + NVIDIA redist, as loose files
```

Built in [`Dockerfile.cuda`](Dockerfile.cuda) — a **separate** image, because the release image
deliberately carries no CUDA toolkit (3–5 GB to serve an artifact it does not produce) and the
payload needs none of its Vulkan packages. Both pin the same Ubuntu release, apt snapshot and Rust
version: the payload is `dlopen`ed by the exe from the other image, so a higher floor here would
fail on exactly the older systems the release image exists to serve, and nowhere else.
`test_linux_build_images.py` asserts that, because "keep these in step" is not something a comment
can enforce.

The payload is not a runnable tree, so it gets the floor audit
(`scripts/check_elf_deps.py`) instead of `smoke.sh`, and no AppImage.

**Building the payload needs no GPU; verifying it does.** The toolkit compiles it; only the three
required cases (absent → CPU/Vulkan, present → offloads, present-with-no-usable-GPU → clean silent
fallback) need an NVIDIA driver on Linux.

**WSL2 GPU passthrough works — verified 2026-07-29**, so those cases run on the Windows box and no
second machine is needed. Docker Desktop's WSL2 backend exposes the host NVIDIA GPU to Linux
containers:

```bash
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

On the bench box that reports `NVIDIA GeForce RTX 3070 Laptop GPU`, `NVIDIA-SMI 610.53`,
`KMD Version: 610.74`, `CUDA UMD Version: 13.3` — i.e. the container sees the real device, and at a
driver above the R580 floor. Re-run it before relying on it on any other machine; it depends on the
host driver and on Docker Desktop's WSL2 (not Hyper-V) backend.

If it ever stops working, **the payload is built but NOT published** until it has run on a real Linux
box with an NVIDIA driver. Do not publish an unrun payload: a backend that fails to `dlopen` reaches
the user as "CUDA didn't work", which is the least debuggable outcome available.

Note this proves *passthrough*, not the payload. The three cases still have to be run against the
packaged payload — that is a separate step, and the reason this one mattered is only that it was
the blocker.

**Windows has no container answer and does not need one.** Its payload builds in the same VS
Developer shell as the main artifact, on the maintainer's own machine — which is also what keeps the
VC++ redistribution grant's "licensed Visual Studio user" condition satisfied with nothing further
to confirm (see [`docs/PROVENANCE.md`](../../docs/PROVENANCE.md)). The asymmetry is a decision, not
an omission.

## The supported floor, measured

| Requirement | Value |
|---|---|
| glibc | `GLIBC_2.34` |
| libstdc++ | `GLIBCXX_3.4.30`, `CXXABI_1.3.13` |
| OpenMP | `GOMP_4.5` (staged, so this binds nothing) |

**glibc is not the binding constraint — `libstdc++` is.** The artifact needs glibc 2.34, *below*
the 2.35 build base, so any statement of the form "we need glibc 2.35" is measuring the wrong
quantity. RHEL/Rocky/Alma 9 has glibc 2.34 and would qualify, but ships `GLIBCXX_3.4.29` and is
therefore **not supported** — bundling our own `libstdc++` was rejected because
`libggml-vulkan.so` dlopens the host GPU driver, which is commonly built against a *newer*
`libstdc++` than ours; overriding it via `$ORIGIN` risks breaking GPU support on current desktops.

Verify both axes before publishing — see `docs/RELEASE.md` §4:

```bash
python3 scripts/check_elf_deps.py dist/staging/knaif-<ver>-linux-x64/bin   # what it requires
installers/linux/check-floor.sh dist/knaif-<ver>-linux-x64.tar.gz          # what a loader does
```

See [`docs/plans/2026-07-15-native-branch-finalization.md`](../../docs/plans/2026-07-15-native-branch-finalization.md)
(Workstream D) and [`docs/NATIVE.md`](../../docs/NATIVE.md).
