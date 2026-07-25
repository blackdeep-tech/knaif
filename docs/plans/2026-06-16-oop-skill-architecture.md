# OOP Skill Architecture

**Status:** Done · **Created:** 2026-06-16 · **Completed:** —
**Owner:** core · **Ref:** PR #15

> **Status note:** Phases 0–5 complete. Legacy skill-loader path removed
> (`skill_class:` required), core control tools de-duplicated, docs rewritten. The only
> legacy surface intentionally left is executor's bare-`CommandAgent` io implementation
> — scoped to the separate io rebuild (see Phase 5).
>
> **Re-verified 2026-07-22** (S7 decision — **kept**; already in the cited-from-source
> tier). Four claims re-checked and still true: the io remainder is genuinely open
> (`cmd_list_files` / `find_files` / `delete_files` / `move_files` still in
> [`executor.py`](../../python/core/knaif/executor.py)); `CORE_HANDLERS` derives from
> `CORE_STEP_MAP`; `_derive_oop_views` is still in
> [`skill.py`](../../python/core/knaif/skill.py); and `PlanBuilder` / `VarRef` are still
> absent, so the Phase 4 deferral held.
>
> **The plan's rules already live in the shipping docs** — the leaf-vs-macro
> discriminator and the purity of `expand` in [TOOL_SCHEMA.md](../TOOL_SCHEMA.md), the
> tool/skill model and the static-planning choice in
> [ARCHITECTURE.md](../ARCHITECTURE.md). Those are the current reference; this file
> records how the interface model was decided.
>
> **Two locations moved after this plan shipped** (the `contracts/` restructure), and the
> text below has been repointed:
> - **`steps.yaml` → `contracts/runtime/steps.yaml`**, not `knaif/steps/`. The Python
>   package `knaif/steps/` still holds the *classes*; only the metadata moved.
> - **`core_tools.yaml` is now two files.** The canonical, language-neutral source is
>   `contracts/runtime/core_tools.yaml`, read by both runtimes; a byte-identical copy
>   ships inside the wheel at `python/core/knaif/core_tools.yaml`, kept in sync by
>   `just sync-runtime` and enforced by a drift-guard test. The decision recorded below —
>   one `load_registry` path for *all* tool metadata — is unchanged; only the file's home
>   became a cross-language contract.

**Goal:** Replace the dict-based skill wiring (`HANDLERS`/`EXPANDERS`/`SUMMARIZERS`/`PREFLIGHTS` + module singletons) with a uniform interface-based model (`Step` / `Intent` / `Skill`), extract a shared step library (`knaif.steps`), and shape the core so a future Rust implementation is a near-mechanical port.

---

## Why

Today a single tool is smeared across up to six places: a `tools.yaml` entry plus the `HANDLERS`, `EXPANDERS`, `SUMMARIZERS`, `PREFLIGHTS` dicts and the `RESULT_FORMATTER` / `ARTIFACT_RUNNER` module globals (see [skills/ffmpeg/python/handlers.py](../../skills/ffmpeg/python/handlers.py), 2,500+ lines). Adding or sharing a tool means hand-syncing several maps. We also intend to:

- ship a **native Rust implementation** later (skills authored/tested in Python, then optionally rewritten in Rust for a dependency-free CLI; C++ only for llama.cpp inference);
- target **developers** building their own skills, reusing core tools, or forking a skill (fork-and-own is acceptable — no overlay/inheritance engine needed);
- keep the small model's job **deterministic** and easy.

This plan makes a tool a single object implementing a contract, keeps all model-facing metadata in YAML (so both language implementations render an identical prompt and validator), and isolates the polymorphism seams behind interfaces.

## Goals

- One uniform way to define a tool: implement `Step` or `Intent`. No dicts, no `globals()`, no decorator.
- Tool **metadata stays in YAML**; classes carry behavior only, linked by `.name`.
- A shared step library `knaif.steps` (+ `steps.yaml`) reused across skills by name.
- Core shaped as **data structures + pure functions + a few interfaces** → portable to Rust (`trait` + `impl`).
- No regression in skill evaluation (case-for-case eval parity, golden-prompt + wiring-equivalence tests).

## Non-goals

- Skill overlay / inheritance / hot-plugin discovery (fork-and-own covers extension for now).
- Converting the eval-critical pipeline (`planner.py`, `prompt.py`) to classes.
- The Rust implementation itself (this plan only shapes Python so the port is mechanical).
- A decorator/macro sugar layer (explicitly deferred — see Deferred).

---

## Decision record

These are settled. They are the contract the tasks below implement.

**Tool & skill model**
- "Tool" is the umbrella; the two concrete kinds are interfaces: `Step` (`handle`, default `preflight`) and `Intent` (`expand`, default `summarize`, default `preflight`). Two interfaces, mirroring today's HANDLERS vs EXPANDERS.
- The discriminator between them is **shape, not visibility**: a `Step` is a leaf (executes → result dict), an `Intent` is a macro (expands → sub-plan of Steps). Model-visibility is a separate YAML flag (`internal: true/false`), orthogonal to the interface. Hence the core control tools (`clarify`/`reject`/`done`/`wait_for_confirmation`) are model-visible **Steps** — leaves the model may emit directly — not a third kind. Intents compile away at expansion; the executor only ever runs Steps.
- Every tool is a class. No function+adapter hybrid, no decorator, no `globals()`.
- A skill is a `Skill` subclass with explicit `tools = [...]`; `skill.yaml` names the class via `skill_class:`. Resolution is **module-relative within the skill dir**: `skill_class: handlers.FFmpegSkill` means class `FFmpegSkill` in the skill's `handlers.py` (the module name before the dot selects the file; bare `FFmpegSkill` defaults to `handlers`). Reuses the existing `spec_from_file_location` loader, so the new class can coexist with the legacy `HANDLERS` dict in the same file during migration. (Phase 5 may rename the file to `skill.py`.)
- Per-skill singletons become `Skill` methods: `format_results`, `run_artifact`, and `preflight` (the latter replaces the `"*"` wildcard preflight). Preserve today's precedence exactly — `tool.preflight()` only if the tool's class **overrides** the default (test `type(tool).preflight is not Step.preflight` / `is not Intent.preflight`, not mere presence — every tool inherits a no-op default), **else** the skill-level `preflight` (mirrors `self.preflights.get(tool) or self.preflights.get("*")` at [agent.py](../../python/core/knaif/agent.py)); the two never both run. ⚠️ If dispatch keys off presence rather than override, ffmpeg — which relies entirely on the wildcard and defines no per-tool preflight — silently loses all input-existence validation.
- Core control tools (`clarify`, `reject`, `done`, `wait_for_confirmation`) also become built-in `Step` classes (a core tool set in `python/core/knaif/`), replacing the `CORE_HANDLERS` dict. They are merged into every skill's registry by name, exactly as `CORE_HANDLERS` is merged today — so "every tool is a class" holds with no exceptions. They stay in core (not a skill dir) and carry no domain logic, respecting the `executor.py` boundary. Their *metadata* lives in `core_tools.yaml`, loaded through the **same** `load_registry` path as a skill's `tools.yaml`, alongside the core `Step` classes in `core_tools.py`. (That file is now canonically `contracts/runtime/core_tools.yaml` with a synced copy in the wheel — see the Status note.)
  - **Amended 2026-06-17 (Phase 5+).** Originally this plan kept core-tool metadata as a Python `CORE_TOOL_DEFS` dict, reasoning the "metadata in YAML" rule governs only skill tools. That was reversed: there is now **one** metadata parsing path (`core_tools.yaml` → `load_registry` → `ToolDef`) for *all* tools, core and skill alike. This removes the dual representation (a hand-built `ToolDef(...)` rebuild in `_merge_core_tool_defs`), and the Rust port reads a single schema for every tool. `_merge_core_tool_defs` is now `registry.setdefault(name, tool_def)`.

**Metadata & data**
- Tool metadata stays in YAML (`tools.yaml` / `steps.yaml` / `core_tools.yaml`); classes hold behavior only, linked by `.name`. All three load through one `load_registry` path.
- `ToolDef`, `HandlerContext`, plan/step stay dataclasses (→ Rust structs).

**Engine scope**
- Interfaces only at polymorphism seams: tools, skills, inference backends.
- `parse_plan` / `validate_plan` / `optimize_plan` / `resolve_args` / `build_prompt` / `select_examples` / `load_registry` stay pure functions (also protects eval-critical code).
- `CommandAgent` stays a class; internals swap the four dicts for a `name → Tool` map + method dispatch.

**Loader seam**
- `Skill.load()` returns an already-built registry (`tools.yaml` ∪ `steps.yaml`, keyed by `.name`) plus the instantiated `name → Tool` map. `CommandAgent` accepts a pre-built registry rather than re-deriving one from a single YAML path, keeping `load_registry` pure and the union deterministic (no temp files, no hidden loader magic).
- During migration the loader **branches on `skill_class:`**: present → new interface path; absent → legacy `HANDLERS`/`EXPANDERS` path. Both coexist from Phase 1 until the legacy path is deleted in Phase 5, so every phase stays green.

**Shared tools / reuse**
- Shared tools live in `knaif.steps` (class) + `steps.yaml` (metadata, now under `contracts/runtime/`), merged into a skill's registry by name; collision = loader error.
- No skill-to-skill imports. Cross-skill reuse = promote to `knaif.steps`, compose via the plan.
- Promotion (private→shared) = relocate class + YAML block + swap list entry; name stable → zero plan/prompt/eval impact.
- Discovery steps return `{count, files}`; consumers accept a bare list or that dict. `knaif.steps` starts flat.

**Expander variables**
- Add a typed `PlanBuilder` + `VarRef` so expanders pass handles, not `"$files"` strings (wire format unchanged). Plain constants are the fallback if the builder proves heavy.
- Variable names are **supplied explicitly**, never inferred from Python locals or class names: `files = p.add("files", steps.ResolveInputs, ...)` serializes to `"$files"`. This is required, not stylistic — today's expanders emit literal `"$files"`/`"$probes"`/`"$recipes"`, and the builder must reproduce those exact strings to keep the wire format (and eval parity) unchanged.
- Expander-internal vars are deterministic code, not model output; model-emitted vars (io-style) are governed by validation.

**Principles**
- Determinism tie-breaker: equal designs → pick what the small model emits most reliably (e.g. `recursive` flag, not `**` glob).
- Keep core as structs + pure functions + a few traits for a near-mechanical Rust port.

---

## Target shapes

### Interfaces (behavior only — metadata is in YAML)

```python
# knaif/tool.py
class Step(ABC):                       # leaf tool: executes, returns a result (no expansion)
    name: str
    @abstractmethod
    def handle(self, args: dict, ctx: HandlerContext) -> dict: ...
    def preflight(self, args: dict, **kw) -> list[str]: return []

class Intent(ABC):                     # macro tool: expands into a sub-plan of Steps
    name: str
    @abstractmethod
    def expand(self, args: dict) -> list[dict]: ...
    def summarize(self, args: dict, **kw) -> str: ...   # default generic rendering
    def preflight(self, args: dict, **kw) -> list[str]: return []
```

### A skill

```python
# knaif/skill_base.py exposes Skill; skills subclass it
class FFmpegSkill(Skill):
    tools = [TrimVideo, InspectMedia, steps.ResolveInputs, ...]   # local + shared

    def preflight(self, tool: str, args: dict, **kw) -> list[str]:
        return check_inputs_exist(args, **kw)          # was PREFLIGHTS["*"]
    def format_results(self, results, *, dry_run): ...  # was RESULT_FORMATTER
    def run_artifact(self, cmd, fixture, out_dir): ...  # was ARTIFACT_RUNNER
```

```yaml
# skill.yaml gains (module-relative: class FFmpegSkill in the skill's handlers.py):
skill_class: handlers.FFmpegSkill
```

### Shared step (defined once, used by many)

```python
# knaif/steps/__init__.py
class ResolveInputs(Step):
    name = "resolve_inputs"
    def handle(self, args, ctx): ...
```
```yaml
# contracts/runtime/steps.yaml  (was knaif/steps/steps.yaml — see Status note)
resolve_inputs:
  description: "Resolve input paths (files, dirs, globs) inside the sandbox."
  required_args: [paths]
  optional_args: [extensions]
  internal: true
  readonly: true
```

Loader builds a skill's registry = skill `tools.yaml` ∪ `steps.yaml` (for shared tools in `Skill.tools`), keyed by `.name`; duplicate name → error.

### PlanBuilder (kills magic `$var` strings in expander code)

```python
def expand(self, args):
    p = PlanBuilder()
    files   = p.add("files",   steps.ResolveInputs, paths=inputs)
    probes  = p.add("probes",  InspectMedia, files=files)
    recipes = p.add("recipes", BuildRecipes, probes=probes, options={...})
    return p.build()        # serializes to the existing dict-with-$var plan
```

`p.add(name, ...)` takes the variable name explicitly and returns a `VarRef`; passing it as an arg serializes to `"$files"`, `probes.field("x")` → `"$probes.x"`. Names are never auto-derived (the existing `$files`/`$probes`/`$recipes` strings must be reproduced verbatim). A reference to an unproduced variable is a build-time error.

---

## Phases & tasks

Order matters: each phase ends green, and the eval gate runs before any skill migration.

### Phase 0 — Baseline & gates `- [x]`
- [x] Capture an eval snapshot on the current backends (the parity baseline for the whole effort). — Baseline is the committed [`skills/ffmpeg/data/eval_snapshot.json`](../../skills/ffmpeg/data/eval_snapshot.json) (from the nl-clarify-gate work; tracked & clean). Headline metrics recorded below; Phase 4 must match these case-for-case (within the `diff_snapshots` 0.02 threshold). A fresh same-day run needs a model backend — run `just eval-honest ffmpeg` to refresh if desired.
- [x] Add a **golden-prompt test**: [`python/core/tests/test_golden_prompt.py`](../../python/core/tests/test_golden_prompt.py) locks the rendered system message for ffmpeg (full + retrieved-examples path) and io. Goldens under `python/core/tests/golden/`. Regenerate with `KNAIF_UPDATE_GOLDEN=1`. Must keep passing through every later phase (io goldens regenerate in Phase 3).
- [x] Record the current full-suite pass state as the reference (below).

**Baseline recorded 2026-06-16:**
- Eval (cheap verifier, 293 rows): `outcome_accuracy=0.8635`, `avg_knaif_score=0.9398`, `tool_accuracy=0.7986`, `schema_validity=0.9829`.
- Core suite `pytest tests/`: **749 passed** (includes the 3 new golden cases).
- Skills suite `pytest skills/`: **335 passed**.

**Acceptance:** ✅ baseline eval present & committed; golden-prompt gate added and green; both suites green.

### Phase 1 — Core interfaces & loader `- [x]`
- [x] Add `Step` / `Intent` ABCs (`python/core/knaif/tool.py`).
- [x] Add `Skill` base (`tools` list; `preflight` / `format_results` / `run_artifact` methods with safe defaults).
- [x] Convert the core control tools (`clarify`, `reject`, `done`, `wait_for_confirmation`) from `CORE_HANDLERS` into built-in `Step` classes; merge them into every skill registry by name (mechanical — bodies move verbatim).
- [x] Teach `skill.py` to **branch on `skill_class:`**: present → instantiate tools → build a `name → Tool` map and return a built registry (`tools.yaml` ∪ `steps.yaml` merged by name, collision = error); absent → keep the legacy `HANDLERS`/`EXPANDERS` path unchanged. Both paths live until Phase 5. Have `CommandAgent` accept the pre-built registry.
- [x] Update `agent.py` dispatch ([agent.py](../../python/core/knaif/agent.py)) to call `tool.handle` / `tool.expand` / `tool.summarize` and the skill-level `preflight` fallback (preserving the `tool.preflight` → skill-`preflight` precedence above).
- [x] **Fail-fast load validation**: when building the `name → Tool` map, assert each class in `Skill.tools` is a `Step`/`Intent` subclass *and* its `.name` resolves to a registry entry (missing metadata → error, alongside the duplicate-name collision error). Catches class/YAML drift at load, not at dispatch.
- [x] Define the inference backend interface in `orchestrator.py` (formalize existing backends behind one contract). No behavior change.
- [x] TDD dispatch/validation against **inline throwaway tool classes** in the test module (no skill dir needed).
- [x] Add **one tiny temp skill-dir test** exercising the real loader path that inline classes can't: `skill_class: handlers.FooSkill` module-relative import, manifest branching (present vs absent), and coexistence of a `skill_class` skill with a legacy `HANDLERS` skill.
- [x] **Wiring-equivalence test**: the new registry resolves the same handler/summarizer/preflight per tool that the old dicts did.

**Acceptance:** new loader/dispatch covered by tests using inline tools + one temp skill-dir test of the `skill_class:` loader path; legacy `HANDLERS` path still loads io/ffmpeg (dual-loader green); core tools resolve as `Step` classes; load validation rejects a non-`Step`/`Intent` class and a class whose `.name` is absent from the registry; `planner.py`, `prompt.py`, `registry.py` untouched; suite green.

**Completed 2026-06-16:** 1123 tests pass (796 core + 327 skills). New files: `python/core/knaif/tool.py`, `python/core/knaif/skill_base.py`, `python/core/knaif/core_tools.py`. Modified: `python/core/knaif/skill.py` (loader branch), `python/core/knaif/agent.py` (dispatch). New tests: `test_tool.py`, `test_skill_base.py`, `test_core_tools.py`, `test_skill_loader_oop.py`, `test_oop_dispatch.py`, `test_wiring_equivalence.py`.

### Phase 2 — `knaif.steps` shared library `- [x]`
- [x] Create `python/core/knaif/steps/` with `steps.yaml`; add `SharedStep` plumbing if needed for the merge. (`steps.yaml` has since moved to `contracts/runtime/`; the classes stayed.)
- [x] Move the `resolve_inputs` *implementation* + sandbox path helpers (`_assert_in_sandbox`) into `knaif.steps` (behavior unchanged). **Leave a thin delegating handler in ffmpeg `handlers.py`** (`cmd_resolve_inputs` → calls the new shared impl) so the legacy `HANDLERS` path keeps dispatching `resolve_inputs` until Phase 4 deletes it. Without this, Phase 2 ends red (ffmpeg is still on the legacy path).
- [x] Document and implement the `{count, files}` discovery contract; consumers accept list-or-dict.

**Acceptance:** `resolve_inputs` importable from `knaif.steps`, metadata in `steps.yaml`, unit-tested standalone; legacy ffmpeg still dispatches `resolve_inputs` via the delegating handler (suite green, ffmpeg untouched on the legacy path).

**Completed 2026-06-16:** 1142 tests pass. New package: `python/core/knaif/steps/` (`__init__.py`, `_resolve_inputs.py`, `steps.yaml`). ffmpeg `cmd_resolve_inputs` is now a 3-line delegating wrapper. New tests: `test_steps_package.py`, `test_resolve_inputs.py`. Note: `_assert_in_sandbox` lives in `knaif.steps._resolve_inputs`; ffmpeg keeps its own copy until Phase 4.

### Phase 3 — Rebuild `io` on the new arch (becomes fixture + reference) `- [x]`
- [x] Implement a minimal `io` skill (file CRUD: list/find/move/delete by type) as `Step`/`Intent` classes + `IoSkill`, consuming `steps.ResolveInputs`.
- [x] Retarget `python/core/tests/conftest.py` fixtures (`registry`, `agent`, `sandbox`) to the new `io`.
- [x] Replace the old io implementation and tests *in place* within `skills/io/` (keep the directory — it must stay a discoverable skill for `list_skills()`); remove the old `HANDLERS`-based files, not the skill dir.
- [x] First real cross-skill share validated: io and (next phase) ffmpeg both use `steps.ResolveInputs`.

**Acceptance:** core suite green against new io; old io *implementation* removed (skill dir retained); `list_skills()`/`create_agent()` still work (directory-driven, no code edits needed).

**Completed 2026-06-16:** 1161 tests pass. `handlers.py` rewritten with `ListFilesStep`, `FindFilesStep`, `DeleteFilesStep`, `MoveFilesStep` + `IoSkill`; `skill.yaml` gains `skill_class: handlers.IoSkill`. Legacy preflight dict consulted as secondary layer on OOP tools so `agent.preflights` post-hoc injection still works. `test_skill.py` updated (handlers→tool_map assertions); `test_skill_oop.py` added (19 tests, all green).

### Phase 4 — Migrate ffmpeg `- [x]`
- [x] Relocate each `cmd_*` / `expand_*` body verbatim into `Step` / `Intent` classes (mechanical — no logic edits). Done via a one-off AST migration script (deterministic re-indent + wrap), then deleted.
- [x] Keep the recipe engine (`_build_one_recipe`, `_build_flags`, `_render_command`) and coercions as **free functions** (not methods).
- [x] Move summarizers into `Intent.summarize`; move the `"*"` preflight into `FFmpegSkill.preflight`; move `RESULT_FORMATTER`/`ARTIFACT_RUNNER` into `FFmpegSkill.format_results`/`run_artifact`. (`_preflight_inputs`/`_format_results`/`_run_artifact` stay free functions; the skill methods delegate — keeps their direct unit tests intact.)
- [~] `PlanBuilder`/`VarRef` **deferred** — expanders kept their literal `"$files"`/`"$probes"`/`"$recipes"` strings (the fallback the plan explicitly allows). Bodies moved verbatim; introducing the builder would have been a logic edit, against "mechanical relocation." Track as a follow-up if expander readability warrants it.
- [x] `resolve_inputs` now uses the shared `knaif.steps.ResolveInputs` (first real cross-skill share: io + ffmpeg).

**Acceptance:** ✅ golden-prompt test byte-identical (ffmpeg + io); ✅ full ffmpeg behavioral suite (315 tests) green — expansion, recipe/flag building, command rendering, summarizers, preflights, result formatting, artifact runner; ✅ full suite 1168 passed. Eval parity established by construction (byte-identical prompt + behavior-identical expanders/handlers) plus the behavioral suite; a fresh live eval run still needs a model backend.

**Completed 2026-06-16:** handlers.py rewritten to 13 Step + 14 Intent classes + `FFmpegSkill`. Prerequisite commit first wired the OOP loader to derive `handlers`/`expanders`/`summarizers` views from `tool_map` and to populate `result_formatter`/`artifact_runner` from the skill instance (evalsuite/cli.py reads `skill.artifact_runner` directly), and fixed `Intent.summarize` to accept `**kw` (skill_dir). Test access migrated to the OOP surface (module key, `_tool()`/`_intent()` helpers, preflight-override assertion).

### Phase 5 — Cleanup & docs `- [x]` (one remainder belongs to the io rebuild)
- [x] Rewrite `docs/TOOL_SCHEMA.md` for the interface model (Step/Intent/Skill, `skill_class:`, `knaif.steps`, `steps.yaml`). Also rewrote `CLAUDE.md` (agent-facing canonical doc) — same stale dict surface.
- [x] Update `docs/ARCHITECTURE.md` pipeline/module table; note the Rust-portability shape (structs + functions + traits).
- [x] **Delete the legacy `skill_class:`-absent loader branch.** `Skill.load()` now has a single path: `skill_class:` is mandatory; a missing one raises a clear `ValueError`. Deleted `_load_handlers`. Migrated the remaining legacy test fixtures (`test_workflow_expansion`, `test_injectors`, `test_skill`) to the OOP form and replaced the dual-loader coexistence tests with a "missing skill_class raises" test.
- [x] **De-duplicate the core control tools.** `executor.CORE_HANDLERS` now derives from `CORE_STEP_MAP`; the duplicate `cmd_clarify/reject/done/wait_for_confirmation` functions are gone, so `core_tools.py` Steps are the single source of truth. (The original "remove the now-dead `CORE_HANDLERS` dict" was a false premise — the *dict name* still backs the bare-`CommandAgent` default, but the dead *duplication* it referred to is removed.)
- [~] **Remainder → io rebuild.** `executor.py` still holds a full legacy io implementation (`cmd_list_files`/`find`/`delete`/`move`) that is the bare-`CommandAgent` default and is unit-tested directly by `test_executor.py`. It is now redundant with the OOP `io` skill. Removing it = deciding the bare-agent default + migrating ~30 `test_executor.py` tests — the **io rebuild** that memory already tracks as a separate effort. Left as-is here.
- [~] **Derived compat views kept.** The `_derive_oop_views` accessors (`skill.expanders`/`summarizers`/`handlers`) stay: they are tool_map-sourced (single source of truth), not the old module dicts; removing them is a ~90-site test migration with no behavioral benefit. Fold into the io rebuild if desired.

**Status:** docs match the code; legacy skill-loader path and core-tool duplication removed; full suite green (1164) and eval parity proven (Phase 4, bit-for-bit vs `local11-success`). The only legacy surface left — executor's bare-agent io implementation — is the io rebuild, intentionally scoped out.

---

## Regression strategy

The refactor is **mechanical relocation**, not a rewrite: handler/expander bodies move unchanged, and `tools.yaml` / `prompt.yaml` are untouched — so the prompt is byte-identical and the model emits identical plans. Risk lives in wiring, guarded by three gates:

1. **Eval parity** — Phase 0 snapshot vs post-migration, case-for-case.
2. **Golden-prompt test** — rendered system message byte-stable across all phases.
3. **Wiring-equivalence test** — new `name → Tool` map resolves the same callables as the old dicts.

**Watch-items:** the `"*"` → `Skill.preflight` move (most likely silent regression in clarify/error behavior); `CORE_HANDLERS` → core `Step` classes (control-flow tools touch every plan — wiring-equivalence test must cover them); `run_artifact` relocation (eval execution arm); `format_results` relocation (CLI output); accidental prompt byte-drift from registry ordering.

## Risks

- **Builder scope creep** — if `PlanBuilder`/`VarRef` balloons, fall back to module constants and ship the builder separately.
- **io fixture mismatch** — new io must reproduce enough of the old io surface (file_type filtering, seeded sandbox) for existing core tests; budget for assertion edits in `test_agent`/`test_api`/`test_cli`/`test_executor`.
- **Backend interface churn** — keep it descriptive of existing behavior; no functional change in this plan.

## Deferred / open

- Decorator (Python) / macro (Rust) sugar over the interface — only if boilerplate proves painful; must generate the interface, never replace it.
- Namespacing `knaif.steps` (e.g. `steps.fs`, `steps.media`) — only past ~10 shared steps.
- Skill overlay/inheritance — out of scope while fork-and-own holds.
- **Dynamic / data-dependent plans** — `Intent.expand` is pure (no `ctx`), so the plan is frozen before any step runs and cannot branch on runtime results (e.g. type-dependent toolchains, dedupe with unknown delete count, re-encode-only-on-failure). All current ffmpeg/io cases are "same pipeline, different data," which the static model handles by pushing per-item choices into a step's output (recipes/flags). Deliberately deferred: if a real case needs runtime re-planning, add a deliberate seam then (model re-prompt after results, or a typed dispatcher step) rather than retrofitting `ctx` into `expand`.
- Final interface names (`Step`/`Intent`) — tentative; confirm during Phase 1.
