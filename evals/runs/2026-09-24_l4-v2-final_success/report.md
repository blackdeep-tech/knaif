# L4 final — v2 on the shipped native binary, after the two documents fixes

The re-run (`../2026-09-24_l4-v2-aligned-rerun_success/report.md`) blocked documents on two defects,
neither the model. Both were fixed (`f8c38f8`: never overwrite an existing file with a derived name,
both runtimes; `2149cbc`: the CLI dumps read-tool results and the lane grades them and counts rewritten
files). Both skills re-run because the CLI and the lane changed. `target/release-cuda`, freshness-checked
(built after the newest native source, `apps/cli/src/main.rs`); local `knaif-qwen3-4b-v2` GGUF; aligned
config; PDFium via `KNAIF_PDFIUM_PATH` (pypdfium2's copy); code `2149cbc`, only the workbench notebook
dirty.

## Verdict

| | outcome (native / bar / python) | knaif (native / bar / python) | safety on binary | L4 |
|---|---|---|---|---|
| ffmpeg | 0.9283 / 0.9177 / 0.9377 | **0.9837** / 0.9637 / 0.9837 | 11/11, 0 breaches | **ACCEPTED 45/45** |
| documents | 0.9817 / 0.9556 / 0.9756 | **0.9946** / 0.9800 / 0.9978 | 9/9, 0 breaches | **ACCEPTED 42/42** |

Coverage 1.0 on both.

## What moved

- **documents knaif 0.8593 → 0.9946.** Outcome unchanged (0.9817, still above Python's 0.9756). Two rows
  remain below 1.0: `documents_042` (0.67, the config-dependent row the snapshot already carries) and
  `documents_142` (0.5).
- **ffmpeg knaif 0.9714 → 0.9837**, outcome unchanged. Not predicted: the lane now counts files a run
  rewrote, so rows whose explicit output lands on an existing fixture are graded instead of scored as a
  missing artifact. It now matches Python's knaif to four decimals. 19 utterances remain below 1.0.

## Prediction (in `run_all.sh`)

- documents knaif ≥ 0.98, ACCEPTED — **held** (0.9946).
- ffmpeg unchanged (0.9283 / 0.9714) — outcome held; knaif rose, for the reason above.

## Decision (fixed before the run)

Both skills L4 ACCEPTED with safety 100% on the binary → the v2 publish is evidence-backed. It still
waits for the owner's explicit go-ahead.
