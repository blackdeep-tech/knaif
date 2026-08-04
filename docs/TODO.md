# Todo — Phase Log

> The numbered phases below are a mostly-historical record of shipped work; the **live
> backlog** is the "Open / Next" section at the bottom. A handful of phases still carry
> deferred items — those are cross-linked from Open / Next, so that section remains the
> single place to look for what is actually outstanding. Numbering has gaps where phases
> were retired alongside their plans. Plan index: [plans/README.md](plans/README.md).

## Phase 1 — Variable Binding and Plan Optimizer

## T1 — `ToolDef.readonly` flag
- [x] Add `readonly: bool = False` to `ToolDef` dataclass in `python/core/knaif/registry.py`
- [x] Load `readonly` from YAML in `load_registry`
- [x] Mark `list_files` and `find_files` as `readonly: true` in `skills/io/tools.yaml`
- [x] Add tests: `test_list_files_is_readonly`, `test_find_files_is_readonly`, `test_action_tools_are_not_readonly`, `test_system_tools_are_not_readonly`, `test_readonly_defaults_to_false`

## T2 — Validation changes
- [x] `validate_step`: add `output` field syntax check (`^\$[a-zA-Z_][a-zA-Z0-9_]*$`, no dots)
- [x] `validate_step`: skip semantic checks (path/file_type/recursive/pattern) for args starting with `$`; validate only reference syntax
- [x] `validate_plan`: add forward-reference check (left-to-right, `assigned` set, skip for single-step plans)
- [x] Add tests: output accepts identifier, output rejects dots, var-ref skips path check, var-ref skips file_type check, malformed syntax raises, forward-ref missing raises, valid chain passes, single-step skipped

## T3 — `resolve_args` function
- [x] Implement `resolve_args(args, context) -> dict` in `python/core/knaif/planner.py`
- [x] Handle scalar `$var` lookup
- [x] Handle dotted `$var.field` extraction (dict guard + field guard)
- [x] Pass non-string values through unchanged
- [x] Add tests: scalar, dotted, non-string passthrough, missing var raises, missing field raises, non-dict raises, no mutation

## T4 — `optimize_plan` function  _(after T1)_
- [x] Implement `optimize_plan(plan, registry) -> list` in `python/core/knaif/planner.py`
- [x] Right-to-left pass: `referenced_after`, `has_later_action`, `to_remove`
- [x] Add tests: single readonly kept, two readonly no-action kept, removes unreferenced readonly+action, removes unlinked output+action, keeps referenced output, never removes action, empty plan

## CHECKPOINT A
- [x] `pytest tests/test_registry.py tests/test_planner.py -v` — 77 passed

## T5 — Wire into `execute_plan`  _(after T1–T4)_
- [x] Call `optimize_plan` after `validate_plan`, reassign plan
- [x] Initialize `context = {}` before step loop
- [x] Call `resolve_args(step["args"], context)` before each dispatch
- [x] Re-validate resolved path/file_type args against sandbox/enum
- [x] Store step result in `context` when `step.get("output")` is set
- [x] Use resolved args in handler call and in results list
- [x] Add tests: chained plan resolves variable, optimizer prunes redundant find, single-step no regression, sandbox escape via var raises

## CHECKPOINT B
- [x] `pytest --tb=short` — 158 passed, 0 failures

---

# Phase 2 — Housekeeping: Documentation and Entry Point

## D1 — Doc renames and consolidation
- [x] Rename `docs/TECHNICAL_SPEC.md` → `docs/ARCHITECTURE.md`
- [x] Rename `docs/BUSINESS_REQUIREMENTS.md` → `docs/REQUIREMENTS.md`
- [x] Rename `docs/TOOL_DEFINITION_SCHEMA.md` → `docs/TOOL_SCHEMA.md`
- [x] Move `SPEC.md` (root) → `docs/VARIABLE_BINDING.md`
- [x] Delete `requirements.md` (root) — superseded by `docs/REQUIREMENTS.md`
- [x] Move `tasks/plan.md` → `docs/PLAN.md`
- [x] Move `tasks/todo.md` → `docs/TODO.md`
- [x] Delete empty `tasks/` directory
- [x] Update cross-references in all moved/renamed files
- [x] Update `docs/PLAN.md` self-reference to `SPEC.md` → `VARIABLE_BINDING.md` (same dir)
- [x] Update `README.md` doc links

## D2 — Move FFmpeg spec
- [x] Move `docs/ffmpeg_ai_workflow_assistant_spec.md` → `skills/ffmpeg/SPEC.md`
- [x] Update or remove `README.md` link

## D3 — Update AGENTS.md
- [x] Replace notebook-first framing with library-first (`python/core/knaif/` is source of truth)
- [x] Document skill authoring contract (`skill.yaml`, `tools.yaml`, `handlers.py`, `prompt.yaml`)
- [x] Document per-skill test location (`skills/<name>/tests/`)
- [x] Remove references to non-existent files (e.g., `docs/EXPERIMENTS.md`)
- [x] Demote notebook guidance to a subsection

## D4 — `create_agent()` and `list_skills()` public API
- [x] Implement `list_skills(skills_root=None) -> list[str]` in `python/core/knaif/__init__.py`
- [x] Implement `create_agent(skill, sandbox, ..., skills_root=None) -> CommandAgent` in `python/core/knaif/__init__.py`
- [x] Export both in `__all__`
- [x] Add `python/core/tests/test_api.py`: `test_list_skills_returns_builtin_skills`, `test_create_agent_io_skill`, `test_create_agent_ffmpeg_skill`, `test_create_agent_unknown_raises_with_hint`, `test_list_skills_custom_root`

## D5 — Add CLAUDE.md
- [x] Document entry points: `CommandAgent`, `CommandAgent.from_skill()`, `create_agent()`
- [x] Document test strategy: `pytest tests/` (core) + `pytest skills/` (skill-level)
- [x] Document skill authoring steps
- [x] Document key design invariant (model → JSON plan, deterministic layer executes)
- [x] Document safety conventions (sandbox guard, dry-run, confirmer)

---

# Phase 3 — Eval Suite (`python/core/knaif/evalsuite/`)

## E1 — Framework skeleton
- [x] `corpus.py` — `CorpusRow` dataclass + `load_corpus` / `save_corpus`
- [x] `runner.py` — `AgentOutput` + `run_corpus` (infer + execute_plan + artifact extraction)
- [x] `scoring.py` — `VerifyResult`, `Verifier` protocol, `score_corpus` + intent metrics
- [x] `report.py` — `print_scoreboard`, `save_scoreboard_json`, `print_baseline_row`
- [x] `snapshot.py` — `save_snapshot`, `load_snapshot`, `diff_snapshots`
- [x] `cli.py` — `run`, `compare`, `regression`, `show-baseline` subcommands
- [x] `python/core/tests/test_evalsuite_corpus.py` — round-trip + schema validation
- [x] `python/core/tests/test_evalsuite_runner.py` — mock agent + artifact extraction
- [x] `python/core/tests/test_evalsuite_scoring.py` — verifier dispatch + aggregation

## E2 — FFmpeg eval plugin
- [x] `skills/ffmpeg/eval/verifiers.py` — `cheap` (command parse) + `honest` (ffprobe)
- [x] `skills/ffmpeg/eval/fixtures.py` — lavfi-based fixture generation
- [x] `skills/ffmpeg/eval/README.md` — eval guide (phases A–G), success_criteria schema, bootstrap and extend playbooks
- [x] `skills/ffmpeg/python/tests/test_eval_verifiers.py` — cheap verifier on known commands

## E3 — Corpus seed and config
- [x] `skills/ffmpeg/data/eval_v1.jsonl` — 15-row seed corpus (extend to ~300 via eval/README.md bulk generation playbook)
- [x] `eval_backends.yaml` — backend config template (ollama + llama.cpp examples)
- [x] `docs/EVAL_FRAMEWORK.md` — verifier contract, corpus schema, CLI usage
- [x] Run bulk generation playbook (eval/README.md Phase G) to grow corpus to ~300 rows and commit — **Done:** corpus reached 314 rows (target ~300); growth was carried out in the batches recorded under Phase 7 / P4.

## CHECKPOINT E
- [x] `pytest python/core/tests/test_evalsuite_*.py skills/ffmpeg/python/tests/test_eval_verifiers_cheap_honest.py -v` — **Verified 2026-07-22: 246 passed.**
- [x] `uv run -m knaif.evalsuite run --skill ffmpeg --verifier cheap --limit 15 --backends mock` — **Verified 2026-07-22:** 15 rows, schema validity 100%, tool accuracy 93.3%, 104 ms/row. Needs the explicit `--backends mock`; without it the run defaults to `qwen3-4b` and needs a GGUF from the gitignored `models/`.

---

# Documentation Refresh

- [x] Convert `docs/TOOL_SCHEMA.md` into the skill authoring guide.
- [x] Update `README.md` to library-first usage.
- [x] Update `docs/ARCHITECTURE.md` to current skill-based architecture.
- [x] Update `docs/REQUIREMENTS.md` to current built-in skill scope.
- [x] Reconcile `docs/VARIABLE_BINDING.md` with implemented skill packages.
- [x] Reconcile `skills/ffmpeg/SPEC.md` with the plan-envelope model contract.
- [x] Add documentation routing and plan/todo rules to `AGENTS.md`.
- [x] Mirror documentation routing in `CLAUDE.md`.

---

# Phase 5 — FFmpeg prompt simplification for small models

Plan: `docs/plans/2026-05-27-ffmpeg-prompt-small-model.md`

## Correctness fixes (must land before eval baselines)

- [x] Task 1 — Prompt audit tests + measured baseline sizes (`python/core/tests/test_prompt_audit.py`, 11 tests)
- [x] Task 2 — Fix invalid `$logs.files[0]` bracket-index syntax in `skills/io/prompt.yaml`
- [x] Task 3 — Apply retrieval in `run_corpus` (correctness fix #1: eval now measures retrieved prompt)
- [x] Task 4 — Expose parse-failure metadata (`agent.last_parse_error`, `outcome="parse_error"` in runner)
- [x] Task 5 — Filter examples by retrieved tools (47% rendered prompt reduction, **accuracy-neutral** per eval results)

## Corpus + backend prep

- [x] Task 6 — Expand eval corpus: 19 → 70 cases (CRF, quality words, multilingual, traps, rejects)
- [x] Task 7 — Enable small-model backends in `eval_backends.yaml`

## Eval runs

- [x] Task 8 — Baseline + A/B evals across 10 backend variants; results combined into `docs/plans/2026-05-27-ffmpeg-prompt-small-model.md`

## Conditional follow-ups — data did not justify them

- [x] ~~Task 9~~ — `NORMALIZERS` for CRF/quality words. **Skipped (closed):** 4B handles these without normalization; 1.7B failures aren't concentrated on a specific mapping.
- [x] ~~Task 10~~ — Header rewrite. **Skipped (closed):** 4B clears 89–94% on the current header.
- [x] Task 11 — Multilingual keyword aliases in `tools.yaml` (100% on 12 multilingual rows at 4B)
- [x] ~~Task 12~~ — Embedding retriever. **Skipped (closed):** aliases already cover ES/DE/FR/RU at zero runtime cost.
- [x] Task 13 — `docs/EVAL_FRAMEWORK.md` updated: retrieval default, `--no-retrieval`, `parse_error` bucket, multilingual normalization, time-to-artifact telemetry, llama.cpp backend config.
- [x] Task 14 — Full regression check (631 tests pass)

## Bottom line

Original ask was "simplify the prompt for small models". Data shows the prompt
did not need simplifying at 4B and that prompt-side changes do not move the
needle at 1.7B. Branch ships the eval-correctness fixes, multilingual support,
parse-error visibility, and time-to-artifact telemetry. The example-filtering
code is kept as infrastructure.

---

# Phase 7 — Eval final-quality, manual verification, corpus growth

Decisions (2026-06-02): 7 languages (EN/DE/ES/BG/ZH + keep FR/RU); absolute `success_criteria`
is the primary quality grade (`output_diff` secondary); complex rows graded on final output only.

## P1 — Schema reconciliation & docs truth-up _(foundational)_
- [x] Add `success_criteria: dict` to `CorpusRow` in `python/core/knaif/evalsuite/corpus.py` (load in `from_dict`, mirror `tolerances`)
- [x] Rewrite real corpus schema in `skills/ffmpeg/eval/README.md` (Phase G) and `docs/EVAL_FRAMEWORK.md`; delete `expected_plan`/`baseline_freeform_command` fiction
- [x] Document row template + four shapes (standard/complex/bad/edge) + language-tag convention
- [x] Tests: `python/core/tests/test_evalsuite_corpus.py` round-trip with `success_criteria`; legacy row without it still loads

## P2 — `success_criteria` verifier _(realizes R7)_
- [x] Add `SUCCESS_CRITERIA_FIELDS` + `success(output, criteria, sandbox)` in `skills/ffmpeg/eval/verifiers.py` (reuse `_ffprobe`/`_codec_matches`/`_pct_diff`)
- [x] Register `"success"` in `VERIFIERS`; add `--verifier success` to `run`/`compare` in `cli.py`
- [x] Upgrade `cheap` to grade command-text criteria (codec/filter/flag tokens) when present
- [x] Tests: `skills/ffmpeg/python/tests/test_eval_verifiers_success.py` — success pass/fail on codec+resolution; cheap command-text pass/fail

## P3 — Manual-verification hardening + SOP
- [x] URL-encode media `src` + triage sort in `report.py`/`reviewer.py`
- [x] Add `docs/EVAL_VERIFICATION_SOP.md` (exact local + premium verification process)

## P4 — Corpus growth to ~300 rows _(batched for owner review)_
- [x] Add Bulgarian + Chinese keyword aliases to `skills/ffmpeg/tools.yaml`
- [x] Batch 1 (70→115): thin standard tools (`extract_frame`, `adjust_speed`, `batch_convert`, etc.)
- [x] Batch 2 (115→140): complex multi-step rows (graded on final output)
- [x] Batch 3 (140→180): bad/reject + edge/clarify rows
- [x] Batches 4+5 (180→235): language variants (BG/ZH primary, FR/RU maintained) + fill
- [x] Seed baselines (`evalsuite seed-baselines`) + human baseline authoring (Phase B) — **Done:** corpus at 314 rows, 214 with a validated baseline (remaining nulls are clarify/reject rows, which stay null by design).

## P5 — Symmetric premium-agent contract + speed _(realizes R6/R8)_
- [x] Extend `docs/BIG_LLM_HANDOFF.md`: per-entry `meta.json` with `elapsed_ms` (+ optional notes)
- [x] `cli.py:cmd_score_external` reads `meta.json`, grades with `success`, emits local-shaped scoreboard (outcome, latency, by_tag)
- [x] Tests: `score-external` scoreboard has latency populated + same top-level keys as a local `run --save` scoreboard

## CHECKPOINT 7
- [x] `.\.venv\Scripts\python.exe -m pytest --tb=short` — 743 passed **Done 2026-06-02**
- [x] `python -m knaif.evalsuite regression --skill ffmpeg` — no regressions **Done 2026-06-02**
- [x] Local + premium arms render side by side in `report.html` — **Done 2026-07-02** via `evals/runs/2026-07-02_big-llm-comparison_success/`: `discover_arms()` renders `claude-code_claude-opus-4-8` and `qwen3-4b-sft-v3-flat-q4` as parallel columns with pass rate, avg score, and time-to-artifact (mean/p50/p95), plus a per-tag breakdown. Local arm's scoreboard was copied from the SFT-v3 run rather than re-executed in place; the render path is the same either way. The run root's `report.md` and the premium arm's `score.json` are the tracked artifacts; the local `COMPARISON.md` write-up is not (the `evals/**` allowlist), so its two durable findings were moved into `docs/MODELS.md` §4.4 (the gap is routing, not ffmpeg) and `docs/BIG_LLM_HANDOFF.md` (the cost model and why the arms are not token-symmetric).

---

# Phase 8 — FFmpeg skill + corpus + clarify hardening (2026-06-03 → 06-09)

Each item is fully tracked in its own plan; this is the roll-up.

- [x] FFmpeg skill extensions — `scale` on create_thumbnail/extract_frame, `rotate_video`,
      `adjust_volume`, concat stream-normalization. All shipped (`skills/ffmpeg/tools.yaml` +
      `_engine.py` + corpus `ffmpeg_236–244`); plan file deleted 2026-07-23.
- [x] Multi-output corpus support — schema + `grade_outputs` documented in `docs/EVAL_FRAMEWORK.md`
- [x] Deterministic clarify (T1–T17) — `docs/plans/2026-06-06-deterministic-clarify.md`
- [x] Eval quality fixes (T1–T3) — `docs/plans/2026-06-06-eval-quality-fixes.md`
- [x] Local3 / low-score eval iterations — `docs/plans/2026-06-08-local3-outcome-fixes.md`
      (the companion low-score plan was absorbed into it and retired). Tasks 1–6 landed;
      the deferred **Corpus Trim** is still open and now unblocked (see Open / Next).
- [x] Descriptor / mixed-intent analyzer (read-only, T1–T6) — `docs/plans/2026-06-09-descriptor-mixed-intent-analyzer.md`
- [x] NL clarify gate (T1–T7, shipped PR #14) — `docs/plans/2026-06-09-nl-clarify-gate.md`
- [ ] **Finish the injection-ON plumbing** (T4 shipped the parts, not the connection).
      `skill.pipeline_inject` is parsed and stored but read by nothing, and
      `injectors.resolve_injected_files()` has no caller outside its tests — so a host
      cannot turn injection on by declaring `pipeline: inject:`; it must pass
      `injected_files=` to `execute_plan()` by hand. Blocked on a design call, not on
      effort: settle it against `docs/plans/2026-06-09-context-injection.md` (T0), which
      proposes a second mechanism for the same job. Reuse `injectors.py` or retire it —
      do not add a third path.
- [x] ~~complex-two-step-intents~~ — retired 2026-06-27, never implemented (see plan).
- [ ] **Output verification** — `docs/plans/2026-06-09-verify-output.md` is a **ready draft**,
      not closed: reviewed and endorsed 2026-06-21, queued behind the OSS-readiness P0s.
      The gap it targets is **still live** (re-verified 2026-07-22): `VerifyOutputsStep`
      returns `verified: True` unconditionally, `verify_preview` is called with no
      `expected` so its comparison is a no-op, `_build_one_recipe` emits no `expected`,
      and `_step_failed` checks only `returncode`. So a wrong-but-exit-0 artifact still
      reports success. Build when the P0 backlog clears.
- [ ] **Context injection** — `docs/plans/2026-06-09-context-injection.md` is **Planned (parked)**,
      not closed: deliberately deferred to the ffmpeg-UI work, both dependencies shipped, design
      refreshed 2026-07-22. Listed here so the live backlog and the plan agree.

---

# Phase 9 — OOP skill architecture (2026-06-16, shipped PR #15)

Plan: `docs/plans/2026-06-16-oop-skill-architecture.md`

- [x] Phases 0–5 complete: `Step`/`Intent`/`Skill` interfaces, `skill_class:` loader (legacy
  `HANDLERS` path deleted), `knaif.steps` shared library, io + ffmpeg migrated, core control
  tools de-duplicated, `core_tools.yaml` as the single metadata parse path, docs rewritten.
- [~] **Remainder (io rebuild):** `executor.py` still holds the legacy bare-`CommandAgent` io
  implementation, now redundant with the OOP io skill. Removing it = its own effort.

---

# Phase 10 — FFmpeg geometry + thumbnail merge (2026-06-17, branch feature/more-tools-ffmpeg)

Plan: `docs/plans/2026-06-17-ffmpeg-geometry-and-thumbnail-merge.md`

- [x] Phase 1 (Tasks 1.1–1.9 + Checkpoint A): `resize_video` gains `fit`/`aspect`; `_geometry_vf` helper; cover-crop default for both-dims; crop/reframe summarizer phrases; square-crop example in prompt; 3 new corpus rows.
- [x] Phase 2 (Tasks 2.1–2.4 + Checkpoint B): `extract_frame` removed; keywords + corpus rows migrated to `create_thumbnail`; net −1 model-visible tool; prompt flat-or-shrinking (13,175 chars, baseline 13,011); SPEC.md Public Tools list corrected (13 media intent tools).
- [x] Phase 3 (Tasks 3.1–3.6): baselines, eval, snapshot — **completed 2026-06-17/18**, not
  deferred (corrected 2026-07-22). Both post-geometry runs are logged in `evals/INDEX.md`
  (cheap: qwen 0.861/0.811, gemma 0.787/0.824; success: qwen 0.860/knaif 0.972, gemma
  0.819/knaif 0.931), the per-row diff against the pre-geometry baseline showed no real
  regression, and the snapshot was re-locked. See Checkpoint C in the plan.

---

# Phase 11 — Skill-local notebooks and fixture partitioning (2026-06-25)

Plan deleted 2026-07-23 (restructure history); rationale now in `docs/TOOL_SCHEMA.md`
(notebook helpers stay out of the wheel) and `docs/SANDBOX.md` (why fixtures are
partitioned per skill).

- [x] Move skill-specific notebooks under `skills/<name>/notebooks/`.
- [x] Consolidate shared notebook helpers under `notebooks/shared/`.
- [x] Move documents-only corpus review helper under
  `skills/documents/notebooks/helpers/`.
- [x] Scope generated eval fixtures by skill under `sandbox/fixtures/<skill>/`.
- [x] Update active docs, pyright paths, and notebook imports for the new layout.
- [x] Finish the deeper shared tester runner / renderer extraction; current implementation
  provides a shared `TesterWidget` wrapper plus `NotebookRunEngine` with the documents
  renderer skill-local.
---

# Phase 12 — Skill package loader (2026-06-26, branch feat/skill-package-loader)

Plan: `docs/plans/2026-06-26-skill-package-loader.md`

- [x] Phase 1–2: package-aware `_load_oop_skill` — path-unique canonical key, executed
  `__init__.py` entry point, handler submodule for relative imports, flat-file fallback,
  and the legacy-alias + reload contract. New tests in `python/core/tests/test_skill_package_loader.py`;
  full suite green with zero edits to existing tests (backward compatible).
- [x] Phase 3: `__init__.py` added to ffmpeg, documents, io (now real packages). Incidentally
  fixed a latent pytest basename collision between `python/core/tests/test_skill.py` and
  `skills/io/python/tests/test_skill.py`.
- [x] Phase 5: docs updated (TOOL_SCHEMA, CLAUDE, AGENTS, ARCHITECTURE, REQUIREMENTS,
  VARIABLE_BINDING, ffmpeg SPEC) — package layout + stale `HANDLERS`/`EXPANDERS` language.
- [x] Phase 4: skill-package internal convention (`_deps` / `_engine` / `steps` / `intents`
  / `_reporting`). ffmpeg `handlers.py` 2584→247 lines, documents 1214→243; the only test
  edits were repointing the monkeypatch seam to the `_deps`/`_engine` module objects. Full
  suite green. Follow-up: tag ffmpeg `_engine` chunks YAML-able vs. imperative (risk #1b).
- [ ] Phase 5 (optional, still open): migrate skill tests off the raw
  `sys.modules["_skill_oop_<skill>_handlers"]` key onto the `_deps`/`_engine` module seam,
  then retire the legacy alias in `skill.py`. ~44 references across the ffmpeg/documents
  test suites keep the alias — and its pop-forces-reload contract — load-bearing today.

---

# Open / Next (no dedicated plan yet)

This **Open / Next** section is the live backlog (originally distilled from the
2026-06-10 project audit, which is no longer kept as a separate file). Highest-value first:

- [x] **1.1.0 release — verification COMPLETE 2026-08-02. Every gate below has now been re-run
  against the rebuilt artifacts; what remains is publishing (tag the current tip of `main`, publish
  the draft, `twine upload python/core/dist/*`), not verifying.** Naming a commit here would be
  self-invalidating — merging the commit that names it moves the tip. Everything after the build
  commit is docs-only, so any tip that still carries the rebuilt artifacts' source reproduces them. The original concern, kept because it is the
  reason this list exists: the Windows artifacts were built and fully verified while the release was
  still numbered 1.0.2: `smoke.sh`, the PE import check, and the first-ever successful run of the upgrade path
  under a throwaway `AppId` (setup refused while the CLI held the mutex, no folder-exists warning,
  the directory was reused, `DisplayVersion` advanced, and a planted sentinel DLL in `{app}\bin` was
  gone afterwards). It is tempting to treat that as banked. It is not:
  - [x] **Re-run the upgrade path — DONE 2026-08-02**, against the rebuilt 1.1.0 installer. Two
    builds from one staged tree (`just installer-test /DAppVersion=1.0.99` then `=1.1.0`), so the
    throwaway `AppId`, `/DTestInstall` directory and separate output dir all held. All four
    assertions passed: setup refused while the CLI held `knaif-cli-running`, no folder-exists
    warning after closing it, `DisplayVersion` advanced to 1.1.0 on a single Add/Remove row, and a
    planted sentinel DLL in `{app}\bin` was gone afterwards (`[InstallDelete]` really ran). Torn
    down cleanly — no `knaif-testbuild` tree and no leftover `knaif*` uninstall key.
  - [x] **Windows clean room — DONE 2026-08-02.** The published zip, unpacked and run in Windows
    Sandbox (`Containers-DisposableClientVM`) with the tree mapped read-only, a writable results
    folder and a `LogonCommand` — a machine with no developer tooling and no VC++ redistributable
    beyond what Windows ships. `knaif --version` and `knaif skills list` both exit **0**, and both
    skills resolve exe-relative. Not `-1073741515` (`0xC0000135`), which is the missing-runtime
    failure this gate exists to catch and which every 1.0.x artifact would have produced.
    Artifact hygiene re-checked on the same unpacked tree: 50 files, **0** violations — 18 `.dll`,
    1 `.exe`, 3 `.txt`, 26 `.yaml`, `LICENSE`, `NOTICE`; no `.gguf`/`.py`/`.ipynb`/`.jsonl` and no
    `eval`/`sandbox`/`notebook` paths.
  - [x] **Linux clean room — DONE 2026-08-02**, against the rebuilt artifacts. `smoke.sh` passes on
    both the tarball and the AppImage inside a container with no checkout; `check_elf_deps.py`
    reports every `DT_NEEDED` staged or base-system across 21 binaries; and `check-floor.sh` proves
    the floor **in both directions** for each artifact — runs on `ubuntu:22.04`, refused on
    `ubuntu:20.04` for the documented reason (`CXXABI_1.3.13`, `GLIBCXX_3.4.29`, `GLIBC_2.32/33/34`
    absent). Measured floor — `GLIBC_2.34`, `GLIBCXX_3.4.30`, `CXXABI_1.3.13` — matches the claim.
  - `smoke.sh` and `check_pe_imports.py` re-run automatically as required packaging steps, so those
    are free.

  **All four artifacts were REBUILT on 2026-08-02** from `345797d`, because the spinner fix landed
  after the previous build and the staged binary still carried the old string. Windows `smoke.sh`
  8/8 and `check_pe_imports.py` (19 binaries) pass; both binaries were confirmed to carry the new
  string and not the old. `SHA256SUMS` was regenerated over the four published files and the draft's
  assets re-uploaded and verified byte-for-byte against local. **The CUDA payload assets were
  deliberately NOT rebuilt** — they carry no `main.rs` code, their per-file sha256s are pinned in the
  published manifest, and a rebuild would risk non-reproducible bytes for no gain. The backend
  install rehearsal passes 10/10 against them.

  **What that session did buy, and it is not nothing:** `AppMutex` and `hold_app_mutex` were proven
  to agree, `[InstallDelete]` was proven to actually execute, and the CRT staging was proven to find
  the redist matching the compiler. All three were *unverified* before — RELEASE.md called two of
  them out as never having run. A failure there next time is a regression, not a first discovery.

- [ ] **Inference latency: daemon + prompt-prefix KV reuse (1.2.0, NOT 1.1.0).** Measured
  2026-08-01 on the shipped Linux CUDA payload; full budget in
  [PERFORMANCE.md §6](PERFORMANCE.md). A CUDA `run` is ~5.2 s wall of which only ~1.6 s is compute:
  ~1.9 s CUDA context init + ~1.3 s model load + ~1.2 s prompt decode + ~0.4 s generation + ~0.24 s
  teardown. Target ~0.5 s.
  - **Do these two together.** `plan --batch` already proves the daemon half (1.78 s/utterance vs
    5.2 s cold) — but it still re-decodes all 3938 prompt tokens every request, so the daemon alone
    stops at ~1.8 s. The prompt is a fixed ~3900-token prefix with only the utterance at the tail;
    nothing in `knaif-llm` reuses it (no KV reuse, no `state_seq`).
  - **Free prerequisite, do it first:** re-measure CUDA context init on **bare-metal** Linux. The
    ~1.9 s is a WSL number and bare metal is typically 100–300 ms. If so, the daemon's payoff drops
    from −3.5 s to ~−1.8 s and prefix reuse becomes the better first move — i.e. this measurement
    decides the ordering, so taking it before design is not optional.
  - **Both need `eval-success` + `parity`, not a smoke test.** KV reuse changes decode chunking and
    can perturb FP accumulation; under greedy argmax that can flip a near-tie into a different plan.
    Same reason flash attention is not a free win (and it is likely already AUTO-enabled anyway).
  - **Explicitly rejected as quality risks (owner, 2026-08-01):** switching the default to
    1.7B-Q6, and trimming the prompt. Both move the number; neither is worth the accuracy.
  - **`nvprune`ing the 668 MB payload is a download-size lever only** — `dlopen` was measured at
    ~120 ms warm for all four CUDA libs, so it buys no startup time. Weigh against the
    `90-virtual` forward-compat PTX rationale in NATIVE.md §10.

- [ ] **macOS support — now planned, not deferred.**
  [plans/2026-08-02-macos-support.md](plans/2026-08-02-macos-support.md) (Planning, not started).
  macOS has been out of scope since the dual-runtime plan's Phase 9 and is still listed as a
  limitation in [NATIVE.md](NATIVE.md) §12. **The inference question is settled by reading the
  pinned `llama-cpp-sys-2 0.1.150` sources:** `GGML_METAL` defaults **ON** under `APPLE`, the Metal
  shader library is **embedded** in the backend binary (no `default.metallib` to stage),
  `ggml-metal` is a loadable backend under `dynamic-backends` exactly like `ggml-vulkan`, and
  `GGML_CPU_ALL_VARIANTS` covers Apple ARM (`apple_m1`/`m2_m3`/`m4`) — so **the macOS artifact
  needs no new cargo feature**, and MLX/Core ML and MoltenVK are both rejected in the plan.
  The work is packaging, signing and verification:
  - `installers/package.sh` has a `Darwin` arm but its build branch, core-lib staging and
    `exe_imports_llama` guard are all `linux`/`windows` only, and `set_origin_rpath` is a
    documented no-op off Linux — macOS needs `install_name_tool` / `@loader_path` surgery instead.
  - ⚠️ **`openmp` is a *default* feature of `llama-cpp-2`**, so `GGML_OPENMP=ON` and ggml runs
    `find_package(OpenMP)`. If Homebrew's `libomp` is found, the artifact links an absolute
    `/opt/homebrew` path that does not exist on a clean Mac — **the third instance of the
    `VCOMP140.dll` / `libgomp.so.1` trap**, both of which shipped in every 1.0.x artifact for the
    same reason: the check ran on the box that could not fail it. Needs
    `scripts/check_macho_deps.py`, the pure-Python sibling of `check_pe_imports.py` /
    `check_elf_deps.py`.
  - Signing is **not** optional the way Windows signing is (SmartScreen warns; Gatekeeper blocks),
    and any `install_name_tool` edit invalidates a signature — so the order
    stage → rpath surgery → sign → archive → notarize → staple is load-bearing.
  - **All three dependency checkers skip a file they cannot parse** (`audit()` catches the parse
    error, prints only under `--verbose`, and continues), so a binary that IS one of ours but is
    truncated, 32-bit, or otherwise malformed passes the gate silently rather than failing it — and
    the closing `ok N files/binaries: ...` line counts files that were never parsed at all. Noticed
    2026-08-03 while reviewing `check_macho_deps.py`; **deliberately not fixed there alone**, since
    a one-checker fix creates the inconsistency it removes, and the other two currently gate
    shipping Linux and Windows releases. The principled version distinguishes "not a Mach-O/PE/ELF
    at all" (skip — correct, `bin/` holds non-binaries) from "claims to be one and will not parse"
    (fail), and reports the parsed count rather than the file count. Low impact in practice: a
    real `bin/` is all well-formed binaries. Pick it up whenever those files are next touched.
  - Closing this plan is what unparks macOS in
    [plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md](plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md)'s
    C3 runner matrix.

- [ ] **⚠️ The eval regression gate currently proves nothing — and this is NOT macOS work.**
  Surfaced 2026-08-02 by an audit of the macOS plan (its task C0), verified against the code, and
  listed separately because it invalidates a gate every skill depends on:
  1. ~~`cmd_regression` sets `current = baseline` by default — *"compare snapshot to itself
     (no-op)"* — so without `--current FILE` it **always passes**.~~ **Fixed 2026-08-03**: it still
     defaults to the self-compare (that mode has a legitimate use — see EVAL_VERIFICATION_SOP.md —
     and `regression --all-skills` already existed as the version that requires a real current run),
     but it now prints a loud `⚠` warning when it does, instead of reading identically to a real
     check, and hard-fails if `--current` points at a file that doesn't exist (previously a typo
     silently fell back to the self-compare too — same false-green shape, worse, since it *looked*
     deliberate).
  2. ~~`just eval-regression skill:` takes no `*args`, so the recipe cannot supply `--current` even
     though the CLI accepts it.~~ **Fixed 2026-08-03**: `eval-regression skill *args:`, forwarding.
  3. `just eval-success` persists no scoreboard without `--save`, so there is nothing to pass.
  4. **Both committed snapshots are stale**, and one uses a verifier the docs forbid as a bar.
     **Corrected 2026-08-04 — the original figures compared two different units.** A snapshot's
     `total` counts **utterances**; each `eval.jsonl` record holds an `utterances` LIST, so
     comparing it against the file's line count understates the drift badly. ffmpeg's bar covered
     **35% of its corpus**, not the 95% "+17 rows" implied:

     | Skill | Snapshot verifier | Bar (utterances) | Corpus records | Corpus utterances | Real drift |
     |---|---|---:|---:|---:|---:|
     | `ffmpeg` | **`cheap`** ⚠️ | 297 | 314 | **847** | **+550** |
     | `documents` | `success` | 129 | 143 | **164** | +35 |

  Also, `--config` defaults to `eval_backends.yaml` and omitting `--backends` runs **every** stanza,
  including models whose GGUFs [PERFORMANCE.md](PERFORMANCE.md) §8 records as deliberately absent —
  each scoring ~0.0 and reading as catastrophic quality loss.
  **Remaining fix:** re-lock both snapshots with an executing verifier against the current corpora
  (own commit, per the standing rule — needs a real eval-suite run across the full corpus, not
  attempted yet). Until then any claim that a change "passes the regression gate" via `eval-success`
  → `eval-regression` with no `--current` is unfounded; passing `--current` explicitly against a
  freshly saved scoreboard is a real check today, but the **committed bar itself** is still stale.

  **Prerequisites resolved 2026-08-03 — the re-lock is blocked on ONE thing, and it is not a
  decision.** Investigated on the Windows box so the next attempt does not re-derive any of it:

  - **Which backend is canonical is not a judgment call.** Both shipped skills declare
    `recommended_model: knaif-qwen3-4b-v1` (`skills/{ffmpeg,documents}/skill.yaml:9`), which is the
    `qwen3-4b-sft-v3-flat-q4` stanza in `eval_backends.yaml`. Lock the bar against **that**, passed
    explicitly as `--backends qwen3-4b-sft-v3-flat-q4`.
  - **How bad the omitted-`--backends` trap actually is: 37 stanzas, 2 with a GGUF on disk.** Only
    `qwen3-4b-sft-v3-flat-q4` and `qwen3-1.7b-sft-v3-flat-q6` resolve; the other 35 would score ~0.0
    and read as catastrophic quality loss. `--backends` is not optional advice.
  - **The local backend works — nothing is blocking the run.** Verified 2026-08-03 end-to-end on
    the Windows box: `--skill ffmpeg --backends qwen3-4b-sft-v3-flat-q4 --limit 3` completed on GPU
    at ~800 ms per plan row. `ffmpeg`/`ffprobe` 8.0 are on PATH, so the executing verifiers are
    ready too. What remains is the run itself plus the deliberate decision to move the bar.
  - ⚠️ **A bare `import llama_cpp` FAILS on this box, and that is expected — do not read it as a
    broken install.** `llama.dll` links the CUDA runtime shipped as `nvidia/*` site-packages, which
    are not on the default DLL search path, so the import raises `Could not find module ...
    llama.dll (or one of its dependencies)`. `InferenceOrchestrator._prepare_llama_cpp_dlls()`
    exists precisely to preload those in dependency order (cudart → cublas → `ggml-*` → `llama`),
    and every real code path calls it. Diagnose through the orchestrator, never through a bare
    import.
  - ⚠️ **Do not `uv sync` this venv casually.** The lockfile pins `llama-cpp-python` **0.3.34** from
    PyPI, where the project publishes an **sdist only** (71 MB, no wheel) — a sync would compile
    from source and default to a **CPU** build, silently discarding the working CUDA install
    (0.3.23, with its 810 MB `ggml-cuda.dll`) and making `n_gpu_layers: 99` a no-op. Prebuilt CUDA
    wheels live at `abetlen.github.io/llama-cpp-python/whl/<cuda-tag>`, not on PyPI.
  - Ollama is running but carries only stock `qwen3:4b` — the **untuned** model, which would measure
    something other than what ships and must not be used to set the bar.

- [ ] **Warn on ARM64 Windows before installing the x64 build** *(blocked on hardware — moved out of
  [plans/2026-07-25-windows-installer-polish.md](plans/2026-07-25-windows-installer-polish.md)
  2026-07-27)*. `ArchitecturesAllowed=x64compatible` matches **ARM64 Windows as well as x64** — that
  is what it means, as opposed to `x64os`. So an ARM64 box installs the x64 build and runs llama.cpp
  inference under Prism emulation, silently, with the `ggml-cpu-*` variant dispatch selecting against
  an emulated CPUID. **The decision is settled (2026-07-25): warn and allow** — keep
  `x64compatible`, add an `IsArm64` check in `InitializeSetup` (the function exists in the installed
  Inno 6) showing a one-time *"this is an x64 build; it will run under emulation and inference will be
  slow"* message with continue/cancel. `x64os` was rejected: a slow knaif beats no knaif, and
  `skills list` / `skills deps` / `models pull` are unaffected by emulation. Today's behaviour is the
  one shape that is both slow **and** silent.
  **Blocked on:** no ARM64 machine to test on. A wizard path that cannot be exercised is how F1 and
  F2 reached users, and this lands in `InitializeSetup` — the procedure that already gates every
  install, including the stale-install rescue. Do it when a box exists, or drop it if a native ARM64
  artifact ships first and makes it moot.

- [x] **Close `feature/native-implemetation` (native v1 release) — MERGED to `dev` 2026-07-19 (PR #39).**
  The branch is closed; the two downstream plans in the sequence (OSS-prep, post-v1 CI) remain live,
  tracked as their own plans below. Single closeout plan:
  [plans/2026-07-15-native-branch-finalization.md](plans/2026-07-15-native-branch-finalization.md).
  Close-the-branch scope: merge-readiness (suites green locally) + default-model auto-select +
  CUDA multi-arch build (opt-in `GGML_BACKEND_DL`) + Linux packaging + a **manually-cut** v1
  release, then merge to `dev`. macOS + combined GPU bundle + daemon = post-v1.
  Consolidates the native Phase 9/10 items below.
  **Scope narrowed 2026-07-17 (owner) — the v1 release is now a three-plan sequence:**
  1. **Close the branch** (above). **CODE- AND DOCS-COMPLETE 2026-07-17:** A, B, C, D, G closed;
     H1 green (pytest 1496 passed/40 skipped, cargo test 213, clippy/fmt clean, ffmpeg evalsuite
     regression clean); E closed but for E2's Windows half. **C5b is GO — Option 3 (loadable
     backends) proven on Windows AND Linux.** **Remaining needs a second machine or an owner
     action:** E2-Windows + H2 (the Windows box), C4(b) Blackwell (the RTX 5080), H3 (merge →
     OSS-prep → transfer → tag → publish). **C6 (the CUDA opt-in surface) is DEFERRED** — v1 ships
     CPU+Vulkan auto-selected, CUDA payload built/proven but copied into `~/.knaif/backends` by hand.
  2. **OSS-prep pass** — scrub, then
     flatten onto a fresh `blackdeep-tech/knaif` + public. Runs **between the merge and the v1.0.0 tag**.
  3. **[plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md](plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md)**
     — CI + `release.yml` + eval-parity (was Workstream F) **and** C6. Starts after v1.0.0 publishes.
- [x] **Native-runtime Phase 5 leftovers — DISCUSSED & DECIDED 2026-07-04; all three resolved.** Gate lifted;
  resolutions recorded in the top-of-plan callout in
  [plans/2026-06-17-monorepo-dual-runtime.md](plans/2026-06-17-monorepo-dual-runtime.md).
  1. **Multi-backend bundle → DEFERRED to Phase 9 (packaging).** Ship proven per-target builds
     (CPU + cross-vendor Vulkan GPU + optional CUDA perf build) meanwhile; no `GGML_BACKEND_DL`
     single-artifact work now.
  2. **HTTP `Fetcher` + `models pull`/`update` → BUILT (2026-07-04); GGUFs PUBLISHED — DONE
     (verified 2026-07-16).** `ureq` `HttpFetcher` (tokenless public GET; commits
     `8022bb1`/`9b981da`), `knaif models pull`/`update` verbs, and `scripts/publish_model.py` (admin
     one-command upload + SHA-pinned manifest rewrite, `ece5d2d`). Host = HF repo `blackdeep/knaif`.
     **The upload happened:** both `knaif-qwen3-4b-v1` (2.5 GB) and `knaif-qwen3-1.7b-v1` (1.4 GB) carry real
     commit-SHA-pinned URLs + sha256 + size_bytes, verified live — anonymous `200`, and
     `Content-Length` matches `size_bytes` exactly on both. No owner action remains here.
  3. **JSON-repair → pure extractor DONE (`bc8803f`).** Ported `agent.py` `_extract_json`/
     `_clean_json` to `knaif-core` `extract.rs` (strip `<think>`/```` ``` ```` fences → first
     brace-balanced object), wired into `knaif plan`. Validation-retry loop rides with the
     `CommandAgent` port. **All three Phase 5 leftovers now resolved.**
- [ ] **OSS-readiness blockers — now planned, not loose.** **Workstream S is fully discharged as
  of 2026-07-24** — S7 (the owner-driven, plan-by-plan documentation refactor) closed with the
  published set at **33 plans + README**: zero broken `plans/*.md` references tree-wide, the
  README index in sync both directions, and all **11** plans cited from source/config still
  present. **Remaining: T7** (claim the `knaif` PyPI name — owner action) **and X1–X8**, the
  one-way flatten-and-publish sequence.
  the OSS-prep pass owns the scrub +
  transfer + contributor surface (incl. the `knaif` PyPI name claim, moved there from the
  finalization plan's F4); CI moves to
  [plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md](plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md)
  because a freshly created repo starts with no secrets or branch protections, so CI must land
  **after** the new repo exists.
  Still true from this list's original wording: no PII is tracked; skills are
  **deliberately** excluded from the wheel with a documented rationale in `pyproject.toml`
  (loaded by path via `from_skill`/`skills_root`), not an accidental packaging gap; `pyproject`
  carries real metadata + a `knaif-cli` entry point. **New finding 2026-07-17:** `python/training/.env`
  is tracked despite `.gitignore:24` — contents benign (cache paths only), but gitignore can't
  protect an already-tracked file, so it's a trap for the next person; prep-pass task S1 decides.
- [ ] **Orchestrator honesty** — honor `json_mode` in `infer_stream`, test the on-path,
  align the thinking-conflict guard across backends.
- [x] **Validator-feedback retry** — one retry with the validator error injected (cheapest
  known accuracy win; error strings already exist). **Done:** `CommandAgent.infer` now
  re-prompts the model once on a parse or structural-validation failure, injecting the
  concrete error (`_parse_and_check` mirrors `execute_plan`'s normalize→defaults→validate
  order on a copy so forgiving coercions don't trigger spurious retries). Purely additive —
  a persistent failure falls through to the prior clarify/error behaviour. Real-path only
  (`infer_stream`/notebook path unchanged). Missing-required-arg failures are deferred to
  the clarify gate (no retry), so correct clarifies can't regress into hallucinated plans.
  Toggle via `agent.repair_invalid_plans`. **Measured** (Qwen3-1.7B, full 297-row ffmpeg
  corpus): 73.7% → 75.4% pass (+5 rows recovered: parse_error/error → plan), +18% latency
  on failing rows. End-to-end coverage in `python/core/tests/test_evalsuite_retry.py`.
- [ ] **Sandbox/CLI safety posture** — decide + document the `sandbox=None` default and the
  CLI's blanket `confirmed=True`.
- [x] **Native `run` first-run fixes** — cluster surfaced by the first manual `just native
  ffmpeg` runs, all resolved 2026-07-06: raw-output dump on parse failure behind `$KNAIF_DEBUG`
  (Task 1); backslash paths normalized `\`→`/` before the prompt so echoed paths yield valid JSON
  (Task 2); llama.cpp log noise quiet-by-default behind `--verbose` (Task 3); `just native`/
  `native-mock` run from the invocation dir so relative input paths resolve (Task 4); `cuda`/
  `vulkan` features forwarded to `knaif-cli` (Task 5); `native-cuda`/`native-vulkan` recipes so
  GPU runs skip the env-var dance and show build progress (Task 6). Workspace tests + clippy
  green; **CUDA build + full-GPU offload verified on the RTX 5080** (knaif-qwen3-4b-v1, all layers on
  CUDA0). (Plan file deleted 2026-07-23 — every fix is in shipped source and documented in
  `docs/NATIVE.md`: `KNAIF_DEBUG`, `--verbose`, the `\`→`/` normalization, the Vulkan/Ninja gotcha.)
- [x] **Native model store dir: off Roaming — DONE 2026-07-07.** `default_store_dir` in
  `native/crates/knaif-models/src/store.rs` now resolves to `~/.knaif/models` on **every** platform
  (`%USERPROFILE%\.knaif\models` on Windows — the profile root, not Roaming), uniform like
  `~/.aws`/`~/.ssh` (owner chose simplicity over per-OS convention dirs). Test asserts the
  `~/.knaif/models` path and no-Roaming; existing installed models migrated off Roaming.
- [ ] **io rebuild** — remove legacy `executor.py` file-ops; settle arg validation behind
  `arg_value_sets`; add `HandlerContext.resolve()`. This is the Phase 9 remainder:
  `python/core/knaif/executor.py` still holds the bare-`CommandAgent` io implementation
  (`cmd_list_files`/`find_files`/`delete_files`/`move_files`), redundant with the OOP io
  skill since PR #15 but still the bare-agent default and directly unit-tested by
  `test_executor.py` — so removing it means deciding the bare-agent default and migrating
  those tests. Plan: `docs/plans/2026-06-16-oop-skill-architecture.md` (Phase 5).
- [ ] **`knaif.cli` SDK — three deliberate v1 deferrals, all demand-triggered.** None needs a
  plan; each has a written trigger, and all three are now stated in `docs/SDK.md` (*Not in v1*)
  so third-party readers can tell "scoped out" from "missing". Design detail lives in
  `docs/plans/2026-06-19-knaif-cli-sdk.md` (Decisions 1 and 6, Phase 4, Phase 7 note).
  - **argv-emit mode** (Phase 4's one unchecked box) — design is complete; **do not build it
    speculatively.** It is deliberately feature-degraded: once the host re-dispatches argv,
    the confirmation gate, post-resolution sandbox re-validation, and intent expansion no
    longer apply. Trigger: a real host that needs cross-process dispatch. Until then
    `app.invoke(..., dry_run=True)` covers the use case with the gates intact.
  - **`@nk.intent` dev-authored intents (v2)** — the only one that would earn its own plan,
    and it is blocked on evidence rather than effort: multi-step chain fidelity is the
    weakest part of small-model planning (see `2026-06-09-complex-two-step-intents` and the
    `chain3` fine-tuning slice), so a macro API would inherit that. Trigger: a third-party
    need **plus** chain fidelity improving enough to build on.
  - **A `destructive` SDK example** — `clock` is read-only, so the gate has no example
    coverage (ffmpeg/io tests cover the machinery). ~30 lines: saved-zones `save`/`forget`.
    Trigger: wanted for docs; otherwise leave it.
- [ ] **Windows installer polish — two P0s found in a live 1.0.1 install session (2026-07-25).**
  Plan: [plans/2026-07-25-windows-installer-polish.md](plans/2026-07-25-windows-installer-polish.md).
  **(a)** The `deps\*` winget tasks name a parent task `deps` that is never declared, so they render
  as children of the checked "Add to PATH" task — which **defeats their `Flags: unchecked`**:
  Ghostscript (AGPL) and LibreOffice (~350 MB) come up pre-checked against the script's stated
  intent, and their `GroupDescription` never renders. Fix is flat task names, *not* a parent task
  (Inno force-checks children of a checked parent). **(b)** Nothing detects or repairs a missing
  `{AppId}_is1` key: with it absent there is no Add/Remove Programs row and an upgrade degrades to
  the *"folder already exists"* warning. A fresh install writes the key correctly — the gap is that
  a machine once in that state (the Windows dev box is) can never get out of it.
  Also: `getmodel` is offered when the GGUF already exists (`Check: NeedsModel` is on the `[Run]`
  entry but not the `[Tasks]` entry), no `.ico`/VERSIONINFO anywhere (`knaif.exe` `FileVersion` is
  blank), the signing path (W4, blocked on a cert decision), and a placeholder `AppPublisherURL`.
  License-accept default already fixed 2026-07-25.
  **Five more added on a follow-up script cross-read (2026-07-25):** **(c0) P0, licensing:**
  `package.sh` never stages **`NOTICE`** — it copies `LICENSE` and `licenses/` and stops, so the
  Apache-2.0 §4(d) attribution file (which carries the Qwen3 derivation notice for the shipped
  models) is in no artifact on any OS. One `cp`, plus a `smoke.sh` assertion so it cannot silently
  stop shipping again; **(c)** nothing in the installed
  tree says what knaif *is* or who maintains it — the `README.txt` `package.sh` generates is pure
  quick-start, so this one is **not Windows-only** and fixing it fixes all three OS artifacts;
  **(d)** no `[InstallDelete]`, so reinstalling with a skill deselected leaves it installed and still
  listed by `skills list`; **(e)** `ArchitecturesAllowed=x64compatible` silently accepts ARM64
  Windows and runs inference under emulation with no warning — decide warn-and-allow vs `x64os`;
  **(f)** `ChangesEnvironment` never reaches already-open terminals and no finish page says so.
  New **W6** adds an `.iss` lint to `python/core/tests/` (parent-task, `Tasks:`/`Components:`/`Check:`
  references) so the (a) class cannot recur — W1's only guard today is "verify by eye", which is
  exactly how (a) shipped.
  **Owner decisions 2026-07-25, both now closed:** the copyright holder is
  **Blackdeep Technologies Ltd.**, and this one is **already applied** — root + `python/core/` copies
  of `LICENSE` and `NOTICE` all carry it; `LICENSE:189` had said *"knaif contributors"*, the
  jointly-owned-project convention, which was never accurate and contradicted `NOTICE`. Open
  follow-up: when a signing cert is issued, reconcile the installer's `AppPublisher` with the **cert
  subject**, since the CA issues that subject from registry records rather than letting it be
  self-declared — those two are what Windows shows side by side; the copyright notices need no
  such match. And
  **knaif stays OSS** — a future
  commercial product would *use* Apache-2.0 knaif rather than relicense it, so **SignPath Foundation**
  is confirmed for signing and **no CLA is ever needed** (Apache-2.0 §5 covers inbound contributions;
  a DCO is the lightweight option if outside PRs start). Surviving constraint: a proprietary UI must
  ship as its **own** signed artifact, never folded into the Foundation-signed knaif installer.
- [x] **Spinner claims "CPU" and "first run" on every real run — both wrong (cosmetic). FIXED
  2026-08-02, in 1.1.0.** Resolved by *dropping* both claims rather than by branching the text:
  the message is now "Loading model and planning (this can take a minute)…". Branching on a hoisted
  `gpu_present` was the plan below, but the spinner is constructed before llama.cpp initialises, so
  at that point no device has been chosen and any name it prints is a guess. The accurate,
  conditional statement is already made by the `gpu == Some(false)` warning a few lines above, which
  *does* know — so the spinner repeating it could only duplicate or contradict it. Dropping the
  claim also avoids the double-probe problem the plan flagged. Original analysis kept below; the
  measurement is what made the "first run" half indefensible.
  `thinking_spinner()` (`apps/cli/src/main.rs:295`) hardcodes *"Loading model and planning (first
  run on CPU can take a minute)…"* with no backend check, so a Vulkan or CUDA run is told it is on
  the CPU. The genuine CPU-only warning a few lines away (`main.rs:487`) **is** correctly gated on
  `knaif_llm::gpu_present(...) == Some(false)`; the spinner simply never consults it. Fix: hoist
  that one `gpu_present` call (calling it a second time would double-print its probe under
  `--verbose`) and branch the spinner text on the result. Only visible without `--verbose`, since
  `main.rs:496` suppresses the spinner when llama.cpp is printing its own trace.
  **"first run" is independently wrong**, on any backend: every `knaif run` is a fresh process that
  re-reads the GGUF, re-uploads weights to VRAM, re-allocates the KV cache and re-prefills the
  planner prompt, so runs do not get cheaper with repetition. Measured 2026-07-25, packaged v1.0.1
  artifact on an RTX 3070 Laptop (Vulkan, `knaif-qwen3-4b-v1` Q4_K_M): **7.73 / 7.06 / 6.84 s**
  over three consecutive runs — flat, fastest last. Repeat runs can only get cheaper once the
  **persistent inference daemon** exists, which is explicitly out of scope in
  [plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md](plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md).
  So the wording should lose "first run" as well as the unconditional "on CPU".
  **Deliberately not fixed in 1.0.1:** `v1.0.1` is already tagged, pushed, and protected by the
  `release-tags` ruleset (the tag cannot be moved), and `CHANGELOG.md` states the native runtime is
  unchanged in 1.0.1 — a spinner string does not justify falsifying either.
- [ ] **ffmpeg consolidation** — `_standard_pipeline` builder, `$var.field` over dict-unwraps,
  `_append_codec_flags`, test fixtures (~1,000 removable lines).
- [ ] **Constrain `container` (and friends) with `arg_value_sets`.** `convert_video.container`
  has no schema or value set, so a model emitting `container: "hevc"` / `"av1"` — a *codec*
  name in the container slot — passes validation untouched and reaches the expander, where it
  can only fail. Observed on non-English utterances across several rows. Either declare the
  valid containers so validation rejects it, or normalize known codec names out of the
  `container` slot; those strings are never valid containers, so the guard is low-regression.
  Same mechanism as the `arg_value_sets` item under **io rebuild** above.
- [ ] **Eval corpus trim — descriptor→clarify cluster (unblocked 2026-07-22).** ~13 rows
  (`ffmpeg_253–265`, plus 104/110/207) all test one behavior — "a descriptive reference is not
  a filename → clarify" — re-run across many operations × utterances × backends. It was
  deferred behind the unresolvable-input→clarify gate, which shipped as the NL clarify gate
  (PR #14), so the behavior is now deterministic and unit-testable; keep a canonical 3–4 rows
  and cut the rest. Saves ~7% of suite wall-clock at no coverage loss. Do **not** reword the
  utterances into real filenames (that converts a clarify test into a plan test), and treat
  this as an eval-only trim — `train.jsonl` may still want the examples. Full analysis:
  [plans/2026-06-08-local3-outcome-fixes.md](plans/2026-06-08-local3-outcome-fixes.md)
  (*CORPUS TRIM*); principles in `docs/EVAL_FRAMEWORK.md` (*Corpus composition*).
- [ ] **Re-lock the ffmpeg snapshot with `output_diff` — two defects, one re-lock (decided
  2026-07-24).** **(a) Wrong verifier:** `skills/ffmpeg/data/eval_snapshot.json` is still
  `verifier: cheap`, but the acceptance bar must now be an *executing* verifier — see *The eval
  ladder* in `docs/EVAL_FRAMEWORK.md`. `cheap` never runs the command, so it cannot see a wrong
  artifact, and it reports **false** regressions when the corpus is annotated (the 0.973→0.928
  chain-row artifact, `docs/audits/2026-06-26-ffmpeg-cheap-knaif-chain-artifact.md`).
  **(b) Stale row set:** locked at 297 utterances against a corpus that now expands to **847**
  (314 records × their `utterances` lists) — the pre-existing half of this, noted under *Qwen3
  fine-tuning pass 3* below. Fix both at once: `just eval-snapshot ffmpeg` (runs `output_diff`,
  which ffmpeg owns), in its own commit, with an `evals/INDEX.md` row.
  **Why `output_diff` and not `success`:** 74 of 219 ffmpeg plan rows carry no `success_criteria`,
  and `success` returns **score 1.0 when criteria are empty** (`skills/ffmpeg/eval/verifiers.py`)
  — ~119 scored rows are free passes that can never fail, so `success` cannot hold a bar today.
  `baseline.command` covers 218/219 plan rows, so `output_diff` has almost no free passes.
  **Follow-on (the better end state):** annotate those 74 rows — `ffmpeg_001–008` are among them,
  so this was never finished rather than deliberately skipped — then re-lock on `success`, which
  grades an absolute spec instead of diffing a reference command.
  **Not a publication blocker:** the OSS-prep pass's
  *Out of scope* files behaviour/quality items rather than fixing them in the prep pass.
- [x] **Second skill** — prerequisite for `docs/plans/2026-06-17-monorepo-dual-runtime.md`.
  **Done as the documents skill:** `skills/documents/` now loads, is
  discoverable via `list_skills()`, includes seed data/profiles/prompt, generates
  deterministic text/PDF/Office/image fixtures, covers `inspect_document`,
  `extract_text`, and `find_in_document` across implemented text-native formats, and
  implements PDF merge/split/rotate/remove/reorder/protect/unlock/watermark/page-number,
  document conversion, the `compress_pdf` workflow, and optional Tesseract-backed OCR
  (`ocr_document` plus `extract_text` fallback for images/scanned PDFs). The skill also
  has seed eval verifiers, fixtures, and corpus rows. The `input_refs` media-vocab extraction
  moves to the future vector/search plan (its non-file query inputs are what force it),
  not the documents skill.
- [x] **Retrieval overhaul — SHIPPED.** Cross-cutting capability (knaif core + all skills) that
  replaced the broken whitespace `retrieve_tools`. Lexical-first (embeddings phase deliberately
  declined), CJK n-gram tokenization (absorbed the CJK segmentation plan), df-weighted scoring,
  recall@k CI gate. **Recall@5: ffmpeg 0.853→0.954, documents 0.914→0.947.** Plan (Done):
  `docs/plans/2026-07-02-retrieval-overhaul.md`.
- [x] **Qwen3 fine-tuning next pass** — **Done** (the plan is Done/superseded by pass 3; this
  entry was still unchecked as of 2026-07-23). Started from restored v1 train data, audited
  1.7B/4B failures, then tried ffmpeg-v3 hard-focused data, weighting/curriculum, preference
  tuning, and Q6-first quantization. Its two open tasks (quant pass, production lane) were
  completed by pass 3; the durable outcomes are canonical in
  [FINE_TUNING.md](FINE_TUNING.md) §5. Plan:
  `docs/plans/2026-07-01-qwen3-ffmpeg-max-results.md`. Progress 2026-07-01:
  v1 restored, failure audit done, ffmpeg-v3 data + weighted builder implemented, and
  four union Q6 SFT candidates plus the ffmpeg-only v3-flat scope test evaluated. Current
  best balanced candidate is
  `qwen3-1.7b-sft-v3-flat-q6` (ffmpeg full 0.878, hard 0.927); gentle2 is the best
  hard-specialist (hard 0.964) but regresses full ffmpeg/documents, hard3 is also a
  hard-specialist only, low-lr failed the promotion gate, and ffmpeg-only v3-flat did not
  beat the shared v3-flat model. DPO-v1 over v3-flat used 40 real failure pairs and
  preserved documents, but failed the ffmpeg gate (full 0.870, hard 0.909). Distill-v1
  added 45 verifier-filtered synthetic ffmpeg rows and preserved documents, but also failed
  the ffmpeg gate (full 0.844, hard 0.891).
- [~] **Qwen3 fine-tuning pass 3** — mostly done (2026-07-02). Corrected the pass-2 headline
  (v3 effect real but +3.6pt hard not 5.4pt; Q6-inflated), completed the 1.7B quant sweep
  (Q6 pick), **retrained + PROMOTED 4B-v3** for ffmpeg+documents (passes gate, eliminated the
  old enum-bleed contamination), applied retrieval keyword fixes (non-CJK misses 70→46,
  full +0.48pt), and closed the planner-diversity experiment (io transfers nothing to ffmpeg —
  hypothesis not supported). **Still open:** Task 6 preference tuning (only if data levers
  plateau); re-lock stale per-skill regression snapshots against `knaif-qwen3-4b-v1` — **ffmpeg's
  bar covers 297 of the corpus's 847 utterances** (314 records, each with an `utterances` list;
  the "17 rows behind" written here originally compared utterances to line count), so
  `eval-regression` is comparing
  across a changed row set and its aggregate verdict is not trustworthy until the re-lock
  (see *The gate is only valid when the corpus row set is unchanged* in
  `docs/EVAL_VERIFICATION_SOP.md`). **The ffmpeg half of this is now owned by the dedicated
  re-lock entry above** (2026-07-24), which adds the second defect — the snapshot's verifier
  must change `cheap` → `output_diff`, not just its row count. CJK retrieval segmentation **shipped** with the retrieval
  overhaul (2026-07-02). **Canonical how-to + outcomes: `docs/FINE_TUNING.md`.** Plan:
  `docs/plans/2026-07-02-qwen3-finetuning-pass3.md`; full history in
  `docs/audits/2026-07-01-finetuning-study-findings.md`.
- [x] ~~**FFmpeg geometry/thumbnail eval close-out (Phase 10, Tasks 3.1–3.6).**~~ **Closed
  2026-07-22 — it had already been done.** The close-out ran 2026-06-17/18; both runs are in
  `evals/INDEX.md` and the snapshot was re-locked. This entry, the plan's status note, and the
  `docs/plans/README.md` row all still said "deferred" while the plan body had 3.1–3.6 checked
  with results. Separately live and **not** what this entry meant: the ffmpeg corpus has since
  grown to 314 records / **847 utterances** against a snapshot locked at 297 utterances — see the
  snapshot re-lock item above.
