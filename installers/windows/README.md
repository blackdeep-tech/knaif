# `installers/windows/` — Windows installer

**Status:** implemented (v1). See [`docs/RELEASE.md`](../../docs/RELEASE.md) for the full release runbook.

`knaif.iss` is an [Inno Setup 6](https://jrsoftware.org/isinfo.php) script that wraps an artifact
already staged by [`installers/package.sh`](../package.sh). It does not build anything itself — stage
first, then compile the installer.

## Artifacts

**One default artifact per OS** (C5b), never one per backend — plus the installer wrapping the same
tree:

| Artifact | Kind | Contents |
|---|---|---|
| `knaif-<ver>-windows-x64.zip` | `vulkan` | portable tree — CPU + Vulkan |
| `knaif-<ver>-windows-x64-setup.exe` | `vulkan` | Inno installer (per-user, no admin), same tree |

The `vulkan` kind **is** CPU + Vulkan: the Vulkan backend is one extra loadable DLL beside the same
CPU backends, and it loses device selection on a box with no usable GPU. So it is a strict superset
of `cpu` and gets the plain name. **`cpu` is a build kind, not a release artifact** (it exists for a
box with no Vulkan SDK, and is named `-cpu.zip` so it cannot overwrite the real one) — do not
publish it.

Both are SHA-256'd into a combined `SHA256SUMS` covering **both OSes'** published artifacts
(see `docs/RELEASE.md`).

## Backends are loadable, not bundled

The functional kinds build llama.cpp with `dynamic-backends` (Option 3 / C5), so `knaif.exe` is
**not self-contained**: its core libs (`llama.dll`, `ggml.dll`, `ggml-base.dll`,
`llama-common.dll`) and the loadable `ggml-*.dll` backends ship **beside it** in `bin\`.
Windows resolves them from the exe's own directory, so no RPATH equivalent is needed.

**CUDA is not shipped.** The default artifact carries CPU + Vulkan only; `ggml-cuda` is an opt-in
payload dropped into `~/.knaif/backends`, which is scanned ahead of the artifact's own backends.
**v1 publishes no CUDA asset and the installer offers no CUDA component** — an opt-in task with no
command behind it is worse than no offer at all. Aligning the Windows `cuda` kind onto Option 3
(it still uses the historical static-with-redist shape) is post-v1.

## Build

From a **"Developer PowerShell for VS"** (needs MSVC + cmake; Vulkan also needs Ninja), at the repo root:

```powershell
just package-native vulkan    # stages dist\staging\ + writes dist\knaif-<ver>-windows-x64.zip
just installer                # -> dist\knaif-<ver>-windows-x64-setup.exe
```

`just installer` locates `ISCC.exe` itself; install it with `winget install JRSoftware.InnoSetup`.
Both default to the `vulkan` kind. To drive Inno directly:

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installers\windows\knaif.iss
```

Package each kind **immediately after its own build** — several feature sets coexist under
`target\release\build\llama-cpp-sys-2-*\`, and cargo does not re-run a cached build script.

Verify every artifact with `installers/smoke.sh <zip>` before release — it unpacks to a temp dir and
runs from an unrelated cwd, so exe-relative resolution and the staged DLLs are what get tested. It
is the check that catches a missing core lib (a `dynamic-backends` exe with no `llama.dll` beside it
dies at process start with `STATUS_DLL_NOT_FOUND`, `0xC0000135`, printing nothing).

## What the installer does

Per-user install to `%LOCALAPPDATA%\Programs\knaif` (no admin). `core` is mandatory; each skill is
an optional component. Optional tasks: add to PATH, download the recommended GGUF model, and install
supporting third-party tools (ffmpeg, Ghostscript, LibreOffice, Tesseract) **via winget** — these are
never bundled, and each is skipped when already present or when winget is unavailable.

v1 ships **unsigned**, so SmartScreen shows *"Windows protected your PC"* → **More info → Run anyway**.

## Uninstall

Removes the installed files and un-appends its own `{app}\bin` PATH entry (never rewriting the rest
of `Path`). It then asks whether to also delete `~/.knaif` — the model store (~2.5 GB) and the
opt-in `backends/` payload dir — and **deletes by default**. Uninstall means gone: most users never
learn that directory exists, so orphaning multiple GB is the more surprising outcome. Answering No
is the escape hatch for someone who intends to reinstall.

That data lives outside the app dir on purpose: it must survive an **upgrade** so a reinstall does
not re-download the GGUF. Upgrades are unaffected — Inno installs over an existing install **without**
running the uninstaller, so nothing prompts and nothing is deleted.

**A silent uninstall also deletes** (`SuppressibleMsgBox` answers with the same default under
`/SILENT` / `/VERYSILENT`). Deliberate and consistent; the only cost is a re-download for a
deployment tool that does uninstall-then-reinstall.
