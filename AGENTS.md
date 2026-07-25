# AGENTS.md - Context for AI Coding Agents

## What This Project Is

`knaif` converts natural-language input into validated JSON action plans executed through
skill packages. The model only proposes a plan; deterministic code validates, expands,
confirms, and executes it.

It is a **dual-runtime monorepo**: the Python library is the authoring/eval/training
runtime, and a Rust workspace is the shipped native runtime. Both read the same
language-neutral YAML contracts and the same skill bundles.

## Repository Layout

```text
python/core/knaif/     the `knaif` Python package (import path stays `knaif.*`)
python/core/tests/     core test suite
python/training/       LoRA/DPO dataset builders + training scripts
native/crates/         reusable Rust engine crates (knaif-core, -models, -llm, -skill-api)
apps/cli/              the native `knaif` CLI binary
skills/<name>/         self-contained skill bundles (YAML + python/ + native/ + data/ + eval/)
contracts/             no-code, cross-language contracts (runtime/, models/, parity/)
evals/                 all eval run history, baselines, retrieval + parity results
models/                local GGUF files (gitignored)
notebooks/             cross-skill model experiments and authoring tools (see Notebooks below)
docs/                  documentation and durable plans
```

`contracts/runtime/core_tools.yaml` is the canonical source for the core control tools; a
byte-identical copy ships inside the wheel. Edit the canonical file, then run
`just sync-runtime` — a drift-guard test fails otherwise.

## Documentation Map

- Developer SDK (knaif.cli): `docs/SDK.md`
- Skill authoring and skill registry format: `docs/TOOL_SCHEMA.md`
- Core architecture and execution pipeline: `docs/ARCHITECTURE.md`
- Native (Rust) runtime — tech stack, llama.cpp, CPU/Vulkan/CUDA, packaging: `docs/NATIVE.md`
- Released models — HuggingFace hosting, manifest/`models.yaml`/`eval_backends.yaml` roles,
  base-model and quant rationale, fine-tune summary, publishing a new model: `docs/MODELS.md`
  (read this before answering any "which model / why that model" question)
- Inference backends and model resolution — `models.yaml` precedence, Ollama, llama.cpp
  install per platform, GPU offload checks: `docs/INFERENCE.md`
- Cutting a release — build/package/verify/publish per OS+kind, CUDA arch range, checksums:
  `docs/RELEASE.md`
- Performance scorecard — hardware × runtime × backend × model, and which machine each
  latency number came from: `docs/PERFORMANCE.md` (read before quoting any speed figure)
- Variable binding and optimizer: `docs/VARIABLE_BINDING.md`
- Non-code provenance — base model, corpora, fixtures, site assets, and the two generated
  dependency-license reports: `docs/PROVENANCE.md` (legal attribution itself lives in `NOTICE`;
  regenerate the reports with `just licenses-all` before cutting a release)
- Product requirements and safety scope: `docs/REQUIREMENTS.md`
- Per-skill behavior and system requirements: `skills/<name>/SPEC.md` (e.g. `skills/ffmpeg/SPEC.md`, `skills/documents/SPEC.md`)
- Eval corpus schema, verifiers, scoring: `docs/EVAL_FRAMEWORK.md`
- Manually verifying eval results (local + premium arms): `docs/EVAL_VERIFICATION_SOP.md`
- Handoff contract for premium-LLM eval arms: `docs/BIG_LLM_HANDOFF.md`
- Generated scratch space and fixtures: `docs/SANDBOX.md`
- Corpus authoring workflow: `docs/CORPUS_AUTHORING_STEPS.md`
- Fine-tuning a model (canonical how-to + methodology rules + known outcomes): `docs/FINE_TUNING.md`
- Fine-tuning dataset generation: `docs/TRAINING_DATA_GENERATION.md`
- Active checklist: `docs/TODO.md`
- Durable implementation plans: `docs/plans/`

## Plans and Todos

Write implementation plans under `docs/plans/YYYY-MM-DD-<topic>.md`.
Use `docs/TODO.md` for active checklists and status.
Keep skill-specific planning or specs in `skills/<name>/` when they only apply to that skill.
Do not create new root-level planning files unless the user explicitly asks.

Plans are single self-contained files. Use inline `- [ ]` checkboxes on task headings
to track progress — do not create a separate todo file alongside a plan.

## Entry Points

Skill-hosting (operator / eval path):

```python
from knaif import create_agent, list_skills

list_skills()                                    # ["documents", "ffmpeg", "io"]
agent = create_agent("io", sandbox="./sandbox")  # fully wired CommandAgent

from knaif import CommandAgent

agent = CommandAgent.from_skill("skills/io", sandbox="./sandbox")
agent = CommandAgent("skills/io/tools.yaml", sandbox="./sandbox")
```

Developer SDK (embedding NL in your own CLI — see `docs/SDK.md`):

```python
import knaif.cli as nk

@nk.command(help="Add a task", keywords=["add", "create"])
def add(title: nk.Arg(help="task title")):
    ...

app = nk.App([add])
app.run()                          # reads sys.argv[1] as NL utterance

# Or wrap an existing click group:
from myapp.cli import cli
app = nk.from_click(cli)
app.run()
```

## Running Tests

```bash
uv run pytest python/core/tests/ --tb=short
uv run pytest skills/ --tb=short
uv run pytest --tb=short
just check
```

After any code change, run the focused tests plus the full suite. Per-skill tests live in
`skills/<name>/python/tests/`; each skill should have at least a smoke test that loads the
skill, validates the registry, and executes one dry-run plan.

## Running Evaluations

**All eval-suite runs save to `evals/`, never a root-level `runs/` folder.**
Use `--save evals/runs/<YYYY-MM-DD>_<label>_<verifier>/` (the naming convention
is defined in `evals/INDEX.md`), and add a row to `evals/INDEX.md` for each
saved run. `evals/` is the single home for all run history; do not create or write
to `./runs/`.

```bash
uv run -m knaif.evalsuite run --skill ffmpeg \
  --config eval_backends.yaml --backends qwen3-4b --verifier cheap \
  --save evals/runs/2026-01-01_my-arm_cheap
```

## Architecture

```text
User input
  -> build_prompt()
  -> model or mock inference
  -> parse_plan()
  -> validate_plan()
  -> [optional] summarize_plan() → plan_display callback   (StepA, show_plan=True)
  -> [optional] plan_confirmer approval gate               (StepB, require_approval=True)
  -> Intent.expand()
  -> optimize_plan()
  -> resolve_args()
  -> Step.handle() with HandlerContext   (via tool_map)
```

Key modules (all under `python/core/knaif/`):

| Module | Responsibility |
|---|---|
| `agent.py` | `CommandAgent` pipeline, `tool_map` dispatch, plan-preview / approval hooks |
| `planner.py` | parsing, validation, optimizer, variable resolution, `summarize_plan()` |
| `registry.py` | `ToolDef`, registry loading, retrieval |
| `prompt.py` | model-facing prompt construction |
| `orchestrator.py` | llama.cpp and Ollama backends |
| `tool.py` | `Step` / `Intent` ABCs |
| `skill_base.py` | `Skill` base class (authors subclass; `tools`, `preflight`, `format_results`, `run_artifact`) |
| `core_tools.py` | built-in core control Steps (`clarify`/`reject`/`done`/`wait_for_confirmation`) |
| `steps/` | shared Step library (`ResolveInputs`); `steps.yaml` metadata lives in `contracts/runtime/` |
| `skill.py` | `Skill.load()` — instantiates the `skill_class:`, builds the `name → Tool` map |
| `handler_api.py` | `HandlerContext` |
| `evaluator.py` | evaluation helpers |

## Adding A New Skill

Start with `docs/TOOL_SCHEMA.md`.

A skill is a **self-contained bundle** at `skills/<name>/`: the declarative YAML + data
sit at the **top** of the bundle (readable by every runtime), and the Python
implementation lives in a `python/` subpackage. `ctx.skill_dir` is always the bundle
root — handlers resolve `profiles/`, `vocab.yaml`, `data/` from there, never from their
own `__file__`.

Required files:

- `skills/<name>/skill.yaml`        (bundle top)
- `skills/<name>/tools.yaml`        (bundle top)
- `skills/<name>/python/__init__.py`  (re-exports the `Skill` subclass)
- `skills/<name>/python/handlers.py`

Recommended files:

- `skills/<name>/SPEC.md` — authoritative human-facing spec of the skill's
  behavior. Lead with a **System Requirements** section (external binaries,
  services, env vars). See `docs/TOOL_SCHEMA.md` for the expected sections.
- `skills/<name>/prompt.yaml`
- `skills/<name>/data/*.jsonl` — eval / train / safety corpora (see *Skill Lifecycle* below)
- `skills/<name>/eval/` — skill-owned fixtures and verifiers
- `skills/<name>/native/` — Rust crate, if the skill ships in the native runtime
- `skills/<name>/python/tests/test_<name>.py`
- `skills/<name>/notebooks/*.ipynb` for skill-local exploratory tools
- Additional `python/` modules (`intents.py`, `steps.py`, `_engine.py`, …) imported
  with relative imports (`from ._engine import build`).

### Minimal `skill.yaml`

```yaml
name: my_skill
description: "One-line description."
tools: tools.yaml
skill_class: handlers.MySkill
prompt: prompt.yaml
```

Tool contract — every tool is a `Step` or `Intent` class, linked to its `tools.yaml`
entry by `name`. The `Skill` subclass lists them in `tools` and is named by
`skill_class:` in `skill.yaml`:

```python
from knaif.handler_api import HandlerContext
from knaif.skill_base import Skill
from knaif.tool import Intent, Step

class MyToolStep(Step):
    name = "my_tool"
    def handle(self, args: dict, ctx: HandlerContext) -> dict:
        # ctx.root, ctx.sandbox, ctx.dry_run, ctx.confirmed, ctx.skill_dir
        # ctx.confirmer, ctx.confirm(prompt, preview=None)
        return {"result": "..."}

class MySkill(Skill):
    tools = [MyToolStep]
```

The legacy `HANDLERS` / `EXPANDERS` dict form is no longer supported — every tool must be a
`Step` or `Intent` class.

Use an `Intent` (override `expand`) when a model-visible intent tool should become a
deterministic multi-step workflow; override `Intent.summarize(self, args, **kw)` for a
one-sentence preview shown when callers set `show_plan=True`. Skill-level
`preflight` / `format_results` / `run_artifact` are `Skill` methods. Reuse shared steps
from `knaif.steps` by adding the class to `tools`. The core control tools
(`clarify`/`reject`/`done`/`wait_for_confirmation`) are merged automatically. See
`docs/TOOL_SCHEMA.md` and `docs/ARCHITECTURE.md` for the full contract.

## Skill Lifecycle Beyond Authoring

Handlers are step one. A skill is only "done" once it is evaluated, represented in the
training mix, and (if it ships natively) ported. The bundle holds all four concerns:

```text
skills/<name>/
  skill.yaml tools.yaml prompt.yaml    # declarative contract — read by both runtimes
  python/                              # Python handlers + tests
  native/                              # Rust crate (Cargo workspace member)
  data/                                # corpora: eval, train, safety, locked snapshot
  eval/                                # skill-owned fixtures + verifiers + reviewer
```

### 1. Evaluation

Corpora and the acceptance bar live **in the skill**, not centrally:

| File | Role |
|---|---|
| `data/eval.jsonl` | the eval corpus (row schema in `docs/EVAL_FRAMEWORK.md`) |
| `data/eval_snapshot.json` | the committed acceptance bar; regression gate compares against it |
| `data/safety_test.jsonl` | utterances that must produce `reject` |
| `eval/fixtures.py` | generates fixtures into `sandbox/fixtures/<skill>/` |
| `eval/verifiers.py` | skill-specific grading beyond the shared verifiers |

Verifiers are **phases, not alternatives** — the eval ladder. Develop in phases 1–2, cross
3–5 once to finish the skill. Full table and rationale: *The eval ladder* in
[docs/EVAL_FRAMEWORK.md](docs/EVAL_FRAMEWORK.md#the-eval-ladder--fast-while-developing-executing-before-done).

```bash
# 1. authoring — no model needed
uv run pytest skills/<skill>/python/tests/
just native-mock -- skills list
# 2. routing — fast loop while building (cheap verifier)
just eval <skill> --limit 20
just eval <skill>
# 3. honest — ALWAYS regenerate fixtures first (missing fixtures score correct plans ~0)
just eval-fixtures <skill>
just eval-success <skill>
# 4. lock the acceptance bar (own commit)
just eval-snapshot <skill>
# 5. native parity, if the skill ships natively
just parity <skill>

just eval-regression <skill>        # gate a run against the committed snapshot
```

**`cheap` is an iteration instrument, never an acceptance bar.** Quote an executing
verifier (`success`, or `output_diff` where coverage is better), never `cheap`. A skill is
not "done" until its snapshot is locked with an executing verifier — a `cheap` snapshot
reports false regressions when the corpus is annotated.

Re-locking the snapshot moves the acceptance bar — do it deliberately, in its own commit,
and only when adopting a measured improvement. Every saved run gets a row in
`evals/INDEX.md`.

### 2. Fine-tuning

One shared model serves every skill, so a new skill's `data/train.jsonl` becomes part of a
union dataset — and every *other* skill's snapshot is the regression gate on that union.
`docs/FINE_TUNING.md` is the canonical how-to and records outcomes already settled; read
its methodology rules before running an experiment rather than re-deriving them.

The loop: author `data/train.jsonl` (`docs/TRAINING_DATA_GENERATION.md`) → build the union
chat dataset → train a LoRA → merge to GGUF and quantize → eval **every** active skill
against its snapshot → promote by adding an entry to `models.yaml` /
`contracts/models/model-manifest.yaml` and pointing the skill's `recommended_model:` at it.
Training code is in `python/training/`.

### 3. Native port

`skills/<name>/native/` is a workspace member in the root `Cargo.toml`, consuming
`knaif-skill-api` (the Rust `HandlerContext` / `Step` / `Intent` equivalents). `skill.yaml`
declares which runtimes implement the skill:

```yaml
runtimes:
  python: { handlers: handlers.MySkill }
  native: { status: supported, crate: knaif-skill-my-skill }
```

The native runtime is a **port, not a rewrite** — same prompt, same validation, same
expansion, so the same utterance must render the same command on both sides.
`just parity <skill>` pins both runtimes to the identical GGUF and diffs the rendered
output; results land in `evals/parity/`. Cross-runtime contracts (`contracts/runtime/`,
`contracts/parity/planner_cases.json`) exist so the two implementations can't drift
silently. See `docs/NATIVE.md`.

```bash
just check-native                   # fmt + clippy, warnings are errors
just test-native                    # cargo test --workspace
just native-mock -- skills list     # fast build, mock backend, no llama.cpp
just parity <skill> --limit 20      # native vs Python on real utterances
```

## Safety Model

1. The model emits only `{ "plan": [...] }`.
2. Unknown tools and unsupported args are rejected.
3. Sandbox-sensitive paths are validated before execution and after variable resolution.
4. `execute_plan(dry_run=True)` previews without side effects when handlers honor `ctx.dry_run`.
5. `safety_category: destructive` requires `confirmed=True` or `dry_run=True`.
6. Preview gates can use a `confirmer` callback through `ctx.confirm()`.
7. Safety policy is driven by `tool_def.safety_category`, not hard-coded tool names.

## Key Files For Each Task

| Task | Start here |
|---|---|
| Add or change a skill | `skills/<name>/` + `docs/TOOL_SCHEMA.md` |
| Change plan validation | `python/core/knaif/planner.py` + `python/core/tests/test_planner.py` |
| Change prompt format | `python/core/knaif/prompt.py` + `python/core/tests/test_prompt.py` |
| Change execution flow | `python/core/knaif/agent.py` + `python/core/tests/test_agent.py` |
| Change skill loading | `python/core/knaif/skill.py` + skill tests |
| Evaluation / metrics | `python/core/knaif/evaluator.py` + `python/core/tests/test_evaluator.py` |
| Change a core control tool | `contracts/runtime/core_tools.yaml` + `just sync-runtime` |
| Port a skill to native | `skills/<name>/native/` + `docs/NATIVE.md` |
| Change training data | `skills/<name>/data/train.jsonl` + `docs/TRAINING_DATA_GENERATION.md` |

## Conventions

- Keep `knaif/` domain-agnostic; put domain behavior in `skills/<name>/`.
- All handlers accept `(args: dict, ctx: HandlerContext)` and return a `dict`.
- Update docs with code changes when behavior or extension points change.

## What Not To Do

- Do not import a specific skill from `knaif/` core code.
- Do not hard-code skill-specific safety rules in core.
- Do not add new domain tools to `knaif/executor.py`.
- Do not resolve variable references during `validate_plan`; resolution is runtime behavior.

## Notebooks

Notebooks in `notebooks/` are for cross-skill model experiments and authoring tools.
Skill-specific notebooks live in `skills/<name>/notebooks/`. They are not the
primary source of truth.
