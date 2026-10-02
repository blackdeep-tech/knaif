# 1.2.1 — sampled equivalence and release clean room (2026-10-02)

**Verdict: equivalent on the sample, every stage; clean room PASS 24/24 with Smart App Control
enforcing.** The 1.2.0 L3/L4 results carry over to 1.2.1 through the sampled equivalence
`1.2.1-sampled`, by owner decision (patch lane, "L4 sampled").

## What was tested

- **Artifacts:** the frozen 1.2.1 build from `7e39b45` (clean tree): the Windows zip and
  installer signed by Blackdeep Technologies Ltd (`knaif-1.2.1-windows-x64.zip`, sha256 `ae2531a9…`),
  and the Linux tarball from the release container (`ff0ecc66…`).
- **CUDA payload:** the 1.2.1 manifest's files: Windows = 1.2.0's measured `ggml-cuda.dll`
  (`43dd6bc2…`), Authenticode-signed (`a3a9a8cd…`); Linux = the unchanged 1.2.0 files. Placed by
  hand without a receipt and checked file by file against the manifest (`*_payload.txt`, 10/10 and
  6/6). Every run was placed on CUDA0.
- **Rules:** `run.sh`, committed in `aae0e0a` before any result. Model `knaif-qwen3-4b-v2`, one
  process per request, `success` verifier.
- **Sample** (seed 20261002): 20 ffmpeg and 10 documents requests at random from those the accepted
  run planned, plus every planned chain request, ffmpeg's `reverse_video` phrasings and every
  documents phrasing that mentions a password: **47 ffmpeg, 31 documents**.

## Results

| Stage | Compared with | ffmpeg | documents |
|---|---|---|---|
| `win` — Windows binary executing for real | T8, `2026-09-28_r5c-windows_success` | 47/47 identical | 31/31 identical |
| `linux` — Linux binary executing for real | T14, `2026-09-29_r5c-linux_success` | 47/47 identical | 31/31 identical |
| `python` — both runtimes, rendered commands | L3, `2026-09-28_r5c-l3-rc2-4b-<skill>` | 43/43 identical | 31/31 identical |

"Identical" means the same plan decision, outcome and knaif score as the accepted 1.2.0 run (win,
linux), or the same status and the same outcome kind and commands on both runtimes as the accepted
L3 row (python). `python_tree.json` records the Python tree the python stage ran on; the gate binds
the equivalence to it.

**A first Linux attempt was discarded, by owner decision.** It reported documents 2/31: the grader
could not import `pypdf`, because an exact `uv sync` in the WSL checkout had dropped the
`documents` dependency group, so every row graded by opening a PDF scored 0 with the same plan as
1.2.0 (ffmpeg was 47/47). It is kept unchanged in `linux-attempt1-grader-env/` with `WHY.txt`; the
stage was re-run after `uv pip install --group documents --group documents-ocr`.

## Release clean room (`t12/`)

Windows Sandbox, no network, no GPU, no developer tooling; Smart App Control **enforcing**. 24/24:
the zip runs and every binary in it is validly signed; the published, unsigned 1.2.0 installer is
**blocked outright** by Smart App Control; with enforcement off it installs, a running 1.2.0 CLI
holds the mutex and the 1.2.1 upgrade is refused while it runs; with enforcement back **on**, the
installed 1.2.0 `knaif.exe` is blocked, and the signed 1.2.1 installer upgrades it in place (one
Add/Remove row at 1.2.1, publisher `Blackdeep Technologies Ltd`, same folder, no leftover library,
17 Blackdeep + 4 Microsoft signatures, all valid); B5 locks a PDF that opens with `p\ss` and not
`p/ss`. OCR with no `--model` on the CPU backends and the bundled PDFium produces the expected text.

**Found:** under enforcement, OCR fails because Smart App Control blocks Tesseract's unsigned DLLs
(`libarchive-13`, `libcurl-4`, `libleptonica-6`, `libtiff-6`; UB-Mannheim build); the Code
Integrity log names nothing of knaif's. Recorded as a known issue in the 1.2.1 CHANGELOG.

Also on the frozen artifacts: Linux floor confirmed in both directions for the tarball and the
AppImage (Ubuntu 22.04 runs, 20.04 refuses); `installers/smoke.sh` PASS; every PE import and ELF
`DT_NEEDED` staged or system-provided; no local paths in the unpacked artifacts.

## Supporting tools under Smart App Control (`tools-sac/`, networking ON)

Follow-up to the Tesseract finding, because the clean room ran offline, where Smart App Control
cannot consult Microsoft's cloud reputation. Windows Sandbox, network on, enforcement on, each tool
as a user gets it (installers verified against winget's sha256 where winget has one):

| Tool | Result |
|---|---|
| FFmpeg, Gyan 9.0.2 zip (winget) | `ffmpeg.exe` and `ffprobe.exe` unsigned and **blocked**; the ffmpeg skill fails |
| Tesseract, UB-Mannheim 5.4.0 (winget) | installer **blocked** (its unsigned NSIS `System.dll`); signature also expired |
| Ghostscript, Artifex 10.07.1 installer | installer **blocked** the same way; `ArtifexSoftware.GhostScript` is no longer in winget |
| LibreOffice 26.8.0.3 MSI (winget) | installs and runs; knaif's docx→pdf succeeds |

Cloud reputation did not rescue any of them. knaif's own signed files were never blocked. Recorded
as 1.2.1 known issues; the fix (bundling and signing the tools knaif depends on) is 1.3.0 work.

## Not committed

The per-request boards and logs of the `win` and `linux` stages (`*_success.json`, `*.log`) stay
on the machines that ran them: the Linux ones carry the WSL checkout's absolute paths, which
`scripts/check_no_local_paths.py` did not flag (it knows the Windows home only). The verdict files
summarize them.
