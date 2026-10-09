# macOS L4 re-run on `af05956` — identical to the first run, row for row

Run 2026-10-03 21:18 – 2026-10-04 02:38 on the same M1 Pro (macOS 27.2), at the Mac contributor's request:
the L4 half of the Mac's list (steps 5–6, C4/D14) again, now on the merged commit `af05956` of
`feat/macos-support` on the fork (the first run, `evals/runs/2026-10-03_mac-l4_success`, recorded
`880a576`, a commit the squash merge of #1 replaced, so no branch contains it). L3 parity was not
re-run.

Same script and decision rules as the first run (`run_all.sh`, unchanged apart from the folder
name and a header line; `run_stages.sh` runs the four stages in order). A fresh checkout outside `~`,
`just package-native metal`: the staged `knaif` is
**byte-identical** to the first run's (`d3e91b27…`); only the zip's own sha256 differs (`4d315984…`
vs `b07bbf5a…`), because a zip records timestamps. Between `880a576` and `af05956` only tests,
`conftest.py` and `scripts/check_no_local_paths.py` changed — no runtime, skill, prompt or model.

## Result

`compare_runs.py <first run> <this run>` (output: `comparison.txt`):

| Cell | Requests | Outcome | Knaif score | Safety | Verdict | Plans differ | Grades differ |
|---|---:|---|---|---|---|---:|---:|
| 4B Metal ffmpeg | 861 | 0.9384 = 0.9384 | 0.9857 = 0.9857 | 11/11 = 11/11 | ACCEPTED = ACCEPTED | 0 | 0 |
| 4B Metal documents | 164 | 0.9756 = 0.9756 | 0.9851 = 0.9851 | 9/9 = 9/9 | ACCEPTED = ACCEPTED | 0 | 0 |
| 1.7B Metal ffmpeg | 861 | 0.9187 = 0.9187 | 0.9780 = 0.9780 | 11/11 = 11/11 | NOT ACCEPTED = NOT ACCEPTED | 0 | 0 |
| 1.7B Metal documents | 164 | 0.9634 = 0.9634 | 0.9945 = 0.9945 | 9/9 = 9/9 | ACCEPTED = ACCEPTED | 0 | 0 |
| 4B CPU sample ffmpeg | 115 | 0.9130 = 0.9130 | 0.9789 = 0.9789 | — | not graded | 0 | 0 |
| 4B CPU sample documents | 35 | 1.0000 = 1.0000 | 0.9926 = 0.9926 | — | not graded | 0 | 0 |
| 1.7B CPU sample ffmpeg | 115 | 0.9304 = 0.9304 | 0.9694 = 0.9694 | — | not graded | 0 | 0 |
| 1.7B CPU sample documents | 35 | 0.9429 = 0.9429 | 0.9905 = 0.9905 | — | not graded | 0 | 0 |

**All 2,350 requests produced the same plan and the same grade in both runs**, on `MTL0` and on
`CPU`. Greedy decoding with this binary and these GGUFs is deterministic on this machine. The 1.7B
ffmpeg miss repeats on the same rows — `batch` 25/29 against 0.896 — so it is a property of the
model on Metal, not run-to-run noise; it stays with the owner (first run's report, and 1.2.0's
Windows Vulkan 1.7B, which missed the same threshold with the same score).

Latency, p50 time to artifact (plan rows, warmup excluded): Metal 4B 7979 → 8009 ms, 1.7B 4052 →
4048 ms on ffmpeg (within 1%); CPU 4B 30.6 → 32.6 s (+7%), 1.7B 16.8 → 17.0 s.

## Records

`accept-native` re-recorded the four `<model>|macos|mtl` cells in `evals/acceptance/*.json`. The
only change is each cell's `run`, which now names this folder; every evidence fingerprint, score
and summary is identical to the first run's record. Verdicts: `<model>/metal/<skill>_verdict.txt`.
The boards, logs and safety results stay on the Mac (utterances and local paths), as in 1.2.0.
