# Documents semantic replay

Replayed 22 captured incumbent plans against copies of the same fixtures, with no model
inference. Checks inspect actual PDF rotations, page text/order, watermark text on every
page, page numbers, and encryption with the requested password (including rejection of
a wrong password). They do not change the main corpus or its scoring.

**21/22 pass.** documents_036 (`rotate sample.pdf 90 degrees`) incorrectly restricts
rotation to page 1. Actual rotations are [90, 0, 0]; the whole-document request and the
skill's default page-selection behavior imply [90, 90, 90]. The benchmark's tool/existence
criteria miss this error and score the row 1.0.

All 22 negative controls, substituting the unchanged source PDF, fail the independent
checks. This is a selected semantic spot-check, not an exhaustive acceptance gate.
`report.json` preserves plans, results, and artifact paths. Reproduce using
`../2026-09-17_audit-control-v1_success/documents_semantic_replay.py` with the original
documents scoreboard, its fixture directory, and a new `--save` directory.
