---
title: The boundary contract
description: Exactly what the model can and cannot do to your program — and what was deliberately left out of v1.
sidebar:
  order: 5
---

The reason to hand a model your CLI is that it cannot do much with it. This page states
the boundary precisely, because a vague answer here is the difference between shipping and
not.

## What the model can do

Emit one JSON object:

```json
{ "plan": [ { "tool": "export", "args": { "fmt": "csv" } } ] }
```

That is the entire surface. It selects a tool by name and proposes arguments.

## What the model cannot do

- **Run anything.** It has no shell, no filesystem, no network. Dispatch is knaif calling
  your Python function.
- **Emit a command string.** There is no field in the plan envelope that could carry one.
- **Reach a function you did not decorate.** Unknown tool names fail validation.
- **Pass an argument you did not declare.** Unsupported arguments fail validation.
- **Escape an enum.** A value outside `choices` fails validation.
- **See your function body**, its source, or its return value on a later turn.
- **Approve its own destructive action.** That gate is registry policy, checked before
  dispatch.

Everything in that list fails **before** your function is called, not inside it.

## What your function is guaranteed

- Typed keyword arguments. `"42"` reaches an `int` parameter as `42`.
- `$var` references between plan steps already resolved to values.
- For a `destructive=True` command: either `confirmed=True` or `dry_run=True`. Never
  neither.
- Sandbox-sensitive paths validated before execution **and again** after variable
  resolution — so a variable cannot smuggle a path out of the sandbox.

## Not in v1

Three capabilities were scoped out deliberately. They are recorded so you can tell
"considered and deferred" from "missing".

### Developer-authored intents (`@nk.intent`)

v1 ships **flat commands only** — one command maps to one step. This keeps the decorator
symmetric with `from_click`, which cannot produce macros from a `click.Group`.

For multi-step work: write the compound operation as ordinary Python inside one command,
or let the model compose several atomic commands using `$var` binding between steps.

The honest reason to wait is that multi-step chain fidelity is the weakest part of
small-model planning today. A macro API would inherit that weakness rather than fix it.

### argv emission

A mode that *prints* argv — `["convert", "v.mp4", "--fmt", "mp4"]` — for a host to
re-dispatch is designed but unbuilt. It is deliberately feature-degraded: once the host
re-dispatches, knaif's confirmation gate, post-resolution sandbox re-validation and intent
expansion **do not apply**. The host owns all of it.

If you need cross-process dispatch today, call `app.invoke(utterance, dry_run=True)` and
render the returned plan yourself. You then see exactly what those gates would have done
before deciding to act on it.

### A destructive example app

The bundled `clock` app is read-only, so it never exercises the confirmation gate. That
machinery is covered by the ffmpeg and documents skill tests instead. The contract is
identical for `@nk.command(destructive=True)`.

## Where this leaves you

The failure mode of an SDK app is a **wrong plan** — the model picks the wrong command, or
extracts the wrong argument — not a destroyed filesystem. That is a bug you can test for,
and [the eval harness](/evaluate/) exists to measure exactly it.
