# L4 — ffmpeg on the shipped native binary, after the N4 crop fix, 2026-09-11

Fourth L4 run, after the aspect-crop filter was fixed on **both** runtimes.
**Supersedes `../2026-09-11_l4-ffmpeg-n1n2_success/`.** Clean tree, `git_sha 0698f5d`, `CUDA0`.

## Verdict: NOT ACCEPTED — 2 of 36 unmet (was 4, then 11, originally 16)

| metric | run 1 | run 2 | run 3 | **run 4** | Python | floor | |
|---|---|---|---|---|---|---|---|
| `outcome_accuracy` | 0.8123 | 0.8430 | 0.88194 | **0.88666** | 0.9020 | 0.88201 | ✓ |
| `avg_knaif_score` | 0.9827 | 0.9835 | 0.9707 | **0.9709** | 0.9738 | 0.9538 | ✓ |
| `schema_validity` | 0.8867 | 0.9174 | 0.9587 | **0.9629** | 0.9847 | — | |
| coverage | 1.0 *(false)* | 1.0 | 1.0 | **1.0** | — | 1.0 | ✓ |
| safety | 5/9 +phantom | 6/9 | 6/9 | **6/9, 0 breaches** | 6/9 | 9/9 | ✗ |

**The aggregate floor is cleared.** Outcomes: `plan` 560 → **564**, `error` 35 → **31**.

## Predictions made before the run, and what happened

Recorded in advance so the fix could be checked rather than asserted:

| predicted | actual |
|---|---|
| `outcome_accuracy` ≈ 0.8867 | **0.88666** |
| `crop` 0.750 → 1.000 | **1.000** |
| `geometry` 0.833 → 1.000 | **1.000** |
| `resize` 0.883 → ~0.914, clears its floor | **0.914** ✓ |
| `edge` **unchanged** at 0.774 | **0.774** |

`edge` was predicted not to move because none of its four native-only failures is a crop row —
that it held is the control showing the gain came from the fix and not from run-to-run drift.

## What remains

1. **`edge` 0.774 against a 0.780 floor — one row of 53.** Its native-only failures are the
   1-frame trim (`ffmpeg_161`, two phrasings), the lossless re-encode where output equals input
   (`ffmpeg_175`), and one generation difference (`ffmpeg_261`, where native's model emitted an
   invalid arg and Python's did not — not a port defect). Fixing any of the first three clears it.
2. **Safety 6/9** — the three *over*-refusals, identical to Python's result on the same corpus.
   No score movement can touch this: safety admits no tolerance. Decided and planned in
   `docs/plans/2026-09-11-reject-clarify-taxonomy.md`.

## Note on what this run measures that Python's does not

The N4 fix improves the product on **both** runtimes, but only native's numbers move. Python
scored these rows 1.0 before the fix and scores them 1.0 after, because the `success` verifier
grades them on command *text* (`filter:crop` present) and the Python eval harness does not
propagate ffmpeg's exit code. Native's lane does. For four rows of this corpus, **the shipped
runtime has been the more honest instrument than the reference it is measured against** — which
is worth remembering when reading the remaining 1.5-point gap to Python as a native deficit.
