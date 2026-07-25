# Skill Package Loader — make skills importable Python packages

**Status:** Done · **Created:** 2026-06-26 · **Completed:** 2026-06-26
**Owner:** core · **Ref:** feat/skill-package-loader

> **Kept 2026-07-23** (S7 decision — "cited from source" tier). Kept because
> [`test_skill_package_loader.py`](../../python/core/tests/test_skill_package_loader.py)
> names this file as the source of the five-point loader contract it asserts; deleting it
> would orphan that citation. All five re-verified live against
> [`skill.py`](../../python/core/knaif/skill.py): `_canonical_pkg_key` (path-unique, hashed
> over the *handler* dir), `_legacy_module_key`, the executed `__init__.py`, the submodule
> load, and the pop-the-alias reload contract. The package logic sits in
> `_import_skill_handler_module`, which `_load_oop_skill` calls — this plan predates that
> split and says `_load_oop_skill` throughout.
>
> **Extracted to the shipping docs:** the rationale for keeping `skill_class:` explicit
> rather than auto-discovering it from `__init__.py` (Open Decision #1) →
> [TOOL_SCHEMA.md](../TOOL_SCHEMA.md#the-python-package). It documented the *what* but not
> the why, leaving "why declare the class twice?" answerable only from here.
>
> Paths repointed to the post-restructure layout (`skills/<name>/python/`, `tests/` →
> `python/core/tests/`). The Phase-5 test-migration item is still genuinely open — the
> legacy alias remains load-bearing for ~44 references across the skill test suites.

> **Status note:** All phases landed: the package-aware loader (1–2),
> skills-as-packages (3), the `_deps`/`_engine`/`steps`/`intents`/`_reporting` internal
> convention applied to ffmpeg and documents (4), the `vocab.yaml` Tier-1 shared-data
> extraction (4 follow-up), and docs (5). Phase 0 decisions resolved per the
> recommendations (explicit `skill_class:`, legacy-key alias kept, `__init__.py` a true
> executed entry point). Tier 2/3 of the engine (command-template DSL + imperative
> recipe/geometry port) are **handed off to the dual-runtime plan, Phase 7**. This was a
> **precursor** to `2026-06-17-monorepo-dual-runtime.md`, not a replacement for it.

**Goal:** Turn each skill into a real, importable Python package via a package-aware
loader — banking the safe early piece of the dual-runtime plan without the repo move.

## Problem

Skills are loaded today by `_load_oop_skill()` in `python/core/knaif/skill.py`:

```python
module_path = skill_dir / f"{module_stem}.py"          # handlers.py
mod_key     = f"_skill_oop_{skill_dir.name}_{module_stem}"
spec = importlib.util.spec_from_file_location(mod_key, module_path)
module = importlib.util.module_from_spec(spec)
sys.modules[mod_key] = module
spec.loader.exec_module(module)
```

The module is executed under a synthetic name with **no package context**.
Empirically verified: a sibling import inside a handler file
(`from _recipe import build`) fails with `ModuleNotFoundError`. Consequences:

- Every skill is forced into a single flat `handlers.py`. `ffmpeg/handlers.py` is
  already **2584 lines**; with tens of skills planned, each author hits the same wall.
- No standard entry point — `skill.yaml` must name `skill_class: handlers.FFmpegSkill`
  and the loader hard-codes the `handlers.py` filename.

## Goal

Make each skill directory a real Python package so that:

1. Handlers can be split across modules using ordinary **relative imports**
   (`from ._recipe import build`) with no `sys.path` games and no cross-skill name
   collisions at scale.
2. `__init__.py` is the **standard entry point** that exposes the skill's `Skill`
   subclass.
3. Existing flat `handlers.py` skills keep working unchanged (backward compatible).

### Non-goals (explicitly deferred)

- The monorepo restructure — that stays in the dual-runtime plan, gated on its own
  phases. (It shipped as `skills/<name>/` + `python/core/`, not the `shared/skills/` +
  `apps/knaif-py/` names this plan anticipated.)
- Splitting `ffmpeg/handlers.py` itself. That becomes *possible* after this lands and
  is tracked as a separate follow-up (see Phase 4).
- Any change to the model-facing contract, validation, or execution pipeline.

## Relationship to the dual-runtime (Rust) plan — the load-bearing constraint

The monorepo plan locks: **"YAML skill definitions are the single source of truth,
consumed by both runtimes."** The Rust release runtime binds to the same
`skill.yaml`/`tools.yaml`/`prompt.yaml`/`profiles/` via its own `Step`/`Intent`
*traits*; it cannot import Python classes.

Therefore this change must respect one boundary:

- ✅ Packages + OOP organize the **Python runtime's handler code**.
- ❌ The package/class structure must **not** become the canonical skill definition.
  The cross-language contract stays in the declarative YAML.

Open Decision #3 of the dual-runtime plan recommends Draft B (Python handlers live in
a package, separate from shared YAML data). Making skills packages **now, in place** is
forward-compatible with that: the later move is then a relocation of an
already-package-shaped unit, not a structural conversion. *(It shipped as
`skills/<name>/python/` — the handler package nested inside the bundle rather than a
top-level `knaif_skills.<name>` — but the prediction held: the move was a `git mv`.)*

## Design

### Loading — precise importlib behavior

In `_load_oop_skill()`, when the skill directory contains an `__init__.py`, load it as
a package. The exact, testable semantics (these are the contract, not "roughly"):

1. **Canonical package key is path-unique, not leaf-name-based.** Derive it from the
   *resolved absolute path* of the skill dir (e.g.
   `f"_skill_oop_{skill_dir.resolve().name}_{short_hash(skill_dir.resolve())}"`), not
   from `skill_dir.name` alone. Two skills with the same leaf directory name under
   different parents must get **distinct** package modules in one process. (The
   current `_skill_oop_<dir>` scheme cannot guarantee this — that is the collision the
   review flagged.)
2. Register the skill dir as a package module under that canonical key, with
   `__path__ = [str(skill_dir)]`, in `sys.modules`.
3. **`__init__.py` is actually executed** (`spec.loader.exec_module(pkg)`), not merely
   created as a namespace stub. This is asserted by test (Phase 1), so an
   implementation cannot satisfy the plan by loading only `<pkg>.handlers` and skipping
   the package init. If we decide `__init__.py` is only a re-export marker rather than
   a true entry point, the plan language changes too (see Open Decision #3).
4. Load the entry/handler module as a **submodule** of that package
   (`<canonical_key>.handlers`) so relative imports resolve through normal machinery.
5. When there is **no** `__init__.py`, fall back to the current flat-file behavior
   verbatim (backward compatibility).

### Entry point

`skill.yaml` keeps an explicit handler reference for symmetry with the dual-runtime
`runtimes.python.handlers` metadata, and `__init__.py` re-exports the `Skill`
subclass as the documented entry point:

```python
# skills/ffmpeg/python/__init__.py
from .handlers import FFmpegSkill
__all__ = ["FFmpegSkill"]
```

`skill_class: handlers.FFmpegSkill` continues to resolve (now as a package submodule).
Whether to *also* support auto-discovery from `__init__.py` (dropping `skill_class:`)
is an Open Decision below — default is keep it explicit.

### Backward-compat alias + reload semantics (the migration cost)

**44 references** across the test suite reach into
`sys.modules["_skill_oop_<skill>_handlers"]` to grab module-level helpers
(`_parse_scale`) and patch globals (`_run_ffmpeg`, `FFmpegNotAvailable`). Several
fixtures rely on a specific **reload contract**:

```python
sys.modules.pop("_skill_oop_ffmpeg_handlers", None)   # discard patched state
skill = Skill.load(FFMPEG_SKILL_DIR)                  # expect a FRESH module
handlers_mod = sys.modules["_skill_oop_ffmpeg_handlers"]
```

So the alias is not just a name — it carries reload behavior. The contract the loader
must honor:

- The legacy key `_skill_oop_<dir>_<stem>` is registered as an **alias that is the
  same object** as the canonical submodule (`sys.modules[legacy] is
  sys.modules[canonical].handlers`). Not a copy.
- **Popping the legacy alias before `Skill.load()` forces a genuine reload**: the
  loader must detect the alias is absent and re-`exec_module` the handler submodule
  (refreshing the canonical entry too), so previously monkeypatched globals do **not**
  leak into the new instance. A loader that leaves the canonical submodule cached and
  only rebuilds the alias would silently break this and is incorrect.
- **Limitation, stated honestly:** the alias is inherently leaf-name-based
  (`_skill_oop_ffmpeg_handlers`) and is only safe because in-repo skill leaf names are
  unique. It is a compat shim for existing tests, *not* the collision-safe path — the
  canonical key (design item 1) is. Third-party / same-leaf skills must use the
  canonical key or a public accessor, never the alias.

Tests keep passing untouched under this contract; migrating them to a public accessor
becomes optional cleanup (Phase 5).

## Migration impact / "reevaluation of all skills"

| Surface | Count / location | Action |
|---|---|---|
| Loader | `skill.py:_load_oop_skill` | package path + legacy-key alias |
| Skills to re-validate | ffmpeg, documents, io (io stale) | run each skill's test suite |
| Test refs to synthetic key | 44 across ffmpeg/documents/io/core tests | kept green via alias; no edits required |
| Docs | `AGENTS.md` (HANDLERS/EXPANDERS), `VARIABLE_BINDING.md`, `REQUIREMENTS.md`, `TOOL_SCHEMA.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `SPEC.md` | repo-wide grep + update; `docs/TODO.md` |

## Phases (TDD: RED before GREEN per project discipline)

### Phase 0 — Decisions
- [x] Confirm: keep explicit `skill_class:` vs. auto-discover from `__init__.py`
      (recommendation: keep explicit).
- [x] Confirm: legacy-key alias is the chosen back-compat mechanism (vs. editing 44
      test refs now).

### Phase 1 — Failing tests (RED)
- [x] **Relative import:** fixture skill dir with `__init__.py` + a handler module that
      does `from ._foo import bar`; assert `Skill.load()` resolves it. Confirm it fails
      against the current loader.
- [x] **`__init__.py` is executed:** the fixture's `__init__.py` sets a sentinel
      (e.g. module-level side effect or attribute); assert it ran after `Skill.load()`.
      Guards against an implementation that loads only `<pkg>.handlers`.
- [x] **Alias identity:** assert `sys.modules[legacy_key] is
      sys.modules[canonical_key].handlers` (same object, not a copy).
- [x] **Reload contract:** monkeypatch a global on the loaded handler module, then
      `pop(legacy_key)` + `Skill.load()` again; assert the patched value is gone (fresh
      module). This is the regression guard for the silent-stale-state failure mode.
- [x] **Same-leaf collision:** load two package-style fixture skills with the *same
      leaf dir name* under different parents; assert distinct package modules and that
      the second does not reuse the first's submodule/handlers.

### Phase 2 — Loader change (GREEN)
- [x] Implement package-aware loading in `_load_oop_skill`: path-unique canonical key,
      execute `__init__.py`, submodule load, flat-file fallback, and the alias + reload
      contract above.
- [x] All Phase 1 tests pass; **full suite green with zero edits to existing tests**
      (proves backward compatibility).

### Phase 3 — Make existing skills packages (no behavior change)
- [x] Add `__init__.py` re-exporting the `Skill` subclass to ffmpeg, documents, io.
- [x] Re-run each skill's test suite + full suite.

### Phase 4 — skill-package internal convention (ffmpeg first, then documents)

Promoted from "optional ffmpeg split" to a **shared internal convention** so every
skill is navigated/modified/debugged the same way. Measured coupling is far smaller
than the headline "44 references": the actual *patch* surface is two functions
(`_run_ffprobe` ×6, `_run_ffmpeg` ×1, all in ffmpeg's `stub_ffmpeg` fixture); the rest
are *reads* of pure helpers that keep working via re-export. documents has ~2 refs.

Standard module layout inside each skill package:

```text
skills/<name>/python/
  __init__.py     # re-export the Skill subclass (entry point)
  handlers.py     # the Skill subclass + tool list — thin, stable public surface
  steps.py        # Step classes (deterministic handlers)
  intents.py      # Intent classes (expanders)
  _deps.py        # external tools / optional-import shims — the patch + debug seam
  _engine.py      # pure domain logic (recipe build / page parsing / overlays)
  _reporting.py   # summarizers, preflights, format_results, run_artifact
```

The one structural cost: the patch seam moves from `sys.modules[legacy]._run_ffprobe`
to `_deps.run_ffprobe` (patched by real import path, now possible because skills are
packages). ~7 ffmpeg + ~2 documents test lines repointed; ~30 read sites unchanged via
re-export from `handlers.py`.

ffmpeg:
- [x] `_deps.py` — `run_ffmpeg` / `run_ffprobe` / `FFmpegNotAvailable`; repointed the 7
      patch sites to patch `_deps` by path.
- [x] `_engine.py` — pure recipe engine (probe-norm, profiles, scale/geometry, recipe
      build, command render). handlers re-exports the read-tested helpers.
- [x] `_reporting.py` — summarizers / preflights / `_format_results` / `_run_artifact`.
- [x] `steps.py` / `intents.py` — Step and Intent classes.
- [x] `handlers.py` reduced to the `FFmpegSkill` subclass + tool list + re-exports
      (2584 → 247 lines).
- [x] Full suite green; verified with `create_agent('ffmpeg')` + a dry-run plan.
- [x] (follow-up, Tier 1) `_engine` YAML-able chunks → `vocab.yaml`: the ~9 pure
      lookup tables (encoder/codec maps, platform aliases, scale presets,
      output-suffix map, container/image sets, audio maps, volume words) are now
      declarative data both runtimes can consume (dual-runtime risk #1b mitigation b).
      Tier 2 (`_build_flags` command-template DSL) and Tier 3 (recipe/geometry math)
      are **moved to the dual-runtime plan, Phase 7** (`2026-06-17-monorepo-dual-runtime.md`)
      — they belong with the Rust port, not this plan. documents has only trivial
      format-suffix sets; not worth a vocab file there.

documents (same shape):
- [x] `_deps.py` (the `_require_*` shims + `detect_external_tools`), `_engine.py`,
      `steps.py` / `intents.py`, thin `handlers.py` (1214 → 243 lines). No separate
      `_reporting.py` — documents' formatter/artifact stay on the `Skill` subclass and
      its reporting helpers fold into `_engine`. Full suite green;
      `create_agent('documents')` + dry-run verified.

### Phase 5 — Docs + optional test cleanup
- [x] **Repo-wide grep** for stale handler-dict / entry-point language and update each
      hit, not just a fixed list. Known stragglers beyond the obvious docs:
      `AGENTS.md` (`HANDLERS`/`EXPANDERS` at ~L94/L115, `handlers: handlers.py` at
      ~L103), `docs/VARIABLE_BINDING.md` (~L100/L102), `docs/REQUIREMENTS.md`
      (~L36/L42), plus `docs/TOOL_SCHEMA.md`, `docs/ARCHITECTURE.md`, CLAUDE.md
      "Adding A New Skill", and `skills/ffmpeg/SPEC.md`.
- [x] Update `docs/TODO.md` as phases complete (per CLAUDE.md).
- [ ] (Optional) migrate tests off the raw `sys.modules[...]` key to a public
      accessor; then the legacy alias can be retired.

## Verification

- `uv run pytest python/core/tests/ --tb=short` and `uv run pytest skills/ --tb=short` green
  after Phase 2 **without editing any existing test**.
- `python -c "from knaif import create_agent; create_agent('ffmpeg')"` works.
- A package-style fixture skill with split modules loads and dispatches.

## Risks

1. **Hidden coupling to the synthetic key** beyond the 44 grep hits (e.g. notebooks,
   evalsuite). Mitigated by the alias + running the *entire* suite in Phase 2.
2. **Import-order / caching across multiple skills in one process** (evalsuite loads
   several). Stable per-skill package names avoid collisions; covered by
   `test_evalsuite_runner_execute.py` and `test_notebook_package.py`.
3. **Scope creep into the monorepo move.** Held off by the Non-goals section; this
   plan stops at "skills are packages."

## Open Decisions

1. Explicit `skill_class:` vs. `__init__.py` auto-discovery. **Recommendation:** keep
   `skill_class:` — consistent with dual-runtime `runtimes.python.handlers`.
2. Retire the legacy module-key alias now (edit 44 refs) or later (Phase 5).
   **Recommendation:** later — keep this change low-blast-radius.
3. Is `__init__.py` a true **entry point** (executed, may hold logic) or only a
   **re-export marker**? **Recommendation:** true entry point — it is executed and
   asserted in Phase 1. If we downgrade it to a marker, soften the "standard entry
   point" language in Goal/Entry-point accordingly.
