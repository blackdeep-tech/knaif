# Plan — Cross-skill eval monitoring & model strategy

**Status:** Done · **Created:** 2026-06-25 · **Completed:** 2026-06-26
**Owner:** eval · **Ref:** feat/stale-skill-status

> **Status note:** All 4 phases landed. It had a sibling plan covering the same day's file
> organization (skill-local notebooks + per-skill fixture partitioning); that one was
> restructure history and was deleted 2026-07-23 after its two rationales moved to
> [TOOL_SCHEMA.md](../TOOL_SCHEMA.md) and [SANDBOX.md](../SANDBOX.md). This plan is the
> eval/model axis.
>
> **Kept 2026-07-22** (S7 decision — this was the last plan no tier had ever classified).
> Every claim re-verified live in
> [`evalsuite/cli.py`](../../python/core/knaif/evalsuite/cli.py): `cmd_run_all_skills`,
> `cmd_regression_all_skills`, `cmd_trend`, the required `--save` / `--current-run`, the
> rejection of per-skill options during a sweep, the per-skill `regression_threshold`
> override, and the git-stamped `meta` block in `matrix.json`. `io` carries
> `status: stale` and `list_skills()` filters it by default.
>
> **Extracted to the shipping docs:**
> - the three cross-skill commands (`run --all-skills`, `regression --all-skills`,
>   `trend --skill`) → [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md#cli-usage), whose CLI
>   section had documented twelve per-skill invocations and none of the cross-skill ones;
> - the **scope boundary** — this gate catches shared-model *forgetting*, not cross-skill
>   *retrieval interference*, which cannot exist while each agent sees only its own
>   registry → same section, next to the commands;
> - **the two ways an aggregate gate lies** — the self-compare false green, and reading
>   "not measured" as "passed" → [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md).
>   Both were discovered here and both generalize to any gate that loops over targets.
>
> Paths repointed (`eval_results/` → `evals/`, `src/skills/` → `skills/`) and one
> memory-key reference replaced with the reason it stood for.

**Goal:** Evaluate a single unified model across all skills, detect cross-skill
regression (esp. after fine-tuning on one skill's utterances), and make run history
skill-aware.

## Decision: one shared model, per-skill (skill-scoped) evaluation

The system is **skill-scoped at inference today.** A caller names the skill up front —
`create_agent(skill, ...)` → `CommandAgent.from_skill(...)` ([__init__.py](../../python/core/knaif/__init__.py))
— and `retrieve_tools` ranks **only that one skill's registry**, never a cross-skill pool
([registry.py](../../python/core/knaif/registry.py)). There is no combined multi-skill
registry or router. evalsuite builds exactly one agent from `args.skill`
([cli.py](../../python/core/knaif/evalsuite/cli.py)). So:

- **One *shared* model serves every skill, but one skill-scoped agent at a time.** The user
  runs a single local model; each skill builds its own agent (its own registry, prompt,
  fixtures) around that *same* model weights. Per-skill models are still a non-starter — a
  community skill must work against the *user's existing* model, not ship its own — but the
  reason is economic/deployment, not routing: routing across skills does not exist today.
- **A contributor delivers a corpus, not a model** — `data/eval.jsonl`, `data/train.jsonl`,
  `data/eval_snapshot.json`, all already in-skill. The maintainer's shared model is
  trained/evaluated against it.
- **Fine-tuning is multi-skill**: train one model on the *union* of all skills'
  `train.jsonl`; never one fine-tune per skill (each would forget the others).

The risk this creates: fine-tuning the shared model on skill X's utterances (or adding a
new skill to the training union) can silently regress skill Y — **catastrophic forgetting**
of the shared weights, and prompt-format/budget drift. **This is what the gate targets.**
Note what it does **not** target: cross-skill *retrieval interference* is out of scope here
because cross-skill retrieval does not exist (each agent only sees its own registry). If a
combined multi-skill agent is ever built, measuring routing interference is a separate
effort layered on top — see Scope / non-goals. **The suite cannot catch shared-model
forgetting today.**

## Current state (investigated 2026-06-25)

- Results are **per-skill at file level, mixed at folder level**: a run dir holds
  `{skill}_{backend}_{verifier}.json` files ([cli.py](../../python/core/knaif/evalsuite/cli.py)),
  but dated run folders + `INDEX.md` are one flat list with no per-skill view/trend.
- **No all-skills command.** `run`, `compare`, `regression`, `fixtures regen` each
  *require* `--skill` ([cli.py](../../python/core/knaif/evalsuite/cli.py)). One skill at a time.
- Acceptance bar is **per-skill, committed in-skill**: `skills/<name>/data/eval_snapshot.json`;
  `regression --skill X` diffs the latest run against that skill's snapshot
  ([cli.py](../../python/core/knaif/evalsuite/cli.py)). This separation is correct and stays.
- Gaps: (a) no sweep over all skills for one model build; (b) no aggregate cross-skill
  regression gate; (c) `INDEX.md` is not skill-aware (no per-skill trend column).

## Target model

- **Primary axis = model build; sub-axis = skill.** One logical "model-build run" =
  that build evaluated against **every** registered skill's corpus → a matrix
  (rows = skills, cols = backend × verifier).
- Per-skill snapshots stay in-skill (the contract). `evals/` stays top-level
  cross-skill history but gains a skill axis. No per-skill `evals/` folders.

## Tasks

### Phase 1 — All-skills sweep
- [x] **Define a concrete stale-skill mechanism** — none exists today: `list_skills()` only
      checks for a directory containing `skill.yaml` ([__init__.py](../../python/core/knaif/__init__.py)).
      Add an optional `status: stale` (default `active`) field to `skill.yaml`, document it in
      `docs/TOOL_SCHEMA.md`, mark `io` stale (it is a partial reference skill, not production), and add a
      `list_skills(include_stale=False)` filter. Test discovery: `io` excluded by default,
      included with `include_stale=True`.
- [x] Add `evalsuite run --all-skills` (and accept `--skill all`) that enumerates active
      skills via `list_skills()`, running each skill's existing per-skill path and writing
      `{skill}_{backend}_{verifier}.json` files into one dated run folder.
- [x] **Nail down option semantics** (see [cli.py](../../python/core/knaif/evalsuite/cli.py)):
      the all-skills command **owns the run folder** — `--save <dir>` is required (no implicit
      default); it creates `<dir>/` and writes every skill's scoreboard + `matrix.*` there.
      Per-skill-only options (`--corpus`, `--fixture-dir`, `--snapshot`) are **rejected** with
      `--all-skills` (each skill uses its own in-skill `data/eval.jsonl` and default fixture
      dir); `--backends`/`--verifier`/`--limit` still apply uniformly across the sweep.
- [x] Skip-and-report skills lacking `data/eval.jsonl` rather than failing the whole sweep.
- [x] Emit a single matrix summary (skills × backends × verifier → outcome/knaif/tool/schema —
      these map directly to `outcome_accuracy`, `avg_knaif_score`, `intent_metrics.tool_accuracy`,
      `intent_metrics.schema_validity`) to stdout and to `matrix.json`/`matrix.md` in the run folder.
- [x] Tests: sweep covers all active skills; `io` (stale) excluded; missing-corpus skill is
      skipped, not fatal; output filenames match the existing per-skill convention.

### Phase 2 — Aggregate cross-skill regression gate
- [x] Add `evalsuite regression --all-skills --current-run <dir>`. **It MUST read the current
      scoreboards** — `cmd_regression` defaults `current = baseline` when `--current` is omitted
      ([cli.py](../../python/core/knaif/evalsuite/cli.py)), so a naive loop would compare each
      snapshot to itself and **always pass (false green)**. For every active skill, load
      `<dir>/{skill}_{backend}_{verifier}.json` (the Phase-1 sweep output) as `current` and diff
      it against that skill's in-skill `data/eval_snapshot.json`
      ([cli.py](../../python/core/knaif/evalsuite/cli.py)).
- [x] Non-zero exit if **any** skill regresses beyond its threshold; print a per-skill
      pass/fail table at the **metric level** (`outcome_accuracy`, `avg_knaif_score`,
      `intent_metrics.*`). **Row-level "offending utterance" naming is not available from
      snapshot diffs** — snapshots store aggregate metrics only, not rows
      ([snapshot.py](../../python/core/knaif/evalsuite/snapshot.py)). _(Optional worst-utterance
      reader from the full scoreboard JSON is deferred — the metric gate is implemented.)_
- [x] Honor per-skill thresholds: optional `regression_threshold` field in a skill's
      `eval_snapshot.json` overrides the global `--threshold` (default 0.02) for that skill.
- [x] **Verifier-aware comparison** (discovered during impl): snapshots are heterogeneous
      (`ffmpeg` = `cheap`, `documents` = `success`). The gate compares each skill only against
      a current scoreboard of its snapshot's verifier. A snapshot verifier the sweep never ran
      is **skipped with a reason** (not a false fail); a snapshot verifier the sweep *did* run
      for other skills but is missing this skill's file is a **coverage gap → hard fail**.
- [x] Tests: injected metric regression in one skill's current scoreboard fails the aggregate;
      a clean sweep exits 0 (and is **not** a self-compare no-op); a skill with no snapshot is
      reported as "no baseline", not a silent pass; verifier-mismatch skip vs same-verifier
      coverage-gap fail are both covered.

### Phase 3 — Skill-aware history
- [x] Extend `evals/INDEX.md` (and the doc text — the stale `ffmpeg_<backend>` naming
      convention) so each model-build run is documented as `<skill>_<backend>_<verifier>.json`
      plus the all-skills `matrix.{json,md}`; the per-skill breakdown lives in `matrix.json`
      and is queryable via `trend`.
- [x] Define the model-build identity recorded per run: `matrix.json` now embeds a `meta`
      block (`label`, `date`, `git_sha`, `git_branch`, `backends`) plus per-skill corpus size
      (`total` per cell), so "documents across the last N model builds" is reconstructable
      from the run folders alone. `--label` overrides the default (save-folder name).
- [x] Add `evalsuite trend --skill X [--last N] [--backend B]` that scans `evals/` for
      `matrix.json` files covering the skill and prints its metric history across builds,
      date-sorted. (Reads the embedded `meta`, not the freeform INDEX table.)

### Phase 4 — Fine-tuning workflow alignment
- [x] Documented the loop in `docs/TRAINING_DATA_GENERATION.md` → "Multi-skill fine-tuning
      loop": union of all `train.jsonl` → train one shared model → `run --all-skills` →
      `regression --all-skills`; block promotion on any per-skill regression.
- [x] Added a "new skill" checklist item in `docs/TOOL_SCHEMA.md` → "Wiring a new skill into
      the shared-model eval": ship `eval.jsonl` + `train.jsonl` + `eval_snapshot.json`;
      confirm the next model build's matrix includes it (`trend --skill <name>`).

## Scope / non-goals

- Does **not** change the per-skill snapshot location or format (in-skill, committed).
- Does **not** introduce per-skill models or per-skill model storage — a single shared
  model evaluated through skill-scoped agents is the deliberate decision above.
- Does **not** build a combined multi-skill registry/router. Cross-skill *retrieval
  interference* is therefore explicitly out of scope; the gate measures shared-model
  forgetting only. A future global-agent plan would layer routing-interference eval on top.
- Fixture partitioning (`sandbox/fixtures/<skill>/`) is handled in the sibling plan's
  Phase 1b, not here.
- Coordinate with the `evals/` `.gitignore` fix (sibling plan Phase 3) so
  `INDEX.md` / `score.json` / `matrix.*` are actually tracked.

## Risks / notes

- An all-skills success-verifier sweep is expensive (real ffmpeg + documents execution).
  Keep `cheap` (routing-only) as the default fast gate; reserve `success` for promotion.
- Stale/partial skills (`io`) must be excluded from both sweep and gate to avoid false
  reds — drive exclusion from the `status: stale` field in `skill.yaml` (Phase 1), not a
  hardcoded skill name in core.
- Phases 1 → 2 → 3 are ordered; Phase 4 is docs and can land alongside.
