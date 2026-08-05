---
title: Evaluate a skill
description: Why a working skill is not a finished skill, and what measuring it actually involves.
sidebar:
  order: 1
---

A skill that works on the ten utterances you thought of is not a finished skill. It is a
prototype that has never met a user.

Evaluation is what turns one into the other, and in knaif it is not optional garnish — the
catalog stage on knaif.org derives from whether you have locked an acceptance bar, and the
shared fine-tuned model treats *every other skill's* bar as a regression gate. Your evals
are how the rest of the project stays safe from your changes, and vice versa.

## What you are actually measuring

Two different things, and conflating them is the most common mistake:

**Routing** — did the model pick the right tool and extract the right arguments? Cheap to
check, because it only needs the plan.

**The artifact** — did the command actually produce the right file? This needs execution,
and it is the only question a user cares about.

The gap between them is real. knaif validates everything up to dispatch and then, [after
dispatch, has only `returncode == 0` to go on](/author/safety/#the-gap-you-should-know-about).
A command can be well-formed, exit 0, and produce an mp3 stream inside a `.flac` container.
Routing was flawless. The artifact is wrong.

**That gap is why the eval ladder exists**, and why a routing-only check can never be the
bar you ship against.

## The shape of the work

```
skills/<name>/
  data/
    eval.jsonl            # the corpus — utterances + what should happen
    eval_snapshot.json    # the committed acceptance bar
    safety_test.jsonl     # utterances that must be rejected
  eval/
    fixtures.py           # generates test inputs
    verifiers.py          # skill-specific grading
```

| | |
|---|---|
| [Writing a corpus](/evaluate/corpus/) | The row schema, and the one contract that silently poisons a corpus if broken |
| [The eval ladder](/evaluate/ladder/) | Five phases, and why `cheap` is never an acceptance bar |
| [Locking a snapshot](/evaluate/snapshots/) | Committing the bar, the regression gate, and when two runs are comparable |

## The one-minute version

```bash
# 1. authoring — no model needed
uv run pytest skills/<skill>/python/tests/

# 2. routing — the fast loop while building
just eval <skill> --limit 20
just eval <skill>

# 3. honest — ALWAYS regenerate fixtures first
just eval-fixtures <skill>
just eval-success <skill>

# 4. lock the bar, in its own commit
just eval-snapshot <skill>

# 5. gate future runs against it
just eval-regression <skill>
```

Live in phases 1–2 while building. Cross 3–5 once to finish the skill, then re-run 3–5 on
any meaningful change.

:::caution[Every saved run gets a row in the index]
Save with `--save evals/runs/<YYYY-MM-DD>_<label>_<verifier>/` and add a line to
`evals/INDEX.md` — **including runs that failed their gate**. Those are the valuable ones;
they are how the project can say *no* to a change with confidence rather than by opinion.
:::
