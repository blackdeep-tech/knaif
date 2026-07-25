# knaif.cli SDK — Developer Guide

`knaif.cli` is a click-like SDK for building natural-language CLI tools.
Your users type free-form sentences; knaif maps them to typed Python function calls.

## Installation

```bash
uv add knaif          # SDK ships in the main package
```

## Quickstart — decorator front door

```python
from typing import Annotated
import knaif.cli as nk

@nk.command(help="Return the current time", keywords=["now", "time", "current"])
def now(
    tz: Annotated[str, nk.Opt(help="IANA timezone (e.g. Asia/Tokyo)")] = "UTC",
    fmt: Annotated[str, nk.Opt(choices=["iso", "unix", "human"])] = "iso",
) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    dt = datetime.now(tz=ZoneInfo(tz))
    return {"time": dt.isoformat(), "timezone": tz}

app = nk.App([now])

if __name__ == "__main__":
    app.run()   # reads sys.argv[1] as the NL utterance
```

```
$ uv run clock.py "what time is it in Tokyo"
{"time": "2026-06-20T09:00:00+09:00", "timezone": "Asia/Tokyo"}
```

## Quickstart — `from_click` adapter

Wrap an existing click CLI with zero changes to your command functions:

```python
import click
import knaif.cli as nk

@click.group()
def cli(): pass

@cli.command()
@click.argument("value")
@click.option("--to-tz", default="UTC")
def convert(value, to_tz):
    """Convert a datetime to another timezone."""
    ...

app = nk.from_click(cli)
app.run()
```

```
$ uv run app.py "convert 2026-06-20T15:00 to Tokyo"
# equivalent to: convert("2026-06-20T15:00", to_tz="Asia/Tokyo")
```

## API reference

### `@nk.command(help, keywords)`

Decorates a function as a knaif CLI command. `help` becomes the tool description
shown to the model. `keywords` bias retrieval toward this command.

### `nk.Arg(help)`

Used as `Annotated` metadata. Marks the parameter as **required**.

### `nk.Opt(help, choices, default)`

Used as `Annotated` metadata. Marks the parameter as **optional**. `choices`
generates an `enum` ArgSchema — the model is shown the allowed values and the
planner rejects anything outside them.

### `nk.Ctx`

Marker class. Annotate a parameter with `nk.Ctx` (or name it `ctx`) to receive
the `HandlerContext` at dispatch time.

### `nk.App(commands, *, orchestrator, sandbox, root, ...)`

Wires a list of `@nk.command`-decorated functions into a `CommandAgent`.

- `invoke(utterance, dry_run, confirmed)` — programmatic call; returns step results.
- `run(argv)` — CLI entry point; reads `sys.argv[1:]` as the utterance.

### `nk.from_click(group, *, orchestrator, **app_kwargs)`

Wraps an existing `click.Group`. Each sub-command becomes a tool. Returns an `App`.

## Type mapping

| Python annotation | `ArgSchema.type` |
|---|---|
| `str` | `"string"` |
| `int` | `"integer"` |
| `float` | `"number"` |
| `bool` | `"boolean"` |
| `list` | `"array"` |
| `nk.Opt(choices=[...])` | `"enum"` |
| `click.Choice([...])` | `"enum"` |

## Local inference

`knaif` is local-only. Use `nk.local_ollama()` to connect to Ollama:

```python
import knaif.cli as nk

orch = nk.local_ollama(model="qwen3:4b")   # None + warning if Ollama unreachable
app = nk.App([now], orchestrator=orch)
```

`local_ollama()` picks defaults that work with reasoning models (Qwen3, DeepSeek-R1):
`thinking_enabled=True`, `json_mode=False`, `max_tokens=2048`. Prefer it over building the
orchestrator by hand — the raw constructor defaults to `json_mode=True` and a 256-token
budget, which **hangs then times out** on a reasoning model. The short version:
`think: false` does not stop the model reasoning, it only stops Ollama separating the
reasoning, which then lands in `message.content` and destroys the JSON. Full explanation
and the `eval_backends.yaml` equivalents: [INFERENCE.md → Reasoning models on
Ollama](INFERENCE.md#reasoning-models-on-ollama--leave-thinking-on).

If you do build the orchestrator directly, pass the same settings:

```python
from knaif.orchestrator import InferenceOrchestrator

orch = InferenceOrchestrator(
    backend="ollama",
    model_name="qwen3:4b",
    model_config={"json_mode": False, "thinking_enabled": True, "max_tokens": 2048},
)
app = nk.App([now], orchestrator=orch)
```

For llama.cpp (GGUF):

```python
orch = InferenceOrchestrator(backend="llama_cpp", model_path="models/qwen3-4b.gguf")
app = nk.App([now], orchestrator=orch)
```

Without an orchestrator, `App.invoke()` uses the mock backend (seeded responses) —
useful for tests and offline development.

### `InferenceBackend` protocol (custom backends)

To wire a backend not shipped by knaif, implement the `InferenceBackend` protocol
from `knaif.orchestrator` and pass the instance directly:

```python
from collections.abc import Iterator
from knaif.orchestrator import InferenceBackend   # runtime_checkable Protocol

class MyBackend:
    def infer(
        self,
        system: str,
        user: str,
        model_name: str = "",
        max_tokens: int = 1024,
    ) -> str:
        ...  # call your API, return raw JSON string

    def infer_stream(
        self,
        system: str,
        user: str,
        model_name: str = "",
        max_tokens: int = 1024,
    ) -> Iterator[str]:
        ...  # yield token chunks

app = nk.App([now], orchestrator=MyBackend())
```

`infer_stream` is optional for non-streaming callers; implement it as `return iter([self.infer(...)])` if your host doesn't support streaming. The protocol is `@runtime_checkable` so `isinstance(obj, InferenceBackend)` works.

## `from_click` limitations

`nk.from_click()` handles the common case of a flat `click.Group` with typed
options and arguments. Features not mapped to knaif semantics:

| click feature | Status |
|---|---|
| Nested `click.Group` inside a group | **Not supported.** Flatten to a single group or use `@nk.command`. |
| `nargs=-1` / variadic arguments | **Not mapped.** Declare the arg as `type: array` with `@nk.command` instead. |
| `click.Context` parameters (`pass_context`) | **Ignored.** Context objects cannot appear in model-generated plans. |
| `click.File` / `click.Path` types | Mapped as `type: string`; add `path_role` via `@nk.command` + `ArgSchema` for sandbox validation. |
| Callback-based click validation | **Not preserved.** Move validation into the function body; use `arg_schemas` (enum/min/max) for declarative checks. |
| `click.Choice` case-insensitive flag | Choices are forwarded to `ArgSchema.enum`; case insensitivity is not enforced at plan time. |
| Multiple decorators stacking commands (`@g.command` on top of `@other.command`) | Behaviour depends on click's command registry; only the last-registered `click.Group` is walked. |

## Not in v1

Three capabilities were scoped out deliberately, not overlooked. They are recorded here
so you can tell "considered and deferred" from "missing".

**Dev-authored intents (`@nk.intent`).** v1 ships **flat commands only** — one command
maps to one knaif `Step`. This keeps the decorator front door symmetric with
`nk.from_click()`, which cannot produce macros from a `click.Group`. For multi-step work,
either write the compound operation as ordinary Python inside one command, or let the
model compose several atomic commands using `$var` binding between plan steps. A
dev-facing macro API is a v2 question, and the honest reason to wait is that multi-step
chain fidelity is the weakest part of small-model planning today — a macro API would
inherit that.

**argv emission.** The SDK dispatches by calling your Python function directly. An
opt-in mode that instead *prints* argv (`["convert", "v.mp4", "--fmt", "mp4"]`) for a host
to re-dispatch is designed but unbuilt. It is deliberately feature-degraded: once the host
re-dispatches, knaif's confirmation gate, post-resolution sandbox re-validation, and intent
expansion **do not apply** — the host owns all of it. If you need cross-process dispatch
today, call `app.invoke(utterance, dry_run=True)` and render the returned plan yourself,
so you can see what those gates would have done.

**A `destructive` example.** The bundled `clock` app is read-only, so it does not exercise
the `destructive` confirmation gate. That machinery is covered by the ffmpeg and io skill
tests rather than by an SDK example; the contract itself is documented below and works the
same for `@nk.command(destructive=True)`.

## Developer boundary contract

- The model emits only `{"plan": [...]}`. Your functions never see raw NL.
- Your function receives typed kwargs; `FunctionStep` coerces string `"42"` → `int 42`.
- `$var` references in args are resolved at runtime before dispatch.
- Enum violations and type mismatches are caught at `validate_plan` time, before
  any function is called.
- `safety_category: "destructive"` steps require `confirmed=True` or `dry_run=True`.

## Core-contract friction notes

These are findings from the `clock` example app, which exercises the full
pipeline with non-file args (timezone names, ISO dates, enum values):

- **Filename guards do not misfire on non-file args.** The `_FILENAME_RE` /
  `_hallucinated_filename` checks in core are tuned for filenames with extensions
  (e.g. `clip.mp4`). IANA timezone strings (`Asia/Tokyo`), ISO dates
  (`2026-06-19`), and enum values (`iso`, `human`) pass through cleanly.
- **Sandbox path re-validation does not touch non-path arg names.** Only args
  named `path`, `src`, or `dst` are re-validated against the sandbox after
  variable resolution. Clock args (`tz`, `fmt`, `start`, `end`, etc.) are
  unaffected.
- **`nl_clarify_gate` fires on intentionally ambiguous requests.** `"convert to
  tokyo"` (missing required `value`) correctly returns a `clarify` response,
  proving the required-arg gate works for non-file domains.

## Example app

A complete time/date CLI using both front doors ships **inside the package**, so it is
runnable straight from a `pip install` — no checkout required:

```bash
python -m knaif.examples.clock "list timezones in europe"
python -m knaif.examples.clock.app_click "how many days from 2026-01-01 to today"
```

It lives at `python/core/knaif/examples/clock/`:

- `store.py` — pure Python logic (no knaif imports)
- `app_decorator.py` — `@nk.command` variant
- `app_click.py` — `from_click()` variant
- `__main__.py` — the `python -m` entry point; resolves Ollama → local GGUF → mock

Inference is best-effort: it uses Ollama if reachable, else a local GGUF, else mock. Mock
needs no model, so the example always runs on a fresh install.
