---
title: Wrapping a click CLI
description: Turn an existing click.Group into a natural-language interface, and know what does not map.
sidebar:
  order: 3
---

If you already have a click CLI, you do not need to rewrite it:

```python
import knaif.cli as nk
from myapp.cli import cli      # your existing click.Group

nk.from_click(cli).run()
```

Each sub-command becomes a tool. Your command functions are untouched — knaif derives the
tool description from the docstring, the argument schema from your click types, and the
required/optional split from whether a parameter has a default.

```console
$ python app.py "convert 2026-06-20T15:00 to Tokyo"
# calls: convert("2026-06-20T15:00", to_tz="Asia/Tokyo")
```

`click.Choice([...])` maps to an `enum` schema, so choices you already declared become
constraints the planner enforces for free.

## What does not map

`from_click` handles a flat `click.Group` with typed options and arguments. Everything
below is a genuine gap, not a rough edge — check this table before assuming your CLI
wraps cleanly.

| click feature | Status |
|---|---|
| Nested `click.Group` inside a group | **Not supported.** Flatten to one group, or use `@nk.command`. |
| `nargs=-1` / variadic arguments | **Not mapped.** Declare the argument as `array` with `@nk.command` instead. |
| `pass_context` / `click.Context` params | **Ignored.** Context objects cannot appear in a model-generated plan. Use [`nk.Ctx`](/sdk/reference/#nkctx). |
| `click.File` / `click.Path` | Mapped as `string`. Add `path_role` via `@nk.command` for sandbox validation. |
| Callback-based validation | **Not preserved.** Move it into the function body, or express it declaratively as enum / min / max. |
| `click.Choice(case_sensitive=False)` | Choices reach the schema, but case-insensitivity is **not** enforced at plan time. |
| Stacked command decorators | Only the last-registered `click.Group` is walked. |

:::caution[Callback validation is the one that bites]
If your CLI relies on click callbacks to reject bad input, that protection **disappears**
when a plan dispatches the function. The plan path validates against the schema, not
against your callbacks. Move anything load-bearing into the function body, where both
entry points run it.
:::

## Mixing both front doors

`from_click` is not all-or-nothing. Where a command does not map — a variadic argument, a
nested group — reimplement just that one with `@nk.command` and pass it alongside.

The natural-language path and your existing CLI stay independent, so an unmapped command
is still reachable the normal way; it simply is not something a user can ask for in a
sentence yet.
