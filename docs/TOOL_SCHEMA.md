# Skill Authoring Guide

This document is the canonical guide for creating and maintaining a `knaif` skill.

A skill is a self-contained directory under `skills/` that defines:

- the model-visible intent tools in `tools.yaml`
- deterministic tool behavior as `Step` / `Intent` classes plus a `Skill` subclass in `handlers.py`
- prompt rules and examples in `prompt.yaml`
- domain metadata, arg value sets, and dataset references in `skill.yaml`
- optional profiles, presets, fixtures, and datasets used by the tools
- optional skill-local notebooks under `notebooks/`

A **tool** is a class implementing one of two interfaces:

- a **`Step`** is a leaf — it executes and returns a result dict (`handle`).
- an **`Intent`** is a macro — it expands into a sub-plan of Steps before execution (`expand`).

A **skill** is a `Skill` subclass that lists its tool classes in `tools = [...]`. `skill.yaml` names the class via `skill_class:`. There are no `HANDLERS` / `EXPANDERS` / `SUMMARIZERS` / `PREFLIGHTS` dicts — every tool is a class, linked to its `tools.yaml` metadata by its `name` attribute.

The core package `knaif/` stays domain-agnostic. It loads skills, validates JSON plans, expands intent tools, resolves variables, enforces safety categories, and dispatches to `Step.handle`. Domain behavior belongs in skills.

## Skill Directory Layout

A skill is a **self-contained bundle**: declarative YAML and data sit at the **top** of the
bundle (readable by every runtime), and each language's implementation lives in its own
subfolder.

```text
skills/my_skill/
  skill.yaml         # required
  tools.yaml         # required
  SPEC.md            # human-facing skill specification (see below)
  prompt.yaml
  vocab.yaml
  profiles/
    ...
  data/
    train.jsonl          # fine-tuning rows for this skill
    eval.jsonl           # eval corpus
    eval_snapshot.json   # committed acceptance bar (regression gate)
    safety_test.jsonl
  eval/                  # skill-owned eval code
    fixtures.py · verifiers.py · reviewer.py · README.md
  python/                # the Python implementation (a real package)
    __init__.py          # required — re-exports the Skill subclass
    handlers.py          # required
    tests/
      test_my_skill.py
  native/                # the Rust implementation, added at the port
    Cargo.toml · src/
  notebooks/
    my_skill_tester.ipynb
```

Only `skill.yaml`, `tools.yaml`, `python/__init__.py`, and `python/handlers.py` are
required. `SPEC.md`, `prompt.yaml`, `data/`, `eval/`, `profiles/`, `python/tests/`, and
skill-local notebooks are strongly recommended for a maintainable skill. `native/` is added
when the skill ships in the native runtime — see [Runtimes](#runtimes-python--native--both).

`ctx.skill_dir` is always the **bundle root**, so handlers resolve `profiles/`,
`vocab.yaml`, and `data/` from there — never from their own `__file__`. Shared notebook
widgets and helpers live in `notebooks/shared/`; skill-specific notebook helpers stay under
`skills/<name>/notebooks/helpers/`.

**Notebook helpers never move into `knaif/`, however reusable they look.** They are
presentation glue — ipywidgets/IPython UI wiring that nobody running `pip install knaif`
would import — and `notebooks/shared/` is deliberately *not* a Python package and not
shipped in the wheel. Promoting a helper into the library would pull `ipywidgets` and
`IPython` into the dependency surface of a package whose whole value is running locally
without them. If a notebook helper contains logic worth sharing, move **that logic** into
core (or `knaif.evalsuite`) and let the helper re-export it; keep the widget wiring in the
notebook layer.

### `SPEC.md` (skill specification)

Every skill should ship a `SPEC.md` at its root — the authoritative human-facing
description of *how the skill behaves and why*, bridging the code and the architecture
docs. The README's documentation section links to skills' specs generically, so a new
skill's `SPEC.md` is discoverable without editing the README. At minimum cover:

- **System Requirements** — external binaries, services, or env vars the skill needs at
  runtime (e.g. ffmpeg shells out to the `ffmpeg`/`ffprobe` binaries, which must be on
  `PATH`), with install hints and the error raised when they are missing. Put this near
  the top so it is the first thing a new user sees.
- **Model contract** — the plan envelope the model emits and what it must *not* do.
- **Public vs. internal tools** and how intents expand into deterministic steps.
- **Safety** — which tools are destructive, what dry-run returns, confirmation gates.
- **Tests** — what the skill's test suite covers and how to run it.

[`skills/ffmpeg/SPEC.md`](../skills/ffmpeg/SPEC.md) is the reference example.

### The `python/` package

The bundle's `python/` directory is a real Python package. Handler code is split across
modules and pulled together with ordinary **relative imports**
(`from ._engine import build`). The loader executes `python/__init__.py` as the skill's
entry point and loads the handler module as a submodule, so relative imports resolve
through normal import machinery and same-named skill directories under different parents
never collide.

```python
# skills/my_skill/python/__init__.py
from .handlers import MySkill
__all__ = ["MySkill"]
```

`skill_class: handlers.MySkill` in `skill.yaml` resolves against `python/`.

**Why declare the class in YAML when `__init__.py` already re-exports it?** Auto-discovery
was considered and rejected. The manifest is the *cross-language* contract: the native
runtime reads the same `skill.yaml` and cannot import a Python class, so `skill_class:` and
`runtimes.python.handlers` are the same `module.ClassName` string in both worlds. Deriving
the entry point from Python package structure would make the Python package the canonical
skill definition — exactly the boundary the declarative YAML exists to hold. The
`__init__.py` re-export is a convenience for humans and `import`, not the binding.

> **Legacy flat layout.** The loader still falls back to handlers sitting alongside the
> YAML at the bundle top, with no package context (so no relative imports). This is
> historical back-compat only — author new skills as bundles with a `python/` package.

#### Standard module layout

Once a skill outgrows a single file, split it along this convention (ffmpeg and
documents both follow it) so every skill is navigated/debugged the same way:

```text
skills/<name>/python/
  __init__.py     # re-export the Skill subclass (entry point)
  handlers.py     # thin: assembles the Skill subclass + tool list, re-exports the package
  steps.py        # Step classes (deterministic handlers)
  intents.py      # Intent classes (expanders)
  _deps.py        # external tools / optional-import shims — the test patch + debug seam
  _engine.py      # pure domain logic (no I/O): builders, parsers, coercions
  _reporting.py   # summarizers, preflights, format_results, run_artifact (optional)
  tests/          # skill test suite
```

Key rule for `_deps.py`: call external tools through the **module object**
(`_deps.run_tool(...)`), never via a bare imported name. Tests then patch the seam
once (`handlers._deps.run_tool`) and every caller sees it, regardless of which
module the caller lives in. `handlers.py` re-exports the package's public names
(via `__all__`) so existing `handlers.X` references keep resolving.

## `skill.yaml`

`skill.yaml` is the manifest. Paths are relative to the skill directory.

```yaml
name: my_skill
description: "One-line description."
version: 0.1.0

tools: tools.yaml
skill_class: handlers.MySkill   # ClassName in handlers.py (module-relative)
prompt: prompt.yaml

recommended_model: qwen3-4b  # name in models.yaml (see "Runtime models" below)

status: active   # or `stale` — optional, defaults to `active` (see "Skill status")

display:                          # end-user catalog copy (see "Display metadata")
  title: My Skill
  tagline: "What it does, in one sentence a non-developer understands."
  category: media

data:
  train: data/train.jsonl
  safety_test: data/safety_test.jsonl

arg_value_sets:
  mode: [fast, balanced, high_quality]

safety:
  unsafe_phrases:
    - "rm -rf"
    - "nuke"
```

`data.train` (`data/train.jsonl`) is the fine-tuning dataset — `utterance → plan`
pairs the model learns from. It is distinct from the eval corpus
(`data/eval.jsonl`), which the model is measured against and which is never used
for training. Generate `train.jsonl` with a big-LLM agent once the corpus is
validated — see [TRAINING_DATA_GENERATION.md](TRAINING_DATA_GENERATION.md).

`arg_value_sets` gives the validator skill-specific allowed values.

`safety.unsafe_phrases` is a list of strings used by mock inference to force a `reject` response when any phrase appears in the user's utterance. It is currently used for known argument names such as `file_type` and can be extended as validator support grows.

### Display metadata

`display:` is **end-user catalog copy**, read only by the website generator
(`scripts/site_data.py`) to build the skill cards on knaif.org. Neither runtime reads it,
so it can never affect behavior.

| Key | Purpose |
|---|---|
| `title` | Human name as shown on a card — `FFmpeg`, not `ffmpeg` |
| `tagline` | One sentence a non-developer understands |
| `category` | Groups the skill in the catalog filter (`media`, `documents`, …) |
| `stage` | Optional override of the derived catalog stage — see below |

It exists because `description:` is written for a *different reader*. That field is
model- and developer-facing — it feeds retrieval and the authoring docs — so reusing it on
a landing page produces flat copy, and deriving a title from `name` produces "Ffmpeg".

**A skill without `display:` fails the site build rather than rendering a degraded card.**
A broken or missing entry in a public catalog is a worse failure than a failed build, and
a silent fallback is exactly how a new skill would ship with placeholder copy nobody
noticed. `display:` is optional to the *runtime* and required to *publish*.

#### Catalog stage

How finished a skill looks on knaif.org. **Derived by default**, because `status:` defaults
to `active` — so without a derived stage, a half-finished skill dropped into `skills/`
would advertise itself as production-ready and nobody would have had to make a wrong
decision for that to happen.

| Stage | Catalog | Derived when |
|---|---|---|
| `stable` | Full card | `data/eval_snapshot.json` exists |
| `preview` | Shown, badged *in development* | No snapshot yet |
| `hidden` | Not published | Only by explicit `stage: hidden` |

The evidence is the **locked acceptance bar** — the same thing that makes a skill "done"
everywhere else in this repo (see [EVAL_FRAMEWORK.md](EVAL_FRAMEWORK.md)). Advertising a
skill and locking its snapshot are therefore the same act, and neither can be forgotten
independently of the other.

`display.stage:` overrides the derivation when it is wrong:

```yaml
display:
  title: Archives
  tagline: "Zip and unzip without remembering the flags."
  category: files
  stage: hidden        # not ready to show at all
```

A `status: stale` skill is **never** published, and `stage:` cannot override that — the
website must not resurface what the runtime hides from `list_skills()`.

`skill_class` names the `Skill` subclass and is resolved **module-relative** to the skill directory: `skill_class: handlers.MySkill` means class `MySkill` in the skill's `handlers.py`. The part before the dot selects the file (`handlers` → `handlers.py`); a bare `MySkill` (no dot) defaults to `handlers`. The loader fails fast if any class in `MySkill.tools` is not a `Step`/`Intent`, has no matching `tools.yaml` entry, or collides on `name`.

### `pipeline:` and the `inject` step (optional, advanced)

A skill may declare an `inject` step, which names injectors that gather runtime
context *before* the model runs. The gathered file set widens what the
[clarify gate](ARCHITECTURE.md#the-clarify-gate) will accept as a concrete file
reference.

```yaml
pipeline:
  - inject:
      - host_files      # available files supplied by the host application
  - intent
  - gate
  - plan
  - execute
```

`Skill.load()` parses the `inject` entry into `skill.pipeline_inject`; unknown
injector names are rejected as typos. Built-ins live in `knaif/injectors.py`;
skill authors register custom ones alongside their handlers.

**The injected file set is host-supplied, never a directory glob.** The available
files are whatever the *frontend* made available — a drop-files UI passes exactly
the files the user dropped. An injector that globbed the sandbox would hand the
model back the very file it was guessing at, which is the failure the gate exists
to stop.

Omitting the step (**every shipped skill today**) means injection OFF: the gate
sees `injected_files=None` and judges the utterance alone. This is deliberate —
the eval harness must run OFF permanently, and the CLI has no pre-staged file set.

> **Status: partially wired.** `skill.pipeline_inject` is parsed and stored but not
> yet consumed by `CommandAgent`, and `resolve_injected_files()` has no caller
> outside its tests — a host currently has to pass `injected_files=` to
> `execute_plan()` itself. Turning injection on means finishing that plumbing. See
> [nl-clarify-gate](plans/2026-06-09-nl-clarify-gate.md) (T4) and
> [context-injection](plans/2026-06-09-context-injection.md).

### Skill status

`status` is optional and defaults to `active`. Set `status: stale` for a skill that is
partial, deprecated, or under rebuild. A stale skill is:

- **hidden from `list_skills()`** by default (pass `include_stale=True` to include it) and
  from the `knaif skills` CLI listing;
- **excluded from the all-skills eval sweep and the cross-skill regression gate**, so a
  known-incomplete skill never produces a false red.

It remains fully loadable for explicit use: `create_agent("<name>")` and
`CommandAgent.from_skill(...)` still work. Use this instead of hard-coding skill names in
core logic.

### Runtimes (Python / native / both)

A skill can be implemented by the **Python** runtime, the **native** (Rust) runtime, or both. The
declarative half of the bundle — `skill.yaml`, `tools.yaml`, `prompt.yaml`, `vocab.yaml`,
`profiles/`, `data/` — is **shared**: both runtimes read the same files, so a tool's name, args,
keywords, and safety metadata are declared exactly once. Only the handlers differ.

```yaml
# skills/<name>/skill.yaml
runtimes:
  python:
    handlers: handlers.FFmpegSkill    # resolved inside skills/<name>/python/ (loaded by path)
  native:
    status: supported                 # consumed by the native runtime + `knaif skills list`
    crate: knaif-skill-ffmpeg         # the crate implementing it
```

| Shape | `runtimes:` block | Where handlers live |
|---|---|---|
| **Python-only** | `python:` only (or omit `runtimes:` entirely) | `skills/<name>/python/` |
| **Native-only** | `native:` only | `skills/<name>/native/` |
| **Both** | `python:` + `native:` | both of the above |

- **Omitting `runtimes:` is legal** and means Python-only — `skills/io/skill.yaml` has no block. The
  native runtime lists such a skill but reports no native support.
- **`runtimes.python.handlers`** is the same `module.ClassName` form as the top-level `skill_class:`
  and resolves module-relative inside the skill's `python/` package.
- **`runtimes.native.status`** is what the native CLI reads (`native/crates/knaif-core/src/skills.rs`);
  `knaif skills list` shows it as `native:<crate>`. Use a non-`supported` status while a native port
  is in progress so the binary doesn't claim a skill it can't execute.
- **Parity is a choice, not a requirement.** Native v1 ships **ffmpeg** and **documents**; `io` stays
  Python-only. A tool that exists in only one runtime must still be declared in the shared
  `tools.yaml` — otherwise the other runtime's validator rejects a plan the model was told it could
  emit. Keep the model-visible surface identical and let `status` express the gap.

See [NATIVE.md](NATIVE.md) §7 for how the native runtime consumes this, and §3 for the crate layout.

### Runtime models

*(model selection, unrelated to the `runtimes:` block above)*

`recommended_model` is the skill's preferred entry in [models.yaml](../models.yaml) at the repo root. The CLI and `create_agent()` use this resolution order at runtime:

1. `--model-path PATH` (raw GGUF, no model_config)
2. `--model NAME` (explicit lookup in `models.yaml`)
3. The active skill's `recommended_model`
4. `models.yaml`'s `default:`
5. Mock inference

This is intentionally separate from [`eval_backends.yaml`](../eval_backends.yaml), which enumerates every backend the eval suite benchmarks. Adding a backend to `eval_backends.yaml` does not affect CLI or library defaults.

## `tools.yaml`

`tools.yaml` is a flat YAML mapping. Top-level entries with a `description` are loaded as tools.

```yaml
my_tool:
  description: "Short model-facing description."
  keywords: [short, trigger, words]
  required_args: [input]
  optional_args: [mode]
  safety_category: safe
  readonly: true
  internal: false
  mock_args:
    input: "{sandbox}"
```

| Field | Required | Meaning |
|---|---:|---|
| `description` | yes | One-line model-facing tool description. Required for loader inclusion. |
| `keywords` | no | Trigger words for retrieval scoring (and mock inference). May be **shared** across tools (retrieval down-weights a keyword by how many tools claim it); a keyword claimed by >4 tools errors as too generic. Add multilingual/CJK terms freely — CJK is matched by character n-gram. Verify coverage with `uv run -m knaif.evalsuite retrieval` (recall@k per script slice). |
| `required_args` | no | Flat argument names that must appear in `args`. |
| `optional_args` | no | Flat argument names accepted when present. |
| `defaults` | no | `{arg: value}` filled in before validation when the model omits the arg. Explicit values are never overwritten. Use it so the model never has to invent or clarify an obvious value — chiefly output filenames (`concat_video` defaults `output: combined.mp4`). Declare a default only where it is genuinely unambiguous; a missing required arg *without* one still fails, rather than silently inventing a value. An arg with a default belongs in `optional_args`. |
| `safety_category` | no | `safe` or `destructive`; destructive tools require `dry_run=True` or `confirmed=True`. Defaults to `safe`. |
| `readonly` | no | Marks side-effect-free tools for optimizer pruning. Defaults to `false`. |
| `internal` | no | Hides workflow-only tools from the model prompt. Defaults to `false`. |
| `mock_args` | no | Template args used by `use_mock=True` inference. |

System tools `clarify`, `reject`, `done`, and `wait_for_confirmation` are injected by core code when missing. Skills may declare `clarify`, `reject`, or `done` themselves when they want custom descriptions or mock args.

#### How CJK keywords match

Retrieval scores a whitespace token-set intersection, which cannot see inside a
non-space-delimited script: `将clip.mp4裁剪为9:16` is a single token and never equals the
keyword `裁剪`. Rather than segment the query (jieba or similar) or special-case keywords
into a substring branch, `_query_tokens` emits **character n-grams of length 1–4 from each
CJK/kana/Hangul run** and feeds them into the same token set. A CJK keyword up to four
characters then matches by ordinary set intersection.

The reason to prefer this over the other two options is that it adds **no dependency and no
second scoring path**: text containing no CJK run produces exactly the tokens it did before,
so Latin and Cyrillic ranking is unchanged *by construction*, not by test. Anything longer
than four characters is a phrase, not a keyword — raise `_CJK_MAX_NGRAM` only with a
retrieval measurement, since every added length multiplies the token set for every CJK query.

**Coverage is not the same as mechanism.** The tokenizer handles kana and Hangul, but only
Chinese keywords are authored in the shipped skills today — Japanese and Korean utterances
will tokenize correctly and still find nothing. Authoring JP/KO keywords is ordinary
`tools.yaml` work needing no code change; verify any script slice with
`uv run -m knaif.evalsuite retrieval` before assuming it is covered.

### Argument Type Validation (`arg_schemas`)

Add an `arg_schemas` block to declare typed constraints for individual args.
`arg_schemas` is optional — tools without it validate exactly as before.

```yaml
my_tool:
  description: "Process a file."
  required_args: [file, mode]
  optional_args: [quality]
  defaults:
    quality: 75
  arg_schemas:
    file:
      type: string
      path_role: input
      help: "Path to input file"
    mode:
      type: enum
      enum: [fast, balanced, high_quality]
      help: "Processing mode"
    quality:
      type: integer
      min: 1
      max: 100
      help: "Quality level (1-100)"
```

| Field | Meaning |
|---|---|
| `type` | `string` · `integer` · `number` · `boolean` · `array` · `enum` |
| `enum` | Allowed values list when `type: enum` |
| `items` | Element type hint for `type: array` args |
| `min` / `max` | Inclusive numeric bounds for `integer` / `number` |
| `path_role` | `input` · `output` · `sandbox` — informs path resolution and SDK coercion |
| `help` | Human-readable description shown to the model |

`arg_schemas` validation fires at two points:

1. **`validate_step`** (planning time) — rejects type mismatches and enum violations before
   any step executes. `$var` references pass through and are validated at runtime instead.
2. **`_execute_steps`** (runtime, after `$var` resolution) — re-checks resolved values so
   a wrong-type variable from a prior step is caught before dispatch.

The `knaif.cli` SDK builds `arg_schemas` automatically from Python type hints
and `Arg`/`Opt` metadata — no manual YAML required for SDK-built tools.

## Public And Internal Tools

High-level intent tools are model-visible. Internal workflow tools are emitted by expanders and should use `internal: true`.

```yaml
prepare_for_platform:
  description: "Convert videos for a target platform."
  required_args: [inputs, platform]
  optional_args: [quality, preview]
  safety_category: destructive

resolve_inputs:
  description: "Resolve input media paths."
  required_args: [paths]
  safety_category: safe
  readonly: true
  internal: true
```

Use high-level tools for the small model's intent extraction. Use internal tools for deterministic steps such as loading profiles, probing files, rendering commands, previewing output, and writing reports.

### Tool granularity — a flag, or its own tool?

When a request combines two operations ("resize to 480p **and** strip the audio"), you
can either add a flag to the producing tool or let the model chain two intent steps.
Chaining is the general answer and the default; small models are the reason it isn't
always enough, since a compound utterance is where they most often drop half the request
or mis-wire the link between steps.

Fuse into a flag only when the second operation **adds a flag to the same command, with
no new input and no new output**. Strip-audio (`-an`) qualifies. Extracting audio does
not — it produces a second deliverable. Replacing audio does not — it consumes a second
input. Those stay separate intents, expressed as a chain or a multi-output row.

The boundary matters because fusion is combinatorial: every A×B pair you fuse is a flag
the prompt must teach and the model must learn, and the set grows as the product of your
tools. The no-new-input/no-new-output test keeps that set small and keeps each fused
flag a genuine property of one command rather than a hidden workflow.

Fusion belongs in the **skill**, as a declared flag on the tool. Do not push it into the
core optimizer — see [ARCHITECTURE.md](ARCHITECTURE.md#plan-optimizer).

### No tool declares that it needs a named file

A per-tool flag — `needs_named_input: true` — was proposed for the clarify gate and
**rejected**. No tool inherently requires a named file: any operation can legitimately
target a batch. "Concat all videos in this folder" and "grab a frame from every video"
name nothing, and both are correct requests to tools that usually take one file.

The tool alone cannot tell you which case you are in — only the utterance can. So the
gate keys off batch signals in the utterance (all / every / each / batch / folder, and
their translations) plus glob/chain token classification, never off a schema field.

The general shape: a field describing what a tool *usually* receives will be wrong
whenever the user phrases the request the other way, and the validator has no way to
notice. Put the test where the varying information is.

## `handlers.py`

`handlers.py` holds the tool classes and one `Skill` subclass. Tool behavior lives in
the classes; pure helpers (recipe engines, coercions, profile loaders) stay as plain
module-level functions. When the skill is a package (has an `__init__.py`), these
helpers can move into sibling modules and be pulled in with relative imports
(`from ._recipe import build`) instead of crowding a single file.

```python
from knaif.handler_api import HandlerContext
from knaif.skill_base import Skill
from knaif.tool import Intent, Step


class MyToolStep(Step):
    name = "my_tool"                       # links to the tools.yaml entry

    def handle(self, args: dict, ctx: HandlerContext) -> dict:
        return {"status": "ok"}


class MySkill(Skill):
    tools = [MyToolStep]                    # every tool class the skill exposes
```

A class's `name` attribute is the only link to its `tools.yaml` metadata — keep them in
sync. The core control tools (`clarify`, `reject`, `done`, `wait_for_confirmation`) are
built-in `Step` classes merged into every skill automatically; do not redeclare them as
classes.

`HandlerContext` (passed to `Step.handle`) provides:

- `root`
- `sandbox`
- `dry_run`
- `confirmed`
- `skill_dir`
- `confirmer`
- `confirm(prompt, preview=None)`

Steps must honor `ctx.dry_run` for side-effecting work. Destructive public tools are
blocked by `CommandAgent.execute_plan()` unless `dry_run=True` or `confirmed=True`, but
the Step still owns the final behavior.

## Intents (Workflow Expanders)

An `Intent` replaces a high-level tool with a deterministic multi-step plan before
execution. Implement `expand`; it returns a list of normal plan steps. Expanded plans
are re-validated, so every internal tool it emits must also be declared in `tools.yaml`.

```python
class HighLevelToolIntent(Intent):
    name = "high_level_tool"

    def expand(self, args: dict) -> list[dict]:
        return [
            {"tool": "resolve_inputs", "args": {"paths": args["inputs"]}, "output": "$files"},
            {"tool": "my_internal_step", "args": {"files": "$files"}, "output": "$result"},
        ]

    def summarize(self, args: dict, **kw) -> str:        # optional, see below
        return f"do the thing with {args.get('target', 'something')}"
```

`expand` is pure (no `ctx`): the whole plan is frozen before any step runs. Per-item
runtime choices belong inside a Step's output (e.g. a `build_recipes` step), not in
branching inside `expand`.

## Plan Summarizers (Optional)

Override `Intent.summarize` to produce a short human-readable clause for the intent.
This is used by `CommandAgent.execute_plan()` when `show_plan=True` to render a
one-sentence preview before expansion (e.g. *"Will convert clip1.mov to mp4, then
extract audio from clip1.mov as mp3."*).

`summarize(self, args, **kw)` takes the step's `args` and returns a short lowercase
imperative clause (no leading capital, no trailing period). The `**kw` carries extra
context — notably `skill_dir` — so a summarizer can read profile files for a richer
phrase; it must still produce a sensible clause when those kwargs are absent. The
default `summarize` returns the tool name; the core falls back to a generic
`"{tool} key=value"` rendering when a clause is empty or raises.

## Per-tool and Skill-level Preflight (Optional)

Preflight validates plan arguments **before** the approval gate runs. It fires after
expansion + optimization, but only when `dry_run=False` — dry-run executions skip it so
callers can test the pipeline without real files on disk. A non-empty return raises a
`ValueError` listing every problem, surfacing errors **before** the user approves.

Two levels, with strict precedence:

- **Per-tool**: override `preflight` on a `Step` or `Intent` for tool-specific checks.
- **Skill-level**: override `Skill.preflight(self, tool, args, **kw)` as the catch-all
  (the replacement for the old `"*"` wildcard).

For each tool the core runs the tool's own `preflight` **only if the class overrides the
default** (`type(tool).preflight is not Step.preflight`); otherwise it runs the
skill-level `preflight`. The two never both run.

```python
class MySkill(Skill):
    tools = [...]

    def preflight(self, tool: str, args: dict, *, root, sandbox=None, **kw) -> list[str]:
        return check_inputs_exist(args, root=root, sandbox=sandbox)  # [] means pass
```

## Result Formatter (Optional)

Override `Skill.format_results` to turn the raw result list into human-readable items.
The CLI calls it after a non-verbose execution.

```python
class MySkill(Skill):
    def format_results(self, results: list[dict], *, dry_run: bool) -> list[dict]:
        """Return a list of {kind, message} items.

        kind ∈ {"error", "command", "output", "info"}.
        """
        return [{"kind": "output", "message": "ok"}]
```

The CLI renders each item uniformly:

| `kind` | rendering |
|---|---|
| `"error"` | red `✗ {message}` on stderr; sets the CLI exit code to 1 |
| `"command"` | `$ {message}` with a bright-black `$` prefix |
| `"output"` | green `✓ {message}` |
| `"info"` | yellow `{message}` |

A skill that does not override `format_results` leaves it returning `None`, and the CLI
falls back to one `info` line per step showing `{tool} ({duration_ms:.0f}ms)`.

## Artifact Runner (Optional)

Override `Skill.run_artifact` to let the eval suite re-execute a rendered artifact
(typically a shell command string) against a real fixture file.

```python
class MySkill(Skill):
    def run_artifact(self, cmd, fixture, out_dir):
        """Re-execute the artifact against a fixture. Return the output path on success."""
        ...
```

A skill that does not override `run_artifact` leaves it returning `None`, and eval
execution against fixtures is skipped (the `artifact_path` on each `AgentOutput` stays
`None`).

## Shared Steps (`knaif.steps`)

Steps reused across skills live in `knaif.steps` (the class) with metadata in
`knaif/steps/steps.yaml`. A skill consumes one by adding the class to its `tools` list —
no copy/paste, no skill-to-skill imports:

```python
from knaif.steps import ResolveInputs

class MySkill(Skill):
    tools = [ResolveInputs, MyToolStep, HighLevelToolIntent]
```

The loader merges `steps.yaml` metadata for any shared step in `tools`. Both `documents`
and `ffmpeg` share `ResolveInputs` this way. Promote a private Step to shared by moving the
class + its YAML block into `knaif.steps`; the `name` stays stable, so plans, prompts,
and evals are unaffected. Discovery steps return `{count, files}`; consumers accept a
bare list or that dict.

## Plan Envelope And Variables

The model and expanders use the same plan envelope:

```json
{
  "plan": [
    { "tool": "resolve_inputs", "args": { "paths": ["clip.mp4"] }, "output": "$files" },
    { "tool": "inspect_media", "args": { "files": "$files" }, "output": "$probes" }
  ]
}
```

Rules:

- `output` declares a variable named `$identifier`.
- Args may reference `$var` or `$var.field`.
- `execute_plan` resolves variables at runtime.
- Path and known enum validation run again after variable resolution.
- Readonly steps may be pruned only when a later action exists and their output is unused.
- Action steps are never removed or reordered.

## `prompt.yaml`

`prompt.yaml` can override the default planner instructions and examples.

```yaml
system_header: |
  You are a planner. Output ONLY a JSON object.
  { "plan": [ { "tool": "<name>", "args": { <params> } } ] }

examples:
  - request: "do the thing"
    output:
      plan:
        - tool: my_tool
          args:
            input: sample.txt
```

`Skill.load()` renders examples into the prompt. Keep examples short, concrete, and limited to model-visible tools unless the skill intentionally teaches the model a multi-step public plan.

## Data Files

Training and safety data are JSONL files. Each line should contain an utterance and a valid plan payload.

```jsonl
{"utterance":"do the thing to sample.txt","plan":{"plan":[{"tool":"my_tool","args":{"input":"sample.txt"}}]},"tags":["manual"]}
{"utterance":"nuke everything","plan":{"plan":[{"tool":"reject","args":{"reason":"Request is unsafe."}}]},"tags":["unsafe"]}
```

Skill tests should smoke-check that data files parse and that expected plans validate.

## Safety Conventions

- The model never emits shell commands.
- The model emits only `{ "plan": [...] }`.
- `safety_category: destructive` requires `dry_run=True` or `confirmed=True`.
- Preview workflows can use the internal core tool `wait_for_confirmation`.
- Sandbox-sensitive handlers should resolve paths through core helpers where possible.
- Skill-specific file access, profiles, and fixtures should live under `ctx.skill_dir`.

## Testing A New Skill

Minimum tests for a new skill:

- `Skill.load()` reads the skill name and description.
- `skill.tool_map` contains every domain tool plus the core control tools.
- Registry loads all public and internal tools; prompt hides `internal: true` tools.
- `CommandAgent.from_skill()` builds an agent whose `tool_map` dispatches each tool.
- At least one dry-run execution works.
- Destructive tools require confirmation when `dry_run=False`.
- JSONL data files contain valid plan payloads.

Useful commands:

```bash
uv run pytest skills/my_skill/python/tests -v
just test-skill my_skill
uv run pytest --tb=short
```

### Wiring a new skill into the shared-model eval

The model is one shared build serving every skill, and the all-skills sweep/gate is what
protects existing skills from regressing when a new one is added (see
`docs/TRAINING_DATA_GENERATION.md` → "Multi-skill fine-tuning loop"). To join that loop a
skill must ship three data files:

- [ ] `data/eval.jsonl` — the benchmark corpus (`run --all-skills` skips skills without it).
- [ ] `data/train.jsonl` — fine-tuning rows, folded into the union the shared model trains on.
- [ ] `data/eval_snapshot.json` — the committed acceptance bar (`run --skill X --snapshot`),
      so `regression --all-skills` can gate the skill. Optional `regression_threshold` field
      overrides the global default for that skill.
- [ ] Confirm the next model build's matrix includes the skill:
      `run --all-skills … && trend --skill <name>`.
- [ ] Mark the skill `status: stale` in `skill.yaml` only while it is incomplete — stale skills
      are excluded from the sweep and gate (and from `list_skills()`).

## Forking Workflow

1. Copy an existing skill, e.g. `skills/documents` for a multi-tool skill or `skills/ffmpeg` for an Intent-heavy workflow skill. (`skills/io` is `status: stale` and under rebuild — not a good fork template.)
2. Update `skill.yaml` name, description, `skill_class:`, data references, and `arg_value_sets`.
3. Replace public and internal tools in `tools.yaml`.
4. Implement the `Step` / `Intent` classes and the `Skill` subclass in `python/handlers.py`; reuse `knaif.steps` where possible.
5. Write `prompt.yaml` with model-visible rules and examples.
6. Add JSONL data and tests.
7. Verify with `CommandAgent.from_skill("skills/my_skill", sandbox="...")`.
