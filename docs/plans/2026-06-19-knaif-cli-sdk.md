# knaif.cli — Developer SDK for Natural-Language CLI Tools

**Status:** Done · **Created:** 2026-06-19 · **Completed:** 2026-06-20
**Owner:** core · **Ref:** PR #24

> **Status note:** All phases implemented (0.5–8 + Phase 6).
>
> **Kept 2026-07-22** (S7 decision). Everything verified present: the seven `knaif/cli/`
> modules, the renamed [`app.py`](../../python/core/knaif/app.py), all three
> `examples/clock/` files, and the three core seams — `ArgSchema` in
> [`registry.py`](../../python/core/knaif/registry.py), `validate_arg_by_schema` in
> [`planner.py`](../../python/core/knaif/planner.py), `CommandAgent.from_registry` in
> [`agent.py`](../../python/core/knaif/agent.py).
>
> **Nothing extracted — [SDK.md](../SDK.md) already carries it**, including the developer
> boundary contract and the `clock` friction findings. It also now has a *Not in v1*
> section covering this plan's three deferrals (see below), and `docs/TODO.md` tracks them
> with their triggers. Cite SDK.md; this plan is the decision record behind it.
>
> **Two predictions verified, one wrong** — the *Relationship to the Monorepo / Dual-Runtime
> Reorg* section made falsifiable forecasts, now scoreable:
> - ✅ **§4 held.** Declarative `arg_schema` was indeed "the portable, language-neutral
>   shape the Rust runtime wants": `native/crates/knaif-core/src/planner.rs` reads
>   `tool.arg_schemas` in three places and `clarify_gate.rs` keys off it too.
> - ✅ **§5 held.** The SDK did *not* satisfy the reorg's "author skill #2" prerequisite;
>   `documents` was authored separately, exactly as argued.
> - ❌ **§2 was wrong about the destination.** The reorg did not produce
>   `apps/knaif-py/src/knaif/cli/`; the SDK lives at `python/core/knaif/cli/`. The
>   *claim* — that the move would be mechanical with the import path unchanged — held:
>   `import knaif.cli as nk` still works. Corrected in place below.
>
> **Still deferred (all three demand-triggered, none needs a plan):** argv-emit mode
> (Phase 4), `@nk.intent` dev-authored intents (Decision 6, v2), and a `destructive` SDK
> example (Phase 7 note). See `docs/TODO.md` for the triggers.
>
> Paths below were written in the pre-monorepo `src/` layout and have been repointed;
> Phase 0's `docs/specs/…` path was never created — the "or reuse this file as the spec"
> option was taken, which is why that box is `[x]`.

**Goal:** Ship `knaif.cli`, a developer-facing, click-like SDK (a submodule of the existing
`knaif` package) that lets a third-party developer add a natural-language front end to
their CLI tool. It acts as an *addition to* or *replacement of* `click`: the developer
describes their commands (via a decorator, or by introspecting an existing `click.Group`),
and knaif becomes the middleware that maps natural language to the exact, validated
parameters those commands need — then dispatches them.

**Audience:** `pip install knaif`, `import knaif.cli as nk`, plug into an existing CLI.
Forking the knaif repo (for skill development, fine-tuning, the native runtime) stays the
path for complex scenarios; this SDK is the zero-fork path.

> **Naming (decided 2026-06-19).** `knaif.cli` is the new SDK for building CLIs.
> `knaif.app` is the existing operator CLI used to test skills (today's `knaif.cli`),
> which is **renamed** to free the `knaif.cli` namespace — see Phase 0.5. The `click`
> library currently powering the operator CLI is unrelated to the SDK's name.

---

## Locked Decisions (2026-06-19)

These were decided with the owner before writing this plan. Do not re-litigate them
without owner sign-off.

1. **Back door = direct function dispatch, argv emission opt-in.** NL → typed kwargs →
   call the developer's Python function directly, reusing the whole knaif engine
   (validation, safety categories, dry-run, intent expansion, variable binding, clarify/
   reject). Argv emission (`["convert","v.mp4","--fmt","mp4"]`) is a secondary, clearly
   feature-degraded output mode for hosts that keep their own dispatcher or run
   cross-process.
2. **Front door = decorator + `from_click` adapter.** Ship both: `@nk.command` on plain
   typed functions (the *replacement-of-click* story, for greenfield tools) **and**
   `nk.from_click(group)` that introspects an existing `click.Group` (the
   *addition-to-click, zero-rewrite* story).
3. **Inference = local only (ollama / llama.cpp / mock).** knaif is a local-inference
   tool by design; no paid or hosted-API backends are shipped. The existing
   `InferenceBackend` protocol stays the documented extension point so a developer *can*
   wire their own backend if they choose, but the SDK ships only the local backends.
4. **Packaging = submodule in `knaif`.** `pip install knaif`; `import knaif.cli`. One
   engine, one test suite, no separate distribution.
5. **Naming = swap.** SDK is `knaif.cli`; the existing operator CLI becomes `knaif.app`.
6. **SDK v1 = flat commands only.** A command maps to a click command → a knaif `Step`
   (symmetric with `from_click`, which cannot produce macros). Multi-step is left to model
   composition over atomic commands via existing `$var` binding; deterministic compound
   ops are written as ordinary Python. Dev-authored intents (`@nk.intent`) are deferred to
   v2.

### Consequences

This plan is the durable record of the decisions above (it absorbs what was briefly a
separate `docs/adr/0007`; the project's decision record lives in `docs/plans/`, per
CLAUDE.md).

- `knaif.cli` becomes a public API surface; semver applies from 0.1.0 onward.
- Core gains two domain-agnostic, optional-compatible additions — `ArgSchema` (typed arg
  metadata) in `registry.py` / `planner.py`, and `CommandAgent.from_registry()` (in-memory
  seam) in `agent.py`. Existing skills load unchanged.
- `InferenceBackend` is documented as the stable extension point for custom backends; no
  hosted backends are added to core.
- `knaif.app` (operator CLI) is functionally identical to the former `knaif.cli`; only the
  import path changed, and the `knaif-cli` console command name is preserved.

---

## Why This Is Mostly Reuse, Not New Architecture

knaif is **already a direct-function-dispatch engine.** Every tool is a class whose
`handle(args: dict, ctx) -> dict` receives a resolved kwargs dict and runs the real work
(`agent.py` `_execute_steps`). The pipeline already: builds a prompt from a tool registry,
infers `{ "plan": [ { "tool", "args" } ] }`, validates it, expands intents, resolves
variables, enforces safety categories, and dispatches to Python callables.

So the SDK is a **new front door that generates the registry + tool map in memory** from
Python metadata, plus a thin runner. The deterministic value props that differentiate
knaif from "just call an LLM" — typed validation of the *exact params*, dry-run,
confirmation gates, multi-step intents, clarify-on-missing-arg — all come for free.

### Key reuse seams and the gaps that block them

| Need | Exists today? | Action |
|---|---|---|
| Resolve NL → typed kwargs → call a callable | Yes (`Step.handle`) | Wrap dev fn in a generic `FunctionStep` |
| Tool registry, prompt, validate, expand, safety, clarify/reject | Yes | Reuse unchanged |
| Plan preview / approval / per-intent callbacks | Yes (`show_plan`, `require_approval`, `plan_confirmer`, `intent_completed`) | Reuse for the runner UX |
| **Typed arg metadata (type / enum / bounds / path role)** | **No** — registry is name-based; roadmap Task 4 "Open, not started" | **Phase 1**: land minimal `ArgSchema` in core |
| **Build an agent from in-memory objects (no YAML files)** | **No** — `CommandAgent.__init__` loads registry from a path; `Skill.load()` reads disk | **Phase 2**: add an in-memory construction seam |
| Sensible default backend for an embedded app | Partial — orchestrator + `models.yaml` exist | **Phase 6**: ergonomic local resolution (ollama → mock) + document the protocol |

### Boundary the SDK must document loudly

Direct dispatch runs **arbitrary developer code**. knaif validates and gates the *plan*
(typed args, sandbox path roles, `destructive` confirmation, dry-run), but the function
*body* is the developer's responsibility. The contract: declare `destructive=True` to get
the confirmation gate; honor `ctx.dry_run` for side effects; mark path args with a
`path_role` to get sandbox validation. knaif cannot sandbox the function body itself.

---

## Module Layout

```text
python/core/knaif/cli/                # the NEW SDK (a package; replaces today's cli.py module)
  __init__.py        # public API: command, Arg, Opt, App, from_click
  decorators.py      # @command, Arg, Opt, signature → ToolSpec introspection
  function_step.py   # FunctionStep(Step): generic callable wrapper + coercion
  build.py           # ToolSpec list → in-memory registry + tool_map → CommandAgent
  click_adapter.py   # from_click(group): click.Command introspection
  runner.py          # App: run() / invoke() / argv-emit mode
  inference.py       # default local backend resolution (ollama → mock); reuses orchestrator
python/core/knaif/app.py               # the existing operator CLI, renamed from cli.py (Phase 0.5)
examples/
  clock/             # the example app — a small read-only time/date CLI
    store.py         #   plain logic (datetime + zoneinfo), no knaif — the "real CLI"
    app_decorator.py #   @nk.command front door
    app_click.py     #   vanilla click.Group wrapped via nk.from_click
```

Core changes (kept domain-agnostic, per the core/skill boundary):
- `python/core/knaif/registry.py` — `ArgSchema` dataclass + loader (Phase 1).
- `python/core/knaif/planner.py` — schema-driven typed validation (Phase 1).
- `python/core/knaif/agent.py` — in-memory construction seam + reuse schema validation after
  variable resolution (Phases 1–2).

No skill-specific logic enters core. `knaif.cli` depends on core; core never imports
`knaif.cli`.

---

## Public API Sketch (target DX)

Decorator (replacement-of-click):

```python
import knaif.cli as nk

@nk.command(help="Add a task", keywords=["add", "create", "new"])
def add(
    title: nk.Arg(help="task title"),
    priority: nk.Opt(choices=["low", "med", "high"], help="urgency") = "med",
    due: nk.Opt(help="YYYY-MM-DD") = None,
):
    ...  # the real work; returns a result dict

@nk.command(help="Delete tasks matching a query", destructive=True)
def delete(query: nk.Arg(help="text to match"), ctx: nk.Ctx = None):
    if ctx.dry_run:
        return {"would_delete": _matches(query)}
    ...

app = nk.App([add, delete])
app.run()   # reads NL from argv/stdin, plans, confirms, dispatches
```

Adapter (addition-to-click, zero rewrite):

```python
import knaif.cli as nk
from myapp.cli import cli   # an existing click.Group

app = nk.from_click(cli)
app.run()                   # same engine, introspected from click metadata
# app.run(emit="argv")      # opt-in: print argv for the host to dispatch
```

Programmatic / embeddable / testable:

```python
results = app.invoke("delete the groceries task", dry_run=True)
```

---

## Phases

MVP = Phases 1–5 + 7 (decorator + `from_click` + example, on mock/local inference).
Phase 6 (backend ergonomics) is small and can land alongside Phase 4. Each phase is
TDD: write the failing test, implement, commit (RED → GREEN → COMMIT).

### Phase 0 — Spec + decisions

- [x] Write `docs/specs/2026-06-19-knaif-cli-sdk.md` (or reuse this file as the spec):
      scope, the dispatch/front-door/inference/packaging/naming decisions, the
      developer-code-boundary contract, and acceptance criteria.
- [x] Record the locked decisions and rationale (direct dispatch over argv, dual front
      door, local-only inference, submodule, knaif.cli ↔ knaif.app name swap) — captured in
      this plan's "Locked Decisions" + "Consequences" sections above.
- [x] Add a "Developer SDK" pointer to `docs/ARCHITECTURE.md` and `README.md`.

**Verify:** docs build/read cleanly; `just check` still green (no source changes).

### Phase 0.5 — Rename the existing operator CLI `knaif.cli` → `knaif.app`

Frees the `knaif.cli` namespace for the SDK. Pure rename; no behavior change. Must land
before Phase 2 creates the `knaif/cli/` package (a module and a package cannot share the
name).

- [x] Move `src/knaif/cli.py` → `src/knaif/app.py` (pre-monorepo paths; now under `python/core/knaif/`), preserving history with `git mv`.
- [x] Update the `pyproject.toml` console script target: `knaif.cli:main` → `knaif.app:main`.
      Keep the command name `knaif-cli` (or rename to `knaif`; owner's call — note in PR).
- [x] Update imports referencing `knaif.cli` (e.g. `from knaif.cli import cli` in
      `tests/test_cli.py`) to `knaif.app`.
- [x] Rename `tests/test_cli.py` → `tests/test_app.py` (git mv) so test naming tracks the
      module; SDK tests use `test_sdk_*.py` to stay distinct.
- [x] Grep the repo + docs for `knaif.cli` / `knaif-cli` / `cli.py` references and update
      prose (ARCHITECTURE, README, CLAUDE.md, eval docs) to the new module name.

**Verify:** `knaif-cli skills` and a mock `run` still work; `tests/test_app.py` green;
full suite green; no remaining import of `knaif.cli` as the operator CLI.

### Phase 1 — Typed `ArgSchema` in core (unblocks generating a registry from type hints)

This is roadmap Task 4, scoped to the minimal slice the SDK needs. Keep it
**optional-compatible**: skills/registries that omit `arg_schema` must keep loading and
validating exactly as today.

- [x] **RED**: tests in `tests/test_registry.py` for loading `arg_schema`
      (type / items / enum / min / max / path_role) into `ToolDef`.
- [x] **RED**: tests in `tests/test_planner.py` for schema-driven validation — type
      mismatch, enum violation, numeric out-of-bounds, and `path_role` sandbox escape all
      raise; `$var` references skip semantic checks (validated at runtime).
- [x] Add `ArgSchema` dataclass + `arg_schemas: dict[str, ArgSchema]` on `ToolDef`
      (`registry.py`); loader populates it from YAML.
- [x] Add `validate_arg_by_schema(...)` in `planner.py`; call it for every schema'd arg.
      Keep existing hard-coded checks for tools without a schema.
- [x] Reuse schema validation after variable resolution in `agent.py` (replace the ad-hoc
      `path`/`src`/`dst`/`file_type` re-checks with a schema-aware revalidation, without
      losing the step `output` field).
- [x] Document `arg_schema` in `docs/TOOL_SCHEMA.md`.

**Verify:** new tests pass; full suite green; existing skills (ffmpeg/io) unaffected.

### Phase 2 — In-memory agent construction seam + generic `FunctionStep`

- [x] **RED**: `tests/test_function_step.py` + `tests/test_agent.py` — build a
      `CommandAgent` from a hand-built registry + `tool_map` (no YAML files) and dry-run
      a one-step plan end to end.
- [x] Add `FunctionStep(Step)` in `cli/function_step.py`: holds `name`, the callable,
      whether the callable wants a `ctx` param, and the arg schema; `handle(args, ctx)`
      coerces args to declared types and calls `fn(**kwargs)` (passing `ctx` if requested).
- [x] Add `CommandAgent.from_registry(registry, tool_map, ...)` in `agent.py`. Reuses
      every existing pipeline method; only the *source* of registry + tool_map changes.
- [x] Core control tools (clarify/reject/done) still merge in automatically.

**Verify:** in-memory agent validates, expands, gates, and dispatches identically to a
YAML-loaded skill on equivalent inputs.

### Phase 3 — Decorator front door

- [x] **RED**: `tests/test_cli_decorators.py`.
- [x] Implement `@command`, `Arg`, `Opt`, `Ctx` in `cli/decorators.py` using
      `inspect.signature` + `typing.get_type_hints` + `Annotated` metadata.
- [x] Map Python types → `ArgSchema`; `choices` → enum.
- [x] `cli/build.py` turns a list of decorated commands into registry + `tool_map` of
      `FunctionStep`s.
- [x] Coercion: JSON-from-LLM strings coerced to declared types before dispatch (in
      `FunctionStep`); enum violations rejected at `validate_step`.

**Verify:** a 3-command decorated app builds an agent and dispatches correct kwargs from
NL via the mock backend; missing/ambiguous params produce a clarify, not a crash.

### Phase 4 — `App` runner

- [x] **RED**: `tests/test_cli_runner.py`.
- [x] Implement `App`: `__init__(commands, orchestrator, sandbox, root, ...)`, calls
      `build_registry()` + `CommandAgent.from_registry()`. `invoke(utterance, dry_run,
      confirmed)` delegates to `agent.run()`. `run(argv)` CLI entry point.
- [x] `invoke()` programmatic API (no I/O) for embedding and tests.
- [ ] argv-emit mode: deferred to a future phase.

**Verify:** end-to-end on mock backend; argv mode output matches expected tokenization.

### Phase 5 — `from_click` adapter

- [x] **RED**: `tests/test_cli_click_adapter.py`.
- [x] Implement `from_click(group)` in `cli/click_adapter.py`: walk `group.commands`; map
      `click.Argument`/`click.Option` → `ArgSchema` via `isinstance` type checks;
      option defaults filled by `_make_wrapper()` so callbacks get full kwargs.
- [x] Document unmapped click features (nested groups, `click.Context` params).

**Verify:** an existing click app gains NL with zero changes to its command functions.

### Phase 6 — Local backend ergonomics + documented protocol

Local-only by design: no paid/hosted backends. This phase just makes "bring your own
local model" frictionless and documents the seam.

- [x] Confirm the existing `InferenceBackend` protocol (`infer(system, user, *,
      max_tokens) -> str`; optional `infer_stream`) and document it as the extension point
      a developer can implement to wire any backend of their own.
- [x] **RED**: `tests/test_sdk_inference.py` — `App(backend=...)` override is honored; with
      no override, resolution prefers a reachable ollama, else falls back to mock with an
      actionable message; a `--model` / model-path escape hatch routes to llama.cpp.
- [x] Implement `cli/inference.py` resolution reusing the existing orchestrator and
      `models.yaml` (no new dependencies).

**Verify:** resolution tests pass; an embedded app runs against ollama when present and
degrades to mock with a clear message when not.

### Phase 7 — Example `clock` app (time/date utility; SDK integration test + non-file leakage probe)

A deliberately tiny, **read-only**, **cross-platform** time/date CLI. Flat commands only
(Decision 6). Doubles as the SDK integration test and — being 100% non-file — a clean probe
for whether core's filename-tuned guards (`_FILENAME_RE`, `_hallucinated_filename`,
`nl_clarify_gate`) misfire on non-file args (dates, timezone names, enum values).

**Cross-platform & dependencies.** Pure stdlib `datetime` + `zoneinfo`; local zone via
`datetime.now().astimezone()`; no OS-specific code. `zoneinfo` reads the IANA database from
the OS, which some hosts (notably Windows, and minimal containers) lack — so the example
declares **`tzdata`** as a plain, unconditional **example-only** dependency. `tzdata` is a
pure-Python data package: harmless on Linux/macOS (system db still wins) and the IANA source
on hosts without one. The SDK and core add **no** new dependencies. If `tzdata` is somehow
absent, `store.py` falls back to UTC + fixed offsets with a clear message.

#### Command spec (all read-only, all flat)

| Command | Params — type · default · choices | Behavior |
|---|---|---|
| `now` | `tz: str = None` · `fmt: enum = "iso"` (iso\|unix\|rfc2822\|human) · `date_only: bool = False` | Current date/time, optionally in `tz`, in the chosen format. |
| `convert` | `value: str` **(required)** · `from_tz: str = None` · `to_tz: str = "UTC"` · `fmt: enum = "iso"` | Convert an ISO/unix/loose time from `from_tz` to `to_tz`. If `value` carries an offset, `from_tz` is ignored. |
| `diff` | `start: str` **(required)** · `end: str = "now"` · `unit: enum = "days"` (seconds\|minutes\|hours\|days) | Elapsed time between two instants, expressed in `unit`. |
| `zones` | `query: str = None` | List/search IANA timezones; a curated common subset when `query` is omitted. |

**`fmt` outputs:** `iso` → `2026-06-19T14:30:00+09:00`; `unix` → `1781000000` (epoch
seconds); `rfc2822` → `Thu, 19 Jun 2026 14:30:00 +0900`; `human` →
`Thursday, June 19, 2026 at 2:30 PM JST`.

**Timezone input:** IANA names (`Asia/Tokyo`, `UTC`, `America/New_York`) plus a small
friendly-alias map for NL (`tokyo`, `new york`/`nyc`, `london`, `paris`, `sydney`, `la`,
`berlin`, `india`…). The SDK handles the typed plumbing; fuzzy alias resolution lives in the
function body — a concrete illustration of the plan-vs-body boundary.

**Type coverage:** enum (`fmt`, `unit`), bool flag (`date_only`), required vs optional,
strings — exercising NL → typed kwargs, clarify-on-missing-required, and enum rejection
across both front doors on identical logic.

#### Example command lines (NL in → resolved dispatch)

```text
clock "what time is it in Tokyo"               -> now(tz="Asia/Tokyo", fmt="iso")
clock "current time in new york, 24-hour"      -> now(tz="America/New_York", fmt="human")
clock "unix timestamp right now"               -> now(fmt="unix")
clock "today's date in UTC"                     -> now(tz="UTC", date_only=True)
clock "convert 2026-06-19T15:00 from London to Tokyo"
                                               -> convert(value="2026-06-19T15:00",
                                                          from_tz="Europe/London",
                                                          to_tz="Asia/Tokyo")
clock "convert 1781000000 to human in Paris"   -> convert(value="1781000000",
                                                          to_tz="Europe/Paris", fmt="human")
clock "how many days from 2026-01-01 to today" -> diff(start="2026-01-01", end="now",
                                                       unit="days")
clock "hours between 09:00 and 17:30"          -> diff(start="09:00", end="17:30",
                                                       unit="hours")
clock "list timezones in europe"               -> zones(query="europe")
clock "convert to tokyo"                        -> clarify: "What time should I convert?"
clock "delete every timezone"                   -> reject / clarify (no such command)
```

(`clock` = `python -m examples.clock`; multi-word NL also works unquoted. The `from_click`
variant additionally keeps its classic flag interface, e.g. `clock now --tz Asia/Tokyo
--fmt human`, *and* gains the NL path.)

#### Build tasks

- [x] `examples/clock/store.py` — pure stdlib datetime + zoneinfo, no knaif imports.
- [x] `examples/clock/app_decorator.py` — `@nk.command` front door over `store.py`.
- [x] `examples/clock/app_click.py` — `click.Group` wrapped via `nk.from_click(clock_cli)`.
- [x] `pyproject.toml` `clock` optional extra pinning `tzdata`; document as example-only.
- [x] **RED→GREEN**: `tests/test_example_clock.py` — store unit tests + plan-dispatch
      plumbing for both front doors; enum rejection verified at `validate_plan`.
- [x] Capture core-contract friction (filename-guard misfires on timezone/date args).

**Note — destructive gate not covered here.** A time/date utility is read-only, so this
example does not exercise the `destructive` confirmation gate; that machinery stays covered
by the ffmpeg/io tests. If we later want the SDK's own destructive coverage in an example,
the smallest honest add-on is a saved-zones config (`save` / `forget`, with `forget`
destructive) — deferred unless wanted.

**Verify:** `python -m examples.clock "what's the date in UTC"` plans and dispatches on at
least one OS without a system IANA db (proving the `tzdata` path); both front-door variants
pass the same test matrix.

### Phase 8 — Packaging, docs, quickstart

- [x] Export the public API from `knaif/cli/__init__.py`; `import knaif.cli as nk` works.
      Exports: `App`, `Arg`, `Ctx`, `Opt`, `command`, `from_click`.
- [x] `pyproject.toml`: `knaif.cli` package ships via `where = ["src"]`; no new runtime deps.
- [x] `docs/SDK.md`: quickstart, decorator + `from_click` guides, local backend setup.
- [x] Cross-link from `docs/ARCHITECTURE.md`, `README.md`, `CLAUDE.md`.

**Verify:** fresh venv `pip install -e .`, run the quickstart end to end; `just check` and
full suite green.

---

## Relationship to Existing Plans

- **Recommended roadmap (2026-05-29) Task 4 (typed arg metadata)** is a hard prerequisite
  and is absorbed as Phase 1 here. Landing it serves both efforts.
- **Core/skill boundary** is preserved: all SDK code lives under `knaif/cli/`; core gains
  only generic, domain-agnostic typed-schema + in-memory-construction support. Core never
  imports `knaif.cli`.

## Relationship to the Monorepo / Dual-Runtime Reorg (2026-06-17)

Short version: **the reorg does not block this plan, and this plan does not block the
reorg. Build the SDK first, in the current `src/` layout.** The interactions are all
either neutral (mechanical path moves) or positive (the SDK de-risks the reorg).

### 1. The SDK is Python-only and never enters native scope

`knaif.cli` dispatches to *live Python functions*. That is inherently a Python-authoring/
embedding capability — there is nothing for the Rust native runtime to mirror. So the SDK
lives squarely in the Python product (`python/core/` after the move — this section
originally predicted `apps/knaif-py/`; see the §2 outcome note) and is simply
**out of scope** for the dual-runtime work. No competition for the native track.

### 2. The move is mechanical for the SDK

When the reorg runs, `python/core/knaif/cli/` moves along with the rest of `knaif/`, and its tests
move with `tests/`. `import knaif.cli as nk` is unchanged. No design rework — just a path
move handled by the reorg's Phase 2.

> **Outcome (2026-07-22):** correct in substance, wrong on the destination. This section
> predicted `apps/knaif-py/src/knaif/cli/`; the reorg landed on
> **`python/core/knaif/cli/`** (tests in `python/core/tests/`). The prediction that
> mattered held — the move was mechanical and `import knaif.cli as nk` is unchanged.

### 3. The SDK *sidesteps* the reorg's most contentious decision

The reorg's Open Decision #3 is "is `shared/skills/` pure data, and does `Skill.load()`
resolve handler modules from a configurable root?" The SDK's in-memory construction seam
(Phase 2) builds a registry + tool map **with no YAML files and no on-disk handler
module at all** — a separate code path from `Skill.load()`. So the SDK is insulated from
however Decision #3 lands. Requirement on us: the Phase 2 seam must be **additive** (a new
`from_registry` / `from_objects` constructor), never a fork of the disk loader, so the
reorg can refactor `Skill.load()` freely without touching the SDK path.

### 4. The SDK *helps* the reorg in two concrete ways

- **It stabilizes the registry contract the Rust port must mirror.** The reorg explicitly
  defers Phase 4+ until the contract settles, and lists `registry.rs` / `planner.rs`
  parity as obligations. Phase 1 here makes argument typing **declarative** (`arg_schema`
  in YAML) instead of Python-hardcoded validation — which is exactly the portable,
  language-neutral shape the Rust runtime wants. Landing it now means the contract Rust
  copies is already typed.
- **It is a second, non-ffmpeg consumer that flushes core leakage early.** Running the
  same core pipeline with non-media tools surfaces ffmpeg-shaped assumptions — notably the
  `input_refs` media-vocab leakage already flagged in project memory — *before* the Rust
  port freezes the contract. Phase 7 captures these as findings.

### 5. But the SDK does **not** satisfy the reorg's "author skill #2" prerequisite

The reorg wants a second *hand-authored YAML skill* (like io/ffmpeg) to prove the
*authoring* contract (`skill.yaml` / `tools.yaml` / `prompt.yaml` / `skill_class:` /
`profiles/` / `arg_value_sets`) isn't ffmpeg-shaped. The SDK bypasses YAML authoring
entirely (it generates the registry from Python), so it exercises the **runtime /
validation / dispatch** contract but not the **authoring** contract. The two are
complementary: the SDK stresses one half, a hand-authored skill #2 stresses the other.
The reorg's prerequisite stands on its own.

### 6. Minor overlaps to keep in sync (not blockers)

- **argv rendering.** The reorg's eval `Artifact` work and this plan's argv-emit mode both
  produce argv; share one `shlex`-based join helper rather than duplicating.
- **Namespaces across the move.** Both `knaif.cli` (SDK) and `knaif.app` (operator CLI)
  keep their names across the reorg; only their path prefix changes.

### Recommended sequencing

1. Land **Phase 0.5 (rename operator CLI)** and **Phase 1 (typed `ArgSchema`)** now —
   Phase 1 is shared infrastructure for the SDK, the recommended roadmap, *and* the Rust
   port.
2. Build the SDK (Phases 2–8) in the current `src/` layout.
3. Author a hand-written **skill #2** (separate effort) to satisfy the reorg prerequisite,
   informed by leakage the SDK surfaced.
4. Run the **monorepo reorg**; `knaif/cli/` and `knaif/app.py` move mechanically with the
   Python product.

## Risk Controls

- Keep `ArgSchema` strictly optional so existing skills and any third-party registries
  load unchanged.
- The in-memory construction seam must reuse the *existing* pipeline methods, not fork
  them — no second execution path to keep in sync.
- The Phase 0.5 rename is pure and lands first; a module (`cli.py`) and a package
  (`cli/`) cannot coexist, so the rename gates the SDK package.
- argv-emit mode is explicitly degraded; document that dispatch-time gates (confirmation,
  sandbox revalidation, intents) do not apply when the host re-dispatches argv.
- Local-only inference: no paid/hosted backend, no new runtime dependency; the SDK reuses
  the existing orchestrator (ollama / llama.cpp / mock).
- **Cross-platform throughout.** No OS-specific code paths in the SDK or examples; rely on
  stdlib and pure-Python packages (e.g. `tzdata`) so Windows, Linux, and macOS behave
  identically. Any platform-conditional logic must be justified and tested on all three.
- Be explicit in docs that knaif gates the plan, not the developer's function body.
```
