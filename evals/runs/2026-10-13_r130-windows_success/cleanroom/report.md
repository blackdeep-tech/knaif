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

It checks, in one Sandbox, Smart App Control enforcing at the start and at the end: the zip (frozen
binary, `1.3.0`, `skills list`, every binary signed); the published 1.2.1 installed, its CLI
(waiting at the model download prompt) blocking the upgrade and still running, then the upgrade
(same folder, no leftovers, signed); a daemon started from the installed 1.3.0, then a reinstall
over it (the daemon stopped, no `knaif.exe` running, `knaif.exe` rewritten and not held) and an
uninstall over it (stopped, removed, `~\.knaif` kept); a fresh install, one real documents request
with no `--model` on the CPU, and an uninstall; `skills deps` naming the tools the box lacks. A setup
that only succeeds by scheduling a reboot fails (`/RESTARTEXITCODE=99`), and a timeout ends the run.
All 30 checks are required; the verdict lands in `grade.txt` next to this file.

**Result:** —

## 2. Manual part (~10 min, at the screen)

In a fresh Sandbox with Smart App Control enforcing, run the **staged** installer
(`sandbox/r130/cleanroom/in/knaif-1.3.0-windows-x64-setup.exe`, the one `stage.sh` hash-checked:
`c671a60a…`) **interactively**. A Sandbox has no winget, so every tool choice is greyed there whatever
Smart App Control says (Evidence, 2026-10-07); judge the wording.

| # | Check | Pass when | Result |
|---|---|---|---|
| M0 | Before starting | `(Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy).VerifiedAndReputablePolicyState` is `1`, and `Get-FileHash` of the installer is `c671a60a…` | |
| M1 | Tasks page, tools | FFmpeg and Tesseract are shown **disabled**, each with its reason | |
| M1b | Components / tasks | **no CUDA component** is offered (RELEASE.md §4: a GPU-less box gets CPU/Vulkan only) | |
| M2 | Tasks page, Ghostscript | there is **no** Ghostscript choice | |
| M3 | Finish page | names the tools that could not be installed and why, and the usual advice for LibreOffice | |
| M4 | After install | `knaif skills deps` in a new terminal marks the same tools missing or blocked | |
| M5 | Overall | nothing on any page is cut off, wrong, or names 1.2.x | |

Notes:
