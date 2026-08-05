---
title: Steps and Intents
description: The tool contract — when a tool does one thing, and when it should expand into a deterministic workflow.
sidebar:
  order: 2
---

Every tool is a class. A **`Step`** does one thing. An **`Intent`** is a tool the model can
choose that expands into several steps before anything runs.

## `Step`

```python
from knaif.handler_api import HandlerContext
from knaif.tool import Step

class InspectMediaStep(Step):
    name = "inspect_media"

    def handle(self, args: dict, ctx: HandlerContext) -> dict:
        if ctx.dry_run:
            return {"would_inspect": args["files"]}
        return {"probes": [...]}
```

`name` links the class to its `tools.yaml` entry. `handle` receives validated, coerced,
variable-resolved arguments and returns a dict.

### `HandlerContext`

| Attribute | What it is |
|---|---|
| `ctx.root` | Process working directory |
| `ctx.sandbox` | The sandbox root, when one is configured |
| `ctx.skill_dir` | **Your bundle root** — resolve `profiles/`, `data/` from here |
| `ctx.dry_run` | Preview only; produce no side effects |
| `ctx.confirmed` | The caller has already approved a destructive action |
| `ctx.confirm(prompt, preview=None)` | Ask mid-workflow, through the host's confirmer |

**Honouring `ctx.dry_run` is your responsibility.** Core cannot enforce it, because only
your handler knows which of its actions have side effects. A handler that ignores it makes
`--dry-run` a lie — and dry-run is one of the two ways a destructive tool is allowed to run
at all.

## `Intent`

Use an `Intent` when a single thing the user asks for is really a workflow. The model picks
one high-level tool; code turns it into the real sequence.

```python
from knaif.tool import Intent

class PrepareForPlatformIntent(Intent):
    name = "prepare_for_platform"

    def expand(self, args: dict) -> list[dict]:
        return [
            {"tool": "resolve_inputs", "args": {"paths": args["inputs"]}, "output": "$files"},
            {"tool": "inspect_media", "args": {"files": "$files"}, "output": "$probes"},
            {"tool": "build_recipes", "args": {"probes": "$probes"}, "output": "$recipes"},
        ]

    def summarize(self, args: dict, **kw) -> str:
        return f"prepare {len(args['inputs'])} file(s) for {args['platform']}"
```

This is the core idea of the whole system. The model's job ends at *"this is
`prepare_for_platform`, platform is whatsapp"* — perhaps thirty tokens. The probe, the
recipe, the codec choice, the filename derivation and the execution are all code, and cost
nothing to run again tomorrow.

### `expand` is pure

It receives no `ctx` and must not branch on runtime state. The **entire plan is frozen
before any step runs**, which is what makes it reviewable, previewable and identical every
time.

Per-item runtime decisions belong inside a `Step`'s output — a `build_recipes` step that
looks at the probe results — not inside `expand`. If you find yourself wanting `if
file_exists(...)` in an expander, that logic belongs in a step.

Expanded plans are **re-validated**, so every internal tool an expander emits must also be
declared in `tools.yaml`.

### `summarize`

Optional. Returns the one-line preview a host shows when `show_plan=True`. Write it for
the person about to approve the action, not for a log.

## Variables between steps

`output: "$name"` publishes a step's return value; later steps reference it in their args.
Resolution happens at runtime, immediately before dispatch — never during validation.

```json
{ "plan": [
  { "tool": "resolve_inputs", "args": { "paths": ["clip.mov"] }, "output": "$files" },
  { "tool": "inspect_media",  "args": { "files": "$files" },     "output": "$probes" }
] }
```

`$var.field` extracts a single field. Forward references fail validation — a plan can only
use what an earlier step produced.

:::caution[Sandbox paths are re-validated after resolution]
A variable could otherwise carry a path out of the sandbox that the pre-execution check
never saw. Core checks sandbox-sensitive paths **before execution and again after variable
resolution**, and this is why.
:::

## Reusing shared steps

`knaif.steps` ships steps every skill needs — `ResolveInputs` is the common one. Add the
class to your `tools` list and declare the matching `tools.yaml` entry; do not
reimplement path globbing per skill.

## Skill-level hooks

These live on your `Skill` subclass, not on individual tools:

| Hook | Runs |
|---|---|
| `preflight` | Before the plan executes — check binaries, fail early with a useful message |
| `format_results` | After execution, to render output for a human |
| `run_artifact` | To open or play what was produced |

## Choosing granularity

A new tool per flag makes the registry huge and retrieval worse; one tool with twenty
arguments makes extraction unreliable. The practical test: **would a user say this as a
separate request?** "Trim it" and "compress it" are separate tools. "Trim it to 10
seconds" is one tool with an argument.
