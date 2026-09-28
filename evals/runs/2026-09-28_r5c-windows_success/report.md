# R5c Windows, second attempt — T7 (L3) on RC `1fa823d`: ffmpeg FAIL, diagnosed and fixed

Run: `run_all.sh t7`, 2026-09-28 11:59–12:56, packaged `knaif-1.2.0-windows-x64.zip` (`ed674018…`)
built from the re-frozen RC `1fa823d`, installed CUDA payload, placement measured by probe (CUDA0,
all 37 layers, every part). Rules and bounds as pre-registered in `run_all.sh` (commit `e6fea79`,
hardened in `1eda1ab` before launch).

## Verdicts

| L3 | Counts (match / mismatch / not comparable / port bug) | Plan disagreement | Verdict |
|---|---|---|---|
| 4B ffmpeg | 284 / 4 / 31 / **9** | 1.35% (bound 4.11%) | **FAIL** (port bugs) |
| 4B documents | 143 / 0 / 0 / 0 | 0% (bound 1.83%) | PASS |
| 1.7B ffmpeg | 288 / 1 / 29 / **10** | ≤ bound | **FAIL** (port bugs) |
| 1.7B documents | 143 / 0 / 0 / 0 | 0% | PASS |

Reports: `evals/parity/2026-09-28_r5c-l3-{4b,1.7b}-{ffmpeg,documents}/`. The acceptance records
hold these verdicts as run; all are stale once the fixes below change the native binary.

## Diagnosis

**The 19 "port bugs" were one instrument defect.** All are concat rows (`ffmpeg_033 034 082 215 231
242 243 244 252`, plus `228` on the 1.7B). Python's argv dump listed each concat command twice:
`run_concat` stores its one command both at the top level and in `outputs`, and
`knaif.app.rendered_argvs` read both. ffmpeg ran once. The commands agree once the duplicate is
dropped (Codex checked all nine 4B rows independently). Fixed in `ded49d7`; not packaged, not
fingerprinted.

**The "mismatches" hid five real port bugs** (Codex audit, 2026-09-28). The comparator called a row
a port bug only when both sides rendered commands, so same-plan rows where one side asked and the
other ran were counted as model disagreement:

- `ffmpeg_128 257 288 289` (4B): the model named an input the user never wrote (`mov`,
  `silent clip`, `4K_clip.mp4`). Python's NL clarify gate asks "Which mov did you mean?"; native
  had no port of that gate, ran, and failed with "input not found: mov". The corpus expects the
  question; in the first attempt's 4B CUDA L4 native got 5 of those 15 utterances wrong this way.
  The gate's grounded-args half (an invented password) was unported too.
- `ffmpeg_136` (1.7B): step 1 of a chain needed a clarify (compress `target: 1080p`, no such
  platform). Native printed the question and ran step 2 anyway, on a file step 1 never produced;
  a real run then failed with "input not found: clip_1080p.mp4". Python stops at the clarify.

Fixed in `a3fea7d` (native NL clarify gate with a 24-case L2 contract on both runtimes, and a
clarify now ends the plan), and the comparator counts any outcome difference on an equivalent
plan as a port bug (`f72a2d8`). Native changed, so this needs a re-freeze, rebuilt artifacts and
a re-run of T7.

Also found, not fixed (interactive only, the lanes run with `--yes`): declining the confirmation of
one step lets native continue to the next, where Python stops.
