# knaif 1.3.0 — Windows clean room

The release plan's gate rule *Clean room* on the frozen, signed Windows files (built from the freeze
`8c94cbe`; `knaif-1.3.0-windows-x64.zip` `7c289f3e…`, `knaif-1.3.0-windows-x64-setup.exe`
`c671a60a…`), in Windows Sandbox with Smart App Control enforcing. Two parts: an automatic run that
grades itself, and the wizard pages, which only a person can judge (`/VERYSILENT` never builds them).

**Status: not run yet.** Each part is launched after the owner approves it.

## 1. Automatic part (~15–20 min)

```bash
bash evals/runs/2026-10-13_r130-windows_success/cleanroom/stage.sh
# open the .wsb it prints; the Sandbox runs sandbox.ps1 by itself and ends with DONE
uv run --no-sync python evals/runs/2026-10-13_r130-windows_success/cleanroom/grade.py
```

It checks, in one Sandbox: the zip (frozen binary, `1.3.0`, `skills list`, every binary signed); a
fresh install, one real documents request with no `--model` on the CPU, and an uninstall; the
published 1.2.1 installed, its running CLI blocking the upgrade, then the upgrade (same folder, no
leftovers, signed); a daemon started from the installed 1.3.0, then a reinstall over it and an
uninstall over it, each leaving the daemon stopped, no `knaif.exe` running and the files replaced or
removed; `skills deps` naming the tools the box lacks. All 26 checks are required; the verdict lands
in `grade.txt` next to this file.

**Result:** —

## 2. Manual part (~10 min, at the screen)

Open a fresh Sandbox with `dist` mapped (any `.wsb` with `dist` read-only, e.g. the signing test's)
and run `knaif-1.3.0-windows-x64-setup.exe` **interactively**. A Sandbox has no winget, so every tool
choice is greyed there whatever Smart App Control says (Evidence, 2026-10-07); judge the wording.

| # | Check | Pass when | Result |
|---|---|---|---|
| M1 | Tasks page, tools | FFmpeg and Tesseract are shown **disabled**, each with its reason | |
| M2 | Tasks page, Ghostscript | there is **no** Ghostscript choice | |
| M3 | Finish page | names the tools that could not be installed and why, and the usual advice for LibreOffice | |
| M4 | After install | `knaif skills deps` in a new terminal marks the same tools missing or blocked | |
| M5 | Overall | nothing on any page is cut off, wrong, or names 1.2.x | |

Notes:
