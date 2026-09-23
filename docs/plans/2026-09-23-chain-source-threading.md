# Chain source threading — stop rewriting inputs the user chose

**Status:** Planning · **Created:** 2026-09-23 · **Last worked:** 2026-09-23 · **Completed:** —
**Owner:** core · **Ref:** found in the workbench
([2026-09-21-skill-prompt-workbench.md](2026-09-21-skill-prompt-workbench.md)); touches the T5b
binding rule in [2026-09-11-reject-clarify-taxonomy.md](2026-09-11-reject-clarify-taxonomy.md)

**Goal:** A plan in which several steps read the **same** file (a fan-out) reaches execution the
way the model wrote it. Today `CommandAgent._forward_thread_reused_sources` rewrites every such
plan into a straight line, which silently turns a correct plan into a wrong one.

**Not a goal:** partial reverse (`reverse_video` over a time range). That stays unsupported by
owner decision 2026-09-23; it is only the utterance that exposed this.

**Does not need a retrain.** The fix is deterministic post-processing of the model's plan. The
model's weights, prompt and training mix are untouched, so it lands on whichever model is
current and does not block or follow the v2 publish.

## The problem, reproduced

Utterance (workbench, 4B, Python runtime):

> strip the audio from clip.mov and save it as silent.mp4, then trim silent.mp4 from 0 to 2
> seconds as part1.mp4, then trim silent.mp4 from 2 to 4 seconds as part2.mp4, then reverse
> part2.mp4 as part2_rev.mp4, then trim silent.mp4 from 4 seconds to the end as part3.mp4, then
> concatenate part1.mp4, part2_rev.mp4 and part3.mp4 into result.mp4

The workbench showed `trim part1.mp4 2–4s`, `trim part2_rev.mp4 from 4s` and
`concat [part3, part3, part3]`, and the engine stopped at step 3 with an empty-cut error. Feeding
the **correct** plan (every trim reading `silent.mp4`) through `_link_chain_intermediates`
reproduces that output exactly, and a second utterance ordering the steps differently reproduces
its output exactly too. The threader cascades: step 1 consumes `silent.mp4`, so every later
`silent.mp4` becomes `part1.mp4`; step 2 now consumes `part1.mp4`, so every later `part1.mp4`
becomes `part2.mp4`; and so on down the plan.

The smallest non-media-specific case:

```text
create_thumbnail clip.mp4 → thumb.jpg
compress_video   clip.mp4 → small.mp4     ⇒ rewritten to compress_video thumb.jpg
```

## Why the threader exists — and must not simply be deleted

Added in documents productionization
([2026-06-22](2026-06-22-documents-productionization.md)): for *"unlock sample-protected.pdf with
pass secret and check if **it** contains beta"* the model pointed `find_in_document` at the still
locked original. The user named the file **once** and said "it"; the model filled in the name
itself and picked the wrong version. The threader repairs that.

Depends on it today:

- core: 5 unit tests in `python/core/tests/test_chain_intermediate_linking.py`
- documents: `test_unlock_then_find_chain_threads_to_unlocked_output`
- ffmpeg T5b: `_collisions.py` and `test_output_collision_binding.py` assume a later reference to
  a single-source producer's input has already been rebound onto its output
- likely, unmeasured: ffmpeg chain rows phrased with "it" — `ffmpeg_116`, `_117`, `_121`, `_267`,
  `_hard_002/003/005/007/015`. The corpus stores commands, not plans, so only an eval run shows
  whether the model leans on the threader there

Native **does not port it**: `knaif-core/src/clarify_gate.rs` ports `_link_chain_intermediates`
only. The runtimes already disagree on both shapes — native runs the fan-out right (measured, T0)
and presumably the unlock→find chain wrong — and no parity test covers it.

## The rule

> Rewrite a later reference to a producer's source **only when the user named that file at most
> once**. If the user wrote the name again, the later step's input is their choice — keep it.

| Case | Mentions of the name | Result |
|---|---|---|
| unlock s.pdf → "check if **it** contains beta" | 1 | threaded (documents fix kept) |
| "trim clip.mp4 …, then resize **it**" | 1 | threaded |
| partial-reverse prompt: `silent.mp4` | 4 | kept |
| "thumbnail of clip.mp4 and compress clip.mp4" | 2 | kept |

Count on the normalized utterance, case-insensitive, matching the basename so `./clip.mp4` and
`clip.mp4` count as one name (the threader already matches across a `./` prefix).

**Accepted cost:** "unlock s.pdf, then search s.pdf for beta" reads the locked original and fails
with the existing clear encrypted-file error. The user wrote that name; the error says why.

**Open, decided in T2:** whether to also refuse a rewrite that changes the file's kind (video →
image, as in "thumbnail of clip.mp4 and compress **it**"). It fixes a real wrong plan but adds a
media-type notion to core, which must stay domain-agnostic — it would have to come from the
tool registry, not from extensions hard-coded in `agent.py`.

## Tasks

### - [x] T0 — Confirm the model's raw plan

**Confirmed 2026-09-23.** The same utterance (the "From silent.mp4 make three pieces" wording),
same 4B model, on the **native** runtime — which has no threader — produced the correct fan-out:
all three trims read `silent.mp4`, the reverse reads `part2.mp4`, and the concat joins
`part1, part2_rev, part3`. It executed end to end: `result.mp4` 8.0 s, h264, no audio. The Python
runtime turned that plan into `trim part1.mp4 …`, `trim part2.mp4 …`, `reverse part3.mp4`,
`concat [part2_rev ×3]` and stopped at step 3. So the model handles fan-out; the threader is the
whole defect, and T5's rows are regression guards, not training material.

### - [ ] T1 — Failing tests first (core)

In `test_chain_intermediate_linking.py`, RED before any change:

- the partial-reverse fan-out plan survives unchanged
- thumbnail + compress with `clip.mp4` named twice survives unchanged
- the five existing tests stay as they are and keep passing — they are the documents contract

### - [ ] T2 — Implement the rule (Python core)

Change `_forward_thread_reused_sources` in `python/core/knaif/agent.py` to take the utterance and
apply the mention count. `_link_chain_intermediates` already has the utterance; pass it through.
Decide the kind-change question here and record the decision in this file.

### - [ ] T3 — Keep T5b true

Re-run `skills/ffmpeg/python/tests/test_output_collision_binding.py`. Add a test for T5b's
identity case under the new rule — step 0 writes `clip.mp4` from `clip.mp4`, a later step reads
`clip.mp4` — and for the case where the user repeats that name. If T5b's "one referent" claim no
longer holds for a repeated name, update the rule text in `_collisions.py` and in the taxonomy
plan rather than working around it.

### - [ ] T4 — Native port

Port the threader, with the new rule, into `knaif-core/src/clarify_gate.rs` after
`link_chain_intermediates`, same order as Python. Add both new shapes plus unlock→find to the L2
parity fixtures under `contracts/parity/` so the runtimes are held to one answer.
`just check-contracts`, `just test-native`, `just check-native`.

### - [ ] T5 — Corpus

Add fan-out rows to `skills/ffmpeg/data/eval.jsonl` with executing `success_criteria`:

- thumbnail + compress of one named file (two `outputs`)
- two trims of one file into two named clips

Leave partial reverse out — it is unsupported, and a row would have to expect a clarify.

### - [ ] T6 — Evidence

On current `main` code and on this branch, same model:

```bash
just eval-fixtures ffmpeg && just eval-success ffmpeg
just eval-fixtures documents && just eval-success documents
just eval-regression ffmpeg <run> && just eval-regression documents <run>
```

Pass bar: no regression against either snapshot on the existing rows, and the new T5 rows
correct. A drop on a chain row means the model repeats a filename while meaning the transformed
file — read those rows before touching the rule. Record each run in `evals/INDEX.md`. Re-lock
the ffmpeg snapshot only in its own commit, and only because T5 added rows.

### - [ ] T7 — Workbench shows what the model said

The workbench prints the plan after deterministic rewriting, which is why this read as a model
failure. Show the raw model plan beside the executed one, and mark the steps that were
rewritten. The logic goes in `notebooks/shared/workbench/` with a unit test.

### - [ ] T8 — Docs

`docs/ARCHITECTURE.md` (chain linking step), the T5b paragraph in `_collisions.py`, and a line
in `docs/TOOL_SCHEMA.md` if skill authors need to know that repeating a filename pins it.

## Sequencing

Starts on its own branch. It is independent of the v2 publish: no retrain, no prompt change. T6
reruns the success evals, so if v2 has been promoted by then, the evidence is measured on v2.
