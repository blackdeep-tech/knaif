# Architecture

`knaif` is a skill-hosting command agent library. A small local model maps natural language to a JSON plan; deterministic Python code validates, expands, optimizes, confirms, and executes that plan.

## Current Pipeline

```text
User request
  -> CommandAgent.build_prompt()
  -> InferenceOrchestrator or mock inference
  -> parse_plan()
  -> chain-intermediate linking + hallucinated-filename gate (in infer())
  -> normalize_plan() -> apply_defaults()
  -> validate_plan()
  -> resolve_stems() -> nl_clarify_gate()
  -> Intent.expand()                                        (per intent tool)
  -> validate expanded plan
  -> optimize_plan()
  -> Step/Intent.preflight() or Skill.preflight()          (skipped if dry_run)
  -> [optional] summarize_plan() → plan_display callback    (StepA)
  -> [optional] plan_confirmer approval gate                (StepB)
  -> resolve_args()
  -> HandlerContext
  -> Step.handle()                                          (via tool_map)
  -> result history
  -> [CLI] Skill.format_results() renders for display
```

StepA and StepB are off by default. They are described in detail under [Plan Preview and Approval](#plan-preview-and-approval) below.

The core invariant is unchanged across skills:

```json
{ "plan": [ { "tool": "...", "args": {} } ] }
```

The model never executes commands, never emits raw shell commands, and never owns safety decisions.

## Core Package

`knaif/` contains the domain-agnostic runtime.

| Module | Responsibility |
|---|---|
| `agent.py` | `CommandAgent`, plan expansion, optimization, execution (`tool_map` dispatch), inference loop |
| `planner.py` | JSON parsing, step validation, variable binding, optimizer |
| `nl_clarify_gate.py` | deterministic pre-expansion clarify gate (see [The clarify gate](#the-clarify-gate)) |
| `registry.py` | `ToolDef`, flat YAML registry loading, retrieval, unsafe phrase loading |
| `tool.py` | `Step` / `Intent` ABCs — the two tool interface kinds |
| `skill_base.py` | `Skill` base class authors subclass (`tools`, `preflight`, `format_results`, `run_artifact`) |
| `core_tools.py` (+ `contracts/runtime/core_tools.yaml`) | built-in core control Steps (`clarify`/`reject`/`done`/`wait_for_confirmation`) and their metadata; the canonical YAML lives in `contracts/runtime/` with a synced wheel copy (`just sync-runtime`), loaded via the shared `load_registry` |
| `steps/` | shared Step library (`ResolveInputs`); `steps.yaml` metadata lives in `contracts/runtime/` |
| `skill.py` | `Skill.load()` — builds the `name → Tool` map, prompt loading, fail-fast validation |
| `handler_api.py` | `HandlerContext` shared by all Steps |
| `prompt.py` | model-facing prompt construction |
| `orchestrator.py` | local inference backends (behind the `InferenceBackend` protocol) |
| `evaluator.py` | evaluation helper functions |
| `cli/` | **Developer SDK** — `@command` decorator, `App` runner, `from_click()` adapter, `local_ollama()` helper. See [docs/SDK.md](SDK.md). |

## Skill Packages

A skill lives under `skills/<name>/` and owns all domain-specific behavior. The
declarative contract sits at the bundle top (readable by every runtime); the Python
implementation lives in a `python/` subpackage. See `docs/TOOL_SCHEMA.md` and `AGENTS.md`
for the authoritative bundle layout.

```text
skills/my_skill/
  skill.yaml
  tools.yaml
  prompt.yaml
  profiles/                 # optional — skill data resolved via ctx.skill_dir
  data/                     # corpora: eval / train / safety
  eval/                     # skill-owned fixtures + verifiers
  native/                   # optional — Rust crate, if the skill ships natively
  python/
    __init__.py             # re-exports the Skill subclass
    handlers.py             # tool classes + Skill subclass
    intents.py steps.py …   # optional — split via relative imports
    tests/
```

`Skill.load()` reads `skill.yaml`, instantiates the `Skill` subclass named by `skill_class:`, builds a `name → Tool` map from its `tools` list (merging the core control Steps and any shared `knaif.steps`), validates each class against its `tools.yaml` entry, renders prompt examples, and passes everything into `CommandAgent.from_skill()`.

Public entry points:

```python
from knaif import create_agent, list_skills
from knaif import CommandAgent

list_skills()
agent = create_agent("io", sandbox="./sandbox")
agent = CommandAgent.from_skill("skills/ffmpeg", sandbox="./media")
```

Built-in skills (`io` is `status: stale` — hidden from `list_skills()` discovery
pending a rebuild, but still loadable explicitly):

- `documents`: local document toolkit — inspect, extract, find, convert, combine, and compress files
- `ffmpeg`: media intent tools expanded into deterministic FFmpeg workflows
- `io`: file listing, recursive search, move, and delete inside a sandbox (**stale** — under rebuild)

## Tool Registry Format

The current registry format is a flat YAML mapping loaded into `ToolDef`. Behavior lives in `Step` / `Intent` classes linked to their YAML entry by `name`. The core does not currently execute subprocess or HTTP handlers from declarative schema definitions.

Example:

```yaml
list_files:
  description: "List files in a folder, optionally filtered by type or pattern."
  keywords: [list, show, folder]
  required_args: [path]
  optional_args: [file_type, pattern]
  safety_category: safe
  readonly: true
  mock_args:
    path: "{sandbox}"
```

Internal workflow tools use `internal: true` so they are valid during execution but hidden from model prompts.

## Steps And Intents

A tool is a class implementing one of two interfaces ([`tool.py`](../python/core/knaif/tool.py)):

```python
class MyStep(Step):       # leaf: executes, returns a result
    name = "my_tool"
    def handle(self, args: dict, ctx: HandlerContext) -> dict: ...

class MyIntent(Intent):   # macro: expands into a sub-plan of Steps
    name = "high_level_tool"
    def expand(self, args: dict) -> list[dict]: ...
```

`Skill.load()` instantiates these into a `name → Tool` map; `CommandAgent` dispatches a
leaf step through `tool_map[name].handle()` and compiles an intent away via
`tool_map[name].expand()` before execution. The FFmpeg skill uses Intents to turn
model-visible operations such as `prepare_for_platform` into internal workflows such as
`resolve_inputs`, `inspect_media`, `build_recipes`, `run_preview`,
`wait_for_confirmation`, and `run_batch` — all of which are Steps. Intents compile away
at expansion; the executor only ever runs Steps.

## Variable Binding

Any plan step may declare:

```json
{ "tool": "inspect_media", "args": { "files": "$files" }, "output": "$probes" }
```

`execute_plan()` stores handler results by output variable name and resolves `$var` or `$var.field` references before each later step. Resolved path and known enum args are revalidated before dispatch.

## Plan Optimizer

`optimize_plan()` removes redundant **readonly** steps — a readonly step is dropped only
when a later action step exists and no subsequent step references its output variable.
Terminal readonly steps are always preserved.

That narrow scope is deliberate. The optimizer does **not** fuse adjacent operations into
one command, even where fusing would be more efficient (two re-encodes of the same file,
say). Fusion needs skill-specific knowledge — which operations commute, which are
order-sensitive — and putting it here would leak that knowledge into core and entangle it
with the general chaining path, where every plan would then have to be checked for
fuseability. Operations that genuinely belong in one command are declared as a flag on
the skill's own tool instead; see
[TOOL_SCHEMA.md](TOOL_SCHEMA.md#tool-granularity--a-flag-or-its-own-tool).

## Plan Normalization

`normalize_plan()` runs between parsing and validation and makes two schema-driven
corrections to what the model emitted:

1. **`$var` output promotion** — an `args["output"]` holding a `$identifier` moves to the
   step-level `output` field. Tools that legitimately declare `output` as an arg always
   receive filenames there, never `$identifier`, so the promotion is unambiguous.
2. **`input` / `inputs` reconciliation, in both directions** — small models freely
   interchange the singular and plural forms. When a tool's schema declares exactly one
   of them and the model supplied the other, the value is unwrapped or wrapped to match.
   A multi-element `inputs` given to a scalar-`input` tool is left alone for validation
   to reject, because that is a genuine error rather than a spelling difference.

Both passes are driven entirely by the tool schema — no tool is named, so core stays
skill-agnostic — and neither ever merges or overwrites a value the model supplied.

The governing principle is **honor the value, don't strip it**. When a model emits a
reasonable argument the schema rejects, the fix is to make the argument work (thread it
through, or reconcile its shape), not to drop it and not to add a blanket "coerce or
discard unknown args" layer. A blanket layer would mask genuine model errors — the
signal that a small model is confused about a tool's interface is exactly what the eval
needs to surface.

## Deterministic Clarify

Deciding *whether to act or to ask* is structural validation, not intent extraction, so
it belongs in code. The model is never the arbiter of a fact it cannot know — such as
whether a file exists. One rule governs it:

> **Input missing or unresolvable → clarify. Output missing → default it and proceed.
> Sandbox escape → reject.**

- **Input** — each required input resolves against `sandbox if sandbox is not None else
  root`. Absent, or naming a file that does not exist → a terminal `clarify` step. The
  system must never guess *which* file to act on.
- **Output** — if only the output is missing, `apply_defaults()` fills the tool's
  declared `defaults` (see [TOOL_SCHEMA.md](TOOL_SCHEMA.md)) and execution proceeds. The
  system *may* name an output; the defaulted name surfaces in the plan preview so the
  user can rename it. A missing output must never trigger clarify.
- **Sandbox escape** — only meaningful when a sandbox is set; that branch is guarded by
  `if sandbox is not None`.

Skill `preflight` errors therefore become terminal outcomes rather than exceptions:
`classify_preflight_errors()` maps them to `reject` (sandbox escape) or `clarify` (all
others), and `execute_plan()` returns that as the plan result. Raises are reserved for
malformed plans — unknown tool, bad output syntax, a preflight function that itself
throws.

Because the base directory is the only difference between eval and the real CLI (which
runs in open mode with `sandbox=None`, resolving against cwd), one code path serves
both. The rule is also language-independent: a file-existence check does not care what
language the request was written in.

### The clarify gate

Preflight only sees plans that survive validation. A second deterministic gate catches
the earlier failure — the model referencing a file the user never gave it — and runs in
two places:

- **At infer time** (`CommandAgent.infer`, post-parse) — `_link_chain_intermediates()`
  first fills in an omitted step-1 `output` and points the consuming step at it, then
  `_hallucinated_filename()` downgrades the plan to `clarify` if any filename-like
  arg is absent from the utterance.
- **Before expansion** (`execute_plan`, after stem resolution) — `nl_clarify_gate()`
  clarifies when a required file input is under-specified and injection cannot resolve
  it, or when a tool's declared `grounded_args` (e.g. a password) hold a value the
  model invented.

`_hallucinated_filename` flags invented **inputs** only. It deliberately skips two
things, and both exclusions are load-bearing:

- the `output` arg — output filenames are the model's to invent (the same asymmetry as
  the governing rule above);
- any value an earlier step declares it will produce — a legitimate chained
  intermediate, not a hallucination.

Without those exclusions the guard overrides correct plans: it was doing exactly that
to every chained ffmpeg request until the exclusions were added, and restoring them
moved `trim` from 44% to 84% outcome accuracy. A guard that inspects every string arg
is itself a form of model-gatekeeping — scope it to the decision it can actually make.

**Matching is name-based, never property-based.** "The 4K video" resolves to a real file
only by matching the token against known *filenames* (`clip_4k.mp4` contains `4k`). The
gate never probes files — no `ffprobe`, no inspecting resolution or codec or duration to
decide which file is "the 4K one". File inspection at plan time is out of scope, so the
only signal is the filename token. This is why injection ON and OFF differ solely in
*whether a filename list is available to match against*, never in *how* matching works.

The gate never substitutes a file — that is the stem resolver's job, or the injection
step's. It only downgrades `plan` → `clarify`. Its second responsibility is symmetric:
a tool's declared `grounded_args` (a password, a key) must hold a value traceable to the
utterance, not one the model invented.

Design rationale and the measured effect of adding the gate:
[nl-clarify-gate](plans/2026-06-09-nl-clarify-gate.md).

## Safety Model

Safety is driven by `ToolDef.safety_category`.

- `safe` tools can execute normally.
- `destructive` tools require `dry_run=True` or `confirmed=True`.
- `wait_for_confirmation` is an internal core tool for preview gates.
- Handlers must respect `ctx.dry_run`.
- Skill authors should use core path-resolution helpers for sandbox-sensitive file operations.

`HandlerContext` carries `root`, `sandbox`, `dry_run`, `confirmed`, `skill_dir`, `confirmer`, and `confirm(prompt, preview=None)`.

**Why this is a rule and not a prompt.** In [the 2026-07-02 agent comparison](experiments/2026-07-02-agent-vs-knaif-realworld.md), the same destructive request ("delete the original clip.mp4") was put to three premium coding agents with full tool permissions: one refused, and two deleted the file. Whether a model-mediated agent blocks a destructive request depends on the CLI/scaffold/model combination on the day. Because knaif's gate is `safety_category`-driven code, the outcome is a property of the registry, not of the model — which is also why skill authors must classify tools honestly.

### Known gap: validation stops at dispatch

Every stage above is deterministically validated — normalize, validate, stem-resolve,
coerce, clarify-gate, preflight — but **all of it happens before the command runs**.
After dispatch the only success signal is `returncode == 0`.

So a command that exits 0 while producing the wrong artifact is reported as success:
a leaked argument yields an mp3 stream inside a `.flac` container, ffmpeg is perfectly
happy, and nothing downstream disagrees. The ffmpeg skill does ffprobe its outputs, but
that step records a summary without asserting it against what was asked, so it cannot
fail. **Exit 0 is not the same as goal achieved, and the pipeline currently cannot tell
the difference.**

The fix is designed but not built — populate an `expected` block from the recipe and
give the existing verify step an assertion, surfaced through a generic `verified: False`
that core honors the way it already honors `returncode`. See
[verify-output](plans/2026-06-09-verify-output.md). Until then, treat a reported success
as "the command ran", not "the artifact is right".

## Plan Preview and Approval

`CommandAgent.execute_plan()` exposes two optional hooks that fire after the intent
plan has been expanded, optimized, and preflighted — but before `resolve_args()` and
execution (see the pipeline diagram above). The preview summarizes the *intent*-level
tools (not the expanded steps), and approval is requested per non-terminal intent:

- **StepA — plan preview** (`show_plan=True`): renders a one-sentence human-readable
  summary of the *intent* plan (e.g. *"Will convert clip1.mov to mp4, then
  extract audio from clip1.mov as mp3."*) via the `plan_display` callback, or `print`
  as fallback. The summary string is also stored on `agent.last_plan_summary` and
  generated by `summarize_plan()` in [`planner.py`](../python/core/knaif/planner.py), which is
  domain-agnostic — per-skill phrasing comes from each intent's `Intent.summarize()`
  (see [TOOL_SCHEMA.md](TOOL_SCHEMA.md)).
- **StepB — approval gate** (`require_approval=True`): pauses and asks the
  `plan_confirmer` callback to approve each non-terminal intent before its workflow runs.
  Approval is **per intent**, not whole-plan: declining intent _N_ stops there but keeps
  the results of intents 1.._N_-1 (for a single-intent plan this means declining returns
  `[]`). Implies `show_plan=True` (cannot approve what you cannot see).

Both flags can be set on the `CommandAgent` constructor or overridden per-call:

```python
agent = CommandAgent.from_skill(
    skill_dir, sandbox,
    show_plan=True,
    require_approval=True,
    plan_display=my_display_fn,
    plan_confirmer=my_approval_fn,
)
agent.execute_plan(payload, show_plan=False)   # per-call override
```

`plan_confirmer` is **distinct** from the existing `confirmer`: the latter is invoked by
`HandlerContext.confirm()` for mid-workflow `wait_for_confirmation` gates inside an
expanded workflow, and has signature `(prompt: str, preview: dict | None) -> bool`.
`plan_confirmer` has signature `(summary: str) -> bool` and only fires for the
per-intent approval gate. Keep them separate; do not multiplex.

**Non-interactive fallback.** When `require_approval=True` and `plan_confirmer is None`,
the gate returns the `confirmed` flag passed to `execute_plan()`. This matches the
contract of [`HandlerContext.confirm()`](../python/core/knaif/handler_api.py) and keeps the
library safe for tests, eval runs, and headless callers — there is no blocking stdin
prompt.

**Decline semantics differ between the two gates:**

| Gate | Trigger | On decline |
|---|---|---|
| Per-intent (StepB) | `plan_confirmer(summary)` returns False for intent _N_ | Stops at intent _N_; `results` keeps intents 1.._N_-1 (single-intent plan → `[]`) |
| Mid-workflow | `wait_for_confirmation` step inside an expanded workflow | Loop breaks but `results` contains every step up to and including the gate |

## Skill-Provided Behavior

A skill's `handlers.py` defines tool classes and one `Skill` subclass. The core never
references skill names directly — it dispatches through the `name → Tool` map and the
`Skill` instance's methods. A skill with an `__init__.py` loads as a package, so its
handler code may be split across modules via relative imports. See
[TOOL_SCHEMA.md](TOOL_SCHEMA.md) for the full contract.

| Hook | Where | Required | Purpose |
|---|---|---|---|
| `handle` | `Step` method | yes (per Step) | Execute a leaf tool; return a result dict. |
| `expand` | `Intent` method | yes (per Intent) | Expand an intent tool into a sub-plan of Steps. |
| `summarize` | `Intent` method | no | One-sentence plan-preview clause for the intent. |
| `preflight` | `Step`/`Intent` or `Skill` method | no | Validate args before the approval gate; per-tool override wins, else `Skill.preflight`. |
| `format_results` | `Skill` method | no | Convert raw execution results into structured CLI items. |
| `run_artifact` | `Skill` method | no | Re-execute a rendered artifact against a fixture (eval suite only). |

Because the core is shaped as data structures + pure functions + a few interfaces
(`Step`/`Intent`/`Skill`, the `InferenceBackend` protocol), it ported to the native
runtime (Rust `trait` + `impl`) as a near-mechanical translation — see
[Dual-Runtime](#dual-runtime).

## Inference Backends

Implemented inference backends:

- `llama_cpp`
- `ollama`

Development and tests often use mock inference. Mock inference is driven by registry `keywords`, `mock_args`, and `unsafe_phrases`.

## Re-Planning Loop

`CommandAgent.run()` executes a bounded re-planning loop:

1. retrieve relevant tools
2. infer a plan
3. execute it
4. append results to history
5. stop on `done`, `clarify`, `reject`, repeated stale steps, or `max_steps`

Direct calls to `execute_plan()` can execute expanded multi-step plans with variable binding in one pass.

### Why static planning is the default (decided 2026-06-06)

Dependent multi-step work — a later step consuming an earlier step's result — could be
served either by static full-plan-up-front execution or by re-planning (infer one step,
execute, feed the result back, infer the next). knaif uses **static planning with
variable binding**; the re-planning loop stays available but is not the default and not
on the critical path.

The distinction that settles it:

- **Data dependency** — the *sequence* of steps is known up front; only the *values*
  flowing between them are runtime-determined (a probe result, a filled quantity).
  Variable binding already covers this: the model emits the whole plan with `$var`
  references and `resolve_args()` wires real values in just before each step runs. One
  inference call.
- **Decision dependency** — the *next action itself* is unknowable until an earlier
  result is observed (branching, unknown-length loops, failure recovery). This is the
  only case that genuinely needs re-planning.

Every chaining case knaif has is data dependency. The deciding factor is safety:
re-planning **executes step A before the model has proposed step B**. For an
irreversible or outward-facing action that means committing to A with no way to show the
user what follows. Static planning lets the approval gate present the *entire* chain and
execute nothing until it is approved — a safety property re-planning structurally cannot
offer.

Reserve `run()` for genuine decision dependency in some future skill.

## Developer SDK (`knaif.cli`)

`knaif.cli` is a click-like SDK that lets a developer add a natural-language
front end to their CLI tool without forking the knaif repo. The SDK generates
an in-memory registry and `tool_map` from Python metadata and reuses the full
core pipeline — typed validation, safety categories, dry-run, variable binding.

Two front doors:

- **`@nk.command` + `nk.App`** — decorate plain Python functions; `nk.App([fn1, fn2])` wires them into a `CommandAgent`.
- **`nk.from_click(group)`** — wrap an existing `click.Group` with zero changes to the commands.

Local backend resolution:

- **`nk.local_ollama(model, url)`** — returns an Ollama orchestrator or `None` (mock fallback with a warning).

See [docs/SDK.md](SDK.md) for the full quickstart, API reference, and backend setup.

## Dual-Runtime

Everything above describes the **Python authoring runtime** — the reference
implementation and the fastest iteration loop. It is not the only runtime. A Python-free
**native release runtime** — a Rust core linking llama.cpp, shipped as a thin CLI —
**exists and is merged** (v1, 2026-07-19); a Tauri desktop shell and mobile shells are
the planned surfaces on top of it. Both runtimes consume the **same canonical,
declarative skill definitions**, and the native side mirrors this pipeline
(parse → normalize → validate → optimize → resolve → dispatch), variable binding, safety
gates, retrieval, and chain auto-linking rather than diverging from it. That mirroring is
enforced, not assumed: `contracts/` holds the cross-language contracts and
`just parity <skill>` pins both runtimes to the same GGUF and diffs the rendered output.

Where to read further: [NATIVE.md](NATIVE.md) for the native runtime's tech stack,
build kinds, and packaging; [`docs/plans/2026-06-17-monorepo-dual-runtime.md`](plans/2026-06-17-monorepo-dual-runtime.md)
for the authoritative design, the locked Phase 0 decisions, and the measured
GPU-backend data behind them.

## Future Work

Future design work may add richer logging, more inference backends, schema-driven handler kinds, broader validation for skill-specific enum values, and package distribution for third-party skills.
