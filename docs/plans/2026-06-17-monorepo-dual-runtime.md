# Monorepo + Native Runtime Plan (Python authoring · Rust/llama.cpp release)

**Status:** Done · **Created:** 2026-06-17 · **Completed:** 2026-07-19
**Owner:** core · **Ref:** PR #39 (merge to `dev`)

> **This plan is the design + history record for the dual-runtime architecture — keep it.**
> Phases 0–9 shipped and v1 was cut and merged (PR #39, 2026-07-19). Phase 10 is reconciled
> below: two items shipped, the parity goal shipped by a different mechanism than specified,
> and the three CI items are owned by
> [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md).
>
> **Layout note (2026-07-23).** This is a dated design/history record. Its progress-log and
> decision entries name paths in the layout that was current *when each was written* — mostly
> the pre-restructure tree (`packages/knaif`, `apps/knaif-cli`, `crates/*`), though later
> entries already use the current one (`native/crates/*`). They are **left as-written** on
> purpose: rewriting a dated log would misdate the work. Translate with
> [2026-07-19-repo-restructure.md](2026-07-19-repo-restructure.md) —
> `packages/knaif`→`python/core`, `apps/knaif-cli`→`apps/cli`, `crates/*`→`native/crates/*`,
> `src/skills`→`skills`, `shared/{runtime,models}`→`contracts/{runtime,models}`,
> `packaging/`→`installers/`.
>
> **Status corrected 2026-07-22** (S7 decision). This line previously read "In progress" and
> `docs/plans/README.md` said *"Planning · Blocked: author a 2nd skill first to stabilize the
> contract"* — stale on every count: the `documents` skill resolved that blocker on 2026-06-21,
> the closeout plan is CLOSED, and the native runtime shipped.
>
> **Not extracted, deliberately.** The OSS-prep pass classified this as extract-then-delete;
> that was reversed on 2026-07-22. Ten files cite it *as* the rationale for the repo's shape —
> `Cargo.toml`, `pyproject.toml`, `python/core/knaif/__init__.py`, and the READMEs of
> `contracts/runtime/`, `contracts/models/`, `native/crates/`, `skills/`, `apps/cli/`,
> `python/core/`, `installers/macos/`. Those READMEs are short *because* they delegate here;
> copying 1,300 lines of design history into each would make them worse. One canonical
> rationale with many pointers is the intended structure.

**Historical progress detail:** Phases 0–4 done; Phase 5 slice + llama.cpp spike done; Phase 6
core done 2026-07-04; Phase 7 ffmpeg + documents complete; Phase 5c done — real llama.cpp
inference wired + verified end-to-end 2026-07-06; Phase 8 runtime side done 2026-07-07 — dep
detection + doctor/preflight + first-run guidance + 429 retry, GGUFs uploaded & `models pull`
verified live; Phase 9 (packaging) — exe-relative resource resolution + portable-zip packaging,
Windows installer via Inno Setup.

> ⚠️ **For remaining work / next steps, use the closeout plan, not this file.** As of 2026-07-15
> the operative doc for finishing and merging `feature/native-implemetation` is
> [2026-07-15-native-branch-finalization.md](2026-07-15-native-branch-finalization.md). **This
> plan is now the design + history record** (the *why*); it is not maintained as the active task
> list. Newer decisions (CUDA arch floor, opt-in `GGML_BACKEND_DL`, default-model, Linux CUDA,
> release home, CI-as-fast-follow) live in the closeout plan; annotations below just point to them.

> **Progress (2026-07-03):**
> - **Phases 0–1 landed** — the 16 Phase 0 decisions are the checklist in *Phase 0* below
>   (a separate consolidated index file existed until 2026-07-23, when it was deleted as a
>   duplicate of that checklist); root workspace scaffolding added (`packages/`, `apps/knaif-cli/`, `crates/`, root
>   `skills/`, `shared/{models,runtime}/`, `packaging/{windows,linux,macos}/` placeholders,
>   `mise.toml`, grouped/native-skip justfile recipes + `just bootstrap`).
> - **Phase 2 core landed** — the Python package moved to `packages/knaif/knaif/` and its
>   tests to `packages/knaif/tests/`, under a **uv workspace** (thin root `pyproject.toml`,
>   `uv.lock` at root). Skill discovery bridged via `_discover_skills_root()`. Suite green
>   from repo root (1509 passed, coverage 83.5%), type-check + lint clean. **Deferred:**
>   `notebooks/`, `scripts/`, `training/` moves (see Phase 2 note).
> - **Phase 3 landed (incl. follow-ons)** — all three skills are now root-`skills/<name>/`
>   bundles (YAML+data at the top, Python in `python/`); the loader is bundle-aware and
>   `ctx.skill_dir` is the bundle root (no more `__file__` data coupling). `src/` is gone.
>   The three follow-on sub-features also landed: `runtimes:`/`dependencies:` skill metadata,
>   `Skill.load(overrides=)` deep-merge + `disabled_tools`, and `core_tools`/`steps` →
>   `shared/runtime/` (canonical + wheel-synced `core_tools.yaml`, drift-guarded). Suite green
>   at **1527 passed, coverage 83.5%**.
> - **Phase 4 core slice landed (2026-07-04)** — Cargo workspace + six engine crates +
>   `apps/knaif-cli` all build (Rust 1.96.0 via mise). `knaif --version` and `knaif skills
>   list` work; the latter reads the bundle `skill.yaml` + Phase 3 `runtimes:` metadata off
>   disk. `cargo test`/`clippy`/`fmt` green; justfile native recipes wired.
> - **Phase 5a offline slice landed (2026-07-04)** — `knaif-llm` (`LlmBackend` trait +
>   `MockBackend` + `KNAIF_LLM_BACKEND`), `knaif-models` (shared `ModelStore`: store resolver,
>   manifest, list/verify/delete/resolve, pull/update behind an injectable `Fetcher`),
>   `shared/models/model-manifest.yaml`, and CLI `models` verbs + the `plan --skill X --json`
>   skeleton. All offline-tested (cargo test/clippy/fmt green).
> - **Phase 5b llama.cpp spike done (2026-07-04)** — `LlamaCppBackend` proven end-to-end on
>   **all three backends** (qwen3-1.7b, 29/29 layers on GPU): CPU (gemma-1b), CUDA (RTX 5080 /
>   Blackwell), and Vulkan (RTX 5080; enumerated NVIDIA + AMD = cross-vendor). Toolchain
>   (MSVC+cmake+libclang) validated; build/runtime prereqs documented (incl. Vulkan needs the
>   Ninja generator). **Still deferred:** the single-artifact CPU+Vulkan+CUDA *bundle* + runtime
>   device selection (`GGML_BACKEND_DL`), real HTTP `pull`, and the JSON-repair port. **Next:
>   Phase 6** (native core runtime port), which also fills in `plan --json` for real.

## Phase 5 leftovers — DISCUSSED & DECIDED (2026-07-04)

The gate is **lifted**. All three were discussed with the owner on 2026-07-04; resolutions
below. Still tracked here + in `docs/TODO.md` + project memory so the decisions (and the one
outstanding owner action) are not lost across phases.

1. **Single-artifact multi-backend bundle → DEFERRED to Phase 9 (packaging).** Compiling CPU +
   Vulkan + CUDA into one `GGML_BACKEND_DL` binary with a detect→pick→CPU-fallback picker is a
   *distribution* concern with a heavy/hot combined build and a non-NVIDIA no-crash guarantee
   that can't be tested on this box. Each backend is already proven **individually**, so until
   packaging we ship **per-target builds**: CPU + cross-vendor **Vulkan** (the universal GPU
   backend, covers NVIDIA/AMD/Intel) + an optional **CUDA** perf build. No dynamic-backend work now.
   **→ REVISITED 2026-07-15 (finalization C5):** reopened as **opt-in `GGML_BACKEND_DL`** — the
   original two objections were premised on a *combined build shipped to everyone*. Shipping the
   CUDA backend as an **opt-in loadable lib** (default = exe + cpu + vulkan; `ggml-cuda`+cublas
   downloaded only when the user opts in) removes both: no ½ GB cost for non-NVIDIA users, and no
   non-NVIDIA crash by construction (an absent lib is never `dlopen`ed). Spike-gated on
   `llama-cpp-sys-2` dynamic-backend build support; thin-launcher fallback if it fails. Decision +
   tasks: [2026-07-15-native-branch-finalization.md](2026-07-15-native-branch-finalization.md) §C5.
2. **Model download path — HTTP `Fetcher` + `models pull`/`update` → BUILT (2026-07-04). Host =
   HF repo `blackdeep/knaif` (public).** Delivered: `ureq` `HttpFetcher`
   ([native/crates/knaif-models/src/fetcher.rs](../../native/crates/knaif-models/src/fetcher.rs), tokenless
   public GET, redirect-following; `8022bb1`); `knaif models pull`/`update` verbs (`9b981da`);
   `scripts/publish_model.py` — admin one-command upload + commit-SHA-pinned manifest rewrite,
   comment-preserving, never auto-commits (`ece5d2d`). Download side needs **no token**; the admin
   upload path holds the token in the publish script only. **Only outstanding owner action:** run
   `publish_model.py` to upload the GGUFs so the manifest `url`/`sha256` go from `TODO` to real.
3. **JSON-repair → pure extractor DONE (2026-07-04, `bc8803f`).** *Correction:* the repair logic
   is **not** in `planner.py` (that `parse_plan` is strict `json.loads`, and the Rust port already
   matches it). It lives in `agent.py` `_extract_json`/`_clean_json`. Ported the **pure extractor**
   to [native/crates/knaif-core/src/extract.rs](../../native/crates/knaif-core/src/extract.rs) (strip
   `<think>`/` ```json ` fences → first brace-balanced `{…}`) and wired it into `knaif plan`
   (`extract_json → parse_plan`). The `repair_invalid_plans` validation-retry loop still rides with
   the `CommandAgent` port (needs inference in the loop).

> **Status note (refreshed 2026-07-03):** Consolidated, and the skill layout decision
> (#3) is settled: skills are self-contained bundles at root `skills/<name>/` — YAML at the
> bundle top, per-language implementations in `python/` / `rust/` subfolders — loaded by a
> single-bundle loader. Skill distribution tiers and the deferred downloadable-skills
> direction are recorded under **Skill Distribution Model**. No monorepo move or native
> Rust implementation has started. The original blocker — authoring a second real
> skill before freezing the cross-language contract — is now satisfied by the
> productionized `documents` skill. This document merges and supersedes two earlier
> drafts (`2026-05-25-monorepo-native-cli.md`,
> `2026-05-26-monorepo-rust-cli.md`), which predate the current skill architecture
> (OOP `skill_class:` model, package-aware skills, `core_tools.yaml`, shared
> `steps/` library, ffmpeg `profiles/`, `vocab.yaml`, `knaif.evalsuite`, retrieval
> gates, and per-skill `recommended_model:`). Where they conflict, this file is the
> single source of truth; resolved decisions are called out under
> **Resolved Decisions** and should be recorded as Phase 0 decision plans.

**Goal:** Define the monorepo + native-runtime target: skills authored/tested in
Python, then optionally released through a Python-free Rust/llama.cpp runtime and
thin native CLI.

### Prerequisite (satisfied; keep the lessons)

The "author a second real skill" prerequisite is complete:
`src/skills/documents/` is productionized and exercises the shared skill contract
(`skill.yaml` / `tools.yaml` / `prompt.yaml`, package-aware `skill_class:`,
`Step`/`Intent`, `core_tools`, shared `steps/`, profiles, `arg_value_sets`, safety
metadata, eval fixtures, and per-skill `recommended_model:`).

The important lesson is narrower than the original blocker: `documents` is still a
file-centric skill, so it validates the package/runtime contract but does **not** by
itself force the `input_refs` media-vocab extraction. That leakage remains a
cross-cutting cleanup risk, not a blocker for Phase 0.

## Goal

Turn `knaif` from a single Python library into a monorepo with two first-class
products that share one canonical, declarative skill definition:

1. **Python authoring package** — the existing `knaif` Python framework, used to
   build, test, evaluate, fine-tune, and iterate on skills. Remains the reference
   runtime and fastest iteration loop. It is the only planned PyPI package; official
   built-in skills stay repo-only and are developed here before being ported to native
   surfaces.
2. **Native release product** — a Python-free Rust runtime library plus a thin CLI
   wrapper. The runtime implements the deterministic `knaif` contract, links llama.cpp
   for local inference, compiles in ffmpeg + documents, and ships first through native CLI
   installers for Windows and Linux (macOS is a post-v1 fast-follow).

Future product surfaces are deliberately anticipated but not built in this plan: a
desktop UI and mobile apps should reuse the same native runtime contract and shared
skill data instead of embedding Python or shelling through the CLI.

The migration is gradual: the Python implementation and its full test suite must stay
green after every phase.

## Locked Decisions

- **No embedded Python** in the end-user app. The core runtime is reimplemented in Rust.
- **Library-first native runtime, CLI first product.** The Rust CLI is a thin wrapper
  around reusable crates (`knaif-core`, `knaif-llm`, `knaif-skill-api`,
  `knaif-skill-*`). Future desktop and mobile apps must be able to call the same
  runtime directly.
- **CLI first, no TUI in v1.** (`ratatui` is a post-v1 possibility, not a goal.)
- **Natural-language command shape:** `knaif run ffmpeg "compress this for discord"`.
- **Native v1 skills are FFmpeg + documents.** Both already work in the Python CLI and
  are productionized enough to justify native parity in v1. `io` is stale and slated
  for rebuild — do **not** make it a native v1 target.
- **YAML skill definitions are the single source of truth**, consumed by both runtimes.
  Skills are **self-contained bundles** under a root-level `skills/<name>/`: the YAML
  sits at the top of the bundle (visible to every app), and each language keeps its
  implementation in a subfolder (`python/`, `rust/`). See **Resolved Decision #3**.
- **Skill selection happens at package/build time** (cargo features + a release manifest).
- **Python distribution stays framework-first.** The `knaif` PyPI package is for
  third-party developers building their own natural-language CLI apps and skills.
  Official built-in skills are repo-only product/reference assets, not features that
  must work from a bare wheel install via `create_agent("ffmpeg")`.
- **Monorepo top-level shape:** root `skills/` for self-contained skill bundles
  (data-first, per-language implementation subfolders), `packages/` for Python packages,
  `apps/` for shipped end-user apps, `crates/` for reusable Rust libraries, and `shared/`
  for no-code model/runtime contracts (`shared/runtime/`, `shared/models/`).
- **llama.cpp integration starts with `llama-cpp-2`.** It is hidden behind the
  `knaif-llm` trait boundary. A vendored `third_party/llama.cpp` submodule remains a
  fallback for release reproducibility, custom build flags, GPU backend control, or
  mobile packaging.
- **GPU backend: one artifact per OS with multiple backends compiled in; selection is
  runtime.** llama.cpp can build several backends together (`GGML_CUDA` + `GGML_VULKAN`) and
  select the device at runtime — and `llama-cpp-sys-2` sets each backend from an independent
  cargo feature, so they are **not** mutually exclusive (verified: llama.cpp build docs +
  `llama-cpp-sys-2` build.rs). So there is **no compile-time variant matrix and no
  install-time backend download**: the shipped binary bundles **CPU + Vulkan + CUDA** on
  Windows/Linux (**Metal** on Apple — the one genuinely compile-time/Apple-only backend),
  and at runtime it **detects → validates a usable device → picks the best → falls back to
  CPU**. **CUDA is preserved by being compiled *into* the one binary**, not shipped
  separately. Vulkan is the cross-vendor default *device* when both apply; CUDA is chosen on
  NVIDIA where preferred. Rationale for keeping both: measured on an RTX 5080, Vulkan
  matched/beat CUDA on the 4B lane and trailed only ~6% on 1.7B, and CUDA remains the
  reliable path in dev/CI where a Vulkan ICD is absent (WSL). *Open implementation detail
  (Phase 5 spike, not a design fork):* `GGML_BACKEND_DL` (each `ggml-*` backend a `dlopen`'d
  lib) is **effectively required**, not merely preferred — a *statically* linked CUDA backend
  carries a load-time dependency on the NVIDIA driver (`libcuda`/`nvcuda.dll`) and will
  **crash at process startup on non-NVIDIA machines, before runtime device selection ever
  runs** ("graceful init failure" never happens). So if `llama-cpp-2` can't expose DL
  cleanly, the fallback is **vendoring llama.cpp via CMake** or **install-time/launcher
  backend selection** — *not* static multi-backend linking.
- **Shipped apps use only llama.cpp for inference.** Every shipped surface — native CLI,
  desktop, mobile — runs inference exclusively through embedded llama.cpp (`knaif-llm`).
  **Ollama is confined to the Python skill-development / eval path** (the Python
  orchestrator's Ollama backend). It is never bundled, embedded, or used as a runtime by any
  CLI/UI/mobile app, and the Rust runtime ships **no** Ollama backend.
- **Windows and Linux are v1 release blockers**, via native installers. **macOS is
  postponed to a post-v1 fast-follow** — the current dev environment is not macOS, so it
  is not a v1 blocker. The Rust core stays cross-platform and the `knaif-llm` boundary
  keeps macOS reachable; only packaging/release for macOS is deferred.
- **GGUF models are not bundled** in installers (size). Install succeeds without a
  model; first run downloads from a repo-controlled manifest (URL + checksum) or asks
  for a model path. Downloaded models live in the shared model store.
- **Model management is a shared runtime capability, not per-app logic.** A single
  `ModelStore` lives in its **own `knaif-models` crate with no inference dependencies**
  (manifest, HTTP download, checksum, atomic install, delete, store-path resolver) and
  exposes **list / pull / update / verify / delete (incl. delete-all)** against the
  manifest. `knaif-llm` depends on `knaif-models` (to locate/load model files); apps and
  the future UI depend on `knaif-models` for management and pull in `knaif-llm` only to run
  inference. Because desktop/mobile **embed** these crates (not shell to the CLI), a
  model-management screen must not force-link `llama-cpp-2`. **CLI and every future UI call
  the same API** — the flow is identical across surfaces; UI never reimplements downloads.
- **Single canonical model store, one uniform path.** Resolved once by a shared resolver:
  `$KNAIF_MODELS_DIR` override → else `~/.knaif/models` on **every** platform
  (`%USERPROFILE%\.knaif\models` on Windows). Deliberately uniform like `~/.aws` / `~/.ssh`
  rather than per-OS convention dirs (simplicity, decided 2026-07-07); `home()` is the profile
  root, so on Windows this is **not** the Roaming profile — multi-GB GGUFs never sync. Installer
  seeding and app-driven downloads write to the **same** store.
- **Install-time model seeding is optional and never the only path.** Installers may offer
  to fetch a base model as an install option, but every model operation
  (download/update/delete) is also fully doable later from the app itself.
- **Model lanes are recommended defaults, not hard requirements.** Desktop/native CLI
  recommends `qwen3-4b-v3`; mobile recommends `qwen3-1.7b-sft-v3-flat-q6`. Users can
  override by model name or path.
- **Skill external dependencies** (e.g. ffmpeg/ffprobe) are declared in `skill.yaml`,
  detected by the installer, and offered via platform package managers
  (winget / Homebrew / distro pkg) with fallback instructions. **PATH is never modified
  without explicit consent.**
- **Eval stays in Python.** The `knaif.evalsuite` package gains a native-CLI arm for
  parity, rather than reimplementing eval in Rust.
- **macOS packaging is deferred to post-v1** (see the platform-blocker decision above).
  An Apple Developer account **is** available, so when macOS lands it should ship
  **signed/notarized** — the earlier "unsigned, no account" assumption is void.

## Future Product Surface Decisions

These decisions shape the native runtime boundary now, even though the UI products are
out of scope for the first native CLI release.

- **Desktop UI:** target Tauri 2 with a React frontend, built as a **standalone app that
  embeds the engine crates directly** (`knaif-core`, `knaif-llm`, `knaif-models`,
  `knaif-skill-*`) in its Tauri Rust backend and exposes `#[tauri::command]` functions to
  React. It **ships as one app, not a UI wrapper that shells out to the native CLI** — the
  CLI-as-subprocess approach is acceptable **only** as an early prototype/compatibility
  bridge, never the shipped architecture. Rationale: it's the point of the library-first
  runtime, gives in-process progress/cancellation and typed errors, avoids version-locking
  the UI to a CLI binary, and matches the mobile constraint below (iOS cannot spawn
  subprocesses, so embedding is mandatory there regardless).
- **Mobile UI:** desktop and mobile do not need to share one UI framework. Mobile apps
  are expected to be simple native-feeling shells: add/select files, enter or dictate
  utterances, run skills, and show progress/results.
- **Mobile implementation direction:** prefer platform-native apps — SwiftUI on iOS
  and Kotlin/Jetpack Compose on Android — because file pickers, speech-to-text, Siri /
  Apple speech integration, and Android speech services are first-class requirements.
  Kotlin Multiplatform / Compose Multiplatform and Flutter remain comparison options,
  but shared mobile UI is not a primary goal.
- **Shared across all products:** declarative skill data, safety/validation semantics,
  model setup strategy, llama.cpp integration where feasible, and eval/parity fixtures.
- **Not shared by requirement:** desktop UI, mobile UI, and interaction patterns.
- **Never required for shipped UI apps:** embedded Python or mobile subprocess execution
  of the CLI. Mobile apps must call the native runtime through a library boundary
  (for example FFI / UniFFI-style bindings) if they run the engine locally.

## Resolved Decisions (record before Phase 4)

These are the points where the two drafts disagreed or where current architecture
forced a fresh choice. Record them as dated Phase 0 decision plans before moving files.

1. **llama.cpp integration — crate vs. submodule.**
   - Draft B: `llama-cpp-2` crate (manages the llama.cpp build for you).
   - Draft A: pinned `third_party/llama.cpp` submodule built via CMake from a build script.
   - **Decision:** start with `llama-cpp-2` behind the `knaif-llm` trait boundary;
     it removes most of the cross-platform CMake burden. Keep the abstraction clean
     enough to swap to a vendored submodule later if version pinning or custom build
     flags (e.g. specific GPU backends) become necessary. The trait boundary makes this
     reversible, so it is low-risk to defer.

2. **Directory names.**
   - **Decision:** use `packages/knaif/` for the Python framework package, not
     `apps/knaif-py/`. The Python project is a package/SDK, not an end-user app.
   - **Decision:** use `packages/training/` for fine-tuning tooling related to the
     Python authoring workflow; it is internal and not planned for PyPI.
   - **Decision:** use root `crates/` for Rust runtime libraries, not
     `apps/knaif-cli/crates/`, because CLI/desktop/mobile all consume the same engine.
   - **Decision:** use root `skills/` (not `shared/skills/`) for skill bundles, so skill
     data is maximally discoverable to every app and not nested under a framework path.
     Each bundle is data-first (`skill.yaml` etc. at the top) with per-language
     implementation subfolders (`python/`, `rust/`). See Resolved Decision #3.
   - **Decision:** use `shared/models/` and `shared/runtime/` for language-neutral
     model/runtime contracts only (model manifest, `core_tools.yaml`, `steps.yaml`).

3. **Skill layout: data-first bundles with per-language implementation subfolders.**
   This is the consequential one. Since this plan was created, the
   `skill-package-loader` plan landed: skills are now real Python packages with
   `__init__.py`, split `handlers.py` / `steps.py` / `intents.py` / `_engine.py`
   modules, and package-relative imports.

   Two earlier drafts framed this as "data separate from code" (Draft B: YAML in
   `shared/skills/`, handlers in `packages/knaif/knaif_skills/<name>/`) vs. "data mixed
   into the code dir" (Draft A). **Both are rejected.** The adopted layout is a third
   option that satisfies the real requirements — YAML visible to *all* apps, a single
   canonical home per skill, and a self-contained download/ship unit:

   - **A skill is a self-contained bundle at root `skills/<name>/`.** The declarative
     YAML (`skill.yaml`, `tools.yaml`, `prompt.yaml`, `vocab.yaml`, `profiles/`, `data/`,
     `SPEC.md`) sits at the **top** of the bundle — never nested inside a language module,
     so any app (Python, Rust, Tauri, Swift, Kotlin) reads it without touching a
     `python/` or `rust/` subfolder.
   - **Implementations live in named subfolders:** `skills/<name>/python/` (a real
     package: `__init__.py`, `handlers.py`, `intents.py`, `steps.py`, `_engine.py`),
     `skills/<name>/rust/` (added at the native port), and future languages as peers.
   - **This is a single-root loader change, not the risky two-root one.** The current
     loader already loads the skill dir as a package with
     `submodule_search_locations=[str(skill_dir)]` (`skill.py`). The only change:
     point the **package root** at `bundle/python/` while the **data root**
     (`ctx.skill_dir`) stays at `bundle/`. Both are deterministic children of one bundle
     folder — there is no second *configurable* filesystem tree to resolve against.
     `skill_class:` (or `runtimes.python.handlers:`) resolves inside `python/`.
   - **Mandatory cleanup:** two data loads currently resolve via `Path(__file__).parent`
     and would break (they'd point into `python/`): `_engine.py`'s module-level
     `_VOCAB_PATH` and `documents/handlers.py`'s profile lookup. Both must move to
     `ctx.skill_dir` (the bundle root). This removes the last coupling of implementation
     code to its own `__file__` for finding declarative data — the correct end state.
   - **Naming (no `knaif.skills` package).** The framework skill machinery stays in the
     existing flat modules — `knaif.skill` (the `Skill.load()` loader; override-merge is
     added here) and `knaif.skill_base` (author base class). We deliberately do **not**
     introduce a `knaif/skills/` package: it would collide conceptually with the root
     `skills/` data directory and needlessly churn the ~20 `from knaif.skill import Skill`
     import sites. The clean split is **plural root `skills/` = bundle data; singular
     `knaif.skill` = loader module.** The rejected `knaif_skills` handler package is gone
     because per-skill `python/` code lives in the bundle and is loaded *by path* — never
     importable via `sys.path`, which keeps official skills repo-only and out of the
     published wheel (matching the locked Python-distribution policy).

   **Platform/app YAML overrides.** Apps that need platform-specific tweaks supply
   **delta files**, never forks. `Skill.load(bundle_dir, overrides=[...])` deep-merges
   base `skill.yaml` ← override(s). Overrides live **with the app**
   (e.g. `apps/knaif-ios/skill-overrides/<name>.yaml`), so the bundle stays clean and
   downloadable; a mobile-only `recommended_model` or a disabled tool is an explicit small
   delta owned by the app, not a copy of the skill.

## Skill Distribution Model (v1 decision + deferred direction)

How a *new* skill reaches a user depends on how much of its execution logic is genuinely
imperative. Skills fall into three tiers:

1. **Pure declarative** — execution is "substitute validated args into a declared command
   template and run a declared binary." No math, no branching.
2. **Declarative + bounded expressions** — needs some computation (arithmetic, a
   conditional, a probe result) but expressible in a small, non-Turing-complete DSL the
   runtime interprets in a sandbox.
3. **Imperative** — genuinely algorithmic (ffmpeg's probe-driven `_build_one_recipe`,
   target-size math, `_geometry_vf`). Needs a real language.

**v1 decision (locked): compile-time skills only.** Every native v1 skill (ffmpeg,
documents — both tier 3) is compiled into the binary; adding an imperative skill is a new
app release. Runtime/plugin skill loading stays out of scope (see *Out of Scope*).

**Deferred direction (separate future plan — not this one).** Tiers 1–2 do **not** need
an app release: they can ship as **signed, declarative-only data packs** downloaded at
runtime, executed by the runtime's generic engine. This is what makes a future skill
registry viable. It is explicitly deferred, but three constraints are recorded now so the
current layout and trait boundaries don't foreclose it:

- **Downloadable ⇒ declarative-only, for security.** A shipped app must never load
  *executable handler code* (`python/` or compiled `rust/`) downloaded at runtime — that
  is arbitrary-code execution and violates the same invariant as "never run model-emitted
  shell." Downloadable skills carry YAML + templates + `profiles/` + `data/` only.
- **Declarative skills need a runtime capability stdlib (the real prerequisite).** A
  skill with no code of its own must get its verbs — read/write file, run a *declared*
  program, probe — from a **trusted standard library of primitive steps owned by the
  runtime** (the grown-up form of today's `knaif.steps` / `core_tools`), compiled into
  *both* Python and Rust. It must **not** depend on a peer domain skill's imperative code
  (that reintroduces the code-distribution problem). Each primitive stays individually
  safe: `run_program` executes only a binary from the skill's declared `dependencies`
  with validated args — never a free-form command — under the existing
  `safety_category` / `dry_run` / `confirm` gates. `shared/runtime/steps.yaml` is reserved
  as this stdlib's home, and `knaif-skill-api` must expose primitives to both runtimes.
- **WASM is the anticipated escape hatch for downloadable *tier-3* skills** (imperative
  code in a capability sandbox), also deferred. Keep the `knaif-skill-api` boundary clean
  enough that a WASM host is a future backend, not a rewrite.

The template/expression DSL, the capability stdlib, WASM, and skill signing/distribution
are **all deferred to a dedicated future plan**. This plan only reserves space for them.

## Current Architecture the Native Runtime Must Mirror

The Rust core (`knaif-core`) must reproduce the deterministic pipeline as it exists
today, not as the older drafts described it. Reference modules in
`src/knaif/` (will move under `packages/knaif/knaif/`):

| Concept | Python source | Native obligation |
|---|---|---|
| Pipeline orchestration | `agent.py` (`CommandAgent`, `tool_map`, plan-preview / approval hooks) | `agent.rs` generic over an `LlmBackend` trait + handler registry |
| Parse / normalize / validate / optimize / resolve | `planner.py` (incl. `normalize_plan`, `summarize_plan`) | `planner.rs` — same normalization, validation, and variable binding |
| Registry + `ToolDef` | `registry.py` | `registry.rs` via `serde_yaml` |
| Prompt construction | `prompt.py` | `prompt.rs` |
| `Step` / `Intent` ABCs | `tool.py` | `Step`/`Intent` trait equivalents; `Intent.expand` → multi-step |
| Core control tools (clarify / reject / done / wait_for_confirmation) | `core_tools.py` + `core_tools.yaml` | merged automatically into every skill's tool map |
| Shared step library (`ResolveInputs`) | `steps/` + `steps.yaml` | shared, reusable native steps |
| Skill loading (`skill_class:` → package-aware tool map) | `skill.py`, `skill_base.py` | load YAML + bind to compiled-in native handlers |
| Variable binding / `$var.field` | `planner.py` + `docs/VARIABLE_BINDING.md` | identical resolution semantics |
| Safety gates | `safety_category`, `ctx.dry_run`, `ctx.confirmed`, `ctx.confirm()` | destructive requires confirm or dry-run |
| Missing-arg / NL clarify gates | `nl_clarify_gate.py` + `agent.py` | same deterministic clarify-before-error behavior |
| Chain auto-linking | `agent.py` (`_link_chain_intermediates`, `_forward_thread_reused_sources`) | same producer-output threading |
| Media vocab | `input_refs.py` (currently ffmpeg-leaning, see memory) | port carefully; this is a known leakage point |
| Retrieval | `registry.py::retrieve_tools` + `knaif.evalsuite retrieval` | port lexical/CJK tokenization + df-weighted scoring; keep recall gate |
| Eval | `knaif.evalsuite` (corpus/runner/scoring/chain/report/descriptor_analysis/retrieval) | add a `rust-cli` arm, do not reimplement |

ffmpeg-specific declarative data the native runtime must consume as-is:

- `profiles/platforms/*.yaml` and `profiles/quality/*.yaml`
- `arg_value_sets` (platform / quality / audio_format / container) in `skill.yaml`
- `safety.unsafe_phrases` in `skill.yaml`
- `recommended_model: qwen3-4b-v3` in `skill.yaml` (runtime selection policy)
- `vocab.yaml` (codec/encoder maps, aliases, presets, container/image/audio sets)
- `data/train.jsonl`, `data/eval.jsonl`, `data/eval_snapshot.json`, `data/safety_test.jsonl`

documents-specific declarative data is also a native v1 target:
`skill.yaml`, `tools.yaml`, `prompt.yaml`, `profiles/`, `data/*.jsonl`, dependency
metadata, and runtime model recommendations.

The core invariant is unchanged: the model emits only
`{ "plan": [ { "tool": "...", "args": {} } ] }`, and the native app **never executes
model-emitted shell** — only validated, declared tools dispatched to compiled-in
handlers (ffmpeg runs through detected `ffmpeg`/`ffprobe` binaries via subprocess).

## Target Repository Structure

```text
knaif/
├── justfile                         # root orchestrator (delegates to app justfiles)
├── mise.toml                        # pins python, uv, rust, cargo, cmake, just, node where needed
├── Cargo.toml                       # Rust workspace over crates/ + apps/knaif-cli
├── README.md                        # package + native product story
├── eval_backends.yaml
├── skills/                          # canonical skill bundles (data-first, cross-language)
│   ├── ffmpeg/
│   │   ├── skill.yaml                # YAML at the TOP of the bundle — visible to all apps
│   │   ├── tools.yaml
│   │   ├── prompt.yaml
│   │   ├── vocab.yaml
│   │   ├── SPEC.md
│   │   ├── profiles/**
│   │   ├── data/*.jsonl
│   │   ├── python/                   # Python implementation (a package; loaded by path)
│   │   │   ├── __init__.py
│   │   │   ├── handlers.py
│   │   │   ├── intents.py · steps.py · _engine.py · _deps.py
│   │   │   └── tests/
│   │   └── rust/                     # native implementation (added at the port)
│   │       ├── Cargo.toml
│   │       └── src/
│   ├── documents/                    # same bundle shape (skill.yaml … python/ rust/)
│   └── io/                           # stale; keep loadable, not native v1
├── packages/
│   ├── knaif/                       # only planned PyPI package: Python SDK/framework
│   │   ├── pyproject.toml
│   │   ├── uv.lock
│   │   ├── justfile
│   │   ├── knaif/                   # moved from src/knaif/ (no src/ layout)
│   │   │   ├── skill.py             # Skill.load() loader + override-merge (no knaif/skills/ pkg)
│   │   │   └── skill_base.py        # author-facing Skill base class
│   │   ├── tests/
│   │   ├── notebooks/
│   │   └── scripts/
│   └── training/                    # internal Python fine-tuning tooling, not PyPI
│       ├── pyproject.toml           # optional later; can start as scripts/modules
│       ├── training/
│       ├── configs/
│       └── scripts/
├── apps/
│   ├── knaif-cli/                   # native Rust CLI app, thin wrapper
│   │   ├── Cargo.toml
│   │   ├── release.toml             # which skills compile in per release
│   │   ├── justfile
│   │   └── src/
│   ├── knaif-desktop/               # future Tauri 2 + React, calls native runtime
│   ├── knaif-ios/                   # future SwiftUI shell, calls native runtime via bindings
│   └── knaif-android/               # future Kotlin/Compose shell, calls native runtime via bindings
├── crates/
│   ├── knaif-core/                  # planner, registry, prompt, validate, safety, optimize, steps
│   ├── knaif-models/                # ModelStore: manifest, download, checksum, delete, store resolver (NO inference deps)
│   ├── knaif-llm/                   # llama-cpp-2 backend (Vulkan/CUDA/Metal) + mock, behind a trait (depends on knaif-models); NO ollama
│   ├── knaif-skill-api/             # HandlerContext, Step/Intent traits, sandbox helpers
│   ├── knaif-skill-ffmpeg/          # native ffmpeg handlers + workflow expansion
│   └── knaif-skill-documents/       # native documents handlers + workflow expansion
├── shared/                          # no-code, language-neutral contracts (skills live at root skills/)
│   ├── models/
│   │   └── model-manifest.yaml      # URLs/checksums + desktop/mobile recommendations
│   └── runtime/
│       ├── core_tools.yaml
│       └── steps.yaml                # reserved home for the future capability stdlib
├── packaging/
│   ├── windows/                     # WiX 4 (MSI) + portable zip
│   ├── macos/                       # POST-V1: signed/notarized tarball (deferred)
│   └── linux/                       # AppImage + tarball
├── third_party/
│   └── llama.cpp/                   # only if llama-cpp-2 stops meeting release needs
├── docs/
│   ├── ARCHITECTURE.md              # gains "Dual-Runtime" section
│   ├── RUST_PORT.md                 # new: Rust trait boundaries
│   ├── PACKAGING.md                 # new
│   └── plans/                       # decisions recorded as dated plans (Phase 0)
├── .github/workflows/
│   ├── ci-py.yml
│   ├── ci-rust.yml
│   ├── eval-parity.yml
│   └── release.yml
├── models/                          # dev-only GGUFs, gitignored, not packaged
├── sandbox/                         # dev-only, not packaged
└── eval_results/                    # dev-only, not packaged
```

## Command Surface

`just` is the human-facing runner; `mise` provisions/pins tools. Root recipes delegate
to per-app justfiles.

```text
just bootstrap            # provision tools via mise; print guidance when it can't
just check               # py checks + native checks
just py <recipe>         # cd packages/knaif && just <recipe>
just train <recipe>      # cd packages/training && just <recipe>
just rs <recipe>         # cargo/native recipes
just test-py             # existing Python test path
just test-native         # cargo test --workspace
just build-native
just native-mock -- --help     # mock backend (no llama.cpp); dev/plumbing/CI
just native ffmpeg "<req>"     # real llama.cpp inference; manual twin of `just cli`
just eval-parity         # corpus through python-agent + rust-cli, diff scores
just package <win|linux>       # macos is post-v1
just release <version>
```

Backends: `uv` (Python), `cargo` (Rust), llama.cpp build via `llama-cpp-2` first,
platform packaging tools behind `just package`.

---

## Phases

Inline `- [ ]` checkboxes track progress. **First milestone = Phases 0–4** (repo shape +
native skeleton); inference, ffmpeg execution, installers, and parity follow once the
layout is stable.

### Phase 0 — Decisions (no file moves)

Record each decision as a dated plan under `docs/plans/` (the project keeps decision
records as plans, not ADRs — see CLAUDE.md).

> **Done 2026-07-03.** Recorded as one consolidated index rather than ~18 near-duplicate
> files. That index was **deleted 2026-07-23**: it restated the checklist below with a column
> of pointers back to this plan, declared this plan the single source of truth, and had drifted
> to pre-restructure path names. The decisions themselves are the checklist below; their
> rationale, measured data, and rejected alternatives are in the Locked/Resolved sections and
> the session log. No source files moved.

- [x] `monorepo-products` — two products (Python authoring + native release).
- [x] `monorepo-layout` — root `skills/*` bundles, `packages/knaif`, `packages/training`, `apps/*`, root `crates/*`, and no-code `shared/*`.
- [x] `native-runtime-strategy` — Python-free Rust core, no embedded Python.
- [x] `skill-bundling` — compile-time skill selection (cargo features + `release.toml`).
- [x] `native-v1-skill-scope` — FFmpeg + documents are native v1 targets; `io` is excluded.
- [x] `model-lanes` — desktop/native CLI recommends `qwen3-4b-v3`; mobile recommends `qwen3-1.7b-sft-v3-flat-q6`.
- [x] `llama-cpp-integration` — start with `llama-cpp-2`; keep vendored submodule as fallback.
- [x] `gpu-backend-strategy` — one artifact per OS bundles CPU + Vulkan + CUDA (Metal on Apple); pure runtime device selection + CPU fallback; no compile-time variants, no install-time backend download. Decided from measured CUDA-vs-Vulkan data + verified multi-backend build (Session log).
- [x] `inference-runtime` — shipped CLI/UI/mobile use only embedded llama.cpp; Ollama confined to the Python dev/eval path (no Rust Ollama backend).
- [x] `installers-models-dependencies` — installers, model manifest, dependency detection, PATH consent, shared model store.
- [x] `model-management` — shared `ModelStore` in its own `knaif-models` crate (no inference deps; list/pull/update/verify/delete/delete-all), one OS-conventional store, embedded identically by CLI and future UI; install-time seeding optional and non-exclusive.
- [x] `desktop-ui-embeds-engine` — desktop UI is a standalone Tauri app embedding the engine crates directly (one app), not a wrapper shelling out to the native CLI.
- [x] `skill-bundle-layout` — root `skills/<name>/` bundles: YAML at bundle top, per-language impl in `python/` / `rust/`; single-bundle loader; app YAML overrides as merge deltas.
- [x] `skill-distribution-tiers` — v1 compile-time only; downloadable declarative-only skills, capability stdlib, DSL, WASM, and signing deferred to a dedicated future plan.
- [x] `product-surfaces` — native runtime is library-first; CLI is first product;
      future Tauri desktop and native mobile shells share runtime/skills, not UI.
- [x] `python-skill-distribution` — `packages/knaif` is the only planned PyPI package;
      official built-in skills remain repo-only product/reference assets.
- [x] Add a short "Dual-Runtime (future)" section to `docs/ARCHITECTURE.md` pointing here.

**Verify:** no source files moved; `just check` (current) still green.

### Phase 1 — Root workspace & command surface (no behavior change)

- [x] Add `packages/knaif/`, `packages/training/`, `apps/knaif-cli/`, and root `crates/` placeholder READMEs describing ownership + migration state.
- [x] Add `packaging/{windows,linux,macos}/` README placeholders.
- [x] Add root `skills/` and `shared/{models,runtime}/` placeholders; document that skill bundles live at root `skills/<name>/` (YAML at top, impl in `python/`/`rust/`) and that `shared/` holds no-code contracts only.
- [x] Add `mise.toml` pinning python, uv, rust, cargo, cmake, just, and node where desktop work later needs it.
- [x] Split the root `justfile` into grouped recipes (`check-py`, `test-py`, `lint-py`, `type-check-py`, `check-native`, `test-native`, `build-native`); keep `just check` / `just test` working as aggregates that skip native cleanly until native sources exist.
- [x] Add `just bootstrap` (mise where possible, actionable guidance otherwise).

**Verify:** `just --list` shows old + new recipes; `just check-py` runs the existing
lint/type/test path; `just check` skips native with a clear message.

### Phase 2 — Move Python package into `packages/knaif/`

> **Done 2026-07-03 (core moves).** The installable package now lives in `packages/knaif/`
> as a **uv workspace member**; the full suite (1509 passed, coverage 83.5%), type-check,
> and lint are green from the repo root. Two implementation decisions (see below) refine
> the plan's literal steps; three secondary moves are **deferred by decision** to a focused
> follow-up.
>
> - **uv workspace, not a full pyproject relocation.** A thin root `pyproject.toml`
>   (`[tool.uv.workspace]` + shared ruff/black/mypy/pytest/coverage config) keeps `uv run`,
>   lint, and pytest working from the repo **root** — which the evalsuite and many tests
>   require, since they resolve data via cwd-relative paths (`src/skills/…`,
>   `eval_results/…`). The installable-package config lives in `packages/knaif/pyproject.toml`;
>   **`uv.lock` stays at the root** (the workspace lock), overriding the plan's "move uv.lock
>   into packages/knaif." `ruff` needed `known-first-party = ["knaif"]` (the old `src/` layout
>   used to signal that automatically).
> - **Skill-discovery gap bridged by a resolver, not a hardcode.** `_discover_skills_root()`
>   honors `$KNAIF_SKILLS_ROOT`, else walks up to the first dir with a `*/skill.yaml` child —
>   so it finds `src/skills` now and root `skills/` after Phase 3 with **no further edit**.
> - **Test path-anchors converted to cwd-relative** (31 sites across ~13 files): the moved
>   tests used `Path(__file__).parent.parent` as "repo root", which broke two levels deeper.
>   Two tests that read moved source now point at `packages/knaif/knaif/`.

- [x] Move `pyproject.toml`, Python README content into `packages/knaif/`. *(Package
      `pyproject.toml` moved; a thin workspace-root `pyproject.toml` + `uv.lock` intentionally
      stay at repo root — see note above. `packages/knaif/README.md` already describes the package.)*
- [x] Move `src/knaif/` → `packages/knaif/knaif/` (drop the `src/` layout).
- [x] Move Python framework tests → `packages/knaif/tests/`.
- [ ] **DEFERRED** — Move Python-side notebooks and scripts → `packages/knaif/notebooks/` and
      `packages/knaif/scripts/`. `scripts/` (gen_skills writes root README + reads `src/skills`;
      bench/agent-vs-knaif are native/experiment tools) and `notebooks/` carry repo-root path
      assumptions; relocating them is a focused follow-up, better sequenced near crates/training work.
- [ ] **DEFERRED** — Move `training/` → `packages/training/`. (Loose scripts, not a package;
      nothing imports it. Moves in the same follow-up as scripts, since `scripts/gen_train.py`
      is its companion.)
- [x] Point root justfile recipes at `packages/knaif` (explicit paths: install/`-e`, `mypy
      packages/knaif/knaif/`, `pytest packages/knaif/tests`). *(No `packages/training` recipes
      yet — training move deferred.)*
- [x] Preserve `import knaif` from an editable install. *(`uv pip install -e packages/knaif`;
      `uv run` from root resolves the workspace member.)*
- [x] **Keep skill discovery working across the Phase 2→3 gap** — done via
      `_discover_skills_root()` (env override + walk-up); `list_skills()` returns
      `['documents', 'ffmpeg']`, resolver points at `src/skills`.
- [x] Update `README.md`, `AGENTS.md`, `CLAUDE.md`, and docs path references.

**Verify:** ✅ `just test-py` (1509 passed, 1 skipped, coverage 83.5% ≥ 80%); ✅
`uv run pytest` from repo root; ✅ `uv pip install -e packages/knaif` then
`python -c "from knaif import create_agent, list_skills; print(list_skills())"` →
`['documents', 'ffmpeg']` (discovery resolves across the gap); ✅ `just type-check`,
`gen-skills-check`, and `ruff` (excluding the pre-existing untracked `scripts/agent_vs_knaif`).

### Phase 3 — Reshape skills into root `skills/<name>/` bundles

> **Done 2026-07-03.** All three skills are now root-`skills/<name>/` bundles (YAML+data at
> the top, Python in `python/`), the loader is bundle-aware, and the `__file__`
> data-coupling is cleaned up. The three follow-on sub-features **have since landed too**
> (commits: metadata `15ae3e2`, overrides `f943538`, shared-runtime — this batch): full
> suite green at **1527 passed, coverage 83.5%**.

- [x] Move each skill (ffmpeg, documents, stale `io`) into a bundle at root `skills/<name>/`:
      declarative YAML (`skill.yaml`, `tools.yaml`, `prompt.yaml`, `SPEC.md`, `profiles/`,
      `vocab.yaml`, `data/`) — plus `eval/`, `notebooks/` — at the **top** of the bundle.
- [x] Move each skill's Python implementation into `skills/<name>/python/` (real package:
      `__init__.py`, `handlers.py`, `intents.py`, `steps.py`, `_engine.py`, `_deps.py`,
      `tests/`). Repo-only, excluded from the wheel (`packages.find` ships only `knaif*`).
- [x] Extend `Skill.load()` / `create_agent()`: data resolves from the bundle root
      (`ctx.skill_dir = skills/<name>/`); the handler package loads from `bundle/python/`
      (`submodule_search_locations=[python/]`, module keys named by the bundle for
      uniqueness). Single-bundle change; **backward-compatible** with the flat layout.
- [x] **Mandatory `__file__` cleanup.** ffmpeg `_engine.py` `_VOCAB_PATH` + `_PACKAGE_PROFILES`
      now resolve from a `_BUNDLE_DIR` (`python/`'s parent = bundle root); `documents`
      `handlers.py` artifact-runner uses the bundle root. Intent-time loaders
      (`intents.py` / `_reporting.py`) already take a `skill_dir` kwarg = the bundle root
      (no `__file__`); verified by the passing summarizer tests. Grep confirms no skill data
      lookup resolves into `python/`.
- [x] Add `Skill.load(bundle_dir, overrides=[...])` deep-merge + removal schema
      (`disabled_tools` list; `null` sentinel removes a key). Core tools can't be disabled;
      unknown tool names are rejected. Overrides only tune/disable — handler code always
      loads from the bundle's `python/`. *(commit `f943538`)*
- [x] Keep framework skill machinery in flat `knaif.skill` / `knaif.skill_base` (no
      `knaif/skills/` package); per-skill `python/` code is loaded by path, not via `sys.path`.
- [x] Preserve the Python distribution policy: the wheel is framework-first; `packages.find`
      includes only `knaif*`, so root `skills/` bundles are never packaged.
- [x] Add `runtimes:` metadata to each active `skill.yaml` (python handlers ref + native
      status/crate), exposed on `Skill.runtimes`. *(commit `15ae3e2`)*
- [x] Add ffmpeg external-dependency metadata (`ffmpeg`, `ffprobe`, required) — `Skill.external_tools`. *(commit `15ae3e2`)*
- [x] Add documents external-dependency metadata (`ghostscript`/`gs`, `libreoffice`/`soffice`,
      `tesseract`, all optional). *(commit `15ae3e2`)*
- [x] Move `core_tools.yaml`/`steps.yaml` into `shared/runtime/` (canonical, cross-language).
      `core_tools.yaml` is import-critical, so a **byte-identical copy ships in the wheel**
      next to the module; the loader (`_resolve_runtime_yaml`) resolves the packaged copy
      first, else `shared/runtime/`. A **drift-guard test** + `just sync-runtime` keep the two
      in sync (rather than a build-time copy hook — simpler + cross-platform on this
      setuptools/Windows stack). `steps.yaml` is reference-only (not loaded by Python) and
      lives solely under `shared/runtime/`. Verified via `uv build`: wheel contains
      `core_tools.yaml`, excludes `steps.yaml` and all `skills/`. *(shared-runtime commit)*
- [x] Update tests that import skill internals directly; prefer the public loader API.
      *(Audited: skill tests use `Skill.load` / `from_skill`, not direct handler imports.)*
- [x] **Configure tooling for the `skills/<name>/python/tests/` layout:** `pytest` testpaths
      now `["packages/knaif/tests", "skills"]`; `pyright` extraPaths repointed; `ruff` lints
      the moved modules. *(mypy still targets `packages/knaif/knaif/` only — skill handler
      code is not type-checked, unchanged from before; broader mypy coverage is a later task.)*

Example `skill.yaml` additions:

```yaml
runtimes:
  python:
    handlers: handlers:FFmpegSkill   # resolved inside skills/ffmpeg/python/ (loaded by path)
  native:
    status: supported
    crate: knaif-skill-ffmpeg
dependencies:
  external_tools:
    - name: ffmpeg
      required: true
      commands: [ffmpeg, ffprobe]
      install: { windows: winget, macos: brew, linux: package_manager }
```

**Verify:** `just test-py`; ffmpeg + documents skill tests green; editable checkout
builds active agents from root `skills/<name>/` bundles (data at bundle root, handlers
loaded from `python/`); vocab/profiles resolve via `ctx.skill_dir` (no `__file__`
coupling); an override delta merges over a base `skill.yaml`; bare PyPI-package docs make
clear that official skills are not bundled for third-party use.

### Phase 4 — Native Rust workspace skeleton

> **Done 2026-07-04 (core slice).** Cargo workspace + all six crates + `knaif-cli` build,
> `knaif --version` and `knaif skills list` work (the latter reads the bundle `skill.yaml` +
> Phase 3 `runtimes:` metadata off disk). Rust toolchain provisioned via mise (`rust 1.96.0`).
> `run`/`plan --json` command bodies and `ci-rust.yml` are **deferred by decision** to a
> focused follow-on (CI consolidated later; no Python CI exists yet either).

- [x] Add root `Cargo.toml` workspace + `rust-toolchain.toml` (pinned `1.96.0`) + `rustfmt.toml`; `/target` gitignored.
- [x] Create root crates `knaif-core`, `knaif-models`, `knaif-llm`, `knaif-skill-api`,
      `knaif-skill-ffmpeg`, `knaif-skill-documents` (`knaif-llm` → `knaif-models`;
      `knaif-models` has no inference deps; skill crates → `knaif-skill-api` → `knaif-core`).
      Five are doc-only skeletons; `knaif-core` implements skill discovery.
- [x] Create `apps/knaif-cli/` binary crate (`knaif`) depending on `knaif-core` (thin wrapper).
- [x] `cargo fmt` / `cargo clippy -D warnings` configs; `just rs <args>` + `check-native` /
      `test-native` / `build-native` / `run-native` wired to cargo (replacing the Phase-1 skips).
- [x] Implement `knaif --version`.
- [x] Implement `knaif skills list` reading the bundle `skill.yaml` files (name, description,
      `runtimes.native.status`/`crate`); ffmpeg + documents show native-supported, io hidden
      unless `--include-stale`. **Decision:** read the declarative YAML off disk (single source
      of truth) rather than a compiled-in list; compile-time *selection* is a Phase 9 concern.
- [ ] **DEFERRED** — `knaif run ffmpeg "<request>"` skeleton (mock / "model not configured").
- [x] `knaif plan --skill <X> --json` (parity-harness interface) — landed in the Phase 5a
      slice (commit `d362f0a`): validates the skill, runs the selected backend (mock →
      `{"plan": []}`), emits the plan envelope as JSON. Phase 6 fills in real inference/validation.
- [ ] **DEFERRED** — `ci-rust.yml` (build + clippy + test on Windows/Linux). Consolidate all CI
      (Python + Rust + parity) in one pass — Phase 10 owns CI/governance.

**Verify:** ✅ `cargo test --workspace` (knaif-core skill-listing test green); ✅
`just build-native`; ✅ `just run-native -- --version` → `knaif 0.1.0`; ✅
`just run-native -- skills list` shows ffmpeg + documents (native crates) and io under
`--include-stale`; ✅ `cargo fmt --check` + `cargo clippy -D warnings` clean.

### Phase 5 — `knaif-llm` backends + llama.cpp

> **Phase 5a done 2026-07-04 (offline slice).** The backend-agnostic trait, mock backend,
> and the entire `ModelStore` + manifest + CLI surface landed, fully offline-tested (commits
> `71f59b8`, `bb86587`, `d362f0a`; plus the `plan --json` skeleton from Phase 4 deferral).
>
> **Phase 5b — llama.cpp spike DONE 2026-07-04 (commits `1eedaec`, `1d5dd90`, `a204237`).**
> Real local inference works end-to-end on Windows via `LlamaCppBackend`, proven on all three
> backends (qwen3-1.7b, **29/29 layers offloaded** on GPU):
> - **CPU** (feature `llama`) — gemma-1b, coherent JSON completion.
> - **CUDA** (feature `cuda`) — RTX 5080, Blackwell / compute capability 12.0.
> - **Vulkan** (feature `vulkan`) — RTX 5080; Vulkan enumerated **both** GPUs (NVIDIA + an
>   integrated AMD Radeon), the cross-vendor case the runtime device-selection design targets.
>
> The toolchain integration (the real unknown) is validated: MSVC (VS 2026) + cmake + libclang
> build llama.cpp from source. **Build/runtime prereqs discovered (for CI/packaging):**
> - **libclang** (LLVM) via `LIBCLANG_PATH`→LLVM/bin — bindgen needs it for every `llama` build.
> - **CUDA:** `CUDA_PATH` must be a Windows-path (MSBuild's CUDA integration); runtime needs
>   `cudart64_13.dll`/`cublas64_13.dll`, which CUDA 13 keeps in `bin/x64/` (ship alongside the
>   exe — Phase 9). CUDA-kernel compile is CPU-heavy (~183 `.cu`, ~24 min) → cap
>   `CMAKE_BUILD_PARALLEL_LEVEL` for thermals.
> - **Vulkan:** `llama-cpp-2` 0.1.150 doesn't forward the sys crate's `vulkan` feature → take a
>   direct `llama-cpp-sys-2` dep to flip it. Build **must use the Ninja generator** (in a VS Dev
>   Shell, `CMAKE_GENERATOR=Ninja` + `VULKAN_SDK`) — under MSBuild the `vulkan-shaders-gen`
>   ExternalProject fails. The Vulkan build is **light (~1 min, no cap needed)**.
>
> **Still open (deferred):** the single-artifact CPU+Vulkan+CUDA *bundle* with runtime device
> selection (`GGML_BACKEND_DL`) — see backend-selection below. All three backends are now
> individually proven; the remaining work is bundling them into one binary + the device picker.

- [x] Define the `LlmBackend` trait: `generate_plan(prompt: &str) -> Result<String>`.
- [x] `MockBackend` only besides llama.cpp — **no `OllamaBackend`**. `backend_from_env`
      honors `KNAIF_LLM_BACKEND` (mock default; `llama` returns a clear "not built yet" error
      rather than silently mocking).
- [x] `LlamaCppBackend` via `llama-cpp-2` (feature `llama`; `cuda`/`vulkan` add that GPU
      backend). Greedy decode via the non-deprecated `token_to_piece_bytes`. **Proven on CPU,
      CUDA, and Vulkan** (see the Phase 5b note; Vulkan needs a direct `llama-cpp-sys-2` dep +
      the Ninja generator). The **one-build-bundles-all-backends + runtime device selection**
      target is the backend-selection spike below (still deferred).
- [x] Shared model-store resolver: `$KNAIF_MODELS_DIR` → else `~/.knaif/models` on **every**
      platform (uniform, like `~/.aws`/`~/.ssh`; Windows profile root, not Roaming — decided
      2026-07-07). One resolver (`knaif_models::store_dir`) for CLI/installer/UI.
- [x] Model path resolution: `ModelStore::resolve_model` (CLI → config → recommended →
      manifest default) + `path_for` (raw path or named model in the store).
      `shared/models/model-manifest.yaml` records the desktop/CLI `qwen3-4b-v3` and mobile
      `qwen3-1.7b-sft-v3-flat-q6` recommendations (URL/sha256 are TODO until hosted).
- [x] `ModelStore` API in `knaif-models` (no inference deps): `list`, `verify`, `delete` /
      `delete_all`, and `pull`/`update` (download + SHA-256 verify + atomic install) behind an
      injectable `Fetcher`. The **real HTTP fetcher is deferred** with the spike, so `pull`
      logic is offline-tested with a fake fetcher.
- [x] CLI verbs: `knaif models list` / `verify` / `rm <name>` / `rm --all` / **`pull` / `update`
      (BUILT 2026-07-04, leftover #2)** via the `ureq` `HttpFetcher`; host = HF `blackdeep/knaif`.
      Plus `scripts/publish_model.py` for admin upload. Outstanding owner action = run it to upload
      the GGUFs and turn manifest `url`/`sha256` from `TODO` to real.
- [x] Tiny inference proof: load model, run one prompt, return text, release — a
      `$KNAIF_TEST_GGUF`/`$KNAIF_TEST_NGL`-gated test (`inference_produces_text`), green on CPU + GPU.
- [x] **DONE 2026-07-04 (leftover #3, `bc8803f`).** Pure fence-strip/`<think>`-strip/brace-extract
      ported to `knaif-core` `extract.rs` and wired into `knaif plan` (`extract_json → parse_plan`);
      the validation-retry loop still rides with the `CommandAgent` port.

**Backend selection — one artifact, runtime device selection.** **DECIDED 2026-07-04 (leftover #1):
the single-artifact bundle below is DEFERRED to Phase 9 (packaging); ship per-target CPU + Vulkan +
optional CUDA meanwhile** (see the top-of-plan callout). Verified: llama.cpp builds
multiple backends together (`GGML_CUDA` + `GGML_VULKAN`) and `llama-cpp-sys-2` sets each
from an independent cargo feature, so they are **not** mutually exclusive. There is **no
compile-time variant matrix and no install-time backend download** — the earlier three-layer
"variant" framing was based on a false one-backend-per-build assumption and is dropped.

- [ ] **Compile-time — bundle all applicable backends into one build.** Windows/Linux: CPU +
      Vulkan + CUDA. Apple: CPU + Metal (Metal is the only inherently Apple-only,
      compile-time backend). No per-GPU variants.
- [ ] **Runtime — the *only* selection layer.** On start: enumerate devices → **validate a
      usable one** → pick best (prefer the discrete GPU; Vulkan vs CUDA per policy) → else
      CPU. Honor `KNAIF_LLM_BACKEND` and `n_gpu_layers`. No app-time download of executable
      backends (that would be an auto-updater/trust problem — out of scope; the app only ever
      downloads *models*, which are data).
- [ ] **Detection checks for a *usable GPU device*, not just a loadable library** (learned on
      WSL: `libvulkan` loads but only exposes lavapipe/CPU). NVIDIA: NVML / `libnvidia-ml` or
      PCI vendor id `0x10DE` (driver, not the CUDA toolkit). Vulkan:
      `vkEnumeratePhysicalDevices` requiring a non-CPU device type (reject
      `lavapipe`/`llvmpipe`). Metal: `MTLCreateSystemDefaultDevice()` non-null.
- [ ] **Spike: how backends are bundled — `GGML_BACKEND_DL` is effectively required.** Each
      `ggml-*` backend must be a `dlopen`'d lib loaded only when its device is present. **Do
      not statically link the CUDA backend into the shared artifact:** it adds a load-time
      dependency on the NVIDIA driver (`libcuda`/`nvcuda.dll`) that makes the process **crash
      at startup on AMD/Intel/no-GPU machines, before device selection runs** — so static
      linking is *not* a viable fallback for a mixed-vendor single artifact. If `llama-cpp-2`
      doesn't expose `GGML_BACKEND_DL` cleanly, escalate to **(a) vendoring
      `third_party/llama.cpp` via CMake** or **(b) install-time / minimal-launcher backend
      selection** — decide in this spike. The runtime *selection model* is unchanged either way.

**Verify:** native tests run without a model via mock; CPU-only inference works on at
least one dev machine; missing model → helpful first-run-setup error; `models pull` then
`models list` shows it in the shared store; `models rm --all` empties the store; a second
app surface pointed at the same store sees the same models.

### Phase 5c — Native agent: prompt + real-inference wiring + repair

> **NEW (2026-07-06).** The glue that was tucked under "the CommandAgent port" and never
> scheduled: make `run`/`plan` produce plans from a **real llama.cpp model**, not the mock/
> `KNAIF_LLM_MOCK_RESPONSE` seam. The `LlamaCppBackend` primitive is done (Phase 5b spike); this
> phase ports the prompt, wires model selection, and adds the validation-retry loop.

- [x] **Prompt construction (`knaif-core` `prompt.rs`) DONE (2026-07-06).** Port of `build_prompt`
      (+ `_SYSTEM_HEADER`/`_EXAMPLES` defaults) and the skill `prompt.yaml` loader (`system_header` +
      `_render_examples`). Single-shot (no history/chain re-prompt yet). Golden-tested. *(Minor
      divergences from Python, both prompt-only, not graded byte-for-byte: tool listing is alphabetical —
      `Registry` is a `BTreeMap`, not tools.yaml insertion order; example JSON is compact.)*
- [x] **Chat template + backend selection DONE (2026-07-06).** `knaif-llm::to_chatml` (ChatML `<|im_start|>`
      + `/no_think`) + `backend_for(model)` (a model path → `LlamaCppBackend` under feature `llama`, else a
      clear "rebuild with --features llama"). CLI `--model` + `llama`/`pdfium` feature passthroughs;
      `build_plan` now: `load_prompt_yaml` → `build_prompt` → `to_chatml` → `backend_for` → infer → extract
      → validate.
- [x] **Repair loop + model-name resolution DONE (2026-07-06, `1968fa6`).** `validator_feedback_prompt`
      (port of `_validator_feedback_prompt`) + CLI `infer_with_repair` (port of the `repair_invalid_plans`
      loop: on parse/validate failure, re-infer once with the error fed back, use the corrected plan if it
      validates, else the original error; gated to real models). `resolve_model_path` — `--model` takes a
      GGUF path **or** a manifest/installed name via `ModelStore::path_for`. 3 scripted-backend tests.
- [x] **REAL inference verified end-to-end (2026-07-06).** Release `--features llama` build + a real fine-
      tuned GGUF (`qwen3-1.7b-ffmpeg`): `run ffmpeg --model … "compress interview.mp4 for email under 20 MB"`
      → the model produced a plan the native runtime validated + expanded into a real `ffmpeg` command. The
      smoke test also **found + fixed a bug** (`llama.rs` set `n_ctx` but not `n_batch` → the large planner
      prompt overflowed the 512 default and asserted; now `n_batch = n_ctx`, `$KNAIF_N_CTX` override).
      **Phase 5c COMPLETE — the native runtime is self-sufficient (no canned plans).**
- [x] **Verify DONE (2026-07-06).** Golden prompt matches Python (modulo the noted alphabetical-tool /
      compact-JSON divergences); `run ffmpeg --model <gguf> "<req>"` produced a valid plan on a real
      fine-tuned GGUF end-to-end; the repair loop recovers a first-try invalid plan (3 scripted-backend
      tests); the mock path (`KNAIF_LLM_MOCK_RESPONSE`) is unchanged. *(The earlier duplicate "Repair
      loop" checkbox was folded into the "Repair loop + model-name resolution DONE (`1968fa6`)" item
      above — it was the same work listed twice.)*

### Phase 6 — Native core runtime contract

> **In progress 2026-07-04 (slices 1–4, commits `247c976`, `fbc053a`).** `knaif-core` now has
> `registry` (ToolDef/ArgSchema/`load_registry`) and `planner` (`parse_plan`,
> `validate_arg_by_schema`, `validate_step`, `validate_plan`, `resolve_args`). `knaif-cli
> plan --skill X --json` loads the skill's `tools.yaml` ∪ `core_tools.yaml` and runs the real
> parse → validate path (mock backend still supplies the plan). 13 knaif-core tests green.

- [x] Port plan-envelope parsing + registry loading (`tools.yaml`/`core_tools.yaml`). *(prompt
      loading + `skill.yaml` field parsing still to do)*
- [x] Port validation: unknown tool, required/optional args, `any_of_args`, unsupported (extra)
      args, per-arg schema (string/bool/int/number/array/enum + min/max), output-variable syntax.
      *(arg-key aliases/coercions + `normalize_plan` and `safety_category`/readonly *gating* still to do)*
- [x] Port variable binding + dotted `$var.field` resolution (`resolve_args`) + var-before-assign.
- [x] Embed + wire core control tools (`core_tools.yaml`) — merged into the registry by the CLI.
      *(shared `steps/`/`ResolveInputs` still to do)*
- [x] Port retrieval scoring/tokenization + full NFD/diacritic normalize + CJK n-grams
      (`retrieve_tools`) — multilingual parity on the real ffmpeg registry (commit `753a741`).
- [x] `validate_step` **sandbox path resolution** (`path`/`src`/`dst`, `../` escape → error;
      open-mode normalize) + `file_type`/`pattern`/`recursive` checks — full parity (commit `56bb9d2`).
- [x] Port `normalize_plan` (output promotion, input/inputs coercion, arg-key aliases, scalar +
      enum coercion), `apply_defaults`, `optimize_plan` (commit `2980ff6`). CLI `plan --json` now
      runs the full pipeline: parse → normalize → apply_defaults → validate.
- [x] **Golden parity fixtures** (`shared/parity/planner_cases.json`) run through both the Rust
      (`crates/knaif-core/tests/parity.rs`) and Python (`test_planner_parity.py`) pipelines —
      14 cases, same valid/invalid + error substring. **Python == Rust proven** (commit `fc38687`).
- [ ] **→ Phase 7** (need the execution/agent layer): dry-run + confirmation gates for
      destructive tools; deterministic clarify gates; chain auto-linking; prompt construction
      (`build_prompt`). Moved because they require handlers/`HandlerContext`/inference, which land
      with the native skills — the *deterministic validation core* (this phase) is complete.

**Verify:** ✅ Rust tests mirror `test_planner.py`/`test_registry.py` behavior; ✅ invalid plans
fail before dispatch (parity fixtures); ✅ both loaders accept the same shared `tools.yaml`/
`core_tools.yaml`. *(destructive dry-run/confirm gating verifies in Phase 7 with execution.)*

> **Phase 6 core complete 2026-07-04.** The deterministic runtime contract is ported to
> `knaif-core` and proven at parity with Python: registry loading, `parse_plan`, full
> `validate_step`/`validate_plan` (incl. sandbox boundary), `resolve_args`, the transform trio
> (`normalize_plan`/`apply_defaults`/`optimize_plan`), and retrieval. `knaif plan --skill X --json`
> runs the real pipeline. Remaining items are execution-layer and move to Phase 7.

### Phase 7 — Native FFmpeg + documents skills

- [x] **Workflow expansion (`Intent.expand` equivalent) DONE for single-recipe intents (2026-07-05).**
      [knaif-skill-ffmpeg `run.rs`](../../skills/ffmpeg/native/src/run.rs) `expand_dry_run`
      maps a validated intent step (`tool` + `args`) → engine `Options` (porting the `_engine.py`
      arg coercions: `_coerce_inputs`, `_quality_from_crf`, `container/audio/image_format_from_output`,
      `_coerce_dimension`/`_bitrate`, the convert codec-in-container fixup) + resolves platform/quality
      profiles (incl. the `crf N` → nearest-profile CRF override), then per input probes → `build_one_recipe`
      → `build_flags` → `render_command`. Collapsed into one native pass (no model-visible intermediate
      steps — the `run` verb owns the workflow). Covers `prepare_for_platform`, `compress_video`,
      `convert_video`, `resize_video`, `trim_video`, `extract_audio`, `create_thumbnail`, `strip_audio`,
      `adjust_speed`, `adjust_volume`, `rotate_video`; **unknown platform → `Clarify`**. **14 tests, each
      argv pinned to Python-engine ground truth.**
- [x] **`concat_video` (join / `-filter_complex concat`) DONE (2026-07-05).**
      [knaif-skill-ffmpeg `concat.rs`](../../skills/ffmpeg/native/src/concat.rs) `concat_filter_args`
      + `build_concat_command` port `_concat_filter_args` / `RunConcatStep`: the one intent that joins N
      inputs into a *single* command. Normalizes (scale + `fps=` + `aresample`, `anullsrc` silence-pad for
      audio-less inputs) when any input mismatches the target or a target is forced, else the minimal
      concat; `-map [outv]` (+ `[outa]` when any input has audio). `run.rs::expand_concat` assembles the
      ordered list (`inputs`, or `base` ++ `append`), resolves+boundary-checks the output (default
      `combined.mp4`), and shares the dry-run/execute probe policy. Needed `fps` on `Probe` (+ `parse_fps`
      in `summarise_probe`, `30.0` in `dummy_probe`). **5 concat tests pinned to Python ground truth**;
      proven end-to-end: `run ffmpeg --yes` joins a 1s + 2s clip → 3.04s output.
      *(Target-size math is a non-feature — `target_size_mb` is carried as recipe metadata but never
      computed into a bitrate in Python either, so Rust already matches.)*
- [x] **Tier 1 DONE (2026-07-04, `8ed5724`).** Consume the shared **`vocab.yaml`** lookup
      tables + `profiles/{platforms,quality}/` directly as typed Rust
      ([knaif-skill-ffmpeg](../../skills/ffmpeg/native/src/): `Vocab`, `PlatformProfile`,
      `QualityProfile`, `FfmpegData::load`) — the Rust runtime shares the data instead of
      re-hardcoding it.
- [x] **Tier 2 (DSL) — REJECTED, decided with the port (2026-07-04, `32e70e7`).** A declarative
      command-template DSL would have to express conditionals, arithmetic (`pts = round(1/speed)`),
      enum→filter mappings (`angle 90 → transpose=1`) and conditional scale-expression selection —
      a whole language needing Python *and* Rust interpreters plus its own parity surface, strictly
      more machinery than porting `_build_flags` imperatively. vocab.yaml's own rule (data in YAML,
      algorithm in code per runtime) points at Tier 3.
- [x] **Tier 3 — imperative engine port COMPLETE (2026-07-05).** The full recipe pipeline is in
      `knaif-skill-ffmpeg` `engine.rs`: `geometry_vf` (`32e70e7`), `coerce_volume_level` (`073a82c`),
      `build_flags` (all 12 modes) + `Recipe`/`Video`/`Audio`/`Trim` + `render_command` (`9b6eaf4`,
      `90efcb5`), `codec_from_encoder`/`parse_scale` (`85def6a`), and `build_one_recipe` — the
      probe+profiles+options orchestrator with all fallback chains + audio-only/gif/single-codec/
      copy-audio edge cases, `operations` summary, output-path derivation + lexical sandbox gate
      (`67cdf59`). `build_one_recipe → build_flags → render_command` produces full `ffmpeg` argv
      natively. **34 engine tests**; final correctness pinned by the eval-parity job (Phase 10).
- [x] **Deterministic command construction DONE (2026-07-05).** The model only names a declared intent
      tool + args; `expand_dry_run` builds the argv deterministically — no model-emitted shell. Sandbox
      boundary enforced on outputs via the engine's `assert_in_sandbox`.
- [x] **Dry-run rendering DONE (2026-07-05).** `run ffmpeg --dry-run` prints the full `ffmpeg` command
      line(s) (one per input), copy-pasteable via a minimal `shell_join` quoter.
- [x] **Confirmation workflow DONE (2026-07-05).** Every ffmpeg intent is `safety_category: destructive`,
      so execution needs explicit consent (native `ctx.confirmed`): `--yes`, or an interactive `y` when
      stdin is a terminal. **Non-interactive without `--yes` refuses** — prints the command(s) that would
      run + how to proceed, never executes silently (`confirm_execution` in main.rs).
- [x] **Execute via detected `ffmpeg` (subprocess) + `unsafe_phrases` DONE (2026-07-05).**
      [knaif-skill-ffmpeg `exec.rs`](../../skills/ffmpeg/native/src/exec.rs) `run_ffmpeg` shells the
      rendered argv (never model shell; `$KNAIF_FFMPEG_BIN` override; missing-binary → install hint),
      port of `_deps.run_ffmpeg`. The **pre-inference `unsafe_phrases` gate** moved to shared
      [knaif-core `safety.rs`](../../native/crates/knaif-core/src/safety.rs) (`is_unsafe_request` token matcher +
      `load_unsafe_phrases` from `skill.yaml`), a faithful port of the Python `infer` reject. Proven
      end-to-end: `run ffmpeg --yes --sandbox` strips audio off a real clip (output has 0 audio streams).
- [x] **Real `ffprobe` probe DONE (2026-07-05).** `exec::run_ffprobe` + `engine::summarise_probe`
      (port of `_deps.run_ffprobe` + `_summarise_probe`; `$KNAIF_FFPROBE_BIN` override). `run.rs` gained a
      `ProbeMode`: **execute real-probes every input** (missing/unprobeable → hard error), **dry-run**
      real-probes existing files but stubs missing ones (`dummy_probe`), mirroring `inspect_media` under
      `ctx.dry_run`. Proven: `run ffmpeg --yes` on a real **4K** clip → whatsapp profile → **1280×720**
      output; execute on a missing input errors; dry-run stubs it. *(Minor divergence: dry-run falls back
      to `dummy_probe` on any probe error incl. a missing ffprobe binary, where Python re-raises
      `FFmpegNotAvailable` — friendlier for a preview, and the eval dry-run uses non-existent fixtures so
      ffprobe isn't invoked. Probe `fps`/`size_bytes` still unmapped — they feed target-size math, not
      ported yet.)*
- [x] **CLI `run ffmpeg` DONE (2026-07-05, [main.rs](../../apps/cli/src/main.rs)).**
      `knaif run ffmpeg "<req>" [--dry-run] [--sandbox <path>] [--yes]`: shared `build_plan`
      (safety gate → backend → `extract_json` → parse → normalize → apply_defaults → validate) →
      `expand_dry_run` → dry-run preview **or** confirmed subprocess execution (per-command ✓/✗ report,
      non-zero exit on any failure). Core control tools (`clarify`/`reject`) short-circuit. Driven offline
      by the `KNAIF_LLM_MOCK_RESPONSE` seam. `--model` accepted but **reserved** (llama.cpp backend).
- [ ] Build native parity tests from existing ffmpeg eval rows. *(argv currently pinned to Python
      ground truth inline in `run.rs` tests; the eval-row-driven harness is the Phase 10 parity job)*
**Documents native port** — **DECIDED 2026-07-05 (PDF stack):** structural ops first with **`lopdf`**
(MIT, pure-Rust); rasterizing ops (compress rasterize-fallback, `convert`→image, OCR) **deferred**
until a rendering approach is chosen (owner picked "structural ops first, defer rasterize" over
bundling PDFium).

- [x] **Tier 1 foundation DONE (2026-07-05, `31596ad`).** `knaif-skill-documents`:
      [`detect.rs`](../../skills/documents/native/src/detect.rs) `ExternalTools::detect`
      (gs/soffice/tesseract on PATH, `KNAIF_<TOOL>_BIN` overrides — port of `detect_external_tools`) +
      [`profile.rs`](../../skills/documents/native/src/profile.rs) compress profiles
      (`{small,balanced,high}.yaml`) + `DocumentsData::load`.
- [x] **Structural PDF engine — page-shuffling family DONE (2026-07-05).**
      [`pdf.rs`](../../skills/documents/native/src/pdf.rs): page-selection parsing
      (`parse_pages`/`resolve_endpoint`/`parse_page_range_specs` — faithful ports incl. `first`/`last`/
      `end`/`all`, strict vs. reorder-padding) + `page_count`, `is_encrypted`, `rotate_pages` (relative,
      normalized), `remove_pages` (delete + empty-doc guard), `reorder_pages` (Kids rebuild + reparent),
      `split` (clone + delete-complement, attrs preserved), `merge` (nest source Pages trees so inherited
      attrs survive; disjoint id renumbering). 22 tests with page-identity tags + disk round-trips.
- [x] **Read family DONE (2026-07-05).** [`text.rs`](../../skills/documents/native/src/text.rs):
      `inspect_document` (format/size/pages/encrypted/has_text_layer), `extract_text` (PDF via lopdf's
      extractor honoring a page spec; `.txt`/`.md` direct), `find_in_document` (literal/regex,
      case-insensitive, ±40-char snippets + spans via the `regex` crate). 8 tests incl. real text
      extraction from a synthetic text PDF. **Deferred:** image OCR + office-format text (heavy deps).
- [x] **`run documents` CLI wired DONE (2026-07-05).**
      [`run.rs`](../../skills/documents/native/src/run.rs) `preview`/`commit` dispatch (reads run
      immediately + print; writes preview output path(s) then commit after confirmation) +
      [main.rs](../../apps/cli/src/main.rs) `run_documents_step` (shared safety gate / `build_plan`
      / clarify-reject with ffmpeg; generalized `confirm_action`). Handles the 8 ported tools;
      unsupported (compress/convert/ocr/watermark/protect) → clear "not implemented natively yet".
      **Proven end-to-end on real reportlab fixtures and cross-validated with pypdf** (a different
      parser): inspect (3 pages/text-layer; encrypted→0), find "Beta"→p2, split 1-2,3→2+1 pages,
      merge sample×2→6 pages, confirm-gate refusal. Sandbox boundary enforced on inputs + outputs.
- [x] **Encryption `protect_pdf`/`unlock_pdf` DONE in-process (2026-07-05).** Upgraded **lopdf 0.34→0.43**
      (encrypt/decrypt/`load_with_password` added post-0.34; zero code changes for the existing ops — all
      31 tests green on the bump). `pdf::protect` = AES-128 (V4, user=owner password, synthesizes a
      trailer `/ID` when absent); `pdf::unlock` = `Document::load_with_password` (handles RC4 **and
      AES-256**). Wired into `run.rs` + the CLI. **Spiked + cross-validated per the dev review:** lopdf
      decrypts the pikepdf **AES-256** fixture; our AES-128 output opens in **pypdf** with the password;
      wrong password rejected. **qpdf was NOT needed** for the real fixtures — keep it as a documented
      future backstop for exotic revisions, not implemented now (dev's "if it passes, in-process +
      fallback" branch). Passwords stay in-process (no argv exposure).
- [x] **Overlay family `watermark`/`add_page_numbers` DONE in-process (2026-07-05).**
      [`overlay.rs`](../../skills/documents/native/src/overlay.rs): `add_text_overlay` draws a
      centered Helvetica `Tj` string per page (position via `position_xy`, opacity via an ExtGState
      `ca`/`CA`), appending an overlay content stream + merging the font/ExtGState into a page-owned copy
      of `/Resources`. `watermark_text` (center, 0.35, 42 pt) + `add_page_numbers` (bottom-center, 12 pt,
      `start_at`). Wired into `run.rs` + CLI. **Cross-validated with pypdf:** watermark text + page numbers
      are embedded and extractable by a different parser. **Image watermark deferred** (needs image-XObject
      embedding) → clear message. 5 overlay tests (incl. disk round-trip). This completes the **12 in-process
      documents tools** (structural + read + encryption + overlay).
**Rendering + external-tool strategy — DECIDED 2026-07-05 (owner).** See project memory
`project_native_rendering_tooling_strategy`. **Bundle PDFium** (`pdfium-render`, BSD) as the shared
rasterizer (render PDF→image for convert→image / OCR page-prep / compress rasterize path) — the "single
working thing", ~10 MB accepted, same engine mobile uses. gs and PDFium are **not** substitutes (PDFium
renders, gs compresses). Per-feature: `compress_pdf` floor = lopdf-lossless + `image`-crate downsample
(keeps text) with **gs installer-offered** for aggressive; `ocr_document` = PDFium render + **Tesseract**
(Apache → bundle-able, or subprocess); office→pdf = **soffice** installer-offered (can't bundle). Bundle
only permissive (PDFium/Tesseract); **installer-PM** (winget/brew/apt, license-clean, not bundling) for
gs/LibreOffice/ffmpeg. Architecture: a **`Rasterizer`/`OcrEngine` trait boundary** keeps the deterministic
core platform-agnostic; desktop = PDFium/subprocess, mobile = PDFium in-proc + platform-native OCR.

- [x] **`Rasterizer`/`OcrEngine` trait boundary + PDFium desktop impl DONE (2026-07-05, `f756e41`).**
      [`render.rs`](../../skills/documents/native/src/render.rs) traits (platform seam) +
      [`pdfium_backend.rs`](../../skills/documents/native/src/pdfium_backend.rs) `PdfiumRasterizer`
      (feature `pdfium`, dynamic-loads the lib → compiles without it; bundled Phase 9). **Verified for
      real** against a downloaded `pdfium.dll` rendering a 3-page PDF.
- [x] **`convert_document` DONE (2026-07-05).** [`convert.rs`](../../skills/documents/native/src/convert.rs):
      →txt/md (extract+write, in-process), image→pdf (`image` crate JPEG-embed as a DCTDecode XObject via
      lopdf, no rasterizer), office→pdf (`soffice` subprocess, installer-managed). pdf→image stays
      NotImplemented (matches Python). Wired into `run.rs` + CLI. **Cross-validated with pypdf** end-to-end:
      PDF→txt, PNG→pdf (1 page), **docx→pdf via real soffice** (1 page); unsupported → clear message. 4 tests.
- [x] **`compress_pdf` DONE (2026-07-05).** [`compress.rs`](../../skills/documents/native/src/compress.rs):
      three backends chosen exactly as `RunCompressStep` (`choose_method`): **lossless** (lopdf
      `compress` + `save_modern`, keeps text), **ghostscript** (`gs -dPDFSETTINGS` subprocess for
      small/balanced when present), **rasterize** (small + no gs → PDFium render pages → JPEG image PDF,
      feature `pdfium`; loses text). Profiles from the bundle; `--dry-run` names the method + text-loss
      warning. Wired into `run.rs` (bundle threaded through) + CLI. **All three proven end-to-end**:
      lossless + **real gs** (both keep text, valid 3-page PDFs via pypdf), and **real PDFium rasterize**
      (2-page image PDF, gated test).
- [x] **`ocr_document` DONE (2026-07-05).** [`ocr.rs`](../../skills/documents/native/src/ocr.rs):
      `TesseractOcr` (subprocess, `$KNAIF_TESSERACT_BIN` override) implements the `OcrEngine` trait +
      `image_to_pdf` (`tesseract … pdf` searchable output). `ocr_document` routes: PDF-with-text-layer →
      copy; image → tesseract; scanned PDF → PDFium render each page (feature `pdfium`) → tesseract → merge.
      Port of `RunOcrStep`/`_write_ocr_pdf`. Wired into `run.rs` + CLI. **All paths proven with real tools**:
      image→searchable PDF (pypdf confirms text layer), text-layer copy, and the full scanned-PDF
      render→OCR→merge (real PDFium + Tesseract, gated test). This completes the `Rasterizer`/`OcrEngine`
      trait boundary with real desktop backends.
- [x] **Image watermark DONE (2026-07-05).** `overlay::add_image_overlay` — image placed ~96 pt at the
      position with a grayscale `/SMask` carrying alpha × opacity (true transparency). pypdf-validated.
- [x] **Office text/page-counts DONE (2026-07-06).** [`office.rs`](../../skills/documents/native/src/office.rs):
      docx/pptx via zip + quick-xml (`<w:t>`/`<a:t>` grouped by paragraph), xlsx via calamine (per-sheet
      rows). `extract_text` also OCRs image inputs (Tesseract). `inspect` page count = slides/sheets.
      **Byte-identical to Python `_extract_office_text`** on the fixtures. All deps permissive (zip/quick-xml/
      calamine, MIT).
- [x] **Documents native port COMPLETE (2026-07-06).** All ~16 tools run natively end-to-end across every
      supported format — structural, read, encryption, overlay, convert, compress, OCR, office — each
      cross-validated against Python's libraries (pypdf) and the real external tools (Ghostscript,
      LibreOffice, Tesseract). PDFium bundled for rendering (feature `pdfium`, native lib packaged in
      Phase 9); gs/soffice/tesseract installer-managed. **Only the eval-row parity harness remains (Phase 10).**
- [x] **DONE (2026-07-06).** Permissive in-process document operations are reimplemented with Rust
      libraries (lopdf, `image`, zip/quick-xml/calamine); large/copyleft tools (`gs`, `soffice`,
      `tesseract`) stay detected subprocess dependencies, never bundled/linked. The rasterizing half that
      was deferred here (compress rasterize, `convert`→image, OCR) has since landed via `compress.rs` /
      `convert.rs` / `ocr.rs` + the PDFium `Rasterizer`/`OcrEngine` backend.
- [x] **DONE (2026-07-05).** CLI: `run documents "<req>"` shares one `cmd_run` with ffmpeg
      ([main.rs](../../apps/cli/src/main.rs)) — `--dry-run`, `--model <path|name>` (resolved to a
      GGUF via `resolve_model_path` → shared `ModelStore`), `--yes`, and `--sandbox <path>` (boundary
      enforced on inputs + outputs) all apply.
- [ ] Build native parity tests from existing documents eval rows. *(Same as the ffmpeg parity item:
      argv/results currently pinned to Python ground truth inline in the crate tests; the eval-row-driven
      harness is the Phase 10 parity job.)*

The Python skill is already package-shaped for this port: `_deps` (shell-out),
`_engine` (pure logic), `steps`/`intents` (handlers), and `vocab.yaml` for ffmpeg
(shared data). The Rust crates mirror that split — shared YAML in, imperative engines
reimplemented.

**Verify:** native dry-run renders expected ffmpeg commands for seeded eval rows and
expected documents previews/results for seeded eval rows; destructive/overwrite refused
without confirm; tests cover missing dependency, missing input, invalid plan,
confirmation rejection.

### Phase 8 — Installer dependency & model setup

> **Runtime side DONE 2026-07-07** (commits `7e3cbea`, `2f8d468`, `7dda7df`, `fd19d03`). The four
> runtime slices landed TDD: (1) `knaif-core::deps` external-tool detection engine reading the
> declarative `dependencies.external_tools` (with an explicit `all_required` flag resolving the
> ffmpeg-both-binaries vs documents-aliases overload); (2) `knaif skills deps [<name>]` doctor +
> `run` pre-inference preflight (execution only; dry-run still previews; documents never blocks);
> (3) first-run guidance pointing at the manifest's public CLI recommendation (not the internal
> `recommended_model` name); (4) 429/503 retry-with-backoff in the `ureq` fetcher. **The GGUFs are
> now uploaded** (manifest `url`/`sha256` real) and the whole model path is **verified live**:
> `models verify qwen3-4b-v1` matches, and a real `models pull qwen3-1.7b-v1` (1.4 GB from HF) streamed
> + checksum-verified + installed. **Remaining Phase 8 items are all installer-packaging → Phase 9.**

- [x] Define the model manifest format (name, URL, SHA-256, size, license/source note, skill compatibility, and surface recommendations for desktop/native CLI vs mobile). *(Done Phase 5; now populated with real URLs/checksums for `qwen3-4b-v1` + `qwen3-1.7b-v1`.)*
- [x] First-run: no model → offer download (via `ModelStore.pull`) or ask for a path; write to the shared store. *(slice 3: guidance names the recommended model — pull it if absent, use it if installed, or `--model <path>`. Non-interactive guidance, not a blocking prompt.)*
- [x] **Model hosting = Hugging Face (`blackdeep/knaif`), tokenless downloads for end-users — never
      require an HF token.** HF's per-client rate limits are fine for distinct end-users at scale
      (limits are per-IP, not per-repo; bytes come from the CDN); they only pinch behind shared-IP
      egress (corporate NAT / CI fleets). **Future scaling option — own CDN.** If HF ever becomes a
      bottleneck (shared-IP throttling, cost, or availability), stand up an owned CDN/mirror as a
      **backup or full migration**. This is *cheap by design*: the `ureq` `HttpFetcher` is
      host-agnostic, so migration is just re-hosting the GGUFs and changing each manifest `url`
      (still commit-/hash-pinned) — no fetcher or runtime change. **HTTP 429/503 retry-with-backoff
      added (slice 4, `7dda7df`)** — honors `Retry-After`, 30s cap. Do NOT solve rate limits by asking
      end-users for a token.
- [x] Full model lifecycle backed by the shared `ModelStore` (Phase 5), not first-run-only: download / update / verify / delete / delete-all, all writing the shared store. The **same API backs the future UI's model-management screen** — this plan wires only the CLI surface. *(pull/verify now proven live end-to-end.)*
- [x] **DONE (Phase 9, Inno installer).** Install-time seeding is an optional installer step: an opt-in, default-checked "Download the recommended AI model now — qwen3-4b-v1 (~2.5 GB)" task runs `knaif models pull` post-install into the shared store, guarded (`NeedsModel`) to skip when already present and non-fatal on failure. The app can still fetch/update/delete later.
- [ ] **→ Phase 9 (installer):** **No install-time backend selection.** The single artifact already bundles CPU + Vulkan + CUDA (Metal on Apple); the installer just installs it. GPU selection is 100% runtime (see Phase 5 "Backend selection"). The installer may *report* detected GPU capability for transparency, but installs one artifact and never downloads executable backend variants. Install never fails when no GPU is present (CPU floor).
- [x] Skill-dependency reader for packaged skills; installer/runtime checks ffmpeg/ffprobe and documents optional tools (`gs`, `soffice`, `tesseract`) with per-feature messaging. *(slices 1–2: `detect_skill_deps` + `knaif skills deps` + the `run` preflight — the shared engine the Phase 9 installer surfaces will reuse.)*
- [x] Managed external-tool install suggestions via winget / Homebrew / distro pkg with fallback instructions. *(slices 1–2: per-OS install hint surfaced from `install:` in skill.yaml. **Refinement for Phase 9:** replace the generic `winget`/`package_manager` tokens with concrete package IDs, e.g. winget `Gyan.FFmpeg`, apt `ffmpeg`.)*
- [x] PATH changes require explicit consent. *(Detection is read-only and never mutates PATH; the installer's own PATH handling is Phase 9.)*
- [ ] **→ Phase 9 (installer):** Install succeeds even when model/dependency steps are skipped or unavailable.

**Verify:** missing model doesn't block install; first run explains model setup;
missing ffmpeg/documents dependencies detected before affected execution; PATH mutation
gated on consent; the single artifact installs without any GPU-variant choice, and a
no-GPU / lavapipe-only machine installs cleanly and runs on CPU (runtime picks the device).

### Phase 9 — Packaging & release

> **In progress 2026-07-07.** Foundational slices landed:
> - **Slice 1 — install-location resource resolution** (`723b798`): `resolve_skills_root`/
>   `resolve_repo_file` gained an exe-relative fallback (`skills_root_near`/`file_near`) so an
>   installed binary finds `skills/`, `shared/runtime/`, `shared/models/` beside the exe (or one up
>   from `bin/`), not just via a dev-checkout CWD walk-up. Verified from `C:\`.
> - **Slice 2 — portable-artifact staging** (`2413a68`): `packaging/package.sh` + `just package`
>   stage a self-contained `knaif-<ver>-<os>-<arch>/` (bin + runtime-only skill data + shared
>   contracts + LICENSE + README) and archive it (zip/tar.gz), with a from-outside-the-checkout
>   self-containment smoke. Base build (no llama/GPU) — 4.0 MB zip on Windows.
> - **Slice 4a — functional artifacts + build-kind-aware packaging** (`1331668`): `package.sh
>   --kind=base|cpu|vulkan|cuda`; `just package-native <kind>` wraps the Dev-Shell build+package.
>   **Empirically: CPU and Vulkan builds both static-link (single self-contained exe, no bundled
>   DLLs); only CUDA needs DLLs** (bundle the redist `cudart`/`cublas` from `$CUDA_PATH/bin/x64`).
>   Verified end-to-end on Windows (Dev-Shell activation via `Enter-VsDevShell`):
>   - **CPU** — 5.3 MB zip; extracted outside any checkout → real inference (qwen3-1.7b, ~33 s).
>   - **Vulkan** — 22 MB zip; built in 1m13s (Ninja generator); runtime **offloaded to the RTX 5080**
>     (enumerated NVIDIA + AMD = cross-vendor), inference in **7.3 s** (~4.5× CPU). Needs the driver's
>     system `vulkan-1.dll` (not bundled). **CUDA build (15–30 min) still pending** — packaging ready.
> - **DECIDED 2026-07-07: Windows installer = Inno Setup** (not WiX). Enterprise/MSI not needed;
>   Inno is native (no .NET), free + source-available, and its `[Components]` + `[Run]` model maps
>   directly to the core-mandatory + per-skill + optional-deps-via-their-own-installers tree
>   ([[project_installer_component_model]]). WiX Burn stays the fallback if MSI is ever required.
> - **Slice 5 — Inno Setup installer** (`packaging/windows/knaif.iss`, `just installer`): per-user
>   (no-admin) install to `{localappdata}\Programs\knaif`; component tree = core (fixed) +
>   `skills\ffmpeg` + `skills\documents`; opt-in PATH task (append + surgical uninstall removal); 3rd-
>   party tools never bundled. **Verified**: silent component-selective install (core + ffmpeg, not
>   documents) → installed exe runs from outside any checkout (`skills list` shows only ffmpeg) → clean
>   uninstall. **Follow-up DONE:** opt-in per-skill winget installs of the external tools (Gyan.FFmpeg,
>   ArtifexSoftware.GhostScript, TheDocumentFoundation.LibreOffice, UB-Mannheim.TesseractOCR), guarded by
>   `ShouldInstall` (winget present AND tool absent) so nothing reinstalls and no-winget degrades cleanly.
>   Non-tech-friendly (no terminal). Positive install path verified manually on a tool-missing machine.
> - **Slice 3 — third-party license notices** (`about.toml`/`about.hbs`, `just licenses`,
>   `packaging/licenses/`): `THIRD-PARTY-RUST.txt` (all 281 Rust crates via cargo-about, permissive
>   only — no GPL/AGPL) + `llama.cpp-LICENSE.txt` (MIT). `package.sh` ships them in the artifact's
>   `licenses/` (Rust always; llama.cpp for inference builds; NVIDIA slot for `--kind=cuda`); the
>   installer installs `licenses/` under core. PDFium/NVIDIA texts reserved for those build kinds.

- [ ] Artifact layout: `bin/knaif`, **GPU backend libs** — **DECIDED 2026-07-15 (finalization C5): opt-in `GGML_BACKEND_DL`.** Default artifact carries CPU + Vulkan loadable backends; the CUDA backend (`ggml-cuda` + cublas redist) is an **opt-in download**, not bundled for everyone. One default artifact per OS, backend selected at runtime; CUDA added via `knaif backend install cuda`. Spike-gated (`llama-cpp-sys-2` dynamic support) with a thin-launcher fallback. Also: compiled-in skill list, `licenses/`, `model-manifest.json`, README. *(base layout + licenses/ DONE slices 2–3; backend-DL spike + CUDA opt-in payload pending — finalization C5/C2/C6.)*
- [ ] **CUDA runtime redistributables — bundle beside the exe; users never install the CUDA SDK.** A CUDA build needs `cudart64_13.dll` (~51 MB) + `cublas64_13.dll` (~443 MB) at runtime; NVIDIA's EULA lists these as **redistributable**, so they ship *next to `knaif.exe`* — no toolkit/SDK on the user's machine, only their existing GPU **driver** (`nvcuda.dll`, present with any NVIDIA GPU; never bundled). Because cublas is ~½ GB, **Vulkan is the default GPU backend** (cross-vendor, zero CUDA payload) and **CUDA is an optional perf build** that carries the redist DLLs. The non-NVIDIA-startup-crash from the driver dep is handled by `GGML_BACKEND_DL` (dlopen CUDA only when an NVIDIA device is present). Ship the NVIDIA redistributable license in `licenses/`.
- [ ] **GPU-build portability across GPU generations.** **Vulkan is portable as-is** — shaders compile to GPU-agnostic SPIR-V, so ONE Vulkan build runs on any Vulkan GPU (any vendor/generation); the RTX 5080 in the slice-4a verification was just runtime device selection, not a build target. **CUDA is NOT portable by default:** kernels compile per compute-architecture, and the dev default `cuda_arch = native` (justfile) builds **only** for the build machine's GPU (sm_120 / Blackwell here → would NOT run on e.g. a 3070 / sm_86). **The release CUDA build MUST use a multi-arch fatbin** (`CMAKE_CUDA_ARCHITECTURES="75;80;86;89;120;…"` + PTX for forward-compat), never `native`, so one CUDA artifact spans a range of NVIDIA cards. Cost: larger binary + longer compile per arch. **Arch floor (how far back — Turing sm_75? Pascal sm_61? older?) — TBD, decide at slice 4c.**
- [ ] Windows: **Inno Setup** installer (component tree: core + per-skill + optional deps via winget/vendor installers) + portable zip (optional "download default model" step); SHA-256 each. GPU backend DLLs bundled. *(portable zip DONE slice 2.)*
- [ ] Linux: AppImage (`linuxdeploy`) + tarball (static-musl if possible). GPU backend `.so`s bundled. (musl caveat: CUDA/Vulkan need glibc + the vendor driver — the static-musl option applies to the CPU-only floor build if we ship one.)
- [ ] **macOS: POST-V1 (deferred).** When picked up: tarball (universal2 if feasible, else per-arch), dylibs in `lib/`, **signed/notarized** (Apple Developer account available). Not a v1 blocker.
- [ ] Third-party notices: llama.cpp, ffmpeg/documents dependency guidance, Rust deps, packaged assets.
- [ ] Release smoke test: install into temp dir, run `--version`, `skills list`, ffmpeg + documents dependency detection.
- [ ] `release.yml` on tag `v*.*.*`: matrix (windows-2022, ubuntu-22.04) → build → package → attach to draft GH Release with checksums. (Add macos-13/macos-14 when macOS packaging lands post-v1.)

**Verify:** clean Win/Linux envs install the artifact; install succeeds without
model and without external tools; first run guides setup; affected executions block
with helpful missing-dependency messages; artifacts exclude `models/`, `sandbox/`,
`eval_results/`, notebooks, and training outputs.

### Phase 10 — CI, eval parity & governance

> **Reconciled 2026-07-22.** Two items shipped, the parity goal shipped by a *different*
> mechanism than specified, and the three CI items were consciously deferred past v1 — they
> are Workstream F of
> [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md), which owns them by
> name. Nothing here is orphaned.

- [ ] Split CI into Python, native, docs, packaging jobs. — **not done; owned by post-v1.**
      `.github/workflows/` does not exist yet.
- [ ] Compatibility job: active shared skill manifests (`ffmpeg`, `documents`) accepted by both Python and Rust loaders; stale `io` is excluded unless explicitly opted in. — **not done as a
      *job*; owned by post-v1.** The underlying contract is guarded off-CI by
      `contracts/parity/planner_cases.json` and `python/core/tests/test_skill_metadata.py`.
- [~] Add a `rust-cli` arm to `knaif.evalsuite` invoking the `knaif plan --skill X --json` command (introduced in Phase 4, filled in Phase 6), capturing stdout; register it in `eval_backends.yaml`. — **goal met by a different design.** No `rust-cli` backend was
      registered in `eval_backends.yaml`; instead
      [`scripts/parity_check.py`](../../scripts/parity_check.py) drives the native binary
      directly and diffs the *rendered command* against Python's, pinning both runtimes to the
      identical GGUF. Comparing rendered output is a sharper test than comparing scores — two
      runtimes can score alike while emitting different commands.
- [~] `just eval-parity`: corpus through `python-agent` + `rust-cli`, diff scores; `eval-parity.yml` runs on Rust PRs using the current promoted/smoke GGUF chosen at implementation time (do not hard-code the retired 1.7B lane). — **shipped as `just parity <skill>`**
      ([justfile](../../justfile)); results land in `evals/parity/` (incl. CUDA runs). The
      **`eval-parity.yml` CI half is not done** — owned by post-v1.
- [x] Docs: how to add a skill for Python-only / native-only / both runtimes. —
      [TOOL_SCHEMA.md](../TOOL_SCHEMA.md) *Runtimes (Python / native / both)*, with the
      three-way table and the rule that omitting `runtimes:` means Python-only.
- [x] `docs/RELEASE.md`; keep `docs/TODO.md` updated as phases complete. —
      [RELEASE.md](../RELEASE.md) ships (build/package/verify/publish per OS+kind).

**Verify:** Python-only PRs don't require native inference; native-only PRs don't run
notebooks; shared-schema changes require both loaders' tests; eval suite compares
Python vs native ffmpeg + documents on the same corpus rows (target: within ±2%,
differences logged).

---

## Cross-Cutting Risks & Further Considerations

1. **Handler duplication / drift.** Two implementations of every handler doubles work.
   Mitigations, in order of preference: (a) keep native v1 limited to the two
   production skills (ffmpeg + documents) and lean on the eval-parity job to catch
   divergence; (b) long-term, push
   more handler logic into declarative YAML (command templates with arg substitution) so
   both runtimes share it; (c) shell out to a shared per-handler CLI. The current
   `Step`/`Intent` + `profiles/` + `arg_value_sets` design already pushes ffmpeg toward
   (b) — preserve and extend that during the port.

2. **Contract instability beyond file-centric skills.** The second-skill blocker is
   resolved by `documents`, but both production skills are file-centric. Keep Phase 0
   decisions reversible where they touch non-file inputs, especially `input_refs`.

3. **`input_refs` media-vocab leakage.** Per project memory, media vocabulary currently
   lives in core but is ffmpeg-domain. `documents` did not force this extraction
   because it also operates on files. The Rust port should mirror whatever the cleaned-up
   boundary becomes, not the current leaky one.

4. **Windows code signing.** Unsigned MSIs trigger SmartScreen. v1 ships unsigned with a
   documented bypass; acquire an OV cert later.

5. **GPU acceleration.** Resolved: **one artifact bundles CPU + Vulkan + CUDA (Metal on
   Apple); the device is selected at runtime, CPU floor** (measured CUDA-vs-Vulkan on RTX
   5080; multi-backend build verified — see Session log). Record GPU + CPU behavior on
   Windows + Linux. Watch: usable-device detection (absent Vulkan ICDs in dev/CI like WSL and
   a long tail of machines → fall back cleanly), the backend-bundling mechanism
   (`GGML_BACKEND_DL` vs static — Phase 5 spike), and Vulkan on older/integrated/fragmented
   mobile GPUs.

## Explicitly Out of Scope for v1

- Desktop GUI (Tauri 2 + React) — separate future product surface; revisit after CLI
  parity.
- Mobile apps — separate future product surfaces; likely SwiftUI iOS and
  Kotlin/Compose Android shells around the native runtime.
- macOS installer/packaging — post-v1 fast-follow (dev env is not macOS). When it lands
  it ships signed/notarized (Apple Developer account available). The Rust core stays
  cross-platform; only macOS packaging/release is deferred.
- Runtime/plugin skill loading — compile-time selection only.
- Downloadable skills, the declarative command-template DSL, the runtime capability stdlib,
  WASM skill modules, and skill signing/distribution — all deferred to a dedicated future
  plan. This plan only reserves layout/boundary space for them (see *Skill Distribution
  Model*).
- Homebrew / Scoop / winget *formulas* for knaif itself — post-v1 (managed dependency
  *install* of ffmpeg via those managers is in scope).
- Auto-update mechanism.
- Telemetry.
- Native `io` skill (stale; rebuild pending).

---

## Session log & open questions (updated 2026-07-03)

Continuation notes for picking this up on another machine. Nothing here is
implemented yet — the repo is still single-package Python; this plan + the bench
scripts are the only artifacts.

**Decisions locked this session (all reflected in the sections above):**
- Skills become self-contained **bundles at root `skills/<name>/`**: YAML at the top,
  per-language impl in `python/` / `rust/`. Single-bundle loader (not a two-root loader);
  `ctx.skill_dir` = bundle root; the `_VOCAB_PATH` / documents `__file__` data loads must
  move onto `ctx.skill_dir` (Resolved Decision #3).
- **No `knaif.skills` package** — machinery stays in flat `knaif.skill` / `knaif.skill_base`
  (avoids clashing with root `skills/`; zero churn to ~20 `from knaif.skill import Skill`).
- **`ModelStore` is its own `knaif-models` crate** with no inference deps (list / pull /
  update / verify / delete / delete-all), OS-conventional shared store; `knaif-llm` depends
  on it. CLI + future UI both embed it.
- **Desktop UI = standalone Tauri app embedding the engine crates** (one app), never a
  wrapper shelling out to the CLI.
- **macOS deferred to post-v1** (dev env is not macOS); ships signed/notarized when it lands
  (Apple Developer account available). v1 blockers = Windows + Linux.
- Skill distribution tiers recorded; downloadable/declarative-only skills + capability
  stdlib + DSL + WASM all deferred to a **separate future plan**.

**RESOLVED — llama.cpp GPU backend strategy.** *(Historical log — the specifics below were
SUPERSEDED; see the ✅ FINAL box after the benchmark table. The benchmark data itself
stands.)* Goal: one install that runs on any hardware (à la Ollama). The original wording
here ("Vulkan-first single build + CPU fallback," CUDA-specific builds as a post-v1
escalation) rested on a wrong assumption that llama-cpp-2 links only one GPU backend per
build. Verified false — so the final decision is a single artifact with **all** backends
compiled in (CPU + Vulkan + CUDA; Metal on Apple) and pure runtime device selection.
- Decision needed a **CUDA-vs-Vulkan** comparison on real NVIDIA drivers.
- WSL can't measure Vulkan here (no NVIDIA Vulkan ICD; only lavapipe/CPU; no sudo). **Ran
  the Vulkan arm on Windows.**

**Measured CUDA-vs-Vulkan (RTX 5080, Windows, driver 610.62, llama.cpp b9864, prebuilt
win-cuda-12.4 + win-vulkan, `llama-bench -ngl 99 -r 3`)** on the two promoted baseline
GGUFs:

| Model | Backend | pp512 t/s | tg128 t/s |
|---|---|---|---|
| qwen3-4b-v3 (Q4_K_M)  | CUDA   | 11660.78 ± 704 | 240.28 ± 1.1 |
| qwen3-4b-v3 (Q4_K_M)  | Vulkan | **12089.68 ± 44** | **246.63 ± 0.4** |
| qwen3-1.7b-v3 (Q6_K)  | CUDA   | **22991.80 ± 2356** | **396.51 ± 1.2** |
| qwen3-1.7b-v3 (Q6_K)  | Vulkan | 19738.22 ± 57 | 371.41 ± 33 |

Read: Vulkan **beats** CUDA on the 4B lane (+3.7% pp, +2.6% tg) and trails on the 1.7B lane
(pp −16.5%, **tg −6.8%**). knaif's decisive metric is `tg` (short JSON-plan outputs), where
the worst-case Vulkan penalty is ~7% and even the slower 371 t/s is far more than enough.
This **confirms the Vulkan-first hypothesis** — the cross-vendor single-build simplicity is
worth a ≤7% tg penalty on NVIDIA. Windows CUDA (tg 240/396) also corroborates the earlier
WSL CUDA baseline (tg 228/380). CUDA/GPU-specific builds stay reachable behind the
`knaif-llm` trait as a post-v1 escalation if a perf case ever demands it.

**✅ FINAL (supersedes the "Vulkan-first single build" wording above; benchmark data stands).**
Corrected after verifying that llama.cpp/`llama-cpp-sys-2` can compile **multiple backends
into one build** with runtime device selection (not one-backend-per-build). v1 decision:
**a single artifact per OS bundles CPU + Vulkan + CUDA (Metal on Apple); the device is
chosen at runtime (detect → validate usable → pick best → CPU fallback).** No compile-time
variants, no install-time backend download (that would be an auto-updater/trust problem —
out of scope). **CUDA is preserved by being compiled into the one binary**, kept for NVIDIA
perf and dev/CI where a Vulkan ICD is absent (WSL). **Ollama is Python-dev/eval only** — the
Rust runtime ships no Ollama backend. Reflected in Locked Decisions, Phase 5, 8, 9, and
risk #5. Open impl detail: bundle via `GGML_BACKEND_DL` (preferred) vs static link (Phase 5
spike). Sources: llama.cpp build docs; `llama-cpp-sys-2` build.rs.

*Harness note:* the CUDA asset regex in `bench_llm_backend.ps1` was anchored to `^llama-`
during this run — the old `bin-win-cuda.*x64\.zip$` also matched the `cudart-...` zip and
downloaded cudart twice instead of the binary. Current releases also ship two CUDA toolkits
(12.4 + 13.3); the script now pairs the first of each.
- Reproducible harness added: `scripts/bench_llm_backend.sh` (Linux/WSL, needs a
  `LLAMA_BENCH` binary) and `scripts/bench_llm_backend.ps1` (Windows; auto-downloads
  prebuilt CUDA + Vulkan llama.cpp, benches both). Run on Windows:
  `./scripts/bench_llm_backend.ps1 -Model "C:\path\Qwen3-4B-Q4_K_M.gguf"`.
- **Measured CUDA baseline (RTX 5080, WSL, llama-bench, -ngl 99, 3 reps)** on the current
  promoted lanes (`qwen3-4b-v3` = `models/qwen3-4b-sft-v3-flat-q4.gguf`, mobile =
  `models/qwen3-1.7b-sft-v3-flat-q6.gguf`) — **both renamed 2026-07-14** to
  `models/knaif-qwen3-4b-v1-q4_k_m.gguf` / `models/knaif-qwen3-1.7b-v1-q6_k.gguf`
  (see [PERFORMANCE.md §8](../PERFORMANCE.md)):
  - 4B-v3 (Q4_K_M) → pp512 ~11.8k t/s, **tg128 ~228 t/s**
  - 1.7B-v3 (Q6_K) → pp512 ~20.4k t/s, **tg128 ~380 t/s**
  Backend throughput is quant/arch-bound (base vs fine-tune GGUF differ <2%), so these
  stand in for any same-size/quant fine-tune. For knaif's short JSON-plan outputs, `tg`
  dominates and is already far more than enough, so a Vulkan speed penalty is likely
  acceptable. **Now confirmed on Windows** (see the RESOLVED block below): Vulkan's tg
  penalty vs CUDA is ≤7% and it even wins on the 4B lane — "Vulkan-first" is locked.
