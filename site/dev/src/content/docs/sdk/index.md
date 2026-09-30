---
title: The knaif SDK
description: A click-like SDK for building natural-language CLI tools that run entirely on local hardware.
sidebar:
  order: 1
---

`knaif.cli` turns plain sentences into typed Python function calls. You write ordinary
functions with ordinary annotations; users type what they want; a small local model works
out which function they meant and what the arguments are.

```python
import knaif.cli as nk

@nk.command(help="Add a task", keywords=["add", "create"])
def add(title: nk.Arg(help="task title")):
    ...

nk.App([add]).run()
```

## Two front doors

**The decorator**, above — best when you are designing the interface with natural language
in mind, because annotations let you declare `choices`, help text and retrieval keywords
that the model actually sees.

**`nk.from_click(group)`** — best when you already have a CLI. It walks an existing
`click.Group` and derives the same metadata from your click decorators, with no change to
your command functions. Read [its limitations](/sdk/from-click/) before committing to it.

Both produce an `App`. Both dispatch by calling your function directly.

## When to use the SDK, and when not to

Use it when **you own the commands** and want a natural-language way in. Your app keeps
its normal CLI; knaif becomes an additional entry point, not a replacement.

Do not use it to build a capability *for knaif itself* — that is a skill, and it lives in
a bundle with its own tools, eval corpus and optionally a Rust port. See
[Author a skill](/author/).

The dividing line is who ships it. A skill ships inside knaif, for knaif's users. An SDK
app ships inside *your* program, for *your* users.

## What you get for free

Everything between the sentence and your function is knaif's, and none of it is the
model's judgement:

- **Type coercion.** A plan carrying `"42"` reaches an `int` parameter as `42`.
- **Enum enforcement.** `choices` become a schema the planner checks; a value outside the
  set is rejected before dispatch.
- **Rejection of unknown tools and unsupported arguments**, at validation time.
- **A confirmation gate** for anything marked destructive, enforced by the registry rather
  than decided per request.
- **Dry-run**, which renders the whole plan with no side effects.

The model emits `{"plan": [...]}` and nothing else. It cannot emit a shell string, and it
never sees your function body.

## Local only

There is no hosted option and no key to configure. Point it at [Ollama or a GGUF via
llama.cpp](/sdk/inference/), or leave it on the mock backend for tests.

That constraint has a payoff: your users' input never leaves their machine, and your
per-request cost is zero however many times they ask.
