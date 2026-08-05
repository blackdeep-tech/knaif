---
title: SDK reference
description: Decorators, annotations, App, and how Python types become plan schemas.
sidebar:
  order: 2
---

## `@nk.command(help, keywords)`

Marks a function as a tool the model can choose.

- **`help`** becomes the tool description the model reads. Write it for the model as much
  as for a human: it is the main signal for picking this command over a neighbouring one.
- **`keywords`** bias retrieval toward this command. They matter more as your command
  count grows, because only retrieved tools reach the prompt.

```python
@nk.command(help="Return the current time", keywords=["now", "time", "current"])
def now(...): ...
```

## Parameter annotations

Both are used as `Annotated` metadata.

### `nk.Arg(help)`

Marks the parameter **required**. The plan must supply it.

```python
def add(title: Annotated[str, nk.Arg(help="task title")]): ...
```

### `nk.Opt(help, choices, default)`

Marks the parameter **optional**. `choices` is the one to reach for — it generates an
`enum` schema, so the model is shown the allowed values *and* the planner rejects anything
outside them.

```python
def export(
    fmt: Annotated[str, nk.Opt(choices=["csv", "json", "parquet"])] = "csv",
): ...
```

That single line does two jobs at once: it constrains what the model proposes, and it
guarantees your function body never receives a fourth value.

### `nk.Ctx`

Annotate a parameter with `nk.Ctx` — or simply name it `ctx` — to receive the
`HandlerContext` at dispatch time. Use it for `ctx.dry_run`, `ctx.confirmed`,
`ctx.sandbox`, and `ctx.confirm(...)`.

```python
def delete(path: Annotated[str, nk.Arg(help="file to remove")], ctx: nk.Ctx):
    if ctx.dry_run:
        return {"would_delete": path}
    ...
```

## Type mapping

Your annotations become the schema the planner validates against.

| Python annotation | Schema type |
|---|---|
| `str` | `string` |
| `int` | `integer` |
| `float` | `number` |
| `bool` | `boolean` |
| `list` | `array` |
| `nk.Opt(choices=[...])` | `enum` |
| `click.Choice([...])` | `enum` |

Coercion happens before your function is called, so a plan carrying `"42"` arrives as
`int` `42`. A value that cannot be coerced fails validation rather than reaching you.

## `nk.App(commands, *, orchestrator, sandbox, root, ...)`

Wires decorated functions into a working agent.

| Method | Does |
|---|---|
| `run(argv)` | CLI entry point. Reads `sys.argv[1:]` as the utterance. |
| `invoke(utterance, dry_run, confirmed)` | Programmatic call. Returns step results. |

`invoke` is what you want in tests — with no `orchestrator` it uses the mock backend, so
the whole path is exercised offline.

```python
app = nk.App([now], orchestrator=orch, sandbox="./workdir")
app.run()
```

## `nk.from_click(group, *, orchestrator, **app_kwargs)`

Wraps an existing `click.Group`; each sub-command becomes a tool. Returns an `App`.
See [Wrapping a click CLI](/sdk/from-click/).

## Marking a command destructive

```python
@nk.command(help="Delete a task", destructive=True)
def remove(task_id: Annotated[int, nk.Arg(help="task id")]): ...
```

A destructive command cannot run without `confirmed=True` or `dry_run=True`. That gate
lives in the registry and is checked before dispatch — it is not a decision the model
makes, and no phrasing of the request can talk it out of the gate.

:::note[The bundled example does not exercise this]
The packaged `clock` app is read-only, so it never hits the confirmation gate. That
machinery is covered by the ffmpeg and documents skill tests instead. The contract is the
same either way.
:::
