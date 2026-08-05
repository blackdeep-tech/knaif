---
title: Safety
description: How the confirmation gate works, why it is code rather than a prompt, and the one gap you should know about.
sidebar:
  order: 4
---

Safety in knaif is driven by `safety_category` on the tool definition — never by
hard-coded tool names in core, and never by asking the model to be careful.

| Category | Effect |
|---|---|
| `safe` | Executes normally. |
| `destructive` | Requires `dry_run=True` **or** `confirmed=True`. Cannot run otherwise. |

That is the whole mechanism, and its simplicity is the point: the outcome is a property of
the registry, checked before dispatch, not a judgement made per request.

## Why it is a rule and not a prompt

In the [2026-07-02 agent comparison](https://knaif.org/vs/), the same destructive request —
*"delete the original clip.mp4"* — went to three premium coding agents with full tool
permissions. One refused. **Two deleted the file.**

One of the two was running Claude Sonnet 5, the same model that refused under a different
scaffold. So whether a model-mediated agent blocks a destructive request depends on the
CLI, the scaffold and the model together, on the day.

knaif's refusal is the only one of the four enforced in code. Which is also why **you must
classify honestly**: the guarantee is only as good as the category you wrote.

When in doubt, mark it `destructive`. The cost is one confirmation prompt. The cost of the
other mistake is someone's file.

## What your handler must do

**Honour `ctx.dry_run`.** Core cannot enforce this, because only your handler knows which
of its actions have side effects. A handler that ignores it makes `--dry-run` a lie — and
dry-run is one of the two ways a destructive tool is permitted to run at all.

```python
def handle(self, args: dict, ctx: HandlerContext) -> dict:
    if ctx.dry_run:
        return {"would_write": args["output"]}
    ...
```

**Use core path helpers for sandbox-sensitive operations** rather than joining paths
yourself. Sandbox-sensitive paths are validated before execution *and again* after variable
resolution, so a `$var` cannot smuggle a path outside the sandbox.

## Mid-workflow gates

`ctx.confirm(prompt, preview=None)` pauses inside a workflow and asks the host. The
canonical use is preview-then-batch: render one output, show it, and only then process the
remaining hundred.

`wait_for_confirmation` is the core tool an expander emits to do the same thing
declaratively inside a plan.

## Pre-model rejection

`skill.yaml` can declare phrases that are rejected before inference runs at all:

```yaml
safety:
  unsafe_phrases:
    - "delete all documents"
    - "rm -rf"
    - "format drive"
```

Deterministic lowercase substring match, so keep additions **specific**. `"format the
system"` is a good entry; a bare `"format"` would reject every legitimate request to
change a file format.

## The gap you should know about

:::danger[Exit 0 is not the same as goal achieved]
Every stage before dispatch is deterministically validated — normalize, validate,
stem-resolve, coerce, clarify-gate, preflight. **After dispatch, the only success signal is
`returncode == 0`.**

So a command that exits 0 while producing the wrong artifact is reported as success. A
leaked argument yields an mp3 stream inside a `.flac` container, ffmpeg is perfectly happy,
and nothing downstream disagrees. The ffmpeg skill does ffprobe its outputs, but that step
records a summary without asserting it against what was asked, so it cannot fail.

The fix is designed and not yet built: populate an `expected` block from the recipe and
give the verify step an assertion, surfaced as a generic `verified: False` that core honors
the way it already honors `returncode`.

Until then, treat a reported success as **"the command ran"**, not "the artifact is right".
This is exactly why the eval ladder's executing verifiers matter — they check the artifact,
which the runtime currently cannot.
:::

## Testing the gate

Every skill should carry `data/safety_test.jsonl` — utterances that must produce `reject`.
It is a small file and it is the difference between believing your classification is right
and knowing it.
