# knaif 1.3.0 — Windows clean room

The release plan's gate rule *Clean room* on the frozen, signed Windows files (built from the freeze
`8c94cbe`; `knaif-1.3.0-windows-x64.zip` `7c289f3e…`, `knaif-1.3.0-windows-x64-setup.exe`
`c671a60a…`), in Windows Sandbox with Smart App Control enforcing. Two parts: an automatic run that
grades itself, and the wizard pages, which only a person can judge (`/VERYSILENT` never builds them).

**Status: PASS, 2026-10-15.** Automatic part 30/30 (`grade.txt`); wizard part M0–M5 all pass, driven
by `wizard.ps1` instead of clicked (owner: "make it automatic"), its record in `wizard/`.

**Smart App Control.** The first automatic run failed only its two Smart App Control checks: the
Sandbox started in *evaluation* (state 2), which watches and blocks nothing (`attempt1-sac-evaluation.log`;
the other 28 passed). On 2026-10-02 the Sandbox started enforcing; the host's own setting is now off,
and a fresh Sandbox follows the host image. Both scripts now switch the throwaway Sandbox to enforcing
(state 1, `CiTool --refresh`) before testing, and prove it took: an unsigned program built inside the
Sandbox is refused ("An Application Control policy has blocked this file"). The re-run passed 30/30
under enforcement. Not a rule change: the rule asks for enforcement during the run, and it held.

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

**Result:** PASS 30/30 (re-run under enforcement, 2026-10-15; `grade.txt`).

## 2. Wizard part (was manual; now `wizard.ps1`, ~1 min)

```bash
bash evals/runs/2026-10-13_r130-windows_success/cleanroom/wizard_stage.sh
# open the .wsb it prints (a fresh Sandbox, after the first one is closed); it ends with DONE
```

`wizard.ps1` runs the staged installer with its wizard, accepts the licence and presses Next /
Install / Finish (UI Automation; Inno's buttons take `BM_CLICK`), and saves each page as
`page-NN.png` plus a dump of its controls. Inno's task and component lists are not exposed to UI
Automation, so the tool-choice items (M1, M2) and the layout (M5) were judged from the screenshots
on the host; the rest the script checks itself.

The original instructions, for a person doing it by hand:

In a fresh Sandbox with Smart App Control enforcing, run the **staged** installer
(`sandbox/r130/cleanroom/in/knaif-1.3.0-windows-x64-setup.exe`, the one `stage.sh` hash-checked:
`c671a60a…`) **interactively**. A Sandbox has no winget, so every tool choice is greyed there whatever
Smart App Control says (Evidence, 2026-10-07); judge the wording.

| # | Check | Pass when | Result |
|---|---|---|---|
| M0 | Before starting | `(Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy).VerifiedAndReputablePolicyState` is `1`, and `Get-FileHash` of the installer is `c671a60a…` | **PASS**: state 1 after the switch, an unsigned probe refused; sha256 `c671a60a…` |
| M1 | Tasks page, tools | FFmpeg and Tesseract are shown **disabled**, each with its reason | **PASS** (`page-05.png`): both greyed and unchecked, each "(blocked by Smart App Control on this PC)"; the group says "needs winget, which this PC does not have". The script's own M1 line failed on a selector bug (it read the Ready page), fixed after the run |
| M1b | Components / tasks | **no CUDA component** is offered (RELEASE.md §4: a GPU-less box gets CPU/Vulkan only) | **PASS**: no control on any page mentions CUDA (`page-04.png`, `page-05.png`) |
| M2 | Tasks page, Ghostscript | there is **no** Ghostscript choice | **PASS** (`page-05.png`): FFmpeg, LibreOffice, Tesseract only |
| M3 | Finish page | names the tools that could not be installed and why, and the usual advice for LibreOffice | **PASS** (`page-08.png`): each of the four tools "not installed", three "(Smart App Control blocks it)", why (no winget), and "Run knaif skills deps" |
| M4 | After install | `knaif skills deps` in a new terminal marks the same tools missing or blocked | **PASS** (`skills-deps.txt`): ffmpeg and tesseract MISS, blocked by Smart App Control; libreoffice MISS with its download link |
| M5 | Overall | nothing on any page is cut off, wrong, or names 1.2.x | **PASS**: all 8 pages read; titles say 1.3.0, no control names 1.2.x, nothing cut off; setup exit 0 |

Notes:
