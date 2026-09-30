# Release 1.2.0 RC3 — supporting tools on Windows: rebuilt, sample-checked

The owner's manual install of RC2 in Windows Sandbox (2026-09-29) ticked every supporting tool and
`knaif skills deps` then reported all four MISS. Two defects: Sandbox has no winget, and setup let
the winget tasks be ticked and skipped them without a word; and Ghostscript, LibreOffice and
Tesseract never put themselves on PATH, so even a successful install stayed MISS. Fixed in
`c9218d8` (tools are also looked up in the install folders `skill.yaml` declares, and launched from
the path found; setup grays the tasks out without winget and reports each tool on its last page),
rebuilt from `7c534e6` with the release scripts.

Unlike RC2 this changes code on the execution path, so `evalsuite equivalence` (a text-fix check)
does not apply. **How the L3/L4 evidence carries over is the owner's decision**; this run gathers
the evidence. Rules pre-registered in `e6976d8` (run.sh header).

## The rebuilt artifacts

| Artifact | RC2 | RC3 (this) |
|---|---|---|
| `knaif-1.2.0-windows-x64.zip` | `b86cbfbf…` | `339d9eed…` |
| `knaif-1.2.0-windows-x64-setup.exe` | `6df5f520…` | `c42ba04d…` |
| `knaif-1.2.0-linux-x64.tar.gz` | `cc622dbb…` | `a884587c…` |
| `knaif-1.2.0-linux-x86_64.AppImage` | `210df1ed…` | `e7d4d570…` |

Windows: against RC2 exactly three of the zip's 67 files differ — `knaif.exe` and both
`skill.yaml`; the VC++ runtime DLLs and everything else are byte-identical (so the packaging's
"VCToolsRedistDir unset, filesystem scan" note changed nothing). CUDA payloads unchanged.

## The checks

| Check | Windows | Linux |
|---|---|---|
| Every tool resolves to its first PATH hit (the binary the accepted runs launched) | 5/5 commands | 6/6 commands |
| 20 ffmpeg (seed `20260929:rc3`) vs accepted run | 20/20 identical | 20/20 identical |
| 40 documents: 10 drawn + every compress / convert / OCR request | 40/40 identical | 40/40 identical |
| Placement / payload | CUDA0 / cuda: ok | CUDA0 / cuda: ok |
| `smoke.sh` | PASS (zip) | PASS (tarball, in WSL) |
| Floor, both directions | — | PASS tarball and AppImage |
| Path scan | clean (zip, installer) | clean (tarball, AppImage) |

"Identical" = same plan decision and same grade (outcome, knaif score) as the accepted run of the
tested binary (Windows T8, Linux T14), per `sample_check.py`.

## The gate

Owner (2026-09-29): carry the L3/L4 results over on this evidence. `evalsuite equivalence` gained a
`sampled` kind for it (`561ca29`, hardened after a Codex audit in `c11a1f0`): it maps the measured
`native` source and each skill's `bundle` to HEAD, only when `skill.yaml` changed under
`dependencies` and otherwise only native sources; it requires this run committed after its rules,
every OS and skill equivalent exactly once, one START then DONE per stage, and each new binary to be
the executable inside the artifact the run recorded. Recorded as `1.2.0-rc3-tools` (RC1 `71884fd` →
HEAD). **The gate is green against the RC3 binaries** (both skills `supported`; every carried result
marked `[equivalent: 1.2.0-rc3-tools (sampled)]`; the three owner-waived 1.7B cells EXCEPTED as
before), and `just check` passes.

**Re-recorded as `1.2.0-rc3-tools-tests`** after PR #61's Linux CI: the new folder-lookup unit
tests could not pass off Windows (fake executables without the execute bit; backslash patterns).
Fixed in the test module only (`ea8bf66`): since the RC3 build commit `7c534e6` the only native or
bundle change is inside `#[cfg(test)] mod tests` of `deps.rs`, so the RC3 artifacts are what this
source builds. `cargo test --workspace` passes on Windows and in the Linux build container; the
gate is green against the RC3 binaries through the new entry.

## Incidents (instrument, not artifact)

- **An orphaned first attempt.** The rules were first committed into an ignored path (`evals/**`),
  so the run was stopped to force-add them (`e6976d8`) and restarted. Stopping killed the shell but
  not the harness under it: the two Windows runs overlapped for ~1.5 min in the same folder and
  fixtures, and one reported `documents_073` ("shrink sample.pdf") with the same plan but no
  output file. Reproduced outside the harness with the exact plan (mock backend), RC2 and RC3 both
  write the output 3/3; quality `high` is lossless and does not even use Ghostscript. The
  contaminated outputs were discarded and the Windows stage re-run alone from a clean folder under
  the same rules: the numbers above.
- `smoke.sh` on the tarball fails under Git Bash on Windows (it cannot create the tarball's
  symlinks); re-run in WSL, PASS (`linux_artifacts/checks.log`).
- `win_tools.txt` is not committed: it echoes `skills deps`, whose paths include the builder's home
  directory. The result is in the table above.
