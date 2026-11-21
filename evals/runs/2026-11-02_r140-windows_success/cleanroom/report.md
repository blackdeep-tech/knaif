# knaif 1.4.0 — Windows clean room

The release plan's exit gate *clean room, upgrade path* (RELEASE.md §4) on the frozen, signed
Windows files of the second release candidate (freeze `4240522`; `knaif-1.4.0-windows-x64.zip`
`ce7f458d…`, the run script's pin; `knaif-1.4.0-windows-x64-setup.exe` `993ebbd6…`, built with it,
Authenticode-signed by Blackdeep Technologies Ltd with a timestamp), in Windows Sandbox with Smart
App Control enforcing. The harness is 1.3.0's (`evals/runs/2026-10-13_r130-windows_success/cleanroom`),
ported: the upgrade starts from the **published 1.3.0** installer (`c671a60a…`, the GitHub v1.3.0
asset), and one check is new (`fresh_pdfium_loads`, below).

**Status: PASS, 2026-11-04.** Automatic part 31/31 (`grade.txt`); wizard part M0–M5 all pass
(`wizard/`).

## 1. Automatic part

```bash
bash evals/runs/2026-11-02_r140-windows_success/cleanroom/stage.sh
# open the .wsb it prints; the Sandbox runs sandbox.ps1 by itself and ends with DONE
uv run --no-sync python evals/runs/2026-11-02_r140-windows_success/cleanroom/grade.py
```

In one Sandbox, Smart App Control enforcing at the start and at the end: the zip (the frozen binary,
`1.4.0`, `skills list`, every binary signed); the published 1.3.0 installed, its CLI (waiting at the
model download prompt) blocking the upgrade and still running, then the upgrade (same folder, one
Add/Remove row, no "already exists" prompt, no leftover libraries, every binary signed); a daemon
started from the installed 1.4.0, then a reinstall over it (the daemon stopped, no `knaif.exe`
running, `knaif.exe` rewritten and not held) and an uninstall over it (stopped, removed, `~\.knaif`
kept); a fresh install, real requests with no `--model` on the CPU, and an uninstall; `skills deps`
naming the tools the box lacks.

**New in 1.4.0's room: `fresh_pdfium_loads`.** The rotate request never loads PDFium, so a second
request does: "compress sample.pdf as small as possible". The Sandbox has no Ghostscript, so it
rasterizes through the bundled PDFium (`fresh-pdfium.log`: `compress (rasterize — removes selectable
text)`; on this 2 KB sample the raster is 114.2 KB, so knaif keeps the original, as 1.4.0 means it
to). Added because `check_macho_deps.py` found the macOS `libpdfium.dylib` declaring macOS 13 while
knaif's floor is 12 (1.3.0 shipped the same library), and no clean room had ever loaded PDFium; the
macOS 12 VM gets the same request.

**Result:** PASS 31/31 (`grade.txt`).

## 2. Wizard part (`wizard.ps1`)

```bash
bash evals/runs/2026-11-02_r140-windows_success/cleanroom/wizard_stage.sh
# open the .wsb it prints (a fresh Sandbox, after the first one is closed); it ends with DONE
```

`wizard.ps1` runs the staged installer with its wizard and saves each page (`wizard/page-NN.png`).
Inno's task and component lists are not exposed to UI Automation, so M1, M1b, M2 and M5 are judged
from the screenshots; the script's own `M1_tools_disabled_with_reason` line reads empty labels from
that list and failed, as it did in 1.3.0, while the screenshot shows the check holding.

| # | Check | Result |
|---|---|---|
| M0 | Smart App Control enforcing, the frozen installer | **PASS** (`wizard.log`): state 1, an unsigned probe refused, sha256 `993ebbd6…` |
| M1 | FFmpeg and Tesseract shown disabled, each with its reason | **PASS** (`page-05.png`): both greyed and unchecked, "(blocked by Smart App Control on this PC)"; the group says it "needs winget, which this PC does not have" |
| M1b | no CUDA component offered | **PASS** (`page-04.png`: core runtime and the two skills only; no control mentions CUDA) |
| M2 | no Ghostscript choice | **PASS** (`page-05.png`: FFmpeg, LibreOffice, Tesseract) |
| M3 | the finish page names the tools not installed and why | **PASS** (`page-08.png`) |
| M4 | `knaif skills deps` marks the same tools | **PASS** (`skills-deps.txt`) |
| M5 | nothing cut off or wrong, no older version named | **PASS**: all 8 pages read, every title says 1.4.0, the tasks page identical to 1.3.0's; setup exit 0 |

The Sandbox's built-in account (`WDAGUtilityAccount`) in the screenshots and logs is exempt from
the local-path rule (AGENTS.md, owner 2026-10-15).
