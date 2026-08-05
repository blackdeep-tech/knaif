---
title: Quickstart
description: Put a natural-language front end on a CLI in about twenty lines, running entirely on your own machine.
---

The fastest useful thing knaif does is turn a sentence into a typed function call. No
account, no key, no cloud.

## Install

```bash
pip install knaif
```

:::note[This installs the SDK, not the skills]
Skill bundles like `ffmpeg` and `documents` are **not** in the wheel — they are reference
content loaded by path, so `list_skills()` is empty on a bare install. Running a skill
means cloning the repo; see [Author a skill](/author/).

The SDK *is* in the wheel, which is why it leads here.
:::

## Your first command

```python
# app.py
from typing import Annotated
import knaif.cli as nk

@nk.command(help="Return the current time", keywords=["now", "time", "current"])
def now(
    tz: Annotated[str, nk.Opt(help="IANA timezone (e.g. Asia/Tokyo)")] = "UTC",
    fmt: Annotated[str, nk.Opt(choices=["iso", "unix", "human"])] = "iso",
) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return {"time": datetime.now(tz=ZoneInfo(tz)).isoformat(), "timezone": tz}

nk.App([now]).run()
```

```console
$ python app.py "what time is it in Tokyo"
```

The model never sees your function and never runs anything. It emits a plan —
`{"tool": "now", "args": {"tz": "Asia/Tokyo"}}` — which knaif validates against the
signature it derived from your annotations, then calls `now(tz="Asia/Tokyo")` with real
typed arguments.

`fmt` shows why the annotations matter: because you declared `choices`, the model is shown
exactly those three values and a plan proposing anything else is rejected before your
function is called.

## Already have a click CLI?

Wrap it. Your command functions do not change at all.

```python
import knaif.cli as nk
from myapp.cli import cli      # an existing click.Group

nk.from_click(cli).run()
```

```console
$ python app.py "convert 2026-06-20T15:00 to Tokyo"
# calls: convert("2026-06-20T15:00", to_tz="Asia/Tokyo")
```

See [Wrapping a click CLI](/sdk/from-click/) for what maps cleanly and what does not.

## Run it with no model at all

Without an orchestrator, `App.invoke()` uses a **mock backend** with seeded responses.
That is not a toy — it is how you test the plumbing in CI without shipping a GGUF:

```python
app = nk.App([now])
result = app.invoke("what time is it in Tokyo", dry_run=True)
```

When you are ready for real inference, see [Connecting a model](/sdk/inference/) —
and read it before reaching for `InferenceOrchestrator` directly, because its raw defaults
hang on reasoning models.

## Where to go next

| You want to | Go to |
|---|---|
| Understand the decorators properly | [SDK reference](/sdk/reference/) |
| Wrap an existing click group | [Wrapping a click CLI](/sdk/from-click/) |
| Point it at a real local model | [Connecting a model](/sdk/inference/) |
| Know what the model can and cannot do | [The boundary contract](/sdk/boundaries/) |
| Build a skill for knaif itself | [Author a skill](/author/) |

## Three things called "knaif cli"

Search results will not tell you which one you have:

| Name | What it is |
|---|---|
| `knaif` | The native binary end users download from [knaif.org](https://knaif.org) |
| `knaif-cli` | The Python console script that runs skills from a checkout |
| `knaif.cli` | The SDK on this page — *the knaif SDK* |
