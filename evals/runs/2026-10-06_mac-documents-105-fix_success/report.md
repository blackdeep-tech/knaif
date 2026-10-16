# documents_105 fix on the Mac — L3 passes, nothing else moves

Run 2026-10-06 01:54–02:52 on the M1 Pro, macOS 27.2 Beta 2, documents only. The fix
(`fix(documents): validate reorder_pages order in the native dry run`, `4d71261`) makes native's dry
run for `reorder_pages` validate `order` against the page count, as its execution and Python already
do. This run shows the port bug gone and the rest unchanged. Procedure and rules: those of
`evals/runs/2026-10-05_mac-l3l4-1.2.1_success`, limited to `documents` (`run_all.sh`, `run_l3.sh`,
`run_stages.sh`).

Build: `just package-native metal` of `4d71261` outside `~`; new `knaif` sha256 `3da78a28…` (1.2.1
before the fix: `c67fcb99…`).

| Cell | L4 outcome / knaif score | Safety | L4 verdict | L3 (before the fix) |
|---|---|---|---|---|
| 4B documents | 0.9756 / 0.9851 | 9/9 | ACCEPTED | **PASS**, 0 port bugs, 0.70% (was FAIL, `documents_105`) |
| 1.7B documents | 0.9634 / 0.9945 | 9/9 | ACCEPTED | PASS, 0 port bugs, 0.70% (unchanged) |

- **L4:** all 164 rows per model have the same plan and grade as before the fix. The executing path
  was already right: `documents_105` still fails on execution with `Unrecognized page reference:
  "original"`, as it should.
- **L3:** `documents_105` now matches, because both runtimes reject the order in the dry run. The
  0.70% left is `documents_057`, as in every earlier run.
- **CPU sample** (35 documents rows per model, Metal backend removed): 4B 1.000, 1.7B 0.943 outcome
  accuracy, as before.

Reports: `evals/parity/2026-10-05_mac-l3-docfix-<model>-documents/` (named with the UTC date of the
run). `evals/acceptance/documents.json`: the two `macos|mtl` cells name this run.

**Not re-run:** ffmpeg. The fix touches only the documents skill, but the macOS ffmpeg cells record the
`knaif` binary's fingerprint, which changed, so they will read as stale until ffmpeg is run on this
binary. L3/L4 for documents on Windows and Linux also need re-running (the owner's analysis).

The boards, logs and safety results stay on the Mac (utterances and local paths).
