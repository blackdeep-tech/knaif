# L4 re-run — v2 on the shipped native binary

Same script, prediction and decision as the invalid first attempt (`../2026-09-24_l4-v2-aligned_success/`),
with its two environment faults fixed: the lane names its model (`public_name`), and PDFium is supplied via
`KNAIF_PDFIUM_PATH` (pypdfium2's copy — a stand-in for the bundling the installer decision calls for).
`target/release-cuda`, freshness-checked; local `knaif-qwen3-4b-v2` GGUF; aligned config; code `fa0c6ff`.

## Verdict

| | outcome (native / bar / python) | knaif (native / bar / python) | safety on binary | L4 |
|---|---|---|---|---|
| ffmpeg | 0.9283 / 0.9177 / 0.9377 | 0.9714 / 0.9637 / 0.9837 | 11/11 | **ACCEPTED 45/45** |
| documents | **0.9817** / 0.9556 / 0.9756 | 0.8593 / 0.9800 / 0.9978 | 9/9 | NOT ACCEPTED — 1 of 42 (knaif) |

ffmpeg reproduced the first attempt to four decimals.

## Why documents' knaif is low — two defects, neither the model

Native makes the same or better decisions than Python (outcome 0.9817 vs 0.9756). Of the 41 rows scoring
below 1.0:

1. **Grading gap (~25 rows).** Read-only tools (`inspect_document`, `extract_text`, `find_in_document`)
   print their answer; the native lane grades files on disk and reads no printed answer, so every
   expected value arrives as `None` (`pages`, `format`, `has_text_layer`, `matches_count`, `text_missing`).
   Checked by hand: `inspect sample.png` prints `png: 1 page(s), … text_layer=false`, `find Gamma in
   sample.pdf` prints the one page-3 match — both correct.
2. **Silent overwrite, both runtimes (16 rows).** A documents tool's default output name that already
   exists is overwritten: `sample.txt → sample.md`, `protect → sample-protected.pdf`, `docx → sample.pdf`
   all replace fixtures. Reproduced on Python too (checksums changed). The Python eval grades the reported
   path, so it never noticed; the native lane counts new files, so it saw none. For a user, "convert
   notes.txt to markdown" would destroy an existing notes.md. v1 has the same defect.

## Prediction

- safety 100% on the binary — held.
- documents ACCEPTED — failed, on the two defects above.
- ffmpeg 0.92–0.935, above its bar — held (0.9283, ACCEPTED 45/45).

## Decision

The rule says any L4 failure blocks the publish. Owner's call (2026-09-24): fix first, then publish —
fix the overwrite in both runtimes, teach the native lane to read read-only answers, re-run documents L4.
