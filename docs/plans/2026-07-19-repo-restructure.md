# Repo Restructure — four pillars: `python` / `native` / `skills` / `apps`

**Status:** Done · **Created:** 2026-07-19 · **Completed:** 2026-07-20
**Owner:** core · **Ref:** builds on [monorepo-dual-runtime](2026-06-17-monorepo-dual-runtime.md);
sequenced **before** the OSS-prep flatten.

> **Kept 2026-07-23** (S7 decision). Re-verified against the tree: every pillar and rename below
> exists exactly as described (`python/core`, `python/training`, `native/crates`,
> `skills/*/native`, `apps/cli`, `contracts/`, `site/`, `evals/`, `installers/`, `media/`), and
> `shared/`, `web/`, `eval_results/`, `packaging/` are all gone — the plan is an accurate record,
> not a stale aspiration. **Load-bearing beyond its own history:** this is the canonical old→new
> path mapping, and dated closeout records that keep the pre-restructure layout on purpose (e.g.
> [native-branch-finalization](2026-07-15-native-branch-finalization.md)) point here to be read.
> Do **not** delete it while any such record survives in the public tree. Already fully annotated
> with its own amendments (R6 `media/` location, the `shared/`→`contracts/` follow-up, the
> 2026-07-20/21 reconciliations); nothing to extract — the *current* layout is described in
> `AGENTS.md`, and this plan is the *transition* record.

**Goal:** Give the monorepo a legible top-level shape before it goes public — four clear pillars
(`python`, `native`, `skills`, `apps`) plus support dirs — and resolve the two naming collisions
(`packages` vs `packaging`) and the one snake_case outlier (`eval_results`). No behaviour changes; this
is a move/rename pass only.

> **Why now:** this is move-heavy (it rewrites workspace member lists, Cargo path-deps, and many
> string references to old paths). Landing it **immediately before the open-source-prep flatten** means
> the giant `git mv` costs nothing in history terms (history is being dropped anyway) and
> `blackdeep-tech/knaif` is **born in its final shape** rather than carrying a reorg in its first commits.
> Do it after the OSS-prep scrub is otherwise ready, as the last change before the flatten.

---

## Decisions (locked 2026-07-19)

1. **Python pillar is named `python/`** (unambiguous, symmetric with `native/`).
2. **`skills/` is its own top-level pillar**, and **native skill impls are co-located** into each
   bundle: `skills/<name>/{python,native}`. This makes bundles truly self-contained (matches the
   "self-contained bundle" contract in `CLAUDE.md` / `docs/TOOL_SCHEMA.md`).
3. **`web/` stays in-repo, renamed `site/`** (public page), distinct from `docs/` (developer docs).

### What co-location costs (read before starting)

Co-locating skill crates means the **Rust crate graph now spans `native/crates/*` *and*
`skills/*/native`**. Because a Cargo workspace's members must sit under its root, **the workspace root
must be the repo root**. Concretely, several Rust config files **must stay at root** — they cannot move
into `native/`:

- `Cargo.toml`, `Cargo.lock` — the workspace manifest + lock.
- `rust-toolchain.toml` — rustup resolves it from the dir `cargo` runs in (repo root) upward.
- `rustfmt.toml` — rustfmt searches from each crate upward; only a **root** copy applies uniformly to
  crates under both `native/` and `skills/`.

Symmetrically, `pyproject.toml` stays at root — not because the uv workspace spans the repo (it only
lists `packages/knaif` today), but because it is the **repo-wide tool config**: `pytest` `testpaths`
includes `skills/`, and `ruff`/`pyright` config is repo-scoped.

**So Q3's honest answer:** the root's *irreducible* residents are the two workspace roots + toolchain
+ formatter + orchestration + meta. The reorg's real win is **pillar clarity**, not a bare root —
accept that trade knowingly.

> **Amended during R6 (2026-07-19):** `about.toml`/`about.hbs` turned out to belong on the "stays"
> list too, not the "leaves" list as first assumed above. `cargo about generate` resolves both the
> template and (by default) its config relative to **cwd**, and every invocation in this repo
> (`just licenses`) runs from the workspace root — so keeping them at root avoids adding an explicit
> `--config installers/licenses/about.toml` flag for no behavioural benefit. Only the loose logos
> actually left root (→ `docs/assets/`). See R6 below.

---

## Target structure

```
knaif/
├── python/                     # ← packages/
│   ├── core/                   # ← packages/knaif   (import package `knaif` UNCHANGED)
│   └── training/               # ← training/        (packages/training stub deleted)
├── native/
│   └── crates/                 # ← crates/  (non-skill crates only)
│       ├── knaif-core/  knaif-llm/  knaif-models/
│       └── knaif-skill-api/    # shared API — stays (not a specific skill)
├── skills/
│   ├── ffmpeg/{ python/  native/ }      # native/ ← crates/knaif-skill-ffmpeg
│   ├── documents/{ python/  native/ }   # native/ ← crates/knaif-skill-documents
│   └── io/{ python/ }                    # python-only; no native/ (honest runtime gap)
├── apps/
│   └── cli/                    # ← apps/knaif-cli   (crate/binary name `knaif-cli` unchanged)
├── shared/                     # models, runtime, parity (cross-runtime) — unchanged by *this* plan;
│                                 renamed → contracts/ in a later, separate pass (2026-07-20, a4d3710)
├── docs/                       # developer docs — unchanged
│   └── assets/                 # ← root logo.png, knaif-logo-rect.svg
├── site/                       # ← web/  (public MkDocs page)
├── evals/                      # ← eval_results/
├── installers/                 # ← packaging/
├── examples/ · scripts/ · notebooks/                      # unchanged
└── (root) Cargo.toml · Cargo.lock · rust-toolchain.toml · rustfmt.toml · pyproject.toml
         about.toml · about.hbs · justfile · mise.toml · models.yaml · eval_backends.yaml
         README · LICENSE · CLAUDE.md · AGENTS.md · .gitignore · .github/
```

**Key invariant:** the Python **import name stays `knaif`** (`packages/knaif/knaif/` → `python/core/knaif/`),
and Rust **crate names stay** (`knaif-skill-ffmpeg`, `knaif-cli`, …). Only *filesystem paths* and the
*manifests that reference them* change — so **no `import` line and no `use` line changes**. That is what
keeps the blast radius manageable.

---

## Steps

Use `git mv` throughout (keeps the moves reviewable in the working tree before the flatten).

- [x] **R1 — Python pillar.**
  - `git mv packages/knaif python/core` · `git mv training python/training`.
  - `git rm -r packages/training` (stub: only a README, not a uv member — confirmed 2026-07-19).
  - Edit **root `pyproject.toml`**: `members = ["python/core"]`; `testpaths = ["python/core/tests",
    "skills"]`; refresh the header comment (it names `packages/knaif` and `eval_results`) and any
    `pyright`/`ruff` path references.
  - Regenerate `uv.lock` if member paths are recorded there.
- [x] **R2 — Native (non-skill) crates.**
  - `git mv crates native/crates` (moves all crates); the skill crates leave in R3.
  - Update **root `Cargo.toml`** `members` + `workspace.dependencies` path-deps for the crates that
    stay: `native/crates/{knaif-core,knaif-models,knaif-llm,knaif-skill-api}`.
- [x] **R3 — Co-locate skill crates.**
  - `git mv native/crates/knaif-skill-ffmpeg skills/ffmpeg/native`
  - `git mv native/crates/knaif-skill-documents skills/documents/native`
  - In **root `Cargo.toml`**: members → `skills/ffmpeg/native`, `skills/documents/native`;
    `workspace.dependencies` path-deps → `{ path = "skills/ffmpeg/native" }` /
    `{ path = "skills/documents/native" }`. **Crate names unchanged**, so no `use` changes.
  - `skills/io` is untouched (python-only).
- [x] **R4 — Apps.**
  - `git mv apps/knaif-cli apps/cli`; update its `Cargo.toml` member path to `apps/cli`
    (package name `knaif-cli` stays → binary name unchanged).
- [x] **R5 — Rename support dirs + fix their references.**
  - `git mv web site` · `git mv eval_results evals` · `git mv packaging installers`.
  - **`evals` fallout (highest-reference):** the eval save-path convention in `CLAUDE.md`
    (`--save eval_results/runs/...`), `eval_results/INDEX.md`'s own naming rules, `evalsuite`
    default paths, and the root `pyproject.toml` comment. Grep and fix every `eval_results` string.
  - **`site` fallout:** `site/mkdocs.yml` `docs_dir`/paths, and any link from `docs/` or `README`.
  - **`installers` fallout:** `docs/RELEASE.md`, `justfile` package targets, and paths inside
    `installers/{package.sh,smoke.sh,linux,macos,windows}`.
- [x] **R6 — Root declutter (only what genuinely can leave).** Landed in `be6fe1e` (2026-07-19).
  - `git mv logo.png knaif-logo-rect.svg docs/assets/` — done; no README/site references needed updating
    (`site/` has its own independent logo copies under `site/docs/assets/`, not root's).
  - `about.toml`/`about.hbs` — **kept at root**, reversing the original plan (see the amendment
    under "What co-location costs" above): cargo-about resolves both paths relative to cwd, and
    every caller runs from repo root, so moving them would only add a `--config` flag for no gain.
  - **Leave at root**: `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `rustfmt.toml`,
    `pyproject.toml`, `about.toml`, `about.hbs`, `justfile`, `mise.toml`, `models.yaml`,
    `eval_backends.yaml`, and repo meta.
  - **2026-07-20 follow-up:** an uncommitted working-tree change had reverted part of this step —
    `docs/assets/{logo.png,knaif-logo-rect.svg}` deleted and re-created at a stray root-level
    `assets/` that nothing referenced. Reconciled back to the committed `docs/assets/` location.
  - **2026-07-21 amendment (owner decision):** the marks now live at a top-level `media/` after
    all — the README header renders `media/knaif-logo-rect.svg`, so the files are referenced and
    no longer stray. `site/docs/assets/` keeps its own copies under the MkDocs-conventional name
    (MkDocs can only serve from `docs_dir`); `docs/assets/` is gone. `docs/PROVENANCE.md` records
    the new locations.
- [x] **R7 — Sweep every remaining path reference.** After the moves, grep the tree for stale paths and
  fix docs/config (not just code):
  ```
  grep -rnI -e 'packages/knaif' -e 'crates/knaif-skill' -e 'apps/knaif-cli' \
            -e 'eval_results' -e 'packaging/' -e 'web/' -e '\btraining/' \
    --include='*.md' --include='*.toml' --include='*.yaml' --include='*.yml' \
    --include='*.py' --include='*.rs' --include='justfile'
  ```
  Targets to expect: `CLAUDE.md` (many path rows + the eval convention), `docs/ARCHITECTURE.md`,
  `docs/SDK.md`, `docs/NATIVE.md`, `docs/RELEASE.md`, `.github/instructions/`, `justfile`, `mise.toml`.
  Re-swept 2026-07-20: no live-config hits remain; the only `eval_results` strings left are inside
  historical docs (`docs/audits/`, `docs/experiments/`, pre-2026-07-19 `docs/plans/*`), which is
  expected — they're rename records, not active references.
- [x] **R8 — Verify (behaviour unchanged).** Re-run in full 2026-07-20:
  - `uv run pytest python/core/tests skills/ --tb=short` → 1501 passed, 38 skipped.
  - `cargo build --workspace` · `cargo test --workspace` · `cargo fmt --all --check` → all green.
  - `cargo about generate about.hbs -o installers/licenses/THIRD-PARTY-RUST.txt` → **not verified this
    pass**, `cargo-about` isn't installed in this environment; the invocation/paths are unchanged from
    the last successful generation (file on disk dated 2026-07-19) so this is a tooling gap, not a
    restructure regression.
  - Skill-load smoke: `create_agent("io", ...)` and `create_agent("ffmpeg", ...)` both load correctly
    (bundle-root `ctx.skill_dir` resolution survived the move, as expected).
  - `just check` end-to-end (pytest + coverage 83% + README inventory check + `cargo fmt --check` +
    `cargo clippy -D warnings`) → green. `apps/cli` (`knaif.exe`) built and `knaif skills list` runs,
    resolving both native skill crates correctly. `mkdocs build` for `site/` — **not verified**, `mkdocs`
  isn't installed in this environment (same class of tooling gap as `cargo-about`, not a regression).

---

## Interaction with open-source-prep

- **Sequence (decided 2026-07-19):** this restructure runs **first** — merged to `dev` **before
  open-source-prep's S/T work begins**, not merely before the X2 flatten. The reason is that most of
  open-source-prep *touches or documents the paths this pass renames*: S3 (notebook paths shift again),
  S5 (`about.toml` moves), S1 (`training/.env` → `python/training/.env`), T1/T4 (CONTRIBUTING/README
  document the layout), T7 (author metadata is in the moved `pyproject.toml`). Doing the scrub/docs
  first would mean re-doing them. So: **restructure → open-source-prep S → T → X**.
- **Can run in parallel** (layout-agnostic, not blocked by this pass): open-source-prep T2
  (`SECURITY.md`), T3 (`CODE_OF_CONDUCT.md`), and the *decision* half of T7 (how to claim the PyPI
  name).
- **R8 is a real gate:** land and verify this pass fully before starting the scrub — don't debug a
  broken workspace and hunt secrets at the same time.
- **Overlap with S3 (local-path leakage):** the notebooks flagged in S3 hard-code paths like
  `.../src/skills/ffmpeg/data/eval.jsonl`; after this reorg those paths shift again. Fix the notebooks
  **once, after** the restructure, so S3 doesn't fix a path this pass then re-breaks.
- **Overlap with S6 (purge paper trail):** this file references old commit-era paths only as *rename
  records*, so it survives S6's scrub — but drop it from the public tree if the plans folder is trimmed.

---

## Out of scope

- **Renaming the import package `knaif`** — it stays; only its directory moves. Same for Rust crate
  and binary names.
- **Splitting `site/` to its own repo** — decided against for now (in-repo, revisit post-v1 if it grows).
- **A `native/` impl for the `io` skill** — `io` stays python-only; the missing `native/` documents the
  gap honestly. Any new impl is a feature, not this pass.
- **Behaviour changes / refactors** surfaced while moving files — file them, don't fix them here.
