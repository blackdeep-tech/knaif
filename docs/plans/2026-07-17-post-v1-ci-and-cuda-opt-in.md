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

> **Progress 2026-07-29 — Workstream U is code-complete; what remains needs the build box.**
> U2/U3/U4/U5 are done. U1, U6 and U7 are `[~]`: every mechanism is built and tested, but each has a
> step that cannot be performed from a checkout.
>
> Still owed, and all of it is execution rather than design:
>
> - **Build both payloads and publish the assets.** `contracts/backends/backend-manifest.yaml` is
>   `status: unpublished` with placeholder URLs, so `backend install cuda` refuses with an
>   explanation rather than fetching a `TODO`. `package.sh` emits a manifest fragment with the real
>   checksums to paste in. Order matters — see `RELEASE.md` §7.
> - **Run the three cases against the PACKAGED payload on each OS.** The spike proved the mechanism
>   with a hand-built lib; this proves what users receive. WSL2 GPU passthrough is confirmed working
>   (decision log), so the Linux half needs no second machine.
> - **Verify the fatbin arch check against a real fatbin.** `verify_cuda_archs` has never run against
>   one, only against its own logic.
> - **Pin `Dockerfile.cuda`'s base image by digest**, and confirm the `13.3-devel-ubuntu22.04` tag
>   exists at all — CUDA 13 dropped several older distributions, and a newer base would silently
>   raise the payload's floor above the artifact's.
> - **Run the Windows installer wizard** to see the CUDA task render and confirm it is genuinely
>   optional. `/VERYSILENT` never builds the task tree, so this is a GUI step.
>
> **Progress 2026-07-30 — the Windows half is built and exercised end to end; only publishing and the
> Linux payload remain.** Of the five items above, three are closed: the payload is **built** (six
> real archs, 54 min, 668 MB), `verify_cuda_archs` has **run against a real fatbin** (and found the
> `sm_120a` truncation to be a live concern), and the **three cases pass against the packaged
> payload** (U6). The install path was rehearsed against a local HTTP server with the real fragment
> checksums — `list` → `install` → `verify` clean, a corrupted sha lands **nothing** in a fresh dir
> and leaves an existing install untouched, and the **stale-payload refusal** was driven end to end by
> stamping a 1.0.9 receipt: `backend list` reports `STALE`, the loader skips the directory, the run
> falls back and exits 0, and re-installing restores it. That is U2's "upgrade test, not just an
> install test", performed rather than argued.
>
> Still owed: **publish the assets** (U1) and the **Linux payload** (U7 — image unbuilt, base tag
> unconfirmed, digest unpinned).
>
> **Progress 2026-07-31 — the Linux payload is built, and building it found four defects.** The
> image is pinned by digest, the payload stages at 698 MB across seven files with per-file SHA256 in
> the manifest fragment, all six architectures are verified as SASS against a real fatbin
> (`sm_75 sm_80 sm_86 sm_89 sm_90 sm_120a`, plus `compute_90` PTX), and the floor audit passes at the
> same `GLIBC_2.34` / `GLIBCXX_3.4.30` / `CXXABI_1.3.9` floor the main artifact declares.
>
> **Every one of the four was invisible to a build that merely succeeded**, which is the argument for
> these checks existing at all:
>
> 1. **The CUDA base image was pinned by tag.** Also: the two-component tag this plan's prose asked
>    for does not exist — NVIDIA publishes three-component versions only.
> 2. **`CUDAARCHS` was never set on the containerised path.** It is the only way in (CMake
>    initialises `CMAKE_CUDA_ARCHITECTURES` from it; `llama-cpp-sys-2` offers no passthrough), and
>    the Windows flow hides it because the maintainer exports it before calling `--no-build`. The
>    container builds *inside* `package.sh`, where nothing else could. Unset, ggml's own default
>    fired and produced a list that is **not a subset of the release list**: SASS for `sm_86`,
>    `sm_89`, `sm_120a` and an unrequested `sm_121a`, with PTX only for `sm_75`, `sm_80`, `sm_90`.
>    A payload shipped from that build would have carried **no SASS for Turing, Ampere-80 or
>    Hopper** — and would have run perfectly on the machine that built it.
> 3. **The arch check died on a fatbin with no PTX.** `grep` exits 1 on no match, and under
>    `pipefail` that killed the script before the loop that names the missing arch. Every all-`-real`
>    list hits it, so the documented `KNAIF_CUDA_DEV_ARCHS` escape hatch had never once completed a
>    run since it was added.
> 4. **The floor audit modelled the payload in an isolation it never runs in** — see U7.
>
> Two costs worth recording. A cold six-arch build is **86 minutes** at three jobs (all 183
> ggml-cuda translation units, once per architecture); the same build on a warm volume restages in
> **34 seconds**, so a failure after the compile is cheap to retry and a failure that forces
> `--clean` is not. And the job cap is sized for Vulkan shader units at ~2 GB each — nvcc peaked
> around 1 GB of 7.5, so a CUDA build leaves most of the machine idle. Distinguishing the two kinds
> when picking the cap is an easy win nobody has taken.
>
> **The wizard GUI run is done (U4), and it was not a formality.** It found a defect no lint could
> reach and changed a shipped default:
>
> - **The Components → Next click stalled for one to two seconds.** `CudaOfferable` is evaluated
>   when Inno rebuilds the task list on leaving that page, and it was spawning `nvidia-smi`
>   synchronously — which costs ~1.8 s on a machine that has a driver, because it initialises one to
>   answer. A blocking `Exec` cannot be narrated (no message loop is running, so nothing repaints),
>   so the probe is now started with `ewNoWait` from `InitializeWizard` and settled in
>   `NextButtonClick`. By the time a user has read the licence and picked components the answer is
>   already on disk; a bounded poll behind a progress page covers anyone faster than that.
> - **The CUDA task is now checked by default.** U4 asked whether the component is "genuinely
>   optional" and the honest answer turned out to be "optional, but off was the wrong default". It
>   renders only when `CudaOfferable` holds — NVIDIA silicon, driver above the floor, no payload
>   installed — so unchecked meant declining acceleration on behalf of a user already proven to
>   benefit. This is **not** the F2 pattern: Ghostscript and LibreOffice render unconditionally,
>   which is what made a checked default wrong for them. The gate and the default are load-bearing
>   together, and `test_cuda_task_is_checked_by_default` says so.
> - **Verification is now a recipe.** `just installer-test` compiles with a throwaway `AppId`, its
>   own install directory and its own output dir, and passes `ISPP` defines through so branches
>   gated on hardware (`/DMinNvidiaDriver=9999`) can be reached without different hardware.
>   `RELEASE.md` §4 points at it instead of a hand-typed `ISCC` line.
>
> Exercised on an NVIDIA machine with a driver above the floor: no stall on Next, the task rendering
> under its own heading and pre-ticked, and the task correctly absent both below the floor and with
> a payload already in place.
>
> One thing the plan expected to decide was instead settled by building: the stale-payload refusal.
> The plan called for measuring whether ggml already rejects a mismatched lib before designing.
> `BackendStore` writes a version receipt and the loader refuses on it regardless, which turns that
> measurement into an optimisation question ("could we be more permissive?") rather than a
> prerequisite.

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

**2026-07-29 — the Linux-only split stands, but `release.yml` is re-scoped: a packaging check that
uploads, not a release.** Owner question — *is a CI that still needs manual steps the right shape?*
The instinct is right and the target was wrong. Manual steps are not the problem: publishing is
manual **by decision** (above), because the irreversible steps stay human. The problem is **two
authorities over one artifact set** — a CI-generated `SHA256SUMS` covering only the Linux half of a
draft the maintainer is still adding Windows files to. One rule removes it: **the checksum manifest
is generated once, locally, over the final complete set.** Three things follow, all in C3:

- **CI is not the only machine that can build the Linux artifact.** `just package-linux --rev=<tag>`
  builds from a clean checkout in the pinned container via Docker Desktop *on the maintainer's
  Windows box*, and the floor comes from the image, not the host. So the job cannot justify itself on
  capability — which is why its trigger moves to `pull_request` as well as the tag, where catching
  packaging breakage is worth something.
- **A self-hosted Windows runner is rejected**, not deferred — see C3 for why a public repo makes it
  the worst available option.
- **The asymmetry gets documented in `RELEASE.md`**, so a fork can see that Linux is fully available
  to it and that Windows is maintainer-built for stated reasons.

**2026-07-29 — the Linux CUDA payload is verified via WSL2 GPU passthrough. SPIKED, and it works.**
Docker Desktop's WSL2 backend exposes the host NVIDIA GPU to Linux containers, so U7's three cases
run here without a second machine. Measured the same day:
`docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi` reports
`NVIDIA GeForce RTX 3070 Laptop GPU`, `KMD Version: 610.74`, `CUDA UMD Version: 13.3` — the real
device, above the R580 floor, from inside a Linux container. The contingency stands if it ever
regresses: the payload is built but published only after a run on a real Linux box with an NVIDIA
driver. Do not publish an unrun payload — a backend that fails to `dlopen` presents to the user as
"CUDA didn't work", which is the least debuggable outcome available. Note this proves *passthrough*,
not the payload; running the three cases against the packaged payload is still owed.

**2026-07-29 — SETTLED: the payload publishes as loose per-file assets, not archives.** Owner
decision, answering U1's *DECIDE FIRST*. Each lib is its own release asset with its own `sha256`,
split across the two tags; `package.sh` stops tarring the payload. Two reasons, in order:

- **It puts no new code on the trusted install path.** `BackendStore` becomes `ModelStore`'s
  existing fetch → `.part` → hash → rename in a loop, plus a stage-then-swap of the directory.
  Nothing extracts an archive, so there is no member-name validation to get wrong on a path that
  runs against a URL. Nothing in the workspace unpacks a tarball today, on either OS.
- **It keeps per-file SHA pinning, which this plan already spent.** U6's rejection of static-linking
  the MSVC CRT into `ggml-cuda.dll` rests on a CRT fix republishing four ~1 MB assets rather than a
  ~123 MB lib. Archives would have quietly taken that back.

Accepted costs: ~7 assets per platform on the release page, and U5's manual fallback becomes a
multi-file download. Both are cosmetic, and the fallback exists to debug `backend install` rather
than as a route anyone is steered to. An archive form can be added on top of a working per-file
store later; removing extraction from a trusted path afterwards is the harder direction.

**2026-07-29 — audit pass against the live tree; four statements in this plan were false and are
corrected in place.** Each was a claim *about existing code*, which is the class of plan error that
survives review and fails at implementation time:

- U2's "`package.sh` stages `contracts/`" — it does not; it copies two named files
  ([`package.sh:458-459`](../../installers/package.sh#L458-L459)). Now a task, not a check.
- U3's "ggml already reports compute capability, so no new probe is needed" — `LlamaBackendDevice`
  carries name/description/backend/memory/type and no compute capability; the `compute capability
  8.6` string is a CUDA-init log, and the nudge by definition runs before CUDA exists on the box.
- U1's per-file assets vs. what `package.sh` actually emits (one tarball) — see U1.
- Workstream C's definition of done ("without anyone touching a build box") vs. the Windows decision
  taken the same day.

One further gap is not a false claim but a requirement with no mechanism behind it: **U2 states that
a stale payload must be refused and then lists only install-time mechanisms.** See U2.

Not adopted, and recorded so they are not re-raised: re-measuring the C1 baseline (the task already
says to), pinning the exact `nvidia/cuda` tag (U7 already says by digest), and making the driver gate
architecture-dependent for sm_90 — a data-center part that will not run this CLI. The `90-real` half
of that last one *is* adopted (U6), because it costs a few MB.

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

- **CUDA is a *correctness* requirement on Blackwell and an *optimisation* on Ampere.** Both are
  served by the same payload, but they justify very different nudge strength — see U3.
  - **"~3%" was the wrong number to quote and is withdrawn** *(2026-07-29)*. It is the **generation**
    column, and knaif's workload is prompt-decode-dominated (~4k-token prompt, ~32 output tokens —
    [`PERFORMANCE.md`](../PERFORMANCE.md) §3), so generation is the least relevant column in the
    table for this product. The **inference total** on the same two rows is 1524 ms CUDA vs 2143 ms
    Vulkan.
  - **But do not simply quote 41% instead — that table does not reconcile.** At the stated per-phase
    rates the two totals should be ~1.47 s and ~1.54 s; the published totals differ by ~600 ms with
    nothing accounting for it, and §6 rules out model load (excluded from "inference total"). So
    **no Ampere CUDA-vs-Vulkan figure is quotable until `PERFORMANCE.md` §2 is re-measured or the
    overhead is explained.** Fixing that doc is a prerequisite for U3's copy, not a footnote.
  - **The two-population split survives either way**, which is why this is a wording fix and not a
    re-decision: even at the pessimistic reading Ampere Vulkan is 2.1 s against CUDA's 1.5 s — both
    interactive, a preference. Blackwell Vulkan runs at CPU speed — not a preference. What changes is
    that U3 may no longer call the Ampere gap a rounding error.
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
- [ ] **C3 — `release.yml`** _(was F5)_: build → package → verify → attach the **Linux** artifacts to
  a **draft** GH Release. Automates part of the manual cut that v1 did in finalization E2/H3.
  (Add macOS only when macOS packaging lands post-v1.)
  - **REVISED 2026-07-29 (second pass) — it is a packaging *check* that happens to upload, not a
    release.** Three changes, all from the same realisation: `just package-linux --rev=<tag>` already
    builds the Linux artifacts from a clean checkout in the pinned container **on the maintainer's
    Windows box** via Docker ([`justfile:371-385`](../../justfile#L371-L385)). The floor comes from
    the image, not the host, so CI is not the only machine that can produce a correct Linux artifact —
    which means the job has to justify itself on something other than capability.
    - **Trigger on `pull_request` as well as on `v*.*.*`.** This is where the job earns its keep:
      packaging breakage found on the PR that caused it is free, and found on release day is
      expensive. Tag-time upload is the smaller half of its value, not the point of it.
    - **CI must NOT generate `SHA256SUMS`.** A checksum manifest covering only the Linux half, sitting
      in a draft that a human then adds Windows files to, is a manifest that is silently incomplete —
      one artifact set with two authorities over it. **`SHA256SUMS` is generated once, locally, over
      the final complete set**, immediately before publishing. That single rule is what makes the
      hand-off unambiguous, and it is the reason the job's output is "artifacts", never "a release".
    - **Say why Windows is maintainer-built, in `RELEASE.md`** — licensed VS install (the VC++
      redistribution grant), the CUDA toolkit a hosted image does not carry, and clean-room +
      GUI-upgrade verification that no runner can perform. A fork reading the workflow should be able
      to tell that the asymmetry is a decision, and that the Linux path is fully available to them.
  - **Rejected: a self-hosted Windows runner** *(2026-07-29)*. It is the obvious way to make CI cut a
    complete release and it is the wrong one. On a **public** repo a self-hosted runner is the
    documented worst case — a fork's PR can execute on the maintainer's machine — and it additionally
    requires that machine online at tag time. It buys nothing `just package-*` does not already give.
    Recorded here so it is not re-proposed as an obvious improvement.
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
  - **Known debt this will surface —** four notebooks carry metadata `nbstripout` strips
    (`notebooks/baseline_authoring.ipynb`, both under `skills/documents/notebooks/`,
    `skills/ffmpeg/notebooks/ffmpeg_skill_tester.ipynb`). Land the strip as its own `chore:` commit
    *before* the hooks job goes green-required, or the first CI run fails on unrelated churn.
    - **The `black` half of this is stale** *(re-checked 2026-07-29: `uv run black --check .` →
      198 files unchanged)*. No reformat is needed; only the metadata strip. The reason to still run
      `black --check` in CI is unchanged — `just check` runs `ruff` but never `black`, so formatting
      is enforced by nothing today.

---

## Workstream U — CUDA opt-in surface _(was finalization C6 + C6a's execution)_

- [~] **U1 — Publish the split payload artifacts** _(C6a's execution half)_. Per C6a, unchanged:
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
  - **"Inside the artifact" means the product artifact, not the payload** *(2026-07-29)*. The payload
    cannot carry the manifest that describes how to fetch the payload. The manifest is read by an
    already-installed `knaif` deciding what to download, so it ships beside `core_tools.yaml` and
    `model-manifest.yaml` in the installed `contracts/` tree — which is exactly why the staging gap
    in U2 has to be closed before this is testable end to end.
  - **DECIDED 2026-07-29 — loose per-file assets** (see the decision log). The table above assumed
    individually addressable files with per-file shas, and `package.sh` disagreed: it emitted a
    single `tar czf` payload containing all four Linux libs
    ([`package.sh:206`](../../installers/package.sh#L206)), which nothing on the fetch path can
    unpack. The packaging moves to the plan, not the other way round — `package.sh` stops tarring
    and stages a payload tree whose files are uploaded individually across the two tags. The
    rejected alternative (two archives + verified extraction in `BackendStore`) is recorded in the
    decision log so it is not re-proposed.
- [x] **U2 — `knaif backend install cuda` / `backend remove cuda`** _(C6's original scope, verbatim)_:
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
    - Note the atomicity is **per file** — `pull_with_progress` is `.part` → hash → `rename` of one
      path ([`store.rs:231`](../../native/crates/knaif-models/src/store.rs#L231)). Installing four
      files that way is four atomic operations, not one, so an interrupted install still leaves a
      torn directory. `BackendStore` wants stage-all → verify-all → swap the directory. Cheap to get
      right when written down, annoying to retrofit.
  - **Scope, therefore:** a `contracts/backends/` manifest, `BackendManifest`/`BackendEntry` types
    carrying a file list, a `BackendStore` paralleling `ModelStore`, the `backend install|remove|list`
    subcommand, and a release-readiness guard mirroring
    `python/core/tests/test_model_manifest_release_ready.py`. Likely native-only — the Python runtime
    does not manage backends — so a guard test rather than a second reader.
  - **Ship the new contract — this is a packaging task, not a check** *(corrected 2026-07-29)*.
    `package.sh` does **not** stage `contracts/`; it creates `contracts/runtime` and
    `contracts/models` and copies exactly two named files
    ([`package.sh:235`](../../installers/package.sh#L235),
    [`:458-459`](../../installers/package.sh#L458-L459)). A new `contracts/backends/` subdirectory
    reaches the installed tree only if it is added there explicitly, with a test. Left as written,
    this failed at the user — a manifest nobody has, hence a payload nobody can install — rather than
    at the build.
  - **Refusing a stale payload needs a mechanism at LOAD time; install-time pinning cannot do it**
    *(2026-07-29)*. Difference 3 above states the requirement and every task under it is an install
    path, which leaves the requirement unmet by construction: `~/.knaif/backends` is deliberately
    outside the install dir and survives an app upgrade, and it is scanned **first**, so an upgraded
    `knaif` loads the previous release's `ggml-cuda` before any `backend install` command could run.
    The loader says so itself — *"Ordering picks the device; it does not decide whether a stale lib
    gets loaded … prevented by pinning it to the exe's build when it is installed, not by the
    loader"* ([`llama.rs:98-111`](../../native/crates/knaif-llm/src/llama.rs#L98-L111)).
    - **Measure before designing.** Both this plan and the audit that found the gap assume a
      mismatched payload is undefined behaviour; neither knows that. Drop a previous-release
      `ggml-cuda` beside a current exe and look: if ggml's registry already rejects it cleanly, a
      version check in `BackendStore` is enough. If it loads and misbehaves, the payload needs a
      version-stamped directory the loader resolves per build. Half an hour, and it picks the design.
    - Either way this needs an **upgrade test**, not just an install test: install payload → bump the
      binary → assert the old payload is refused rather than loaded.
- [x] **U3 — Driver-aware detection gate** _(C6's audit finding, verbatim)_: offer/nudge CUDA only
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
    **Ampere** it is an optional optimisation, worth ~618 MB only to someone who wants it. Detect the
    **compute capability**, not just the vendor.
    - **Corrected 2026-07-29 — "ggml already reports it, so no new probe is needed" is false.**
      `LlamaBackendDevice` exposes index/name/description/backend/memory/device_type and **no**
      compute capability; the `compute capability 8.6` line is a CUDA-backend init log, and the whole
      point of the nudge is that it fires on a machine where CUDA is *not* installed, so that log
      does not exist. Use the probe U3 already requires for the driver — one `nvidia-smi
      --query-gpu=driver_version,compute_cap --format=csv` (or the NVML equivalent) returns both
      fields, so this costs nothing beyond what the gate above already does. Verified on the bench
      box: `NVIDIA GeForce RTX 3070 Laptop GPU, 610.74, 8.6`.
    - **Say "Ampere", not "Ampere and earlier".** sm_86 is the only pre-Blackwell architecture ever
      measured; Turing and everything else in the arch list are unmeasured, and the copy should not
      imply otherwise.
    - **Do not call the Ampere gap a rounding error** — the "~3%" it rested on is withdrawn, and no
      replacement figure is quotable until `PERFORMANCE.md` §2 is reconciled (see *Why this is
      release-blocking*). Until then the Ampere nudge says CUDA is faster and optional, without a
      number.
  - **Do not gate on a hardcoded "Blackwell is broken" list.** The defect is in a llama.cpp/driver
    code path and may be fixed upstream, at which point a baked-in list silently lies in the other
    direction. Phrase the nudge from the measured local reality where possible, and re-measure the
    Vulkan/CUDA split on each supported architecture when the llama.cpp pin moves — record it in
    `docs/PERFORMANCE.md`, which is already the file that owns this claim.
- [x] **U4 — Re-enable the installer's CUDA component.** v1 ships the Windows installer with **no
  CUDA component** precisely because U2 didn't exist for its opt-in task to call (finalization C6).
  Restore it once U2 lands, and verify the component is genuinely optional.
  - **Two things the opt-in task inherits from `backend install`, unaddressed until now**
    *(2026-07-29)*. Ticking that box makes the installer download **~618 MB mid-install**. Decide
    what it does when the network is absent or the download fails — an installer that rolls back a
    working knaif because an optional GPU extra timed out is a worse outcome than no component at
    all, so the failure must be non-fatal and re-runnable from the CLI afterwards.
  - **And decide what uninstall does with it.** `backends_dir()` sits outside the install dir by
    design (that is what makes `backend install` elevation-free), so uninstalling knaif leaves
    ~618 MB behind unless the uninstaller is told about it. `backend remove` exists for the user who
    knows; the uninstaller is for the one who does not.
- [x] **U5 — Document the manual path's retirement.** `docs/RELEASE.md` / `NATIVE.md` will, as of v1,
  tell CUDA users to copy the payload into `~/.knaif/backends` by hand. Replace that with the command
  — and keep the manual path documented as the fallback, since it is what the loader actually
  supports and it is how U2 will be debugged.
  - **Also correct the Vulkan claim** flagged in *Why this is release-blocking*: `RELEASE.md` §6 and
    any release body derived from it currently tell every NVIDIA user that Vulkan is enough.

### - [x] U6 — Make `package.sh --kind=cuda` emit a real payload on Windows

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

- [x] Add a Windows branch to `package.sh`'s `cuda` kind that stages `ggml-cuda.dll` **plus** the
      NVIDIA redist DLLs from `$CUDA_PATH/bin/x64` into a payload tree, mirroring the Linux `.so.13`
      branch — and **stop producing the static app** for that kind, or keep it behind an explicit
      legacy flag so nobody publishes it by accident. *(The legacy shape is `--legacy-windows-cuda-app`
      and is not publishable. The payload also carries the four CRT DLLs, which is why it measures
      **668 MB**, not the 618 MB this plan estimated before they were part of it.)*
- [x] Keep the `NVIDIA-CUDA-EULA.txt` hard-fail the Linux branch already has. The redist carries a
      redistribution condition the VC++ runtime does not, and the payload is the artifact that ships it.
- [x] Build at the full release arch list, not the spike's single arch:
      `CUDAARCHS="75-real;80-real;86-real;89-real;90-virtual;120-real"` (`RELEASE.md` §3), and verify
      with `cuobjdump --list-elf` / `--list-ptx` that every arch actually landed — matching
      `sm_[0-9]+[a-z]*`, since `sm_[0-9]+` silently truncates `sm_120a`. *(Built and verified
      2026-07-30: six SASS archs + `compute_90` PTX, 54 minutes. The `sm_120a` concern is live, not
      theoretical — a `120-real` request does land as `sm_120a`. `KNAIF_CUDA_DEV_ARCHS` exists for
      packaging iteration and names its output `-DEVARCH` so it cannot be published by mistake.)*
  - [x] **Add `90-real` to that list** *(2026-07-29)*, in `RELEASE.md` §3 as well as here so the two
        cannot drift. `90-virtual` alone leaves Hopper on PTX JIT, and PTX JIT is the documented
        exception to CUDA's minor-version driver compatibility — i.e. exactly the case the R580 floor
        does *not* cover. A few MB of fatbin removes the caveat. **Deliberately not doing** the
        larger version of this fix (making the minimum-driver gate architecture-dependent): sm_90 is
        a data-center part that will not run this CLI, and a per-arch driver floor is complexity
        bought for nobody. Note also that the R580 floor is a documented CUDA 13 requirement, not
        something tested here — the bench box is on R610.74.
- [x] **Re-run the three cases on Windows against the packaged payload**, not a hand-built one. The
      spike proved the mechanism; this proves the thing users receive. **Done 2026-07-30**, against
      `dist/staging/knaif-1.1.0-windows-x64/bin/knaif.exe` (the vulkan artifact) and the payload
      installed by `backend install` from a local server: (1) empty backends dir → Vulkan1 = RTX
      3070, 36/36 layers, exit 0, soft Ampere nudge fires; (2) payload present → `load_backend:
      loaded CUDA backend from …\ggml-cuda.dll`, `found 1 CUDA devices (Total VRAM: 8191 MiB)`, all
      layers on `CUDA0`, nudge silent, identical rendered command — **cross-build ABI compatibility
      now proven against the packaged payload, not a hand-built one**; (3) no usable GPU → falls back
      to Vulkan0, exit 0, and a plain run prints nothing but the command.
  - [x] **`CUDA_VISIBLE_DEVICES=""` does not test case 3 on Windows** *(found 2026-07-30)*. With the
        empty string, `ggml_cuda_init` still reported `found 1 CUDA devices` and every layer went to
        `CUDA0` — the case passed by not running. Windows treats an env var set to the empty string
        as *unset*, so the recipe deletes the variable it meant to set. **Use
        `CUDA_VISIBLE_DEVICES="-1"`**, which produces the intended `failed to initialize CUDA: no
        CUDA-capable device is detected`. The empty-string form is written into this plan and twice
        into [native-branch-finalization](2026-07-15-native-branch-finalization.md), so **the
        2026-07-16 Windows case-3 ✔ there should be read as unproven**; the Linux one is unaffected
        (POSIX keeps an empty variable set, and NVIDIA documents that as "no devices visible").
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

### - [~] U7 — A CUDA build image, separate from the release image

The pinned Linux release image deliberately has **no CUDA toolkit**
([`Dockerfile:77-79`](../../installers/linux/Dockerfile#L77-L79)): it would add 3–5 GB to serve an
artifact that image does not produce, and the file already names the fix — *"if a CUDA payload build
is ever containerised it belongs in a separate image `FROM nvidia/cuda:*-devel-ubuntu22.04`."*
That is now needed, because a published payload must not inherit its floor from whoever built it —
the same argument [portable-builds](2026-07-27-portable-builds.md) made for the main artifact.

- [x] `installers/linux/Dockerfile.cuda` on `nvidia/cuda:13.3.0-devel-ubuntu22.04`, **pinned by
      digest** (`sha256:e7cb1151…`) alongside the tag, in the release image's `ubuntu:22.04@sha256:…`
      form, reusing its apt snapshot and Rust pin so the two floors cannot drift.
      - **The tag this plan asked for does not exist** *(2026-07-30)*. NVIDIA publishes
        three-component versions only — there is no `13.3-devel-ubuntu22.04` alias, and requesting
        one fails with `no such manifest`. The file always named a real tag; it was this plan's prose
        that was wrong, which is worth recording because the same shorthand appears wherever a CUDA
        image is discussed.
      - **The floor question is closed rather than reopened.** CUDA 13 did drop several older
        distributions, but 22.04 is not among them: `13.3.0-devel-ubuntu22.04` and
        `13.3.1-devel-ubuntu22.04` are both published, so the payload's glibc floor matches the
        release image by construction. 13.3.1 is newer and deliberately not adopted here — moving the
        toolkit under a payload whose Windows half is already built is its own decision, not a side
        effect of adding a digest.
- [x] A `just` recipe beside `package-linux`, with **its own cache volume** — the one-volume-per-kind
      rule exists because kinds hard-link into the same `target/release/` and a stale SONAME symlink
      makes the next kind panic with `AlreadyExists`. `just package-linux --kind=cuda` routes to
      `Dockerfile.cuda`, its own image and `knaif-target-cuda`.
- [~] **The payload's floor must be checked like any other artifact.** `scripts/check_elf_deps.py`
      over the staged payload, and a load test in a floor container — a backend that fails to
      `dlopen` presents as "CUDA didn't work", which is the least debuggable outcome there is.
      - **Floor audit passes** *(2026-07-31)*: `ok 4 binaries: every DT_NEEDED is staged or
        base-system`. The measured floor is `GLIBC_2.34` / `GLIBCXX_3.4.30` / `CXXABI_1.3.9` — the
        same floor the main artifact declares, so the payload cannot fail on a distro the artifact
        itself claims to support. That equality is the whole reason for the separate pinned image.
      - **The audit had to be taught a fourth way a dependency can be satisfied.** It knew three —
        staged in the directory, base system, GPU driver — and a payload has a fourth: the host
        application. `libggml-cuda.so` needs `libggml-base.so.0`, which ships in the main artifact
        and is already loaded by the process that `dlopen`s the backend, so the linker resolves it
        from the link map rather than from the payload directory. Tolerated for loadable `ggml-*`
        backends only; a core library still hard-fails, and the main-artifact audit is untouched
        because its `staged` check short-circuits first.
      - **Windows never showed this**, and still does not audit its payload at all:
        `check_pe_imports.py` runs over the main artifact's `bin/`, which contains `ggml-base.dll`.
        The Linux path audits the payload directory alone, which is stricter. Worth closing that
        gap, but as its own change rather than inside U7.
      - **Still owed: the load test itself.** The floor audit reads ELF headers; it does not prove
        the backend `dlopen`s. That needs a Linux knaif artifact to load it, which is a separate
        build — the three cases stay open until it has run.
- [x] **Spike WSL2 GPU passthrough BEFORE relying on it** *(decided 2026-07-29)*. Building the
      payload needs the toolkit, not a GPU — but running the three cases needs an NVIDIA driver on
      Linux, and there is no second machine. Docker Desktop's WSL2 backend can expose the GPU to a
      Linux container; if it does, U7's verification runs here with no extra hardware. Treat it as
      unproven until a container reports the device. **If it does not work, the payload is built but
      not published** until it has run on a real Linux box with an NVIDIA driver — an unrun backend
      reaches the user as "CUDA didn't work", which is the least debuggable failure available.
      - **IT WORKS — verified 2026-07-29**, written up in
        [`installers/linux/README.md`](../../installers/linux/README.md). This box was stale, not
        outstanding. Re-confirmed 2026-07-30 on a newer host driver (R610 series, container CUDA UMD
        13.3), so the three cases run here and the payload is not blocked on a second machine.
      - Re-confirming costs a **30 MB base image, not the 4 GB devel one** — the container runtime
        injects the driver and `nvidia-smi`, so proving passthrough needs no CUDA image at all.
        `docker run --rm --gpus all ubuntu:22.04 nvidia-smi` is the whole check.
      - What it does **not** prove: Ampere is the *optional* half of the story. The Blackwell case
        that makes CUDA a must-have rather than an optimisation still has no hardware behind it here,
        and the nudge's two strengths remain asserted on one card.
- [x] **Windows has no container answer and does not need one** *(reaffirmed 2026-07-29; stated in
      [`installers/linux/README.md`](../../installers/linux/README.md))*. Its
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

CI runs on every PR with the split jobs green — **including Linux packaging**, so a packaging break
surfaces on the PR that caused it. A `v*.*.*` tag additionally attaches the **Linux** artifacts to a
draft release, built in the pinned container rather than on a runner. No `SHA256SUMS` comes out of
CI: the maintainer adds the Windows artifacts, generates the checksum manifest once over the complete
set, and publishes.

**Corrected 2026-07-29** — this previously read "a tag produces a draft release with artifacts +
`SHA256SUMS` without anyone touching a build box", which is wrong twice. Windows artifacts are built
on the maintainer's machine, so CI can never cut a complete release; and a `SHA256SUMS` emitted by CI
would cover only the Linux half of a set a human is still adding to — one artifact set with two
authorities over it. A tag that produces a complete draft is not the bar. A tag that produces the
half that *can* be automated, without leaving behind a manifest that looks complete and is not, is.

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
