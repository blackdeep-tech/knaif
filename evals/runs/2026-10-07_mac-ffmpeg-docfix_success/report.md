# macOS ffmpeg L3/L4 on the release build — unchanged

Run 2026-10-07 02:00–08:47 on the M1 Pro, macOS 27.2 Beta 2, ffmpeg only. The `documents_105` fix
(#14) touched only the documents skill, but it changed the `knaif` binary, so the macOS ffmpeg cells'
binary fingerprint read as stale. This run re-measures them on the release build of
`feat/macos-support` (`8cbab23`), made with `just package-native metal` in a checkout outside `~`.
Its `knaif` is `3da78a28…`, byte-identical to the binary the documents fix run measured
(`evals/runs/2026-10-06_mac-documents-105-fix_success`), so documents and ffmpeg are now measured on
the same binary.

Procedure and rules: those of `evals/runs/2026-10-05_mac-l3l4-1.2.1_success`, limited to `ffmpeg`
(`run_all.sh`, `run_l3.sh`, `run_stages.sh`). The run read a fixed copy of the unsigned zip, not
`dist/`, because signing the release rewrites `dist/`.

| Cell | L4 outcome / knaif score | Safety | L4 verdict | L3 |
|---|---|---|---|---|
| 4B ffmpeg | 0.9384 / 0.9857 | 11/11 | ACCEPTED | PASS, 298/298, 0 port bugs, 0.00% |
| 1.7B ffmpeg | 0.9187 / 0.9780 | 11/11 | NOT ACCEPTED (`batch` 25/29 < 0.896) | PASS, 298/298, 0 port bugs, 0.00% |

- **L4:** all 1,952 rows (861 per model on Metal, 115 per model in the CPU sample) have the same plan
  and grade as on 1.2.1 before the fix. The CPU samples read 4B 0.913 and 1.7B 0.930 outcome
  accuracy, as before.
- **The 1.7B `batch` miss** is unchanged and stays with the owner.
- **Records:** `evals/acceptance/ffmpeg.json`'s two `macos|mtl` cells name this run. L3 reports are
  in `evals/parity/2026-10-07_mac-l3-release-<model>-ffmpeg/`.

With the documents fix run, every macOS L3 and L4 cell is now measured on the release binary. The
boards, logs and safety results stay on the Mac (utterances and local paths).
