# Promotion verdict — sft-v4 over sft-v3 (T7 precondition)

**Decision: PROMOTE `qwen3-4b-sft-v4-flat` as the public `knaif-qwen3-4b-v2`.**
Recorded 2026-09-17, before any snapshot was re-locked.

T7 requires this order deliberately. The promotion question is *candidate vs the shipped
model*, and once a snapshot is re-locked over the candidate the comparison becomes the
candidate against itself. So the verdict is taken on the arms as measured here — both under
scoring policy 2, on one instrument, same corpora, same fixtures — and only then is the
baseline moved.

## Aggregates

| | control `sft-v3` (shipped) | treatment `sft-v4` | delta |
|---|---:|---:|---:|
| ffmpeg outcome, n=851 | 0.916569 | **0.936545** | **+2.00 pp** |
| ffmpeg avg_knaif | 0.983390 | 0.982131 | −0.13 pp |
| documents outcome, n=164 | 0.963415 | **0.981707** | **+1.83 pp** |
| documents avg_knaif | 1.000000 | 1.000000 | ±0 |
| ffmpeg safety gate | 11/11 | 11/11 | — |
| documents safety gate | 9/9 | 9/9 | — |

Coverage 1.0 on all four records.

## Required slices — the part that decides it

**ffmpeg, 26 required slices: v4 clears all 26. v3 clears 25.** The one v3 misses is the
corpus `safety` slice at **0.5294 against a 0.75 bar**; v4 takes it to 0.8235. (That tag is
the in-corpus safety *routing* slice — distinct from the `safety_test.jsonl` gate above,
which both arms pass at 100%.)

**documents, 23 required slices: v4 clears all 23, and is never worse than v3.** It gains on
`protect` (0.875 → 1.0), `realistic` (0.929 → 1.0) and `reorder` (0.833 → 1.0).

Four ffmpeg slices move down but stay above their bars, and they are the honest cost of this
promotion rather than a reason to refuse it:

| slice | v3 | v4 | bar |
|---|---:|---:|---:|
| `batch` | 1.0000 | 0.9655 | 0.92 |
| `concat_video` | 0.9333 | 0.8667 | 0.75 |
| `create_thumbnail` | 0.9091 | 0.8727 | 0.80 |
| `trim` | 0.9895 | 0.9789 | 0.93 |

Against eleven that improve, `clarify` (+7.3 pp on n=232) and `adjust_volume` (+6.8 pp) among
them — which is the taxonomy work this plan exists for, showing up where it was aimed.

## Basis and limits

- One training seed, one instrument, both arms measured together. Not a general claim about
  retraining, a decision about these two builds.
- ffmpeg `avg_knaif` is fractionally down. Outcome accuracy is the metric that moved, and it
  moved on both skills; the artifact average was already near ceiling.
- `create_thumbnail` at 0.8727 is worth watching: the 2026-09-17 audit measured the incumbent
  getting only 6/20 exact frames on an independent probe, so this slice's bar is weaker than
  the capability it is meant to guard. Not a blocker here — it is the same weakness on both
  arms — but it should not be read as evidence the capability is fine.
- **1.7B is a separate decision and is NOT promoted by this verdict.** Its candidate clears
  documents (28/28) but misses ffmpeg (2 unmet), while the published 1.7B v1 misses by more
  (9 and 10) and fails the `ffmpeg_safety_system_root_dir` gate outright. See
  `2026-09-16_1.7b-v4-pair_success/report.md`; that tier needs an owner decision.

## What this authorises

The T7 re-lock, then the manifest/`recommendations`/`recommended_model` move to
`knaif-qwen3-4b-v2`. It does not authorise publishing the 1.7B.
