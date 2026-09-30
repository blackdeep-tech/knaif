# Policy-v3 integration run — does the corrected instrument reproduce?

**Status: complete. ffmpeg reproduces the corrected instrument exactly; documents differs by
one row. Both skills ACCEPTED under the hardened gate. No snapshot was written.**

Incumbent `qwen3-4b-sft-v4-flat-q4` (model sha256 `a9c26005…`, byte-identical to the
2026-09-17 audit control), freshly regenerated fixtures in an isolated sandbox, run on the
integrated working tree — the speed + capture fixes plus the `POLICY_VERSION` 3 bump and
the acceptance-gate hardening. Same argv as the audit control; only the run label differs.

## Result

| | this run | corrected instrument (2026-09-17 pair control) | committed snapshot (policy 2) |
|---|---:|---:|---:|
| ffmpeg outcome | **0.9388954172** | 0.9388954172 | 0.9377203290 |
| ffmpeg avg_knaif | **0.9801227169** | 0.9801227169 | 0.9779445397 |
| documents outcome | **0.9817073171** | 0.9817073171 | 0.9817073171 |
| documents avg_knaif | **0.9978354978** | 1.0 | 1.0 |
| ffmpeg safety | 11/11 | 11/11 | — |
| documents safety | 9/9 | 9/9 | — |

Coverage 1.0 on both, no fixture-integrity drift, policy stamped 3.

**ffmpeg reproduces to ten decimal places on 851 utterances.** `ffmpeg_282` — "make clip_4k
play at quarter speed" — executes and scores 1.0, where the committed snapshot recorded it as
an execution error. That is the speed fix measured on the shipped path rather than in a unit
test.

## The one difference, and what it means

`documents_042#0` ("put sample.pdf pages in reverse order") emitted
`order: "-1"` here and `order: "reverse"` in both earlier runs — a different plan for the
same utterance, from the same model bytes and the same prompt. Its score is 2/3 rather than
1.0, which is the entire documents delta: `1 - (1/3)/154 = 0.99784`.

Inference runs at `temperature=0.0`, so this is not sampling. It is run-to-run variation in
llama.cpp GPU inference itself. **The consequence is that the evaluation is not bitwise
reproducible, and a single documents row is worth 0.22 pp of `avg_knaif_score`.** Read against
the candidate rejection recorded in `2026-09-17_sft-v5-symbolic-pair-v1_success`, whose
documents artifact component was 1.0 → 0.99669 (~0.33 pp, one row scoring 0.5): that component
is the same order as the noise measured here and is not on its own decision-grade. The
rejection does not rest on it — the three *outcome* regressions do, and outcome accuracy
reproduced exactly on both skills — but the artifact half of that argument should not be
quoted without this caveat.

One row in 1,015 differed. Treat ~99.9% as the reproduction rate, not 100%.

## Acceptance

Both skills pass the hardened gate on this evidence: ffmpeg **39** thresholds met, documents
**36** (31 and 28 before the hardening). That matters for the pending re-lock: the stricter
gate is not blocking a legitimate run.

## What this run does NOT do

It does not re-lock either snapshot. Both `eval_snapshot.json` files remain policy-2 records,
and the four tests that fail because of that still fail. This run is the evidence a re-lock
would be taken from, not the re-lock.
