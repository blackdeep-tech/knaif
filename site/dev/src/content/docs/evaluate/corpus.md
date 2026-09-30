---
title: Writing a corpus
description: The eval.jsonl row schema, success criteria, baselines — and the contract that quietly poisons a corpus when broken.
sidebar:
  order: 2
---

`data/eval.jsonl` is one JSON object per row. A row is not one utterance — it is **one
intent**, with every phrasing that should mean the same thing.

```jsonl
{
  "id": "ffmpeg_042",
  "utterances": [
    "combine clip1.mov and clip2.mov into one mp4, reversed",
    "junta clip1.mov y clip2.mov en un mp4 al revés"
  ],
  "expected_outcome": "plan",
  "expected_tool": "concat_video",
  "fixture": "clip.mp4",
  "baseline": {"command": "ffmpeg -i clip1.mov ...", "validated_by": "human"},
  "success_criteria": {"container": "mp4", "vcodec": "h264"},
  "tags": ["concat", "multilingual", "es"]
}
```

Each utterance runs as `<id>__<idx>`, so `ffmpeg_042__0` and `ffmpeg_042__1` are graded
separately while sharing one fixture, one baseline and one set of criteria.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique, e.g. `ffmpeg_042` |
| `utterances` | yes | Every phrasing of the same intent |
| `expected_outcome` | yes | `plan`, `clarify`, or `reject` |
| `expected_tool` | for `plan` | The intent tool the planner should choose |
| `fixture` | no | Filename with extension; resolved under `sandbox/fixtures/<skill>/` |
| `baseline` | no | `{command, validated_by}` — a human-validated freeform command to compare against |
| `success_criteria` | no | Absolute quality spec for the `success` verifier |
| `tolerances` | no | Verifier tolerances, e.g. `{"duration_s": 1.0}` |
| `tags` | no | Drives per-tag breakdowns — `multilingual`, `trap`, `complex`, language codes |
| `expected_tools` | no | Expected sequence for chains; `expected_tools[0]` must equal `expected_tool` |
| `grade` | no | `full` (default) or `routing` |

## The utterance-equivalence contract

:::danger[Break this and the row manufactures permanent false negatives]
Bundling utterances on one row **asserts they are true paraphrases of one intent** — same
expected outcome, same criteria, same fixture.

If some utterances name their inputs and others do not, or one is genuinely ambiguous where
the rest are not, then no model can satisfy all of them. The row will score badly forever,
and it will look like a model problem rather than a corpus problem.

When in doubt, split into two rows. Rows are cheap; a permanently-failing row is not.
:::

## Row shapes

| Shape | `expected_outcome` | For |
|---|---|---|
| **Standard** | `plan` | One intent, straightforward phrasing |
| **Complex** | `plan` | Multi-step; graded on the final output |
| **Bad** | `reject` | Unsafe, impossible, or exfiltration requests |
| **Edge** | `clarify` | Boundary values, typos, genuinely ambiguous phrasing |

**Feature gaps are `clarify`, not failures.** If your skill cannot do something yet, the
correct behaviour is to ask rather than guess — so grade it as `clarify` and still record
the `baseline.command` showing what the user probably wanted. That turns your corpus into
a prioritised feature list as a side effect.

## Clarify rows are not filler

A skill that confidently does the wrong thing on an ambiguous request is worse than one
that asks. In the [published comparison](https://knaif.org/vs/), *"make my video better"*
is the row where knaif asks and all three premium agents assume and act.

That behaviour only survives because it is graded. Write the ambiguous rows.

## `success_criteria`

An **absolute** spec for the produced artifact — not a diff against a baseline:

```json
"success_criteria": {
  "container": "mp4",
  "vcodec": "h264",
  "max_size_mb": 25,
  "duration_s": 10
}
```

This is what the `success` verifier grades against, and it is the most precise signal
available. Rows carrying it are what let a skill lock a `success` snapshot.

Leave it `{}` for `clarify` and `reject` rows — there is no artifact to grade.

## `baseline`

A pre-recorded, human-validated freeform command for the same request. It serves two jobs:

- The `output_diff` verifier runs it and compares knaif's artifact against its output.
- Every scoreboard reports "knaif scored X, freeform baseline scored Y", so you always
  know whether a low score means knaif is bad or the task is hard.

## Multilingual rows

Add language variants as extra `utterances` on the same row and tag them (`es`, `zh`, …).
They share the fixture and criteria, so coverage costs almost nothing to add — and per-tag
breakdowns then tell you exactly which language slice is dragging.

Retrieval must be able to *find* the tool before the model can pick it. Check the retrieval
layer separately before concluding the model is at fault:

```bash
uv run -m knaif.evalsuite retrieval
```

## Safety corpus

`data/safety_test.jsonl` holds utterances that must produce `reject`. Small file, and it
is the difference between believing your `safety_category` classification is right and
knowing it.

## Sizing

Start with 20–30 rows covering every tool, then grow toward the hard tail. For reference,
the shipped skills carry 846 (ffmpeg) and 164 (documents) utterances.

Growth changes what a score means, though — see [when two runs are
comparable](/evaluate/snapshots/#when-two-runs-are-comparable) before reading a drop as a
regression.
