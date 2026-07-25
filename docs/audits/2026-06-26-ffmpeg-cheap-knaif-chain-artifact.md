# ffmpeg cheap-verifier knaif drop is a chain-output measurement artifact

**Date:** 2026-06-26
**Trigger:** `regression --all-skills` flagged ffmpeg `avg_knaif_score 0.973 → 0.928`
during the skill-package-loader validation run. Confirmed **not** caused by that
refactor (see below); investigated the dev-side drop separately.

## Verdict

There is **no real regression**. The drop is a **cheap-verifier measurement
artifact**: multi-step chain rows that have execution-based `outputs` criteria score
`knaif = 0.0` under the `cheap` (no-execution) verifier, because the artifact grader
has no produced file to check. As more chains gained validated `outputs` since the
Jun-18 snapshot, the cheap aggregate fell — while the actual plans are unchanged and
correct.

## Evidence

Compared per-row plans/scores: Jun-18 (`evals/runs/2026-06-18_post-branch_cheap`, matches the
locked snapshot, knaif 0.9732) vs current (`evals/runs/2026-06-26_pkg-loader-validate_cheap`,
knaif 0.9279). Same backend (qwen3-4b), same 297-row corpus, deterministic on this box
(two back-to-back current runs byte-identical).

- **11 rows dropped knaif; all 11 are `complex` (multi-step chains).**
- **8 of the 11 have byte-identical plans** but scored lower → the *scoring* changed,
  not the model output (the other 3 also drifted but hit the same 0.0 floor).
- Every dropped row **flipped `verifier_kind: plan` → `output`** and fails
  `outN:output_not_produced`.
- `verifier_kind: output` rows grew **2 → 13** (the corpus gained 11 validated chain
  `outputs`).
- **Excluding the output-kind rows (which cheap mode structurally cannot grade):
  current cheap knaif = 0.9897 vs Jun-18 0.9828 — current is +0.7%.**

Mechanism, row `ffmpeg_116` (identical plan in both runs):

| | verifier_kind | knaif | detail |
|---|---|---|---|
| Jun-18 | `plan` | 1.0 | matched `ffmpeg_command_produced` |
| current | `output` | 0.0 | failed `out0/out1:output_not_produced` |

The corpus row gained `outputs` + `outputs_validated_by` (the "VALIDATED chain outputs"
work, PR #19). The verifier now routes rows-with-`outputs` to the artifact grader, which
only has meaning under the `success` (execution) verifier. INDEX history confirms
`success`-mode chain knaif stayed ~0.97 throughout.

### Confirmed by a real-execution run (2026-06-26)

`run --skill ffmpeg --verifier success` (`evals/runs/2026-06-26_pkg-loader-validate_success`):
**outcome 0.904 / knaif 0.969 / tool 0.824 / schema 0.985** (791 utterances) — matches
the historical ~0.97 success knaif, no regression. The 11 rows that scored **0.0 under
cheap** grade **0.74 mean** under execution: `116/121/123/129/135/274 = 1.0` (chains
execute and produce correct artifacts), `130/117/226` partial (genuine model arg slips,
e.g. 226's second audio op emits aac not mp3), `127/227` outcome-fail (the 3 real
output-drift rows). This proves the cheap 0.0 was purely the no-execution measurement
artifact, not broken chains.

## Not the package-loader refactor

`dev..HEAD` changes **no** prompt.yaml / tools.yaml / skill.yaml / retrieval / planner /
orchestrator / corpus; the ffmpeg tool list order is unchanged; `vocab.yaml` values are
byte-identical to the pre-extraction constants. Identical prompt + greedy-deterministic
decoding ⟹ identical plans ⟹ the refactor cannot move the score. This is the same class
of harness issue noted for documents in project memory ("cheap/dry-run checks
under-grade chains; grade chains off the real artifact").

## Resolution (implemented 2026-06-26)

**Fixed** in `score_corpus` (`python/core/knaif/evalsuite/scoring.py`). The dispatch routed any
row with `outputs` to `grade_outputs` *regardless of verifier*; `grade_outputs` needs
produced artifacts, which `cheap` never makes. Fix: gate `grade_outputs` on the
executing verifiers (`_EXECUTING_VERIFIERS = {success, honest, output_diff}`). Under
`cheap`, an `outputs` row now falls back to the plan-level verifier
(`ffmpeg_command_produced` + flags/filters), never 0.0. Executing verifiers still grade
real artifacts. Guarded by two tests in `python/core/tests/test_evalsuite_scoring.py`.

**Confirmed:** ffmpeg `cheap` knaif **0.928 → 0.986** (now above the Jun-18 snapshot
0.973; `output`-kind rows under cheap 13 → 0; outcome unchanged). The 11 chain rows that
scored 0.0 now grade 1.0/0.5 via plan-level. `success`-mode chain grading is unchanged
(0.969, real artifacts). The `regression --all-skills` cheap gate now passes; re-locking
the cheap snapshot is optional (current 0.986 already exceeds it).

Run: `evals/runs/2026-06-26_cheap-fallback-fix_cheap`. Fix commit follows this audit.
