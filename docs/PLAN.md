# Implementation Plan — Phase 1: Variable Binding and Plan Optimizer

> **Historical — completed.** Phases 1 and 2 below shipped long ago (status tracked in
> [TODO.md](TODO.md)). Newer work lives in [plans/](plans/) — see [plans/README.md](plans/README.md).
> Kept for the design rationale; not an active plan.

Source of truth: [VARIABLE_BINDING.md](VARIABLE_BINDING.md) §1–§6, §8–§10.

---

## Dependency Graph

```
T1 (ToolDef.readonly)
│
├──► T4 (optimize_plan)
│
T2 (validation: output field, $var skip, forward-ref)   [independent of T1]
T3 (resolve_args)                                        [independent of T1/T2]
│
T4 depends on: T1
T5 depends on: T1, T2, T3, T4
```

Parallel-safe pairs: **T1 + T2 + T3** can all be written simultaneously.
T4 must follow T1. T5 must follow all four.

---

## Task 1 — `ToolDef.readonly` flag

**Files:** `knaif/registry.py`, `skills/io/tools.yaml`

### What to build
Add `readonly: bool = False` field to `ToolDef`. Update `load_registry` to
read it from YAML. Mark `list_files` and `find_files` as `readonly: true` in
`tools.yaml`. All other tools default to `False`.

### Acceptance criteria
- `registry["list_files"].readonly is True`
- `registry["find_files"].readonly is True`
- `registry["delete_files"].readonly is False`
- `registry["move_files"].readonly is False`
- `registry["clarify"].readonly is False`
- A YAML tool with no `readonly` key defaults to `False` (no regression).
- `ToolDef` dataclass has `readonly` as the last field with default `False`.

### Verification
Add to `tests/test_registry.py`:
```
test_list_files_is_readonly
test_find_files_is_readonly
test_action_tools_are_not_readonly          (delete_files, move_files)
test_system_tools_are_not_readonly          (clarify, reject, done)
test_readonly_defaults_to_false             (custom yaml without readonly key)
```
Run `pytest tests/test_registry.py` — all existing tests must still pass.

---

## Task 2 — Validation: `output` field + `$var` skip + forward-reference

**Files:** `knaif/planner.py` (`validate_step`, `validate_plan`)

### What to build

**2a — `validate_step`: `output` field syntax check**
- If a step contains `"output"`, value must match `^\$[a-zA-Z_][a-zA-Z0-9_]*$`.
- Dots are NOT allowed in output declarations (they are reference-only).
- Raise `ValueError("Step output must be a $identifier, got: ...")` if malformed.

**2b — `validate_step`: skip semantic checks for `$var` arg values**
- For any string arg starting with `$`, validate only its reference syntax:
  `^\$[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?$`
- Skip: sandbox path resolution (`path`, `src`, `dst`), `_VALID_FILE_TYPES`
  enum check, `recursive` bool check.
- Raise `ValueError` only if the reference syntax itself is malformed.

**2c — `validate_plan`: forward-reference check (multi-step plans only)**
- Walk steps left-to-right, maintaining `assigned: set[str]`.
- For each `$varname` reference in a step's args, assert `varname in assigned`.
- After validating a step, add its `output` variable (if any) to `assigned`.
- Skip this check when `len(plan) == 1` (re-planning loop mode).
- Raise `ValueError(f"Plan step {i}: variable '$foo' used before it is assigned.")`.

### Acceptance criteria
- `validate_step` accepts `{"output": "$market"}` without error.
- `validate_step` rejects `{"output": "$market.exchange"}` (dot in declaration).
- `validate_step` accepts `{"path": "$target"}` without sandbox check.
- `validate_step` accepts `{"file_type": "$kind"}` without enum check.
- `validate_step` rejects `{"path": "$bad ref!"}` (malformed reference syntax).
- `validate_plan` raises on step using `$x` where no earlier step declares `$x`.
- `validate_plan` accepts a valid two-step chained plan.
- `validate_plan` skips forward-ref check for single-step plans.

### Verification
Add to `tests/test_planner.py`:
```
test_validate_output_accepts_identifier
test_validate_output_rejects_dot_notation
test_validate_output_rejects_no_dollar
test_validate_var_ref_skips_path_sandbox_check
test_validate_var_ref_skips_file_type_enum_check
test_validate_var_ref_malformed_syntax_raises
test_validate_forward_ref_missing_raises
test_validate_forward_ref_valid_chain_passes
test_validate_forward_ref_skipped_for_single_step
```
Run `pytest tests/test_planner.py` — all existing tests must still pass.

---

## Task 3 — `resolve_args` function

**Files:** `knaif/planner.py` (new function, exported from module)

### What to build
```python
def resolve_args(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Return a new args dict with $var and $var.field references resolved."""
```
Rules:
- Iterate over `args` items. Build and return a new dict (do not mutate input).
- If a value is a string starting with `$`:
  - Split on the first `.` → `(varname, fieldname_or_None)`.
  - Look up `varname` in `context`. Raise `ValueError` if absent.
  - If no `.`: return the whole context value.
  - If `.fieldname`: `context[varname]` must be a dict; return `context[varname][fieldname]`.
    Raise `ValueError` if not a dict or field absent.
- Non-string values are passed through unchanged.
- Error messages must include the original reference string and step context
  (caller provides step index via message prefix if needed).

### Acceptance criteria
- `resolve_args({"exchange": "$best"}, {"best": "Binance"})` → `{"exchange": "Binance"}`
- `resolve_args({"exchange": "$m.exchange"}, {"m": {"exchange": "Binance", "price": 41000}})` → `{"exchange": "Binance"}`
- `resolve_args({"amount": 2}, {})` → `{"amount": 2}` (non-string passthrough)
- Missing variable: `resolve_args({"x": "$unknown"}, {})` raises `ValueError`.
- Missing field: `resolve_args({"x": "$m.foo"}, {"m": {"bar": 1}})` raises `ValueError`.
- Non-dict with dot: `resolve_args({"x": "$m.foo"}, {"m": "scalar"})` raises `ValueError`.
- Input `args` dict is not mutated.

### Verification
Add to `tests/test_planner.py`:
```
test_resolve_scalar_var
test_resolve_dotted_var
test_resolve_non_string_passthrough
test_resolve_missing_var_raises
test_resolve_dotted_missing_field_raises
test_resolve_dotted_non_dict_raises
test_resolve_does_not_mutate_input
```
Run `pytest tests/test_planner.py` — all existing tests must still pass.

---

## Task 4 — `optimize_plan` function

**Files:** `knaif/planner.py` (new function, exported from module)

**Depends on: Task 1** (registry must have `.readonly` field).

### What to build
```python
def optimize_plan(
    plan: list[dict[str, Any]],
    registry: dict[str, ToolDef],
) -> list[dict[str, Any]]:
    """Return a new plan with redundant readonly steps removed."""
```

Algorithm (right-to-left single pass, spec §4.2):
1. Track `referenced_after: set[str]` — variable names referenced by all steps to the right.
2. Track `has_later_action: bool` — True once a non-readonly step has been seen.
3. A step is marked for removal if: `readonly AND has_later_action AND (no output OR output_varname not in referenced_after)`.
4. Collect `$var` references from each step's args into `referenced_after` (strip `$`, split on first `.`).
5. Return a new list excluding removed steps.

### Acceptance criteria (all five spec §4.1 worked examples):
- `[list_files]` → `[list_files]` (no later action)
- `[list_files, list_files]` → `[list_files, list_files]` (no action step)
- `[find_files (no output), move_files]` → `[move_files]`
- `[find_files (output:$f), move_files]` (move doesn't reference `$f`) → `[move_files]`
- `[find_files (output:$f), buy_btc (args use $f)]` → `[find_files, buy_btc]` (both kept)
- Action steps are NEVER removed regardless of position.
- System tools (`clarify`, `reject`, `done`) — unknown `.readonly` defaults `False`, treated as non-readonly, never removed.

### Verification
Add to `tests/test_planner.py`:
```
test_optimize_single_readonly_kept          (terminal answer case)
test_optimize_two_readonly_no_action_kept
test_optimize_removes_readonly_no_output_before_action
test_optimize_removes_readonly_unreferenced_output_before_action
test_optimize_keeps_readonly_when_output_referenced
test_optimize_never_removes_action_steps
test_optimize_empty_plan_unchanged
```
Run `pytest tests/test_planner.py` — all existing tests must still pass.

---

## CHECKPOINT A — All unit tests green

Before proceeding to Task 5, verify:

```
pytest tests/test_registry.py tests/test_planner.py -v
```

All existing tests must pass. All new tests from Tasks 1–4 must pass.
No changes to `agent.py` yet — `execute_plan` still works as before.

---

## Task 5 — Wire into `execute_plan` + revalidate after resolution

**Files:** `knaif/agent.py`

**Depends on: Tasks 1, 2, 3, 4**

### What to build
Update `execute_plan` to:

1. **After `validate_plan`, before the step loop**: call `optimize_plan(payload["plan"], self.registry)`.
   Assign the result back; operate on the optimized plan.

2. **Inside the step loop**, before dispatching the handler:
   - Initialize `context: dict[str, Any] = {}` once before the loop.
   - Call `resolved_args = resolve_args(step["args"], context)`.
   - Re-apply semantic validators to resolved args (path sandbox, file_type enum).
     Raise `ValueError` with a clear message if resolved value fails validation.

3. **After executing the handler**: if `step.get("output")` is set, store the
   result: `context[step["output"].lstrip("$")] = result`.

4. Use `resolved_args` (not the original `step["args"]`) when calling the handler
   and when recording `results.append({"tool": tool, "args": resolved_args, "result": result})`.

No changes to `run()`, `infer()`, `build_prompt()`, or any other method.

### Acceptance criteria
- **Chained plan**: a two-step plan where step 2 references step 1's output executes
  correctly end-to-end. Step 2 receives the resolved value.
- **Redundant find+move**: `[find_files, move_files]` (same args, no output) → optimizer
  removes `find_files`; `move_files` executes successfully.
- **Single-step plan**: existing `list_files`, `delete_files`, `move_files` plans execute
  unchanged (no regression).
- **Sandbox escape after resolution**: a `$var` that resolves to `"../../../etc"` is
  rejected at execution time.
- **`execute_plan` return value**: `results` contains only the executed (post-optimization)
  steps, with resolved args.

### Verification
Add to `tests/test_agent.py`:
```
test_chained_plan_resolves_variable          (step 2 gets step 1's result field)
test_optimizer_prunes_redundant_find         (find+move → only move runs)
test_single_step_no_regression               (list_files unchanged)
test_sandbox_escape_via_var_raises           (resolved path outside sandbox raises)
```
Run:
```
pytest tests/test_agent.py tests/test_planner.py tests/test_registry.py -v
```
All new and existing tests must pass.

---

## CHECKPOINT B — Full test suite green

```
pytest --tb=short
```

All tests across all files must pass. No regressions anywhere.

---

## Implementation Order Summary

| Order | Task | Files touched | Parallelizable |
|-------|------|---------------|----------------|
| 1 | T1: ToolDef.readonly | registry.py, tools.yaml | Yes (with T2, T3) |
| 2 | T2: Validation changes | planner.py | Yes (with T1, T3) |
| 3 | T3: resolve_args | planner.py | Yes (with T1, T2) |
| 4 | T4: optimize_plan | planner.py | After T1 |
| — | CHECKPOINT A | — | All unit tests green |
| 5 | T5: Wire execute_plan | agent.py | After T1–T4 |
| — | CHECKPOINT B | — | Full suite green |

---

## What is explicitly NOT in scope (Phase 1)

- Changes to `run()` or the re-planning loop.
- Changes to `prompt.py` (system header, examples).
- Any trading, chess, or other domain tool definitions.
- Skill packages (`skills/` directory, `handler_api.py`, `skill.py`).
- `_format_history` changes for `output` variable display.

---

# Phase 2 — Housekeeping: Documentation and Entry Point

## D1 — Doc renames (logical nomenclature)

**Goal:** All files in `docs/` follow a consistent naming convention — uppercase, short, topic-first.

| Current path | New path | Reason |
|---|---|---|
| `docs/TECHNICAL_SPEC.md` | `docs/ARCHITECTURE.md` | The file describes architecture, not a spec |
| `docs/BUSINESS_REQUIREMENTS.md` | `docs/REQUIREMENTS.md` | Shorter; "business" is implied at project level |
| `docs/TOOL_DEFINITION_SCHEMA.md` | `docs/TOOL_SCHEMA.md` | Shorter; "definition" is redundant |
| `SPEC.md` (root) | `docs/VARIABLE_BINDING.md` | Feature spec belongs in `docs/`; name reflects content |
| `requirements.md` (root) | _(delete)_ | Superseded by `docs/REQUIREMENTS.md` |
| `tasks/plan.md` | `docs/PLAN.md` | All project docs centralised under `docs/` |
| `tasks/todo.md` | `docs/TODO.md` | All project docs centralised under `docs/` |
| `tasks/` directory | _(delete empty dir)_ | No longer needed after move |

### Acceptance criteria
- All files exist at new paths.
- Old paths do not exist.
- `tasks/` directory is removed.
- All cross-references within the moved/renamed files are updated to point to new paths.
- `docs/PLAN.md` reference to `../SPEC.md` updated to `VARIABLE_BINDING.md` (same dir).
- `README.md` doc links updated.

---

## D2 — Move FFmpeg skill spec to skill folder

**Goal:** `docs/ffmpeg_ai_workflow_assistant_spec.md` is a skill-specific document; it belongs co-located with the skill it describes.

| Current path | New path |
|---|---|
| `docs/ffmpeg_ai_workflow_assistant_spec.md` | `skills/ffmpeg/SPEC.md` |

### Acceptance criteria
- File exists at `skills/ffmpeg/SPEC.md`.
- Old path does not exist.
- `README.md` link updated (or removed if no longer referenced from top-level docs).

---

## D3 — Update AGENTS.md

**Goal:** `AGENTS.md` currently describes a notebook-first workflow; the project now has a proper library. Rewrite to reflect current conventions.

### What to update
- Replace notebook-first framing with library-first: the source of truth is `knaif/`, not notebooks.
- Document the skill authoring contract: `skill.yaml`, `tools.yaml`, `handlers.py`, `prompt.yaml`.
- Document where per-skill tests live: `skills/<name>/tests/`.
- Remove references to conventions that don't exist (e.g., `docs/EXPERIMENTS.md`).
- Keep notebook guidance but demote it to a subsection.

### Acceptance criteria
- `AGENTS.md` describes `CommandAgent` and `CommandAgent.from_skill()` as the primary entry points.
- Skill authoring steps are documented.
- No references to non-existent files.

---

## D4 — `create_agent()` and `list_skills()` public API

**Files:** `knaif/__init__.py`, `knaif/agent.py` (minor), tests

**Goal:** Add a module-level `create_agent()` factory and `list_skills()` discovery function so callers never need to construct filesystem paths manually.

### What to build

```python
# knaif/__init__.py additions

def list_skills(skills_root: Path | str | None = None) -> list[str]:
    """Return names of all available built-in skills."""

def create_agent(
    skill: str,
    sandbox: Path | str,
    root: Path | str | None = None,
    orchestrator: "InferenceOrchestrator | None" = None,
    confirmer: Any | None = None,
    skills_root: Path | str | None = None,
) -> "CommandAgent":
    """Return a CommandAgent configured for the named built-in skill."""
```

`skills_root` defaults to the `skills/` directory adjacent to the `knaif/` package.
`create_agent` resolves the path, delegates to `CommandAgent.from_skill()`.

Export both in `__all__`.

### Acceptance criteria
- `from knaif import create_agent, list_skills` works.
- `list_skills()` returns `["ffmpeg", "io"]` (or current installed skills, sorted).
- `create_agent("io", sandbox=tmp_path)` returns a `CommandAgent` with the I/O skill wired.
- `create_agent("ffmpeg", sandbox=tmp_path)` returns a `CommandAgent` with the FFmpeg skill wired.
- `create_agent("unknown", ...)` raises `ValueError` with a message listing available skills.
- `list_skills(skills_root=custom_path)` enumerates skills from a custom directory.

### Verification
Add `tests/test_api.py`:
```
test_list_skills_returns_builtin_skills
test_create_agent_io_skill
test_create_agent_ffmpeg_skill
test_create_agent_unknown_raises_with_hint
test_list_skills_custom_root
```

---

## D5 — Add CLAUDE.md

**Goal:** Provide AI coding agents with project context so they don't need to re-discover conventions each session.

### What to write
- Entry points: `CommandAgent`, `CommandAgent.from_skill()`, `create_agent()`.
- Test strategy: `pytest tests/` for core, `pytest skills/` for skill-level tests.
- Adding a skill: directory layout, required files, how to register.
- Key design invariant: model proposes JSON plan; deterministic layer validates and executes.
- Safe conventions: sandbox guard, dry-run flag, `confirmer` callback.

### Acceptance criteria
- `CLAUDE.md` exists at repo root.
- Covers all five points above.
- No stale information (no references to deleted/renamed files).

---

## Phase 2 Implementation Order

| Order | Task | Parallelizable |
|-------|------|----------------|
| 1 | D1: rename docs | Yes (with D2) |
| 1 | D2: move ffmpeg spec | Yes (with D1) |
| 2 | D3: update AGENTS.md | After D1, D2 (needs final paths) |
| 2 | D4: create_agent + list_skills | Independent of D1–D3 |
| 3 | D5: CLAUDE.md | After D1–D4 (needs final paths + API) |
