---
title: The eval ladder
description: Five phases from mock-backend smoke test to native parity — and why cheap is an iteration instrument, never an acceptance bar.
sidebar:
  order: 3
---

Verifiers are **not alternatives to choose between**. They are phases.

Live in phases 1–2 while building a skill. Cross 3–5 once to finish it. Re-run 3–5 on any
meaningful change.

| Phase | Command | Needs | Speed | Answers |
|---|---|---|---|---|
| **1. Authoring** | `uv run pytest skills/<name>/python/tests/` | nothing | seconds | Does it load, validate, dry-run? |
| **2. Routing** | `just eval <skill> --limit 20`, then full | model | minutes | Does the model pick the right tool? |
| **3. Honest** | `just eval-fixtures <skill>` **then** `just eval-success <skill>` | model + binaries | slow | Is the artifact actually right? |
| **4. Lock** | `just eval-snapshot <skill>` | model + binaries | rare | Commit the acceptance bar |
| **5. Parity** | `just parity <skill>` | model + native build | slow | Does native render what Python renders? |

## The verifiers

| Verifier | Checks | Executes? | Row must carry |
|---|---|---|---|
| `cheap` | The rendered command **string** — codec tokens, flags, filters — plus the outcome | No | — |
| `honest` | Runs it against a fixture, then probes the output | Yes | — |
| `output_diff` | Runs it and compares against the row's human-validated `baseline` | Yes | `baseline` |
| `success` | Runs it and grades the file against absolute `success_criteria` | Yes | `success_criteria` |
| `grade_outputs` | Multi-output chains — `success` logic per deliverable | Yes | `outputs[].criteria` |

Only `cheap` is required to exist. That is a floor, not a target.

## The rule

:::danger[`cheap` is an iteration instrument, never an acceptance bar]
A skill's committed snapshot is **always** an executing verifier — `success` where rows
carry `success_criteria`, `output_diff` where they carry `baseline` commands. A skill is
not done until its snapshot is locked with one of those.
:::

There are two independent reasons, and the second is the one that surprises people.

**It cannot see the artifact.** `cheap` never runs the command, so it inherits the
[validation-stops-at-dispatch gap](/author/safety/#the-gap-you-should-know-about) whole. A
command string can look perfect and still produce the wrong file.

**It reports false regressions.** When 11 ffmpeg chain rows gained validated `outputs`,
their `verifier_kind` flipped from `plan` to `output` and scored 0.0 under no-execution.
The cheap aggregate dropped **0.973 → 0.928** with *no behaviour change whatsoever* — the
corpus had merely been annotated.

> A bar that moves when you annotate the corpus is not a bar.

## Phase 3 has a prerequisite that will cost you an afternoon

:::caution[Generate fixtures first. Always.]
An executing verifier with missing fixtures **does not error**. It silently scores
near-zero on correct plans.

A documents baseline once landed at outcome ≈0.55 with a knaif score of **1.000** — 58 of
129 rows errored purely because `sandbox/fixtures/documents/` did not exist.

**If an executing run looks catastrophically bad while routing looks fine, check fixtures
before anything else.** Not the model, not the prompt, not the corpus.
:::

```bash
just eval-fixtures <skill>     # first, every time
just eval-success <skill>
```

## Choosing between the two executing verifiers

By **coverage of your corpus**, not by preference:

- Prefer **`success`** — it is more precise, grading against an absolute spec.
- Fall back to **`output_diff`** when more rows carry a `baseline` than
  `success_criteria`.

Changing a skill's snapshot verifier is a deliberate re-lock in its own commit. Never
automatic, never a side effect of another change.

## Command-shaped vs plan-shaped skills

The table above describes ffmpeg, which renders **one shell command string** per intent, so
the runner captures that string, executes it, and probes the result.

Not every skill works that way. A **plan-shaped** skill — documents is the reference case —
executes through library calls. There is no command to capture, so `_extract_artifact`
returns `None`, and any check needing a produced file fails for every destructive row.

:::caution[This failure is silent]
The run completes. The rows score. Destructive rows are simply understated, and nothing in
the scoreboard says why.
:::

Two rules follow:

**Implement `Skill.run_artifact`.** When present, the runner falls back to
`artifact = json.dumps(plan)`, and `run_artifact` replays that plan — copying
plan-referenced inputs into a working directory and materialising the real output file to
grade. Command-shaped skills are unaffected.

**Grade with `success`, not `honest`.** `honest` is defined against a probe of a
*command's* output. `success` grades the produced file against the row's criteria, which is
what a plan-shaped skill can actually satisfy.

If a new skill's destructive rows all score suspiciously low on `output_exists`, check this
first.

## Reading a scoreboard without fooling yourself

**A 100% pass rate does not mean 100% correct.** The verifier score only runs on rows whose
outcome was `plan`. Always read `outcome_accuracy` from the per-backend JSON alongside it.

**`parse_error` is its own bucket**, deliberately kept separate from `clarify` so the
small-model JSON-emission failure mode cannot hide inside "it asked for clarification".

**The first row of each run is a warm-up** and is excluded from timing, so model-load and
cold-KV cost do not skew the mean.

## Phase 5 — parity

If your skill ships in the native runtime, `just parity <skill>` pins both runtimes to the
identical GGUF and diffs the rendered output on real utterances. See [Python to
native](/native/).
