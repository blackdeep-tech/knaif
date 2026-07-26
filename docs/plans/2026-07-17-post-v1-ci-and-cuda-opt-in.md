# Post-v1 — CI, release automation, and the CUDA opt-in surface

**Status:** Planning · **Created:** 2026-07-17 · **Completed:** —
**Owner:** core · **Ref:** follows [native-branch-finalization](2026-07-15-native-branch-finalization.md); runs after the OSS-prep pass

> **Kept 2026-07-23** (S7 decision — **unexecuted roadmap**, and load-bearing as the named
> owner of scope two *kept* plans moved out: `native-branch-finalization` (Workstream F, and
> C6) and `monorepo-dual-runtime` (Phase 10's CI remainder) both point here by name. Deleting
> it would orphan work that has no other home.) Not in the cited-from-source sense — its
> referrers are docs — but this is the third tier mislabel found in S7; see the prep plan.
>
> **Re-verified live 2026-07-23 — nothing has started, and the gap claims still hold:**
> `.github/workflows/` does not exist (C1–C3 unbuilt), and `apps/cli/src/main.rs`'s `Command`
> still has only `SkillsAction` and `ModelsAction`, so `backend` is still a new subcommand.
> Paths repointed to the post-restructure tree (`packages/knaif` → `python/core`,
> `apps/knaif-cli/` → `apps/cli/`, `crates/` → `native/crates/`, `packaging/smoke.sh` →
> `installers/smoke.sh`) — C1's job definition and C3's gate both named directories that no
> longer exist, which for a plan whose whole output is CI config is a build failure waiting to
> happen. The C1 baseline was re-measured (see the note there): stale green-state numbers are
> worse than none, because the task's own instruction is to *reproduce* them, not discover them.
>
> **Status note:** Not started; **nothing here may begin before v1.0.0 is published**. This plan
> collects the two things the v1 branch consciously did **not** ship: **CI/release automation**
> (Workstream F, exempt from the v1 Definition of Done since 2026-07-15) and the **CUDA opt-in
> surface** (C6, deferred 2026-07-17). Both were moved out so the finalization plan could close
> without carrying open boxes it would never tick — this is a scope **relocation**, not new work and
> not a re-decision. Every decision that governs the CUDA half (C1's arch list, C5's Option 3, C6a's
> split + hosting, C6b's scan order) is **already made and must not be re-litigated**; it lives in
> the finalization plan and is referenced, not restated.
>
> **Ordering constraint that matters:** C6 is what bakes GitHub URLs into an artifact. It must not
> ship until the org transfer is done, or an installed release depends on GitHub's org redirect
> forever. The OSS-prep plan does that transfer before v1.0.0 publishes, so by the time this plan
> starts the constraint is already satisfied — **write the URLs against `blackdeep-tech/knaif`.**

**Goal:** Automate what v1 did by hand (CI + `release.yml`) and make the already-built CUDA payload
installable with one command instead of a manual file copy.

---

## Why these two live in one plan

They are independent in code and could be split. They share a file because they share a **release**:
C6 is the first thing that publishes assets to a GH Release *tag* (C6a: lib on the product tag,
redist on `redist-cuda-13.3`), and F5 is what automates publishing to that tag. Doing C6 by hand and
then automating it a week later means cutting the same release mechanics twice. Do F5 first, then let
C6's assets ride it.

If only one gets funded, **F1 is worth more than C6**: CI protects every future change, whereas C6
saves a documented file copy for the subset of users who have both an NVIDIA card and a reason to
want CUDA over Vulkan.

---

## State at hand-off (what v1 already proved — do not redo)

| Fact | Where it was established |
|---|---|
| 5-arch fatbin builds; `CUDAARCHS` env var is the only way to set the arch list | finalization C2 |
| The arch list actually took (`cuobjdump --list-elf` → exactly the requested archs) | finalization C4(a) |
| Option 3 (`GGML_BACKEND_DL`) works on **both** Windows and Linux | finalization C5a/C5b |
| The loader scans `~/.knaif/backends` **first**, so an installed CUDA backend wins the device | finalization C6b |
| The Linux payload builds, loads, and wins the device | finalization D4 |
| Payload shape = **split** (`ggml-cuda` 125 MB on the product tag; NVIDIA redist 493 MB on `redist-cuda-13.3`); both land in **one** backend dir; per-file sha in the manifest | finalization C6a |
| Driver floor is **R580+** (CUDA 13); `nvcuda.dll` presence alone is insufficient | finalization C1 |
| Eval-parity GGUF = `qwen3-4b-v1` (`knaif-qwen3-4b-v1-q4_k_m.gguf`, on HF `blackdeep/knaif`) | finalization F3 |

**So the CUDA work here is the surface only** — the payload exists and works today via a manual copy
into `~/.knaif/backends`.

---

## Workstream C — CI & release automation _(was finalization Workstream F)_

- [ ] **C1 — Split CI jobs** _(was F1)_ in `.github/workflows/`: `python` (pytest + lint/type on
  `python/core`), `native` (`cargo test`/`clippy`/`fmt`, base + `--features llama`),
  `docs`, `packaging`. Python-only PRs must not require native inference; native-only PRs
  must not run notebooks.
  - **Baseline to encode:** the green state, **re-measured 2026-07-23** — `uv run pytest`
    **1532 passed / 7 skipped**, `cargo test --workspace` **216 passed**, clippy clean on both
    `--workspace --all-targets` and `-p knaif-cli --features llama`, `cargo fmt --all --check` clean.
    CI's first run should reproduce those, not discover them. *(Finalization A1's original figures —
    1494/38 python, 204→213 cargo — are superseded; the skip count fell because the restructure
    resolved the conditional imports behind most of them. Re-measure again before writing the job:
    a baseline is only useful on the day it is taken.)*
  - **Note:** CI secrets/protections do **not** survive an org transfer — which is exactly why this
    plan runs *after* OSS-prep. Set them up once, in the final org.
- [ ] **C2 — Loader compatibility job** _(was F2)_: assert the active shared skill manifests
  (`ffmpeg`, `documents`) load in **both** the Python loader and the Rust loader; stale `io`
  excluded unless explicitly opted in.
- [ ] **C3 — `release.yml`** _(was F5)_: on tag `v*.*.*`, matrix (windows-2022, ubuntu-22.04)
  → build → package → attach to a draft GH Release with `SHA256SUMS`. Automates the manual cut that
  v1 did in finalization E2/H3. (Add macos-* only when macOS packaging lands post-v1.)
  - **The manual procedure is the spec:** `docs/RELEASE.md` (finalization G1) documents exactly what
    v1 did by hand. `release.yml` should mechanize *that*, and any divergence is a bug in one of them.
  - Reuse `installers/smoke.sh` (finalization E1) as the job's gate — it already checks
    version-vs-`Cargo.toml`, exe-relative skill resolution from an unrelated cwd, and an offline mock
    `plan --json`, and it never downloads.
- [ ] **C4 — Eval-parity lane** _(was F3's unbuilt half; the decision itself stays in finalization
  F3)_: register a `rust-cli` backend shelling `knaif plan --skill X --json` in `eval_backends.yaml`
  + `just eval-parity` diffing `python-agent` vs `rust-cli` (±2%). Use the `knaif-*` / `qwen3-4b-v1`
  names — do **not** hard-code the retired lane.
- [ ] **C5 — Enforce the git conventions in CI** _(added 2026-07-25, when the conventions landed in
  CONTRIBUTING.md)_. The hooks in `.pre-commit-config.yaml` are **opt-in**, so today a contributor
  who never ran `just hooks-install` is caught only at review. Two small jobs close that:
  - a `hooks` job running `pre-commit run --all-files --show-diff-on-failure` (use the
    `pre-commit/action` or a plain `uv run`), which also makes the formatters a gate — `just check`
    currently runs `ruff` but never `black --check`, so formatting is unenforced;
  - a **PR-title lint**, since PRs are squash-merged and the title *is* the commit subject on
    `main`. Reuse `scripts/check_commit_msg.py` — it takes a file path, so the job writes the title
    to a temp file and calls it. One implementation, so the hook and CI cannot disagree.
  - **Branch protection** on `main` in the same pass: require the C1 jobs, squash-merge only,
    linear history, no direct pushes. Like all settings here, protections do **not** survive an org
    transfer — this is why the whole plan runs after OSS-prep.
  - **Known debt this will surface:** four notebooks are not `black`-formatted and carry metadata
    `nbstripout` strips (`notebooks/baseline_authoring.ipynb`, both under
    `skills/documents/notebooks/`, `skills/ffmpeg/notebooks/ffmpeg_skill_tester.ipynb`). Land that
    reformat as its own `chore:` commit *before* the hooks job goes green-required, or the first CI
    run fails on unrelated churn.

---

## Workstream U — CUDA opt-in surface _(was finalization C6 + C6a's execution)_

- [ ] **U1 — Publish the split payload artifacts** _(C6a's execution half)_. Per C6a, unchanged:
  | Artifact | Tag | Why |
  |---|---|---|
  | `ggml-cuda` (~125 MB) | the **product release** (e.g. `v1.1.0`) | ABI-coupled to the exe's build; a tag-scoped URL structurally cannot serve a newer lib to an older exe |
  | `cudart`/`cublas`/`cublasLt` (~493 MB) | a dedicated **`redist-cuda-13.3`** tag (pre-release, never deleted) | keyed to the CUDA toolkit, shared across releases |
  - Write every URL against **`blackdeep-tech/knaif`**. The repo is public from v1.0.0 (E0a), so the
    tokenless `HttpFetcher` works against release assets with no auth.
  - The backend manifest is a **bill of materials, not a catalog** (C6a) — it ships *inside* the
    artifact, pins that release's exact files by per-file sha, and **must never resolve "latest"**.
    A backend mismatch is undefined behaviour that reads like a driver bug, which is why this is
    stricter than the model manifest.
- [ ] **U2 — `knaif backend install cuda` / `backend remove cuda`** _(C6's original scope, verbatim)_:
  reuse the `HttpFetcher` + SHA-pinned manifest path (host is not a constraint) writing to the backend
  dir the loader scans; the installer's opt-in task calls the **same** command.
  - Today `apps/cli/src/main.rs` has `Command` with only `SkillsAction` and `ModelsAction`
    (re-checked 2026-07-23 — still true) — `backend` is a new subcommand. The only existing trace is `backends_dir()` in
    `native/crates/knaif-models/src/store.rs`, whose doc comment already states the contract this
    task depends on: the dir sits **outside** the install dir so `backend install` needs no
    elevation and still works when the install is read-only (AppImage mount).
  - Model the fetch on `models pull`: same fetcher, same sha-pinning, same store-outside-the-install-dir
    shape (`~/.knaif/backends`, per C6b).
- [ ] **U3 — Driver-aware detection gate** _(C6's audit finding, verbatim)_: offer/nudge CUDA only
  when an NVIDIA GPU is present **and the driver is R580+** (CUDA 13 floor) — `nvcuda.dll` presence
  alone is insufficient; probe the driver version (e.g. NVML / `nvidia-smi`) and, if too old, tell the
  user to update rather than install a payload that will fail to load. First-run nudge fires only when
  NVIDIA + adequate driver + CUDA backend absent.
  - **Tests** (from C6): install→present→auto-selected next run; absent→CPU/Vulkan, no fault;
    **old-driver→no offer + update hint**; nudge fires only when applicable.
- [ ] **U4 — Re-enable the installer's CUDA component.** v1 ships the Windows installer with **no
  CUDA component** precisely because U2 didn't exist for its opt-in task to call (finalization C6).
  Restore it once U2 lands, and verify the component is genuinely optional.
- [ ] **U5 — Document the manual path's retirement.** `docs/RELEASE.md` / `NATIVE.md` will, as of v1,
  tell CUDA users to copy the payload into `~/.knaif/backends` by hand. Replace that with the command
  — and keep the manual path documented as the fallback, since it is what the loader actually
  supports and it is how U2 will be debugged.

---

## Definition of done

CI runs on every PR with the split jobs green; a `v*.*.*` tag produces a draft release with
artifacts + `SHA256SUMS` without anyone touching a build box; and an NVIDIA user on an R580+ driver
runs one command, restarts, and gets CUDA — while a user on an old driver or an AMD card is told
something true instead of being handed a payload that won't load.

---

## Explicitly out of scope (inherited from finalization; recorded so they aren't re-opened)

- **macOS packaging / notarization** — still deferred; add `macos-*` to C3's matrix only when it lands.
- **Combined "everyone-gets-CUDA" single bundle** — out. The DL mechanism ships; the heavy combined
  bundle does not.
- **S3/CloudFront mirror** — open as a fast-follow if GH Releases' reach or speed disappoints. The
  fetcher has **no fallback logic today**, so this is real work, not a config change. Cost was never
  the deciding factor (C6a).
- **Persistent inference daemon**, **first-class logging**, **Windows code signing** — each its own plan.
