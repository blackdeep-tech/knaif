# L4 — v2 on the shipped native binary (first attempt: invalid, two environment faults)

`target/release-cuda` (rebuilt and freshness-checked by the script), local
`knaif-qwen3-4b-v2-q4_k_m.gguf`, aligned compute config, `success`, every utterance, safety on the
binary. Code `44e05a4`.

## What the gate said

| | outcome (native / bar / python) | knaif (native / bar / python) | verdict |
|---|---|---|---|
| ffmpeg | 0.9283 / 0.9177 / 0.9377 | 0.9714 / 0.9637 / 0.9837 | NOT ACCEPTED — 1 of 45: **identity** |
| documents | 0.9390 / 0.9556 / 0.9756 | 0.8526 / 0.9800 / 0.9978 | NOT ACCEPTED — 4 of 42: identity, both aggregates, `ocr` 7/7 |

## Why neither verdict is about the model

1. **Identity.** The `native-cli` lane lost its `public_name` in the 2026-09-15 repoint, so the
   scoreboard recorded the model as `None` and the gate refused to compare it with the
   `knaif-qwen3-4b-v2` baseline — correctly: a run that cannot name its model is not evidence.
   ffmpeg cleared every one of its 44 quality thresholds; identity was its only failure.
2. **PDFium.** All 7 OCR utterances failed with "could not load the PDFium library". No PDFium
   library is staged beside any native build on this machine (the only copy is pypdfium2's, in
   `.venv`). With `KNAIF_PDFIUM_PATH` pointed at it, the same binary OCRs `sample-scanned.pdf`
   correctly. On the 157 non-OCR documents utterances native scores **0.9809 against Python's
   0.9745** — they differ on one row, which native gets right.

This was documents' first native L4 measurement ever.

## Prediction, written before the run

- safety 100% on the binary — see the safety JSONs; not reached by the gate as a failure.
- documents ACCEPTED — **failed**, for the environment reasons above; on non-OCR rows native
  matches or beats Python.
- ffmpeg 0.92–0.935, above its bar, chain slices the risk — **held** on quality (0.9283, every
  slice met).

## Next

Fix the lane's `public_name`, supply PDFium via `KNAIF_PDFIUM_PATH` (a stand-in for the bundling
the installer decision calls for — TODO added), and re-run:
`../2026-09-24_l4-v2-aligned-rerun_success/`.
