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

See [`docs/plans/2026-07-15-native-branch-finalization.md`](../../docs/plans/2026-07-15-native-branch-finalization.md)
(Workstream D) and [`docs/NATIVE.md`](../../docs/NATIVE.md).
