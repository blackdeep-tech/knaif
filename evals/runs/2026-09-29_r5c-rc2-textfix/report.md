# Release 1.2.0 RC2 — the text fix: v2 model names, rebuilt, verified equivalent

The owner's manual install of the RC `71884fd` Windows installer (2026-09-29) found it offering
`knaif-qwen3-4b-v1` as the default download. A search of everything knaif ships found the v1 name
in five places, each a hand-typed copy of "the current model" with no test tying it to the model
manifest: the installer's default-on download task and its post-install text, the artifacts'
README quick start, NOTICE (which attributed only the v1 models) and two CLI help strings compiled
into the binary. Fixed in `36f9651`, with `test_shipped_model_names.py` failing on any such drift.
The post-install text also claimed CUDA was "the difference between GPU speed and CPU speed" on
the newest NVIDIA cards, which the 2026-09-25 re-measurement retired; corrected in the same commit.

**Owner decision:** fix the text, rebuild with the release scripts, keep the acceptance results,
and verify the rebuilt binaries with a short sample instead of re-running the evaluation.

## The rebuilt artifacts

| Artifact | RC `71884fd` (tested) | RC2 (this) |
|---|---|---|
| `knaif-1.2.0-windows-x64.zip` | `929b2df0…` | `b86cbfbf…` |
| `knaif-1.2.0-windows-x64-setup.exe` | `76328a0b…` | `6df5f520…` |
| `knaif-1.2.0-linux-x64.tar.gz` | `4fbba4a9…` | `cc622dbb…` |
| `knaif-1.2.0-linux-x86_64.AppImage` | `643f52f1…` | `210df1ed…` |

The CUDA payloads are unchanged (their bytes match the backend manifest).

**Byte-level equivalence.** Windows: `build-native-kind vulkan` recompiled only `knaif-cli`; 64
of the zip's 67 files are byte-identical to the RC, the three that differ being `knaif.exe`,
README.txt and NOTICE. `knaif.exe` has the same size and differs in 29 bytes: the two text bytes
and link metadata (the PE timestamp, the debug-directory timestamps and PDB id). Linux (container
build): same file lists in the tarball and the AppImage, the same three files differ; `knaif`
has the same size, `.rodata` differs in exactly the two text bytes, every other loaded section
(`.text`, `.data`, `.data.rel.ro`, `.got`, …) is byte-identical, and the rest is the
build-id note and LLVM's hash suffix on local symbol names.

## The sample check (pre-registered in `3591830`)

Per OS, the rebuilt binary, 4B on CUDA, ran 20 ffmpeg + 10 documents requests drawn before the run
and was compared with the accepted run of the tested binary (Windows T8, Linux T14):

| OS | ffmpeg | documents | Payload | Help text |
|---|---|---|---|---|
| Windows | 20/20 identical (plan decision, outcome, score) | 10/10 | cuda: ok | names v2 |
| Linux | 20/20 identical | 10/10 | cuda: ok | names v2 |

## The gate

`evalsuite equivalence` recorded `1.2.0-rc2-textfix` (`evals/acceptance/equivalences.json`):
the native source differs from `71884fd` only by `knaif-qwen3-4b-v1` → `knaif-qwen3-4b-v2` in
`apps/cli/src/main.rs` (checked by the command), no other fingerprinted input moved, and the
old → new binary per OS. **The gate is green against the RC2 binaries** (both skills
`supported`, every carried result marked `[equivalent: 1.2.0-rc2-textfix]`) and **stale against
the RC binaries**, which were not built from this source.

## The installer check on RC2 (T12 re-run)

The T12 checks, unchanged except the artifact hashes (pre-registered in `1c758bc`), on the RC2
`-setup.exe` (`6df5f520…`) and zip (`b86cbfbf…`) in Windows Sandbox: **PASS, 14/14** — clean-room
zip runs; 1.1.0 installs; the RC2 installer refuses while a 1.1.0 CLI runs, then upgrades in place
(one row, same folder, no leftover libraries); the OCR row runs with no `--model` on CPU with the
bundled PDFium and its output text checks out. Also on the RC2 artifacts: `smoke.sh` PASS (zip),
the Linux floor in both directions for the tarball and the AppImage, and the local-path scan of
all four artifacts clean.
