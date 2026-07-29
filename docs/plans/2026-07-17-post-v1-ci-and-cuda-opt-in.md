# Post-v1 — CI, release automation, and the CUDA opt-in surface

**Status:** Active — **Workstream U is release-blocking** · **Created:** 2026-07-17 · **Completed:** —
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
> **Status note — superseded for Workstream U 2026-07-28** (see the decision log): U is **started and
> release-blocking**. Its gating precondition is met either way — the constraint that mattered was the
> **org transfer**, not the tag, and the repo has been `blackdeep-tech/knaif` since OSS-prep. Note the
> original wording is loose about "published": v1.0.0 and v1.0.1 went to **PyPI only**, and no GitHub
> Release has ever existed, which is precisely why the first one can now carry CUDA. Workstream C is
> unaffected and remains not-started.
>
> **Status note (original, 2026-07-17):** Not started; **nothing here may begin before v1.0.0 is published**. This plan
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

---

## Decision log

**2026-07-28 — Workstream U is now release-blocking; the first published release waits for it.**
Owner decision, taken mid-cut of what was going to be a Vulkan-only 1.0.2. Two facts drove it:

- **Vulkan is not a viable NVIDIA shipping target on Blackwell.** See *Why this is release-blocking*
  below. The measurement predates this plan and was simply never connected to the ship decision.
- **There are no users and no installs.** No GitHub Release has ever existed and no native artifact
  has ever been downloadable ([windows-installer-polish](2026-07-25-windows-installer-polish.md),
  *Release framing*). So there is no upgrade path to protect, no published checksum to invalidate,
  and no cost to holding the release except time. The first artifact anyone downloads can simply
  **be** the one with CUDA in it, rather than being followed by a fast-follow that fixes it.

This **reverses** the C6 deferral of 2026-07-17 — which was correct on its own terms (a published
payload with no command to fetch it, and an installer task with nothing behind it, are both worse
than no offer) and is answered here by building the surface rather than by shipping without it.

**2026-07-28 — SETTLED: this release is `1.1.0`, not `1.0.2`.** The release on the bench was
versioned `1.0.2` and its changelog already strained to explain a patch that publishes a platform.
Adding a **new CLI subcommand** (`knaif backend`) and a GPU backend distribution surface makes PATCH
wrong under semver by any reading, and U1's own table had already assumed the product tag would be
`v1.1.0`. Applied the same day across the three declarations, `Cargo.lock`, the Rust licence report,
the changelog, and every doc that named the release or used it in a command example.
[portable-builds](2026-07-27-portable-builds.md) carries the reversal of its own 2026-07-27 decision.
1.0.0 and 1.0.1 remain spent on PyPI, so this is the next number, not a renumbered first release.

**2026-07-28 — the packaging-correctness work already done is not discarded.** The Windows artifacts
built and verified on 2026-07-28 (app-local CRT, `NOTICE`, task-page fixes, upgrade path exercised
under a throwaway `AppId`) stay valid and simply ride this release instead of the earlier one. The
verification that must be **re-run** after CUDA lands is the clean-room pass on both OSes and the
checksums, because the artifact set changes.

**2026-07-29 — CI builds Linux only; Windows artifacts are built locally and attached by hand.**
Owner decision. Windows release builds run **directly on Windows, never in a container** — and
specifically on the maintainer's own box rather than a hosted runner. Three consequences worth
stating, because each closes a question that was otherwise open:

- **The VS licensing question is moot.** The VC++ redistribution grant is limited to licensed Visual
  Studio users; that condition is satisfied by the maintainer's own VS Community install and stops
  being a question the moment the build never moves to a hosted image. Nothing to confirm, and
  [`PROVENANCE.md`](../PROVENANCE.md)'s "licensed builder" condition already describes this shape.
- **No CUDA toolkit install per job.** Hosted Windows images do not ship the CUDA toolkit, so a
  Windows payload job would download multiple GB every run. Building locally avoids it entirely.
- **CI can never cut a complete release on its own**, and that is accepted. Windows artifacts are
  attached by hand, which is what `docs/RELEASE.md` already describes.

**2026-07-29 — `release.yml` builds and verifies; it does not publish.** The irreversible steps stay
under human control. Consistent with `RELEASE.md` §5: a pushed tag cannot be moved under the
`release-tags` ruleset, a published Release is public immediately, and a PyPI version can never be
reused. CI producing checked artifacts and a human performing the one-way step is the split that
matches which failures are recoverable.

**2026-07-29 — CI lands AFTER 1.1.0; the first release is cut by hand.** This accepts the cost the
plan warned about ("doing C6 by hand and then automating it a week later means cutting the same
release mechanics twice") and takes it deliberately: `RELEASE.md` is the spec `release.yml`
mechanises, and **that spec has never been executed end to end**. Encoding an unexecuted procedure
means debugging the procedure and its automation simultaneously. Cut once by hand, then automate a
path known to work. Workstream U is already release-blocking; putting CI in front of it would delay
the first release for no gain.

**2026-07-29 — the Linux CUDA payload is verified via WSL2 GPU passthrough if that works.**
Docker Desktop's WSL2 backend can expose an NVIDIA GPU to Linux containers, which would let U7's
three cases run without a second machine. **Unproven here — it needs a short spike before being
relied on**, and if it does not work the payload is built but published only after a run on a real
Linux box with an NVIDIA driver. Do not publish an unrun payload: a backend that fails to `dlopen`
presents to the user as "CUDA didn't work", which is the least debuggable outcome available.

---

**Goal:** Automate what v1 did by hand (CI + `release.yml`) and make the already-built CUDA payload
installable with one command instead of a manual file copy.

---

## Why these two live in one plan

They are independent in code and could be split. They share a file because they share a **release**:
C6 is the first thing that publishes assets to a GH Release *tag* (C6a: lib on the product tag,
redist on `redist-cuda-13.3`), and F5 is what automates publishing to that tag. Doing C6 by hand and
then automating it a week later means cutting the same release mechanics twice. Do F5 first, then let
C6's assets ride it.

~~If only one gets funded, **F1 is worth more than C6**: CI protects every future change, whereas C6
saves a documented file copy for the subset of users who have both an NVIDIA card and a reason to
want CUDA over Vulkan.~~

**Superseded 2026-07-28.** That judgement rested on "a reason to want CUDA over Vulkan" being a
preference. On Blackwell it is not a preference — see below — and the file copy it describes does
not exist on Windows at all. **U now outranks C, and gates a release; C does not.** The reasoning
was sound given what was believed at the time; the measurement that contradicts it was taken
2026-07-14, after this plan was written, and was never connected back to it.

---

## Why this is release-blocking

**Vulkan is not a viable NVIDIA shipping target on Blackwell.** From
[`docs/PERFORMANCE.md`](../PERFORMANCE.md) §2 — the canonical scorecard, measured 2026-07-14 on
knaif's real workload (Qwen3-4B q4_k_m, 3938-token ffmpeg prompt, `n_ctx = 8192`), not on
`llama-bench`:

| GPU | Backend | generation |
|---|---|---:|
| RTX 3070 Laptop (Ampere, sm_86) | CUDA | 90.9 tok/s |
| RTX 3070 Laptop | Vulkan | 88.6 tok/s — **within 3%** |
| RTX 5080 (Blackwell, sm_120) | CUDA | ~80 tok/s |
| **RTX 5080** | **Vulkan** | **~5.7 tok/s — tied with CPU (~5.9)** |

A user with the newest NVIDIA card runs the "GPU" artifact at CPU speed. That is not a slower
option, it is a product that reads as broken, and no amount of release-note wording fixes it. The
sm_120 / coopmat2 path is the identified culprit.

Three consequences that shape the workstream:

- **CUDA is a *correctness* requirement on Blackwell and a ~3% *optimisation* on Ampere.** Both are
  served by the same payload, but they justify very different nudge strength — see U3.
- **A Vulkan-only artifact cannot be the first thing anyone downloads.** With no users today
  (decision log), that is a scheduling choice rather than a regression to manage.
- **Do not derive hardware ratios from the `5080` CUDA row.** PERFORMANCE.md flags it as suspect —
  a laptop Ampere part out-decoding a desktop Blackwell is not physically credible. It is directionally
  fine for the Vulkan comparison here and unfit for anything else.

> **Related doc bug, fix with this plan:** [`docs/RELEASE.md`](../RELEASE.md) §6 tells users the
> default artifact "auto-selects Vulkan when a capable driver is present, else CPU — on every vendor,
> so most users need nothing else." That is false on Blackwell and is currently the text a release
> body would be written from.

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
- [ ] **C3 — `release.yml`** _(was F5)_: on tag `v*.*.*`, build → package → verify → attach to a
  **draft** GH Release with `SHA256SUMS`. Automates part of the manual cut that v1 did in
  finalization E2/H3. (Add macOS only when macOS packaging lands post-v1.)
  - **REVISED 2026-07-29 — the original matrix `(windows-2022, ubuntu-22.04)` is wrong twice over**;
    see the decision log. Both corrections matter more than they look:
    - **Linux only, and inside the pinned container.** Building the Linux artifact directly on an
      `ubuntu-22.04` runner reintroduces the exact defect
      [portable-builds](2026-07-27-portable-builds.md) exists to eliminate: a runtime floor inherited
      from whatever the builder happens to be, with nothing to warn you. The job runs
      `installers/linux/build-in-container.sh`, so the floor is the image's, not the runner's. A
      hosted runner has Docker, so this costs nothing.
    - **Windows is not in the matrix at all.** Windows release builds happen locally on the
      maintainer's box and are attached by hand.
  - **It builds and verifies; it does not publish.** Draft only, never `--publish`. The tag, the
    public Release and the PyPI upload stay manual because none of them can be undone.
  - **Two checks cannot move into CI, and saying so prevents a false sense of coverage:**
    the **Windows clean room** needs Windows Sandbox, which exists on no runner; and the **installer
    upgrade path** needs a GUI run, since `/VERYSILENT` never builds the task tree. Both stay in
    `RELEASE.md` §4 as human steps. A green pipeline is not a verified release.
  - **Sequencing: this lands AFTER 1.1.0 ships** (decision log). `RELEASE.md` is the spec, and it
    has never been executed end to end — mechanising an unexecuted procedure debugs both at once.
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
  - **The backend manifest does not exist yet, and "reuse the manifest path" understates this**
    *(established 2026-07-28)*. `contracts/` holds `models/` and `runtime/` only; `knaif-models`
    exposes `backends_dir()` — the resolved directory — and nothing behind it. There is no manifest
    file, no `BackendEntry`, no store. Five structural differences from the model manifest, each a
    schema change rather than a parameter:
    1. **Many files per entry, not one.** A model is a single GGUF with one `sha256`. A backend is
       `ggml-cuda` plus the NVIDIA redist libs, plus whatever runtime must sit beside them on that
       OS. C6a already calls for per-file shas; `ModelEntry`'s shape cannot express it.
    2. **Two source tags, one destination directory.** The ABI-coupled lib rides the product tag
       while the redist rides `redist-cuda-13.3` (C6a), so URLs are per-file and point at different
       releases while all files land in one directory.
    3. **The binding is inverted.** The model manifest is deliberately forgiving — an upgrade with an
       unchanged recommendation re-downloads nothing, because the store keys on the model's own
       filename. A backend is the opposite: an upgrade MUST replace the ABI-coupled lib, and an
       older payload against a newer binary has to be **refused**, not tolerated. This is the
       "stricter than the model manifest" requirement above, made concrete.
    4. **Platform-keyed.** GGUFs are platform-independent; backend payloads are per OS and arch.
    5. **It should carry the driver floor** that U3 gates on, so bumping the toolkit is one edit
       beside the payload rather than a second edit in the gate.
  - **What genuinely is reusable**, and it is real leverage: the injectable `Fetcher` trait, the
    verify-then-atomically-install logic, `VerifyOutcome`, and `backends_dir()`. The download
    plumbing is done; the schema and the store are new.
  - **Scope, therefore:** a `contracts/backends/` manifest, `BackendManifest`/`BackendEntry` types
    carrying a file list, a `BackendStore` paralleling `ModelStore`, the `backend install|remove|list`
    subcommand, and a release-readiness guard mirroring
    `python/core/tests/test_model_manifest_release_ready.py`. Likely native-only — the Python runtime
    does not manage backends — so a guard test rather than a second reader.
  - **Confirm the new contract actually ships.** `package.sh` stages `contracts/` into the artifact,
    but verify it picks up a new subdirectory rather than assuming: a manifest that does not reach
    the installed tree is a payload nobody can install, and it would fail at the user rather than at
    the build.
- [ ] **U3 — Driver-aware detection gate** _(C6's audit finding, verbatim)_: offer/nudge CUDA only
  when an NVIDIA GPU is present **and the driver is R580+** (CUDA 13 floor) — `nvcuda.dll` presence
  alone is insufficient; probe the driver version (e.g. NVML / `nvidia-smi`) and, if too old, tell the
  user to update rather than install a payload that will fail to load. First-run nudge fires only when
  NVIDIA + adequate driver + CUDA backend absent.
  - **Tests** (from C6): install→present→auto-selected next run; absent→CPU/Vulkan, no fault;
    **old-driver→no offer + update hint**; nudge fires only when applicable.
  - **Nudge strength is not uniform, and this is new** *(2026-07-28)*. The gate above decides
    *whether* CUDA is installable; it does not distinguish the two populations in *Why this is
    release-blocking*. On **Blackwell (sm_120)** a Vulkan run generates at CPU speed, so the payload
    is what makes the product work and the nudge should be prominent and stated in those terms. On
    **Ampere and earlier** Vulkan is within 3% of CUDA, so the same message would be scaremongering
    over a rounding error — there it is an optional optimisation, worth ~618 MB only to someone who
    wants it. Detect the **compute capability**, not just the vendor: ggml already reports it
    (`Device 0: … compute capability 8.6`), so no new probe is needed.
  - **Do not gate on a hardcoded "Blackwell is broken" list.** The defect is in a llama.cpp/driver
    code path and may be fixed upstream, at which point a baked-in list silently lies in the other
    direction. Phrase the nudge from the measured local reality where possible, and re-measure the
    Vulkan/CUDA split on each supported architecture when the llama.cpp pin moves — record it in
    `docs/PERFORMANCE.md`, which is already the file that owns this claim.
- [ ] **U4 — Re-enable the installer's CUDA component.** v1 ships the Windows installer with **no
  CUDA component** precisely because U2 didn't exist for its opt-in task to call (finalization C6).
  Restore it once U2 lands, and verify the component is genuinely optional.
- [ ] **U5 — Document the manual path's retirement.** `docs/RELEASE.md` / `NATIVE.md` will, as of v1,
  tell CUDA users to copy the payload into `~/.knaif/backends` by hand. Replace that with the command
  — and keep the manual path documented as the fallback, since it is what the loader actually
  supports and it is how U2 will be debugged.
  - **Also correct the Vulkan claim** flagged in *Why this is release-blocking*: `RELEASE.md` §6 and
    any release body derived from it currently tell every NVIDIA user that Vulkan is enough.

### - [ ] U6 — Make `package.sh --kind=cuda` emit a real payload on Windows

**This is the gap that would otherwise ship an NVIDIA story with a hole in it** — U1–U5 assume a
payload exists for each OS, and on Windows it does not. `--kind=cuda` there still produces the
**historical static-with-redist app**, not an Option 3 loadable payload
([`NATIVE.md:284`](../NATIVE.md#L284), [`RELEASE.md:66-72`](../RELEASE.md#L66-L72)), so a Windows
user has nothing to install and the installer component in U4 would have nothing to place.

**The mechanism is already proven on Windows — only the packaging is missing.** Finalization
(2026-07-16) built a loadable `ggml-cuda.dll` by hand and ran all three required cases against the
**non-CUDA exe**: default → CPU; payload dropped in → `load_backend: loaded CUDA backend from …` with
real layer offload; payload present but no usable GPU (`CUDA_VISIBLE_DEVICES=""`) → clean silent CPU
fallback, exit 0. That run also proved **cross-build ABI compatibility** between the default artifact
and an independently produced payload, and that the **NVIDIA redist resolves from beside
`ggml-cuda.dll`** in the backends dir with no PATH games. So this task is a packaging change on a
proven path, not a spike.

- [ ] Add a Windows branch to `package.sh`'s `cuda` kind that stages `ggml-cuda.dll` **plus** the
      NVIDIA redist DLLs from `$CUDA_PATH/bin/x64` into a payload tree, mirroring the Linux `.so.13`
      branch — and **stop producing the static app** for that kind, or keep it behind an explicit
      legacy flag so nobody publishes it by accident.
- [ ] Keep the `NVIDIA-CUDA-EULA.txt` hard-fail the Linux branch already has. The redist carries a
      redistribution condition the VC++ runtime does not, and the payload is the artifact that ships it.
- [ ] Build at the full release arch list, not the spike's single arch:
      `CUDAARCHS="75-real;80-real;86-real;89-real;90-virtual;120-real"` (`RELEASE.md` §3), and verify
      with `cuobjdump --list-elf` / `--list-ptx` that every arch actually landed — matching
      `sm_[0-9]+[a-z]*`, since `sm_[0-9]+` silently truncates `sm_120a`.
- [ ] **Re-run the three cases on Windows against the packaged payload**, not a hand-built one. The
      spike proved the mechanism; this proves the thing users receive.
- [~] **Rejected: statically linking the MSVC CRT into `ggml-cuda.dll`** *(considered and dropped
      2026-07-28)*. It was raised to shrink the set of Microsoft files knaif redistributes, and it
      is more defensible here than for the main artifact — a loadable backend sits behind ggml's
      **C ABI** with paired alloc/free through the backend vtable, so the cross-heap hazard that
      rules out `/MT` for `knaif.exe` ↔ `llama.dll` is structurally much weaker. Dropped on two
      independent grounds:
  - **It destroys servicing granularity.** App-local deployment means knaif owns CRT updates (see
    [`PROVENANCE.md`](../PROVENANCE.md)). With the CRT as separate DLLs, a Microsoft security fix
    updates four files of ~1 MB total, each individually SHA-pinned in the payload manifest. Linked
    in, the same fix requires re-publishing **`ggml-cuda.dll` itself — ~123 MB**. (Not the whole
    ~620 MB payload: `cublasLt64_13` and friends are NVIDIA's, live on the `redist-cuda-13.3` tag,
    and are untouched by a CRT fix. The asymmetry still runs the wrong way by two orders of
    magnitude.)
  - **The reason for wanting it evaporated.** The motive was licensing discomfort, and there is no
    licensing problem: Microsoft documents app-local deployment as a supported method with its own
    walkthrough, and the Distributable List grants the files. Static linking would not have removed
    the determination anyway — MSVC's static runtime is Microsoft code under the same terms. It
    removes files, not obligations.
  - Corroboration, not proof: **Ollama ships the CRT dynamically in each backend directory**
    (ten files per dir, four dirs), rather than linking it in.

### - [ ] U7 — A CUDA build image, separate from the release image

The pinned Linux release image deliberately has **no CUDA toolkit**
([`Dockerfile:77-79`](../../installers/linux/Dockerfile#L77-L79)): it would add 3–5 GB to serve an
artifact that image does not produce, and the file already names the fix — *"if a CUDA payload build
is ever containerised it belongs in a separate image `FROM nvidia/cuda:*-devel-ubuntu22.04`."*
That is now needed, because a published payload must not inherit its floor from whoever built it —
the same argument [portable-builds](2026-07-27-portable-builds.md) made for the main artifact.

- [ ] `installers/linux/Dockerfile.cuda` on `nvidia/cuda:13.3-devel-ubuntu22.04`, pinned by digest,
      reusing the release image's apt snapshot and Rust pin so the two floors cannot drift.
- [ ] A `just` recipe beside `package-linux`, with **its own cache volume** — the one-volume-per-kind
      rule exists because kinds hard-link into the same `target/release/` and a stale SONAME symlink
      makes the next kind panic with `AlreadyExists`.
- [ ] **The payload's floor must be checked like any other artifact.** `scripts/check_elf_deps.py`
      over the staged payload, and a load test in a floor container — a backend that fails to
      `dlopen` presents as "CUDA didn't work", which is the least debuggable outcome there is.
- [ ] **Spike WSL2 GPU passthrough BEFORE relying on it** *(decided 2026-07-29)*. Building the
      payload needs the toolkit, not a GPU — but running the three cases needs an NVIDIA driver on
      Linux, and there is no second machine. Docker Desktop's WSL2 backend can expose the GPU to a
      Linux container; if it does, U7's verification runs here with no extra hardware. Treat it as
      unproven until a container reports the device. **If it does not work, the payload is built but
      not published** until it has run on a real Linux box with an NVIDIA driver — an unrun backend
      reaches the user as "CUDA didn't work", which is the least debuggable failure available.
- [ ] **Windows has no container answer and does not need one** *(reaffirmed 2026-07-29)*. Its
      payload builds in the same VS Developer shell as the main artifact, on the maintainer's own
      machine — which is also what keeps the VC++ redistribution grant's "licensed Visual Studio
      user" condition satisfied without anything further to confirm (see
      [`PROVENANCE.md`](../PROVENANCE.md) and [portable-builds](2026-07-27-portable-builds.md) P1).
      Say so, so the asymmetry is a decision rather than an omission.

---

## Definition of done

**Split, because only one half gates the release** *(2026-07-28)*.

**Workstream U — required before the first published release:**

An NVIDIA user on an R580+ driver runs one command, restarts, and gets CUDA — **on Windows and on
Linux**, from a payload built by the pinned toolchain rather than by hand. A Blackwell user is told
plainly that they need it; an Ampere user is told it is optional and why; a user on an old driver or
an AMD card is told something true instead of being handed a payload that won't load. Both payloads
pass the three cases (absent → CPU/Vulkan; present → offloads; present-with-no-usable-GPU → clean
silent fallback) **as packaged**, and `docs/RELEASE.md` no longer claims Vulkan suffices everywhere.

**Workstream C — not release-blocking, and explicitly allowed to land after:**

CI runs on every PR with the split jobs green, and a `v*.*.*` tag produces a draft release with
artifacts + `SHA256SUMS` without anyone touching a build box.

---

## Explicitly out of scope (inherited from finalization; recorded so they aren't re-opened)

- **macOS packaging / notarization** — still deferred; add `macos-*` to C3's matrix only when it lands.
- **Combined "everyone-gets-CUDA" single bundle** — out. The DL mechanism ships; the heavy combined
  bundle does not.
  - **Re-examined 2026-07-28 and still out, but the reason changed.** It was out because Vulkan was
    believed to serve NVIDIA adequately, which Blackwell disproves. It stays out on **size**: the
    NVIDIA redist is **493 MB** (`cublasLt64_13` alone is 442 MB) against a **26 MB** artifact, and
    bundling re-ships half a gigabyte of byte-identical NVIDIA DLLs to every user on every release,
    including the majority with no NVIDIA card at all.
  - **What this leaves open, and U3 must answer:** a Blackwell user who installs and runs before
    installing the payload gets one CPU-speed request and may conclude the product is broken. The
    opt-in download is only acceptable if the nudge reaches them **before** that first slow run, not
    after. If it cannot, revisit — a third "cuda" artifact per OS is the fallback, not a bundle.
- **S3/CloudFront mirror** — open as a fast-follow if GH Releases' reach or speed disappoints. The
  fetcher has **no fallback logic today**, so this is real work, not a config change. Cost was never
  the deciding factor (C6a).
- **Persistent inference daemon**, **first-class logging**, **Windows code signing** — each its own plan.
