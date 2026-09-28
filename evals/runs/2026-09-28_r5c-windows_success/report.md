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

## T7 re-run on RC `71884fd` — PASS on all four

Run 2026-09-28 14:06–15:02, packaged zip `929b2df0…` built from `71884fd`, installed CUDA
payload, placement probed (CUDA0, all 37 layers, every part). Rules unchanged; the comparator
now also counts "same plan, different outcome" as a port bug (`f72a2d8`) and compares the plans
when neither side renders a command (`c8e041d`), so this run is held to a stricter instrument
than the first pass.

| L3 | Counts (match / mismatch / not comparable / port bug) | Plan disagreement | Verdict |
|---|---|---|---|
| 4B ffmpeg | 297 / 0 / 31 / 0 | 0.00% (bound 4.11%) | PASS |
| 4B documents | 142 / 1 / 0 / 0 | 0.70% (bound 1.83%) | PASS |
| 1.7B ffmpeg | 299 / 0 / 29 / 0 | 0.00% (bound 4.11%) | PASS |
| 1.7B documents | 142 / 1 / 0 / 0 | 0.70% (bound 1.83%) | PASS |

Reports: `evals/parity/2026-09-28_r5c-l3-rc2-*`. Prediction (written before the run): 0 port
bugs, ffmpeg ~1.4% (4B) / ~0.3% (1.7B), documents ~0.7%. The ffmpeg disagreement came out lower,
because all of the first pass's ffmpeg "mismatches" were the missing gate.

The one documents disagreement on both models is `documents_057`: native plans
`add_page_numbers` with `position: bottom-center`, Python with `position: bottom`.

**After the run** (instrument only, nothing packaged): `875cd12` makes L3 read each CLI's exit
status and pass the request after `--`. A scan of these four reports found no native failure
hidden as a match; `ffmpeg_053` (`rm -rf /`) had failed on usage on both sides and so tested
nothing, which the `--` fix repairs. Neither changes a verdict here. The gate then read the 1.7B
cells as stale ("model"): it compared every cell with the recommended (4B) model. Fixed in
`10cd496`; `check-gate` now reads L3 valid for both skills and both models.

## T8 — Windows GPU L4 cells on RC `71884fd`: 7 of 8 accepted

Run 2026-09-28 15:15–18:23, packaged zip `929b2df0…`, one process per utterance executing for
real, graded by `success`; placement measured per cell; safety run on the same binary
(`8f1ac7a0…`), model and backend as each cell's quality half (provenance checked by
`accept-native`).

| Cell | Skill | Outcome (floor / Python) | Avg score (floor / Python) | Safety | Verdict |
|---|---|---|---|---|---|
| 4B CUDA | ffmpeg | 0.9431 (0.9231 / 0.9431) | 0.9841 (0.9641 / 0.9841) | 11/11 | ACCEPTED |
| 4B CUDA | documents | 0.9817 (0.9556 / 0.9756) | 0.9803 (0.9800 / 0.9818) | 9/9 | ACCEPTED |
| 4B Vulkan | ffmpeg | 0.9408 (0.9231 / 0.9431) | 0.9858 (0.9641 / 0.9841) | 11/11 | ACCEPTED |
| 4B Vulkan | documents | 0.9756 (0.9556 / 0.9756) | 0.9873 (0.9800 / 0.9818) | 9/9 | ACCEPTED |
| 1.7B CUDA | ffmpeg | 0.9210 (0.8999 / 0.9199) | 0.9785 (0.9585 / 0.9785) | 11/11 | ACCEPTED |
| 1.7B CUDA | documents | 0.9634 (0.9500 / 0.9634) | 0.9945 (0.9745 / 0.9945) | 9/9 | ACCEPTED |
| 1.7B Vulkan | ffmpeg | 0.9187 (0.8999 / 0.9199) | 0.9780 (0.9585 / 0.9785) | 11/11 | **NOT ACCEPTED** |
| 1.7B Vulkan | documents | 0.9634 (0.9500 / 0.9634) | 0.9945 (0.9745 / 0.9945) | 9/9 | ACCEPTED |

**Both 4B GPU cells are accepted**, so no release-blocking entry failed. The 4B CUDA documents
average passes by 0.0003 (0.9803 against 0.9800); on Vulkan the same skill clears it by 0.0073.

**1.7B Vulkan ffmpeg: one required slice short.** `batch` outcome 0.862 against a floor of 0.896
(25 of 29 where 26 were needed). The whole difference from the 1.7B CUDA cell is one utterance,
`ffmpeg_229[4]` ("批量将所有视频转换为HEVC", batch-convert all videos to HEVC): CUDA plans
`convert_video *.mp4 → hevc`, Vulkan asks a clarifying question. Same binary, prompt and
pipeline; the model tips differently on a near-tie token between the two backends, in Chinese,
a known weak spot. CUDA holds the slice at 26/29, the narrowest pass. Safety is 11/11.

**Owner decision (2026-09-28): not a blocker.** Released with the cell recorded as NOT ACCEPTED
and the gap in the release notes. `check-gate` keeps reading the cell as failing; how an owner
exception is recorded without turning it into a pass is settled at T17.

## T9a — 4B CPU confirmation: 0 flips in 130, the 2026-09-25 CPU plans stand

Run 2026-09-28 19:04–19:35 on RC `71884fd`'s packaged artifact, GPU hidden (placement CPU, all 37
layers), one process per utterance as shipped. The pre-drawn sample (seed 20260928, committed
before the run: 100 ffmpeg + 30 documents rows that reach the model) was compared with the
2026-09-25 CPU plans at the decision level by `t9a_confirm.py`: **0 decision flips** on both
skills; none missing, none set aside. Prediction written before the run: 0 flips, with a ~50%
risk of at least one. By the agreed rule the 2026-09-25 CPU plans stand for the release binary;
T9b composes the 4B CPU cell from the CUDA cell and the rows those plans cannot vouch for.
Sampling does not certify the unsampled rows (Codex's reservation, on record; the owner's rule
stands).

## T9b — 4B Windows CPU cell, composed: ACCEPTED on both skills

Run 2026-09-28 21:46–22:05 on RC `71884fd`'s packaged artifact (binary `8f1ac7a0…`), GPU hidden
(placement CPU, all 37 layers), one process per utterance, `success` verifier. `rerun-set`
compared the 4B CUDA cell (T8) with the 2026-09-25 CPU plans on the full plan (every step field)
and chose the rows the CPU plans cannot vouch for: **ffmpeg 60** (44 planned differently, 16 with no
reused plan) and **documents 4** (1 + 3: `documents_020 083 084 105`). The plan's estimate (37 + 1)
counted decision-level differences only; the full-plan rule is stricter. Those rows ran on the CPU,
`compose` replaced them in the CUDA board, and `accept-native` graded the result with safety run on
the CPU binary.

| Cell | Skill | Outcome (floor / Python) | Avg score (floor / Python) | Safety | Verdict |
|---|---|---|---|---|---|
| 4B CPU (composed) | ffmpeg | 0.9419 (0.9231 / 0.9431) | 0.9857 (0.9641 / 0.9841) | 11/11 | ACCEPTED |
| 4B CPU (composed) | documents | 0.9756 (0.9556 / 0.9756) | 0.9818 (0.9800 / 0.9818) | 9/9 | ACCEPTED |

Coverage 861/861 and 164/164. The 60 re-run ffmpeg rows scored 0.883 outcome on their own (they
are the rows where the backends plan differently, so the hard ones). The boards and the acceptance
records carry `composed` with both sources' sha256 and provenance; `gate` names the cell "composed,
not a full run".

Two notes for T17, neither changing a verdict:

- The 4B CUDA documents board (T8, the base here) records `git_dirty: true`: the report and plan
  were being edited while T8 ran. The evidence fingerprints, not that flag, decide staleness, and
  they match.
- An acceptance record keeps only the verdict's first line, so the 1.7B Vulkan ffmpeg record reads
  "1 of 45 thresholds unmet:" without naming the batch slice. The owner-exception record at T17
  should carry the unmet threshold.
