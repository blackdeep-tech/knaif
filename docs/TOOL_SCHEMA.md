# Skill Authoring Guide

This document is the canonical guide for creating and maintaining a `knaif` skill.

A skill is a self-contained directory under `skills/` that defines:

- the model-visible intent tools in `tools.yaml`
- deterministic tool behavior as `Handler` / `Intent` classes in `python/` (plus an optional `Skill` subclass for hooks)
- prompt rules and examples in `prompt.yaml`
- domain metadata, arg value sets, and dataset references in `skill.yaml`
- optional profiles, presets, fixtures, and datasets used by the tools
- optional skill-local notebooks under `notebooks/`

A **tool** is a named capability with a YAML contract in `tools.yaml`, either one the model sees
or an internal one. Its code is a class implementing one of two interfaces:

- a **`Handler`** executes and returns a result dict (`handle`).
- an **`Intent`** expands into a sub-plan of plan steps before execution (`expand`).

A **plan step** is one entry in a plan (`{"tool": …, "args": …}`); the word "step" means only
that. A **toolkit** (`toolkits/<name>/`) is a shared set of tools with the same `tools.yaml` +
`python/` layout as a skill, but no prompt or corpus; a skill uses one by declaring
`toolkits: [fs]` (see [Toolkits](#toolkits-shared-tools)).

Every tool is defined **once in YAML and once as a class**, and nothing else. The loader
discovers the classes in the bundle's `python/` modules and matches each to its `tools.yaml`
entry by its `name` attribute, strictly 1:1. Nothing lists the classes: there is no
`tools = [...]`, no `skill_class:`, and no `HANDLERS` / `EXPANDERS` dicts.

The core package `knaif/` stays domain-agnostic. It loads skills, validates JSON plans, expands intent tools, resolves variables, enforces safety categories, and dispatches to `Handler.handle`. Domain behavior belongs in skills and toolkits.

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
    __init__.py          # makes python/ a package (relative imports)
    *.py                 # the tool classes, in any modules (handlers.py, steps.py, …)
    tests/
      test_my_skill.py
  native/                # the Rust implementation, added at the port
    Cargo.toml · src/
  notebooks/
    my_skill_tester.ipynb
```

Only `skill.yaml`, `tools.yaml` and the `python/` modules holding the tool classes are
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
(`from ._engine import build`). The loader executes `python/__init__.py`, imports every
top-level module in `python/` (`handlers` first; `test_*` modules are skipped), and collects the
classes **defined** there:
- every `Handler`/`Intent` subclass with a `name`;
- at most one `Skill` subclass.

A class imported from somewhere else (core, a toolkit) is not the skill's. Same-named skill
directories under different parents never collide.

**Why discovery, when it was once rejected?** The objection was that deriving the skill from
Python package structure would make the Python package the canonical skill definition. It
doesn't here: **`tools.yaml` stays the canonical list of every tool**, and classes are only
matched against it. A class with no entry, or an entry with no class, is an error. The native
runtime reads the same `tools.yaml`, and its own entry point is `runtimes.native.crate`. It never
read `skill_class:`, so the "same string in both worlds" that justified naming the class was
never used. Reversed 2026-10-09.

> **Flat layout.** The loader also accepts handler modules sitting alongside the YAML at the
> bundle top (no package, so no relative imports). Author new skills with a `python/` package.

#### Standard module layout

Once a skill outgrows a single file, split it along this convention (ffmpeg and
documents both follow it) so every skill is navigated/debugged the same way:

```text
skills/<name>/python/
  __init__.py     # package marker
  handlers.py     # thin: the optional Skill subclass (hooks), re-exports the package
  steps.py        # Handler classes (deterministic tools)
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

toolkits: [fs]   # optional: shared toolkits whose tools this skill's plans use
expose: []       # optional: toolkit tools the model may see (internal otherwise)

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

`file_kinds` (optional) says which of the skill's files are the same kind of thing, as
`kind: [extensions]`:

```yaml
file_kinds:
  video: [mp4, mov, mkv, webm, gif]
  image: [jpg, jpeg, png]
```

Both runtimes read it for chain threading and for the scratch keep rule (below). When a later
step names an earlier step's
source file, core rewrites it onto that step's output ("convert clip.mp4 to mkv then strip
*its* audio"), but never onto a file of a different kind: "make a thumbnail of clip.mp4 and
compress *it*" compresses the video. An extension no kind lists is unrestricted, and one
listed under two kinds fails the skill load.

Threading also stops when the **user repeats the filename**: "thumbnail of clip.mp4 and
compress clip.mp4" is two steps over one file, and the plan runs as written. When you write
corpus rows or examples, a name written twice pins that step to the original file.

`batch_kinds` (optional) lists the kinds whose batch glob covers the whole kind:

```yaml
batch_kinds: [video]
```

In a request for many files, an input glob `*.<ext>` whose extension the request does not name
also reads the other extensions of that kind in its folder: "make all the videos here 720p"
written as `*.mp4` covers the .mov and .mkv files beside it (`["*.mp4", "*.mkv", "*.mov"]`). An
extension the request names as the files' type ("every .mov", "the mp4s") is never added. A
format named once, only as the value of the step's `produces: {arg}` argument, is what the step
converts *to*, not the files' type: "convert all the videos to mp4" covers the mp4s and the other
videos alike, whichever glob the model wrote. A one-path `input` given
several globs this way, or by the model, runs once per file. Each kind must be one `file_kinds`
declares. Pinned by `contracts/parity/kind_glob_cases.json`.

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

The file names are fixed (`tools.yaml`, `prompt.yaml`, `python/`), so `skill.yaml` names no files and no classes. A manifest that still declares `skill_class:`, `tools:` or `prompt:` is rejected with a message saying why. The loader fails fast when a `tools.yaml` entry has no class, a class has no entry, two classes share a `name`, a name collides across the skill, its toolkits and core, or the bundle defines more than one `Skill` subclass.

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
| **Python-only** | omit `runtimes:` | `skills/<name>/python/` |
| **Native-only** | `native:`, and no `python/` folder | `skills/<name>/native/` |
| **Both** | `native:` | both of the above |

- **Omitting `runtimes:` is legal** and means Python-only — `skills/io/skill.yaml` has no block. The
  native runtime lists such a skill but reports no native support.
- **The Python runtime needs no declaration:** a skill has one when it has a `python/` folder.
- **`runtimes.native.status`** is what the native CLI reads (`native/crates/knaif-core/src/skills.rs`);
  `knaif skills list` shows it as `native:<crate>`. Use a non-`supported` status while a native port
  is in progress so the binary doesn't claim a skill it can't execute.
- **Parity is a choice, not a requirement.** Native v1 ships **ffmpeg** and **documents**; `io` stays
  Python-only. A tool that exists in only one runtime must still be declared in the shared
  `tools.yaml` — otherwise the other runtime's validator rejects a plan the model was told it could
  emit. Keep the model-visible surface identical and let `status` express the gap.

See [NATIVE.md](NATIVE.md) §7 for how the native runtime consumes this, and §3 for the crate layout.

### External tools (`dependencies.external_tools`)

Third-party programs a skill runs as subprocesses. knaif detects them and never bundles them or
changes `PATH`; `knaif skills deps` reports them, a run refuses early when a `required` one is
missing, the Windows installer offers each `install.windows: winget` tool through winget, and the
macOS `.pkg` each tool through Homebrew.

```yaml
dependencies:
  external_tools:
    - name: libreoffice            # one vendor package = one installer task
      required: false              # true → blocks execution and defaults the task on
      all_required: false          # true → every command is needed (ffmpeg + ffprobe);
                                   # false → the commands are aliases, any one satisfies
      commands: [soffice, libreoffice]
      install: { windows: winget, macos: brew, linux: package_manager }
      windows:
        winget: TheDocumentFoundation.LibreOffice    # `winget install -e --id …`
        download: https://www.libreoffice.org/download/download-libreoffice/   # hint without winget
        dirs: ['%ProgramFiles%\LibreOffice\program']
      macos:
        brew: libreoffice                             # `brew install …`
        cask: true                                    # true → `brew install --cask …` (an .app)
        dirs: [/Applications/LibreOffice.app/Contents/MacOS, /opt/homebrew/bin, /usr/local/bin]
```

A tool with no winget package declares `install.windows: download` and omits `winget:`: the
installer offers no checkbox for it, and `skills deps` names the `download` page (Ghostscript,
whose package left the winget catalog in 2026).

A command resolves to `$KNAIF_<CMD>_BIN` when set, else the first hit on `PATH`, else the first
folder declared for this OS (`windows.dirs`, `macos.dirs`) holding it. List the folders the vendor's
own installer uses: most Windows installers never add themselves to `PATH`. On macOS list
Homebrew's `bin` — a knaif started outside a login shell (the `.pkg` postinstall, a GUI) does not
have it on `PATH` — and, for a cask, the folder inside the `.app`, which is never on `PATH`.
`%VAR%` is expanded from the environment
(a folder naming an unset variable is skipped), and `*` matches within one path component, newest
version first. The skill must **launch the binary this lookup returns** (native:
`knaif_skill_api::tools`), not a bare name, or `skills deps` reports a tool the run cannot start.
The installers mirror this block — the Windows installer's winget ids, commands and folders
(`python/core/tests/test_installer_iss.py`) and the macOS `.pkg`'s Homebrew names
(`python/core/tests/test_installer_pkg.py`) — and those tests fail when they drift.

### Runtime models

*(model selection, unrelated to the `runtimes:` block above)*

`recommended_model` is the skill's preferred entry in [models.yaml](../models.yaml) at the repo root. The CLI and `create_agent()` use this resolution order at runtime:

1. `--model PATH` naming a GGUF file (raw GGUF, no model_config)
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
  effects: [fs.read]
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
| `grounded_args` | no | Args whose value the user must have typed (a password). A value the request does not contain is invented: the plan becomes a `clarify` asking for it. |
| `stated_args` | no | Args only the request can state, a quantity such as `compress_video`'s `target_size_mb`. A value the request does not state is left out and the plan runs without it, with a note in the plan (`left out target_size_mb 15: the request does not say it`). A number counts as stated when the request holds it, or the same size in KB or GB, within 1%. Both runtimes; `contracts/parity/stated_args_cases.json`. |
| `safety_category` | no | `safe` or `destructive`; destructive tools require `dry_run=True` or `confirmed=True`. Defaults to `safe`. |
| `readonly` | no | Marks side-effect-free tools for optimizer pruning. Defaults to `false`. |
| `internal` | no | Hides workflow-only tools from the model prompt. Defaults to `false`. |
| `effects` | **yes** (skill and toolkit tools) | What the tool may do: `fs.read`, `fs.write`, `net`, `exec:<program>`; `[]` for none (an Intent that only expands). A bundle with an undeclared tool does not load, in either runtime. See *Declared effects* below. |
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

Tool classes live in any `python/` module (`handlers.py`, `steps.py`, `intents.py`, …). Tool
behavior lives in the classes; pure helpers (recipe engines, coercions, profile loaders) stay as
plain module-level functions, in sibling modules pulled in with relative imports
(`from ._recipe import build`).

```python
from knaif.handler_api import HandlerContext
from knaif.skill_base import Skill
from knaif.tool import Handler, Intent


class MyTool(Handler):
    name = "my_tool"                       # links to the tools.yaml entry

    def handle(self, args: dict, ctx: HandlerContext) -> dict:
        return {"status": "ok"}


class MySkill(Skill):                      # optional: hooks only
    def format_results(self, results, *, dry_run): ...
```

A class's `name` attribute is the only link to its `tools.yaml` metadata, and the loader checks
it both ways. The core control tools (`clarify`, `reject`, `done`, `wait_for_confirmation`) are
built-in `Handler` classes merged into every skill automatically. Do not redeclare them as
classes; restating one in `tools.yaml` is allowed and needs no class.

`HandlerContext` (passed to `Handler.handle`) provides:

- `root`
- `sandbox`
- `dry_run`
- `confirmed`
- `skill_dir`
- `confirmer`
- `confirm(prompt, preview=None)`
- `work_dir`: this run's scratch folder (see *The run scratch* below)
- `call_tool(name, args)`: run another tool of this skill (its own, a declared toolkit's, or
  core) in the same run. This is how a handler reuses a toolkit tool.
- `file_finished(file, failure=None)`: a step that works on many files says how each one ended,
  done or failed with a one-line reason (no paths in it). Core prints a failure as it happens,
  lets a failed command among done files continue the run instead of stopping it, and ends with
  the batch summary (`3 done, 1 failed in 4.2s, 12.3 MB`). A step over one file is no batch:
  its report is ignored and its error takes the usual path. ffmpeg's `inspect_media`,
  `build_recipes` and `run_batch` report this way. When core itself runs a chain once per file,
  it reports each file and only keeps the step's reason.

Handlers must honor `ctx.dry_run` for side-effecting work. Destructive public tools are
blocked by `CommandAgent.execute_plan()` unless `dry_run=True` or `confirmed=True`, but
the Handler still owns the final behavior.

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
runtime choices belong inside a tool's output (e.g. a `build_recipes` step), not in
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

- **Per-tool**: override `preflight` on a `Handler` or `Intent` for tool-specific checks.
- **Skill-level**: override `Skill.preflight(self, tool, args, **kw)` as the catch-all
  (the replacement for the old `"*"` wildcard).

For each tool the core runs the tool's own `preflight` **only if the class overrides the
default** (`type(tool).preflight is not Handler.preflight`); otherwise it runs the
skill-level `preflight`. The two never both run.

```python
class MySkill(Skill):
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

        kind ∈ {"error", "command", "output", "text", "info"}.
        """
        return [{"kind": "output", "message": "ok"}]
```

The CLI renders each item uniformly:

| `kind` | rendering |
|---|---|
| `"error"` | red `✗ {message}` on stderr; sets the CLI exit code to 1 |
| `"command"` | `$ {message}` with a bright-black `$` prefix |
| `"output"` | green `✓ {message}` |
| `"text"` | the message as is, every line indented, no colour: what a read found (extracted text, a page count, matches) |
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

## Output Collisions (Optional)

Override `Skill.resolve_output_collisions` to rewrite plan outputs that would destroy a file,
and rebind the steps that referred to them. Core calls it once per plan, **after stem
resolution and before expansion** — the first point where both the whole plan and real disk
state are visible — and the default is a no-op.

```python
class MySkill(Skill):
    def resolve_output_collisions(self, plan, *, sandbox=None):
        """Return the plan, with colliding outputs renamed and consumers rebound."""
        ...
```

The rule a skill implements here, if it implements one:

> A name an earlier step declares it will write binds, for every later step, to what that
> step actually wrote. Substitute the old name with the resolved one across steps **strictly
> after** the producer; never re-infer which file was meant.

Whether an output collides at all, and what a free replacement is called, are **skill
policy** and stay in the skill. ffmpeg renders every command with `-y`, so an output equal to
its own input truncates the source before ffmpeg reads it (`skills/ffmpeg/python/_collisions.py`);
a skill whose handlers write to a temporary file and move it has no such problem. Core supplies
the extension point and keeps no naming logic of its own.

## Toolkits (shared tools)

Tools reused across skills live in a **toolkit**: `toolkits/<name>/` with the same layout as a
skill, minus the parts the model sees.

```text
toolkits/fs/
  tools.yaml        # the canonical contract for each tool
  python/*.py       # the Handler/Intent classes, discovered 1:1 like a skill's
  SPEC.md
```

A skill uses a toolkit by declaring it, with no copy of its YAML and no imports:

```yaml
# skills/my_skill/skill.yaml
toolkits: [fs]
```

- **Every tool of a declared toolkit** joins the skill's registry and tool map.
- **Toolkit tools are internal** (never shown to the model) unless the skill lists them under
  `expose:`. Then they are part of the skill's vocabulary and need prompt examples and training
  rows like any other public tool.
- **A name must be unique** across the skill, its toolkits and core, and a collision is an error.
  Names stay plain (`resolve_inputs`, not `fs.resolve_inputs`).
- **Toolkits are found** in `$KNAIF_TOOLKITS_ROOT`, else in `toolkits/` beside `skills/`
  (`Skill.load(..., toolkits_root=...)` overrides both).
- **A toolkit has no `Skill` subclass,** because it has no hooks.
- **Evidence:** a skill's acceptance evidence fingerprints its declared toolkits, so a toolkit
  edit invalidates the records of every skill that uses it.

`ffmpeg` and `documents` both take `resolve_inputs` from `toolkits/fs` this way. **Promote a
private tool to a toolkit when a second skill needs it** (the rule of two): move its class and
YAML block, keep the `name`, and declare the toolkit in both skills. Plans, prompts and evals are
unaffected. Discovery tools return `{count, files}`; consumers accept a bare list or that dict.

A handler reuses a toolkit tool through the context, never by importing it (toolkit modules load
under private package names): `ctx.call_tool("commit_output", {"staged": s, "path": p})`. The
call runs in the same run, with the same scratch and the same dry run.

`toolkits/fs` provides:

| Tool | Does |
|---|---|
| `resolve_inputs` | files, folders and globs inside the sandbox → `{count, files}` |
| `stage_output` | a path in the run scratch with the final file's name → `{staged}` |
| `commit_output` | checks a staged file (exists, not empty), then moves it to its final path → `{path, bytes}`. A failure removes the staged file and leaves the user's folder untouched. A final path inside the scratch is refused. |
| `ensure_parent` | makes the (resolved) parent folder of a path inside the sandbox or scratch |
| `plan_output_path` | the one free-name rule, suffix and numbering chosen by the caller (`_converted_2`, `-1`) |
| `user_side_path` | a path, or where it would sit in the user's folder if it is in the run scratch; anchor a default output on it so nothing derived from a routed intermediate lands in the scratch |

The native runtime has the same rules as plain functions in the `knaif-toolkit-fs` crate
(`toolkits/fs/native/`), which skill crates call directly.

## The run scratch

Intermediate files belong to knaif, not to the user. Every run that executes gets a fresh
`<OS temp>/knaif/run-<id>/`, handed to handlers as `ctx.work_dir` (`knaif.scratch`):

- **Deleted when the run ends,** on success and on failure. There is no persistent cache.
- **Kept under `KNAIF_DEBUG`.** `KNAIF_SCRATCH_ROOT` moves the root.
- **A dry run writes nothing.** `work_dir` is still a path, so commands can name it, but the
  folder is never created.
- **Stale `run-*` folders** older than a day are swept when a run starts, since a run killed
  hard cannot delete its own.
- **Sandbox rule.** A path the model writes is checked against the sandbox only. A path core or a
  step put into this run's scratch (a routed intermediate, a `$var` value) is also accepted. A
  final output can never land in the scratch.
- **Chain intermediates** go there when the skill opts in (`scratch_intermediates: true` in its
  `skill.yaml`, read by both runtimes): a step's `output` that a later step reads moves into `work_dir` under the
  same name, with every later reference. An output nothing reads is a deliverable, and a file
  the user named in the request stays where the plan put it.
- **The keep rule.** An intermediate the user *asked for* stays too, although they never named
  the file. One rule, two deterministic signals, and core never names a tool:
  - **Tool signal:** every later step reading it makes a **new thing** from it: a file of
    another kind ("convert clip.mov to mp4, then extract its audio" leaves the mp4 and the mp3),
    or a tool that declares `derives: true` because its output is a piece, a page range or a
    join rather than the input improved ("compress report.pdf and extract pages 1-3" leaves the
    compressed whole and the pages). **Not when every reader is a join** (`joins_inputs: true`):
    pieces made for a join are on the way, so "compress all the PDFs here and merge them into
    one" leaves only the merged file.
  - **Request signal:** the user named its format as a bare word and no later reader writes that
    format again: "convert clip.mov to mp4 and make a gif preview" leaves the mp4 and the gif,
    while "convert to mp4 and make it smaller" leaves only the smaller mp4, because that is mp4
    again. Extensions are ASCII in every language, so this needs no word lists; the extension of
    the step's own input never counts ("cut the mp4 at 3 seconds and make a gif" names the
    source).

  Otherwise a later step refines it, so it moves: "convert to mp4, then speed it up" leaves only
  the sped-up mp4. Kinds are the skill's `file_kinds`; what a reader writes is its tool's
  `produces:` in `tools.yaml`, a kind or `{arg: <name>}` when the kind follows an argument naming
  an extension, else its explicit `output`'s kind (a `{format: <extension>}` declaration, for a
  tool that always writes one format, only names what the plan leaves: `compress_video` declares
  `{format: mp4}` so a compressed `.mov` "leaves 1 mp4"; the keep rule ignores it). The
  declaration wins over the extension the
  model wrote: `extract_audio` writing `clip_audio.mp4` still writes audio, so a wrong extension
  cannot delete a second result:

  ```yaml
  extract_audio:
    produces: audio
  convert_document:
    produces: {arg: to_format}   # to_format: txt -> text, pdf -> document
  trim_video:
    derives: true                # a piece: its source stays
  split_pdf:
    derives: true                # a page range: its source stays
  ```

  A tool without `produces:` writes the same kind as its input. Core never names a tool: a new
  skill gets the rule by declaring its kinds and what its tools write.
- **A result saved per file in a batch.** A tool can declare `batch_output: <ext>` (documents
  `extract_text: batch_output: txt`): when core repeats a step of it over many files, each
  file's result is saved as `<name>.<ext>` next to that file, the name core gives each lifted
  step's `output`, and the preview's "leaves" line counts the files. (extract_text saves one
  file's text too, and declares `output` as an ordinary argument.)
- **A text answer for a pipe.** A tool whose result is text can declare `stdout: <result field>`
  (documents `extract_text: stdout: text`). `knaif run --stdout` (both CLIs) then prints that
  field alone on stdout and saves nothing, the handler told by `ctx.to_stdout`; the run's own
  lines go to stderr. A plan with any tool that declares none refuses the flag before anything
  runs. Contracts `cli_flag_cases.json` (the flag) and `cli_output_cases.json` (what each CLI
  prints).
- **Keeping everything.** `--keep-intermediates` on `knaif run` (both CLIs) keeps every step's
  output where the plan put it, and so do words in the request: "keep the intermediate files",
  "keep everything", "behalte alles", "保留中间文件". The phrases are a keep verb next to a scope,
  never `save` (which names outputs); a negated English request keeps the default. Python
  `knaif.scratch.wants_everything_kept`, Rust `knaif_core::scratch::wants_everything_kept`,
  contract `contracts/parity/keep_request_cases.json`.
- **Scratch paths are never printed.** The one exception is a preview clip, which the user is
  meant to watch. Evidence shows them as `<work>/…` (`evalsuite.redact`).

## URL inputs

A skill can take an http(s) URL wherever it takes an input file
(`convert https://host/talk.mov to mp4`). It opts in in `skill.yaml`:

```yaml
toolkits: [fs, net]
url_inputs:
  accepted: [video/*, audio/*, application/octet-stream]   # content types; a trailing * is a prefix
  max_mb: 2000
```

- **A URL is never a path.** Validation allows one only in an input arg (`inputs`, `input`,
  `files`, `base`, `append`, or an arg with `path_role: input`). Only http and https are allowed,
  and never a user name or password. The rules (`knaif.urls`) are pinned for both runtimes by
  `contracts/parity/url_inputs_cases.json`.
- **The fetch stage** runs after the clarify gates and before routing, collision naming,
  expansion and preflight. It replaces every URL with a file at the top of the run scratch, so
  no later stage sees a URL, and outputs derived from it are named in the user's folder
  (`user_side_path`). The download itself never reaches the user's folder.
- **Nothing is fetched without approval.** The CLI's `--allow download` (or `--yes`, which
  allows everything) approves up front. Otherwise each URL asks `Download <url>? [y/N]` (Enter is
  no), and with no terminal the run stops, naming the flag. In the library, set
  `agent.allow_download` or `agent.download_confirmer(url, max_bytes)`.
- **A dry run requests nothing.** It names the scratch path the download would take
  (`agent.last_downloads`), so the rendered plan is complete. That file is not there yet, so a
  handler's dry run must not open an input that does not exist (as with a chain intermediate):
  a read returns an empty stub marked `"mode": "dry_run"`.
- **The policy** (redirects, timeouts, size cap, content types, no cookies or credentials) lives in
  the net toolkit's `download_file`; see `toolkits/net/SPEC.md`.
- **A skill without `url_inputs:`** clarifies: it works on local files.
- **Both runtimes.** Python: `CommandAgent._fetch_url_inputs`, `knaif-cli run --allow download`.
  Native: `fetch_url_inputs` in `apps/cli` (`knaif run --allow download`), with the
  `knaif_core::urls` rules and the `knaif-toolkit-net` crate (ureq).

## Declared effects

Every skill and toolkit tool declares in `tools.yaml` what it may do to the world:

| Effect | Means |
|---|---|
| `fs.read` | reads files (a toolkit lookup that looks at the disk counts) |
| `fs.write` | writes, moves or deletes files, through the fs toolkit |
| `net` | reaches the network |
| `exec:<program>` | runs an external program, e.g. `exec:ffmpeg`, `exec:soffice` (lowercase letters, digits, `_`, `-`) |

`effects: []` declares a tool with none. A tool that declares nothing is refused when its bundle
loads (Python `Skill.load`, native `knaif_core::load_skill_registry`); the core control tools are
core's and exempt. The two runtimes agree through `contracts/parity/effects_cases.json`.

**At run time (Python)** a step is held to its declaration:

- `ctx.exec(program, argv, **kwargs)` is the one way a step runs a process (`subprocess.run`
  underneath); it raises `EffectNotDeclared` unless the tool declares `exec:<program>`.
  `argv[0]` may be the program's full path; `program` is the name the tool declares.
- `ctx.call_tool(name, args)` raises `EffectNotDeclared` unless the calling tool declares every
  effect of `name`, so a step without `fs.write` cannot write through the fs toolkit.
- A context built outside the agent (a bare `HandlerContext`) is not restricted.

**The lint** (`python/core/tests/test_tool_modules_io_lint.py`, in `just check`) bans direct
I/O in tool modules (`skills/*/python/*.py`, `toolkits/*/python/*.py`): `subprocess`,
`urllib`/`httpx`/`requests`/`socket`, shutil's copy/move/remove functions, pathlib and `os`
writes and deletes, and `open` in a write mode. Every exception is listed with its reason (for
example a library that writes the staged path `stage_output` handed out); an exception that no
longer matches anything fails the lint.

**Native** reads and validates the same declarations when a bundle loads; enforcing them at run
time waits for a native step context (audit F11).

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

Extra header lines that only some requests need go under `rules:`, so every other prompt stays
byte for byte as it was. A rule joins the end of the header when one of its `tools` is in the
prompt, or, with `when: many`, when the request asks for many files (a batch word, a plural file
word or a glob, not counting a URL or a known folder named as a place:
`nl_clarify_gate.asks_for_many_files`, pinned by `contracts/parity/many_files_cases.json`). An
example marked `when: many` is likewise shown only to such a request.

```yaml
rules:
  - tools: [split_video, extract_frames]
    text: |
      - ONE FILE INTO MANY: ...
  - when: many
    text: |
      - MANY FILES, SEVERAL STEPS: write the steps as for ONE file ...

examples:
  - request: "Rotate every mkv in this folder 90 degrees and make them 1080p."
    when: many
    output: ...
```

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
2. Update `skill.yaml` name, description, `toolkits:`, data references, and `arg_value_sets`.
3. Replace public and internal tools in `tools.yaml`.
4. Implement the `Handler` / `Intent` classes in `python/` (and a `Skill` subclass only if you need hooks); take shared tools from a toolkit rather than writing your own.
5. Write `prompt.yaml` with model-visible rules and examples.
6. Add JSONL data and tests.
7. Verify with `CommandAgent.from_skill("skills/my_skill", sandbox="...")`.
