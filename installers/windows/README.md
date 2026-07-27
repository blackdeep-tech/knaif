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

**The task defaults are load-bearing.** ffmpeg defaults *checked* (the ffmpeg skill requires it);
Ghostscript (AGPL), LibreOffice (~350 MB) and Tesseract default *unchecked*. Through 1.0.1 all four
shipped **pre-checked** against that stated intent: the entries used dotted `deps\*` names, which
declare a parent task that was never defined, and Inno rendered them as children of the preceding
checked task — discarding both their `unchecked` flag and their group heading. Task names must stay
flat. Declaring a real `deps` parent does *not* fix it, because Inno force-checks the children of a
checked parent.

"Already present" now means what the runtime means: the probe honours `PATHEXT`, the
`KNAIF_<CMD>_BIN` overrides, and each tool's `all_required` flag from `skills/*/skill.yaml` — so
ffmpeg counts as satisfied only when **both** `ffmpeg` and `ffprobe` resolve, while any one of
`gs` / `gswin64c` / `gswin32c` satisfies Ghostscript.

The model task is hidden entirely when the GGUF is already in `~/.knaif/models`, and the wizard's
final page says to open a new terminal and run `knaif skills deps` — `ChangesEnvironment` only
reaches processes started after the broadcast, so the PATH task cannot affect an already-open shell.

The installed tree carries `LICENSE` **and `NOTICE`** (Apache-2.0 §4(a) and §4(d)) beside
`licenses/` for third-party notices.

v1 ships **unsigned**, so SmartScreen shows *"Windows protected your PC"* → **More info → Run anyway**.
Signing is tracked in [`docs/plans/2026-07-27-code-signing.md`](../../docs/plans/2026-07-27-code-signing.md).

## Uninstall

Removes the installed files and un-appends its own `{app}\bin` PATH entry (never rewriting the rest
of `Path`). It then asks whether to also delete `~/.knaif` — the model store (~2.5 GB) and the
opt-in `backends/` payload dir — and **keeps it by default**. *(Changed in 1.0.2; through 1.0.1 it
deleted by default.)*

Two independent defaults govern that, and both now point at *keep*: `MB_DEFBUTTON2` focuses **No**
in the dialog, while `SuppressibleMsgBox`'s default answer is what `/SILENT` and `/VERYSILENT` use.
Setting one without the other leaves the other path deleting, which is exactly how 1.0.1 shipped —
**a silent uninstall destroyed the model store without asking**, including for a deployment tool
doing uninstall-then-reinstall. Deleting is recoverable only by re-downloading 2.5 GB; keeping costs
disk space the user can reclaim at will.

That data lives outside the app dir on purpose: it must survive an **upgrade** so a reinstall does
not re-download the GGUF. Upgrades are unaffected either way — Inno installs over an existing
install **without** running the uninstaller, so nothing prompts and nothing is deleted.

### Upgrade detection depends on a registry key

Inno recognises a prior install **only** through its `…\Uninstall\{AppId}_is1` key under `HKCU`.
With that key absent there is no Add/Remove Programs row, and installing over the existing tree
degrades to a *"folder already exists"* warning instead of upgrading.

Inno writes the key at install and removes it at the **end** of an uninstall, so a cancelled
uninstall leaves it in place. Reaching the broken state normally means the key was deleted by hand
or by a registry cleaner. It is also why scratch builds must be compiled with a throwaway `AppId`
(see [`docs/RELEASE.md`](../../docs/RELEASE.md)): with a shared `AppId`, tearing a test install down
by deleting "its" key deletes the real install's registration.

Since 1.0.2 setup detects and offers to repair that state: if
`%LOCALAPPDATA%\Programs\knaif\unins000.exe` exists with no matching key, it offers to run the
orphaned uninstaller before continuing. Two limits worth knowing:

- **Only the default directory is discoverable.** Nothing records a custom `/DIR=` once the key is
  gone, so a non-default install in that state must be removed by hand.
- **The offer defaults to No and runs the old uninstaller interactively.** That uninstaller was built
  from an older script and still deletes the model store by default, so setup never invokes it
  silently — the prompt tells the user to answer No when it asks about their models.
