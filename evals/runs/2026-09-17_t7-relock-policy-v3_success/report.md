# T7 — re-lock both snapshots under scoring policy v3

**Status: complete. Both snapshots written, both skills ACCEPTED, suite green.**

T7 of [the reject/clarify plan](../../../docs/plans/2026-09-11-reject-clarify-taxonomy.md).
The promotion verdict was recorded **first**, in
[`PROMOTION_VERDICT.md`](../2026-09-15_t6g-sizing-vs-sending_success/PROMOTION_VERDICT.md),
because re-locking over the candidate and then comparing to the snapshot compares the
candidate to itself. Only then was anything written.

Incumbent `qwen3-4b-sft-v4-flat-q4` (public `knaif-qwen3-4b-v2`, model sha256 `a9c26005…`),
freshly regenerated fixtures, `run --snapshot` for each skill.

## What is now frozen

| | locked (policy 3) | previous (policy 2) | change |
|---|---:|---:|---|
| ffmpeg outcome | **0.9388954172** | 0.9377203290 | +0.12 pp |
| ffmpeg avg_knaif | **0.9801227169** | 0.9779445397 | +0.22 pp |
| documents outcome | **0.9817073171** | 0.9817073171 | — |
| documents avg_knaif | **1.0** | 1.0 | — |

Coverage 1.0 both, `fixture_integrity: []` both, safety 11/11 and 9/9.
**S2: ffmpeg ACCEPTED (39 thresholds), documents ACCEPTED (36).**

**No model improved.** Every point of the ffmpeg movement is the instrument correction the
policy bump exists for: the executing runner now grants confirmation during command capture,
so `ffmpeg_282` executes its already-correct quarter-speed plan instead of erroring, and three
rows are graded on their final output instead of an intermediate. `documents` does not move at
all, which is the control on that claim.

## Reproduction

ffmpeg's figures are now the **third** independent reproduction of the corrected instrument,
to ten decimal places, across separate sandboxes and fixture regenerations — the
2026-09-17 pair control, `2026-09-17_policy-v3-integration_success`, and this run.

documents is locked at `avg_knaif 1.0`, its stable value: 4 of 5 observations give 1.0, and
one gave 0.9978 because `documents_042#0` emitted a different plan at `temperature=0.0`. That
row flakes; the value locked here is the one it settles on. See the integration run's report
for the measurement.

## Scope

This freezes the Python lane. It does **not** promote the model pointer — the manifest still
carries `url: TODO`/`sha256: TODO` for `knaif-qwen3-4b-v2`, and both skills' `recommended_model:`
and the manifest `recommendations:` still name v1. That is T7's follow-on, gated on publishing
the GGUF. L3 parity and the L4 native lane (T8) are stale and unrun.
