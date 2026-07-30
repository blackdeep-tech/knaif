# Native Branch Finalization — close `feature/native-implemetation`

**Status:** Done · **Created:** 2026-07-15 · **Completed:** 2026-07-19
**Owner:** core · **Ref:** PR #39 · merge `e74f82a`

**Goal:** Close `feature/native-implemetation` — merge-readiness, default-model auto-select, CUDA
multi-arch build, Linux packaging, and a manually-cut v1 release — then merge to `dev`.

> **Status note:** **CLOSED — merged to `dev` on 2026-07-19 (PR #39, merge `e74f82a`).** This is the close
> point per H3.2 ("the branch is closed here; everything below is release mechanics"). Definition of
> Done met: **H1 green on both boxes**, the **v1.0.0 artifact set built + smoked + checksummed (E2)**,
> and **H2's Windows install smoke verified (2026-07-19)**. Workstreams **A, B, C, D, E, G are closed**
> (E2 includes the `knaif-`-prefixed model-identifier rename + artifact rebuild). Everything still open
> is **post-close** and lives in the follow-on plans — OSS-prep → org transfer → tag → publish, then
> CI + CUDA opt-in — **not** branch work. The one non-blocker left is C4(b) Blackwell runtime proof
> (needs the RTX 5080). See **["Not closable from the Linux dev box"](#not-closable-from-the-linux-dev-box--what-each-one-waits-on-2026-07-17)**.
**Done:** Workstream A (A1/A2/A3/A4), runtime (B1/B2/B3), **C5a
Windows AND Linux — all three legs each (CPU, Vulkan, CUDA opt-in)**, **C5b GO — Option 3 on both
OSes**, **D0–D4 (Linux packaging: package.sh Linux path, CPU+Vulkan artifacts, AppImage+tarball,
clean-env verify, CUDA opt-in payload)**, C2 multi-arch build + C4(a) fatbin proof, **C3**, **G1–G4
(RELEASE.md, NATIVE.md truth-up, per-runtime skill docs, status pass)**, **H1**, E1, E3.
**All owner decisions are now made** — C1 amended (`90-virtual`), C6b
(scan `~/.knaif/backends` first), C6a (split the payload; **all on GH Releases**), E0a (public at
v1.0.0; org move → Blackdeep).
**Scope narrowed 2026-07-17 (owner):** **C6 is DEFERRED** (with C6a's execution) and **Workstream F
(CI) is MOVED OUT** — both now live in
[2026-07-17-post-v1-ci-and-cuda-opt-in.md](2026-07-17-post-v1-ci-and-cuda-opt-in.md). The **org move
happens AFTER this branch closes**, via a dedicated OSS-prep pass — safe because deferring C6
removes the last thing that would bake a GitHub URL into an artifact (the shipped model manifest
points at HF `blackdeep/knaif`, and no artifact carries a GitHub org URL).
**Remaining before merge:** none — **merged to `dev` 2026-07-19 (PR #39).** Post-close work
(OSS-prep → transfer → tag → publish; then CI + CUDA) lives in the follow-on plans. ·
**Created:** 2026-07-15 · **Owner:** core · **Target merge:** dev
**Scope decision (2026-07-15, refined 2026-07-16, narrowed 2026-07-17):** ship a **v1 native release**
by closing the branch with: merge-readiness + CUDA multi-arch build + Linux packaging + a
**manually-cut** v1 release + default-model auto-select. **CI + `release.yml` automation + Phase 10
eval-parity + the CUDA opt-in *surface* (C6) are post-merge FOLLOW-ONS, not close blockers** (see
Definition of Done — the single authoritative scope statement). macOS is explicitly **out** (post-v1).
**v1 ships CPU + Vulkan auto-selected; the CUDA payload is built and proven (D4/C2/C4a) but installed
by hand** until C6 lands in the follow-on plan.

> **THIS IS THE OPERATIVE DOC for closing the branch — execute from here.** It is the single
> source of truth for remaining work and for every decision made 2026-07-15/16. The dual-runtime
> plan ([2026-06-17-monorepo-dual-runtime.md](2026-06-17-monorepo-dual-runtime.md)) is the
> **design/history record** (the *why* behind the architecture); where the two differ on what to
> do next, **this file wins**. Decisions locked here are mirrored back into the dual-runtime plan
> only as pointers, not as a second task list.
>
> Closeout plan for the whole native effort on `feature/native-implemetation` (~305 files,
> Phases 4–9 of the dual-runtime plan). Everything here must land — or be consciously deferred
> with a recorded decision — before the branch merges to `dev`. Reference docs:
> [NATIVE.md](../NATIVE.md) §12, [PERFORMANCE.md](../PERFORMANCE.md).
>
> **Layout note (2026-07-23).** This is a **point-in-time closeout record**, accurate to the
> pre-restructure tree it was written against. Paths below use the **old layout** and are left
> as-written on purpose — rewriting a dated history to the current tree would falsely imply the
> work was done against it. Translate with [2026-07-19-repo-restructure.md](2026-07-19-repo-restructure.md):
> `packages/knaif` → `python/core`, `apps/knaif-cli` → `apps/cli`, `crates/*` → `native/crates/*`,
> `shared/{runtime,models}` → `contracts/{runtime,models}`, `packaging/` → `installers/`. The
> restructure post-dates the 2026-07-19 merge this plan records.

## Definition of done

The branch is closeable when: both test suites are green **locally**, the artifact set builds,
installs, and runs cleanly on Windows **and** Linux — **CPU + Vulkan on both; the CUDA payload built
and proven, but not yet installable by command (C6 deferred)** — a **manual v1 release** is cut
(merge → tag → publish, per H3), `run` is usable without `--model`, and every checkbox in Workstreams
**A–E, G, H** is checked or struck-through with a reason.

**Two explicit EXEMPTIONS from that "every checkbox" rule** (owner calls 2026-07-15 and 2026-07-17).
This is the single scope statement — the header, TODO, and plan index all defer to it:

1. **Workstream F (CI + `release.yml` automation)** — post-merge. The first v1 release is cut by
   hand; merge gates on **local** green (H1), not on `.github/workflows/`, which does not yet exist.
2. **C6 + C6a's execution (the CUDA opt-in surface)** — post-merge. The payload itself is done and
   proven (C2 built the 5-arch fatbin, C4a verified it, D4 proved the Linux payload loads and wins
   the device); what's deferred is `knaif backend install cuda`, the R580 driver gate, and the
   first-run nudge. **v1 users get CPU/Vulkan automatically and can drop the payload into
   `~/.knaif/backends` by hand** (C6b's loader already scans it first).

Both exemptions now live in
[2026-07-17-post-v1-ci-and-cuda-opt-in.md](2026-07-17-post-v1-ci-and-cuda-opt-in.md).

**Release-ordering consequence (2026-07-17):** because C6 is deferred, **nothing in a v1 artifact
references the GitHub org**, so the org move no longer has to precede E2.
It moves to the post-close OSS-prep pass. **Order: E2 → H1/H2 → merge →
OSS-prep → transfer → tag → publish.** If C6 is ever pulled back into v1, E0a's original
"transfer first" constraint returns with it — its manifest is what bakes the URLs.

---

## Workstream A — Merge-readiness (must be green first)

- [x] **A1 — Green baseline established 2026-07-16.** Nothing was red; no fixes needed.
  | Check | Result |
  |---|---|
  | `uv run pytest --tb=short` | **1494 passed, 38 skipped** (now 1496 — A4 added 2) |
  | `cargo test --workspace` | **204 passed** (now 213 — B1 added 9) |
  | `cargo test --features llama` (`$KNAIF_TEST_GGUF=models/knaif-qwen3-1.7b-v1-q6_k.gguf`) | **205 passed** — `llama::tests::inference_produces_text` **ran** (not skipped): real CPU inference |
  | `cargo clippy --workspace --all-targets -- -D warnings` | clean |
  | `cargo clippy -p knaif-cli --features llama --all-targets -- -D warnings` | clean — `llama-cpp-sys-2`/`llama-cpp-2` genuinely compiled (~3 min) |
  | `cargo fmt --all --check` | clean |
  - **`--features llama,dynamic-backends` could NOT run — the feature does not exist yet.**
    `dynamic-backends` ships in **`llama-cpp-sys-2`**, but neither `knaif-llm` nor `knaif-cli`
    forwards it (`knaif-llm` exposes only `llama`/`cuda`/`vulkan`). Adding that passthrough **is
    C5a's first step**, so this clippy pass moves to **C5a's** exit criteria, not A1's.
- [x] **A2 — DONE 2026-07-17.** Reconciled `docs/TODO.md` + `NATIVE.md` against reality.
  - **E0a's three follow-ups: (a) and (c) were already closed** — the manifest header now opens
    "THIS IS A BILL OF MATERIALS, NOT A LIVE CATALOG", and the guard exists as
    `packages/knaif/tests/test_model_manifest_release_ready.py` (recommended model must have a real
    url + sha; 7 tests pass with the version guard). **(b)** — TODO's "uploading the GGUFs is the last
    owner action" — is already corrected to "PUBLISHED — DONE (verified 2026-07-16)".
  - **DOC-TRUTH BUG FOUND + FIXED: `NATIVE.md` §5.3 documented the backend scan order backwards.**
    It said "the exe's directory, then `$KNAIF_BACKENDS_DIR`" — the **opposite** of what C6b decided
    and what `backend_dirs` actually does (`~/.knaif/backends` FIRST). Left as-is it would have taught
    the next reader the exact arrangement that made the opt-in CUDA payload inert. Rewritten with the
    PCI-id/first-registered-wins reasoning.
  - Also truthed-up: §5.3 (`dynamic-backends` is the **shipping** model, not "shape not yet locked";
    `$ORIGIN` RPATH; 9–14 CPU variants), §9 (Linux tarball + AppImage, per-kind table, Linux builds
    itself), §10 (release builds use `dynamic-backends`; Linux one-step `package.sh`), §12 (see G2).
  - `docs/TODO.md`'s native block already carried the 2026-07-17 rescope; G4 does the final pass.
- [x] **A4 — Version bumped 0.1.0 → 1.0.0 + drift guard added (2026-07-16).** All three surfaces
  moved together: `Cargo.toml:17` `[workspace.package]`, `packages/knaif/pyproject.toml:7`,
  `packaging/windows/knaif.iss:18` `AppVersion`. Guard =
  `packages/knaif/tests/test_version_consistency.py` (2 tests): asserts the three **agree** (rather
  than pinning a literal, so a future bump touches only the declarations) + semver shape (E0 tags
  `v*.*.*`). **Mutation-tested:** injecting drift into `knaif.iss` fails the test and names the
  offending surface. Grep for other `0.1.0`: only `skills/*/skill.yaml` (per-skill versions, a
  **separate** namespace — deliberately not bumped) and `dist/staging/` (stale build output).
- [x] **A3 — Branch-name typo: acknowledged, no action (by design).** `implemetation` is
  misspelled; it is only a local branch name and dies with the branch at H3.6. No code change.

---

## Workstream B — Runtime completeness

- [x] **B1 — Default-model auto-select — DONE 2026-07-16.** `run` is now usable without `--model`.
  Implemented in `apps/knaif-cli/src/main.rs` as `select_model` (+ `select_model_with`, the same
  logic with the build's llama capability injected so both build shapes are testable) →
  `offer_recommended_download` → `ask_yes_no`. Wired into `cmd_run` (`DownloadPolicy::Prompt`) and
  `cmd_plan` (`YesOnly`, or `Never` under `--batch`/`--json`); `--yes` added to `PlanArgs`.
  - **`confirm_action` reuse (as required):** rather than a second prompt path, the tty/consent
    primitive was extracted as `ask_yes_no` and `confirm_action` now calls it — the two differ only
    in what a non-tty *means* (destructive → error; optional download → decline), which is why one
    function could not serve both directly.
  - **Consent prompt size is read from the manifest's `size_bytes`** (`human_size`) instead of a
    hard-coded "~2.5 GB", so it can't drift from the model actually offered.
  - **Order of operations fixed:** model resolution moved from the top of `cmd_run` to *after* the
    empty-request bail, safety gate, and dependency preflight — previously it ran first, so a
    download could have preceded a rejection.
  - **BUG FOUND + FIXED during verification (would have shipped):** auto-select turned a working
    mock run into a hard `this build has no llama.cpp backend` error on any **default (non-llama)
    build** that happened to have the model on disk — caught by driving the real binary, not by the
    unit tests. `select_model` now never auto-selects without the llama backend compiled in (an
    explicit `--model` still errors clearly, never silently downgrades). Pinned by
    `mock_only_build_never_auto_selects`.
  - **Verified end-to-end** (`--features llama`, store = repo `models/`): no `--model` → silent
    auto-select → **real CPU inference → a correct ffmpeg command** (73 s); `KNAIF_LLM_BACKEND=mock`
    → mock in 0.12 s (proving it short-circuits before the model load); default build → mock +
    guidance, no error. **9 tests** in `main.rs` cover precedence 1–4 hermetically (a `TODO`
    manifest url makes an attempted pull fail before any socket, so no test touches the network).
    The **consent gate is mutation-tested**: making a non-tty count as consent fails
    `missing_model_never_downloads_without_consent`.
  - **Not verified (needs a real hosted pull):** the interactive `y` → pull → run happy path and the
    progress bar. Tests prove a pull is *attempted*; a successful download is exercised only by
    `models pull`'s own path. **Original spec retained below.**

  In `apps/knaif-cli/src/main.rs`,
  `cmd_run`/`cmd_plan` pass the mock when `--model` is omitted. **Explicit precedence (in order):**
  1. `--model <name|path>` given → real model (authoritative, unchanged).
  2. else `KNAIF_LLM_BACKEND=mock` → mock (explicit opt-out wins over auto-select; offline/eval).
  3. else recommended model **installed** (`recommended_model_status()`, `main.rs:929`) →
     resolve via `resolve_model_path`, use it (silent).
  4. else **consent/download flow** (below).
  - **Consent/download flow (not installed):** prompt `Download recommended model <name>
    (~2.5 GB)? [y/N]` → on `y`, `ModelStore.pull` (progress bar) then run. **`--yes` skips the
    prompt and downloads** (`--yes` = "don't ask, proceed"). **Non-interactive without `--yes`**
    (stdin not a tty) → today's first-run guidance + mock; **never** a multi-GB download without
    consent, so CI/piped runs don't block. Reuse the existing `confirm_action` tty/consent gate
    (`main.rs:646`) rather than a second prompt path.
  - **`plan` specifics (audit):** `PlanArgs` has **no** `--yes` today (`main.rs:83`; only `RunArgs`
    does, `:120`). **Add `--yes` to `PlanArgs`** for the download opt-in, but **`plan` is
    non-prompting by default** and **`--batch` / `--json` never prompt and never auto-download**
    (batch loads the model once up front; a prompt mid-stream or bytes on JSON stdout would corrupt
    output). All prompts + the progress bar go to **stderr**, never stdout.
  - **Order of operations:** run request parsing, safety-gate, and dependency preflight happen
    **before** any download starts — never pull 2.5 GB only to then reject the request.
  - Keep `repair` tied to "a real model is active". Tests: precedence 1–4; installed → silent;
    interactive-yes → pull→run; declined → guidance+mock; `--yes` → pull without prompt;
    non-interactive no-`--yes` → guidance+mock (no download); `KNAIF_LLM_BACKEND=mock` beats
    auto-select; `plan --batch`/`--json` never prompt/download; explicit `--model` still wins.
  - Update NATIVE.md §5.6 + §12 to remove the "no runtime default model" gap.
- [x] **B2 — Watermark: DONE, docs corrected (verified 2026-07-16).** Image watermark is
  implemented and wired (`add_image_overlay`, image-XObject + soft-mask alpha) and covered by
  `image_watermark_embeds_xobject_with_softmask` in
  [skills/documents/native/src/overlay.rs](../../skills/documents/native/src/overlay.rs)
  — **ran it: 1 passed.** The stale "deferred" claims were removed from **NATIVE.md §12** and the
  **overlay.rs module doc comment**. No implementation work remained; this was a doc-truth fix.
- [x] ~~**B3**~~ — **Out of scope by design; nothing to do (confirmed 2026-07-17).** Orchestrator
  `json_mode` honesty in `infer_stream` is a **Python** `orchestrator.py` item, not native. It stays
  in TODO Open/Next and does not block the native merge. Checked off as a *record*, not as work.

---

## Workstream C — CUDA release build (Phase 9)

CPU and Vulkan are already proven as static single-exe builds; under Option 3 (C5) they become
loadable `ggml-cpu`/`ggml-vulkan` backends in the default artifact. CUDA is the one build kind
still pending and the one with portability caveats.

- [x] **C1 — DECISION (2026-07-15): CUDA arch floor = Turing sm_75; single multi-arch fatbin.**
  **AMENDED 2026-07-16 (owner): `120-virtual` → `90-virtual`.** Release build uses
  **`CUDAARCHS="75-real;80-real;86-real;89-real;90-virtual;120-real"`** (note: the **`CUDAARCHS` env
  var** is the only way in — see C2; and add `90-**real**` only if H100/Hopper cloud runs are a target,
  currently **out**), never `native`.
  - **Why the amendment:** `120-virtual` produced **no forward-compatible PTX**. ggml rewrites every
    `12X` → `12Xa` (`ggml-cuda/CMakeLists.txt:80-92`), so it emitted `sm_120a` PTX — an
    **architecture-specific** target that by NVIDIA's definition cannot JIT to any other arch,
    including later ones. `90-virtual` is untouched by that regex (it only matches `12[0-9]`), so its
    PTX survives as `compute_90` and JITs forward to any later `sm_`. This is **upstream's own idiom**
    — ggml's default list carries `89-real 90-virtual` for exactly this. Cost: PTX size only, no
    extra SASS. Blackwell SASS coverage is unchanged (`120-real` → `sm_120a`, which is what a 5080 runs).
  All archs live in **one fatbin** — NO per-arch libs. **Under the chosen Option 3 (C5) that fatbin
  lives inside the loadable `ggml-cuda` backend lib (`.dll`/`.so`), not in `knaif.exe`** (only the
  Option-1 fallback would static-link it into the exe). The redist `cudart`/`cublas`/`cublasLt` beside
  it are arch-independent.
  - **Rationale (do not re-litigate):** the runtime bundles **CUDA 13** redist, and **CUDA Toolkit 13.0
    removed offline compilation *and* library support for pre-Turing archs** (Maxwell/Pascal/Volta,
    sm_50–70) — so sm_75 is the toolkit's hard floor regardless of any coverage argument. Pascal/Maxwell
    users fall back to the **Vulkan** build or CPU — not cut off. `120-virtual` PTX gives forward-compat
    to post-Blackwell GPUs. *(The `-real`/`-virtual` CMake syntax is correct per CMake docs.)*
  - **Cost:** per-arch SASS adds to the `ggml-cuda` lib's fatbin + compile time (the 15–30 min estimate);
    the ~½ GB cublas redist is arch-independent and unchanged by arch count.
  - **Driver floor (audit):** CUDA 13 needs an **NVIDIA R580-or-newer driver** — mere `nvcuda.dll`
    presence is insufficient. Detection (C6) and RELEASE.md must state and check this.
  - **Verification → C4:** verifiable on sm_86 (3070) + sm_120 (5080); document tested-vs-shipped in RELEASE.md.
- [x] **C2 — Multi-arch CUDA payload BUILT (2026-07-16; checkbox trued-up 2026-07-17).** The 5-arch
  release build ran on both OSes: its measured output is what C6a sizes (`ggml-cuda.dll` = 125.1 MB
  vs 30.9 MB single-arch), **C4(a)** proved the arch list took, and **D4** built and proved the Linux
  payload. Only C6's *install surface* is outstanding, and that is deferred — see the follow-on plan.
  Original task text kept below as the build record.
  Build in a VS Developer shell with the chosen `CMAKE_CUDA_ARCHITECTURES`. Option 3 → a loadable
  `ggml-cuda` backend lib; Option 1 → a static `--features llama,cuda` exe. Then package via
  `packaging/package.sh`. Expected 15–30 min compile either way.
  - **HOW TO ACTUALLY SET THE ARCH LIST (found 2026-07-16 — the plan never said, and there is no
    obvious hook):** `llama-cpp-sys-2`'s `build.rs` sets **only `GGML_CUDA=ON`** and **never**
    `CMAKE_CUDA_ARCHITECTURES`, nor does it expose a passthrough. The way in is the **`CUDAARCHS`
    environment variable** (CMake ≥3.20 initialises `CMAKE_CUDA_ARCHITECTURES` from it; local cmake
    is **4.3.1**, bundled with VS at
    `…/Microsoft Visual Studio/18/Community/Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin`
    and **not on PATH**). It works because ggml guards its default with
    `if (NOT DEFINED CMAKE_CUDA_ARCHITECTURES)` (`ggml/src/ggml-cuda/CMakeLists.txt:8`), so a
    defined value wins. So C2's build line is:
    `CUDAARCHS="75-real;80-real;86-real;89-real;90-virtual;120-real" cargo build … --features llama,cuda[,dynamic-backends]`
    (C1's list **as amended** — `90-virtual`, not `120-virtual`; see C1 and C4).
    **PROVEN 2026-07-16:** built with `CUDAARCHS=86-real`, then `cuobjdump --list-elf` reported
    **exactly `sm_86`** and `--list-ptx` reported **nothing**. That is only explicable if `CUDAARCHS`
    was honoured — ggml's default would have produced `75-virtual 80-virtual 86-real 89-real
    90-virtual`. So the mechanism works and **C4(a) is the check that proves it took**
    (`"$CUDA_PATH/bin/cuobjdump" --list-elf <lib> | grep -oE 'sm_[0-9]+' | sort -u`).
  - **Good news on the `native` trap:** `build.rs` sets `GGML_NATIVE=OFF` unless Rust itself uses
    `target-cpu=native`, so the `set(CMAKE_CUDA_ARCHITECTURES "native")` branch does **not** fire by
    default. **But the fallback is still not C1's list** — ggml defaults to
    `75-virtual 80-virtual 86-real 89-real 90-virtual …`, i.e. **80 and 75 as PTX-only, no `120`
    at all**. Shipping without `CUDAARCHS` would mean **no Blackwell SASS**. Setting it is mandatory,
    and **C4(a)'s `cuobjdump` check is what proves it took**.
- [x] **C3 — CLOSED 2026-07-17.** Both stated remainders are done: the **Linux `.so.13` collection**
  landed with D0 (`package.sh` collects `libcudart`/`libcublas`/`libcublasLt.so.13` from
  `$CUDA_PATH/targets/x86_64-linux/lib`, dereferencing the SONAME symlink so the payload carries the
  real file) and the **opt-in payload shape** was settled by C6a (split) and implemented by D0's
  `--kind=cuda` payload. The EULA guard fires on both OSes. Original entry below.
  <details><summary>Original (2026-07-16): licence half done, redist bundling open</summary>
  **`packaging/licenses/NVIDIA-CUDA-EULA.txt` was MISSING** and `package.sh` only **warned** — so a
  `--kind=cuda` release would have shipped NVIDIA's `cudart`/`cublas`/`cublasLt` **without the licence
  their EULA requires**, with nothing but a line in a build log to notice. Added the EULA verbatim from
  `$CUDA_PATH/EULA.txt` (CUDA **v13.3**, 69.8 KB — refresh when the bundled toolkit moves) and changed
  `package.sh` to **fail hard** instead of warn: a licence violation is worse than a failed build.
  **Verified the guard fires** (exit 1 with an actionable message, reached after the redist step).
  Measured redist: **cudart 0.5 MB + cublas 50.3 MB + cublasLt 442.2 MB = 493 MB**.
  Remaining: the Linux `.so.13` collection (D0) and the final opt-in payload shape (C6a).
  already globs `cublasLt64_*.dll`, don't drop it) with the CUDA **opt-in payload**, plus the **NVIDIA
  redistributable EULA** in `licenses/`. Windows: `.dll` from `$CUDA_PATH/bin/x64`. Linux: `.so.13`
  from the toolkit lib dir (handled by the D0 Linux packaging task, not the current Windows-only path).
  Users supply only their GPU **driver** (R580+, C1), never the SDK.
  </details>
- [~] **C4 — (a) fatbin inspection DONE 2026-07-16 + a C1 CORRECTION; (b) runtime proof partial —
  BLOCKED on the RTX 5080, a different machine (see "Not closable from the Linux dev box").**
  **Update 2026-07-17:** sm_86 is now runtime-verified on **Linux** too (the D4 CUDA opt-in proof, RTX
  3070). The Blackwell/sm_120 leg still needs the 5080; 75/80/89 remain "built, UNVERIFIED" with no
  card and no interpolation claim — `docs/RELEASE.md` states exactly that in its arch table.
  - **C2 multi-arch build ran (33m09s):** `CUDAARCHS="75-real;80-real;86-real;89-real;120-real;120-virtual"`
    → `ggml-cuda.dll` **125.1 MB** (vs 30.9 MB single-arch). `CMakeCache.txt` confirms CMake received
    the full list. **`cargo clean -p llama-cpp-sys-2` first is MANDATORY** — `always_configure(false)`
    means an incremental build silently keeps the old arch cache.
  - **C4(a) result — every intended arch is present:** `cuobjdump --list-elf` → **`sm_75 sm_80 sm_86
    sm_89 sm_120a`**; `--list-ptx` → **`sm_120a`**. (Use `--list-ptx`, not `--dump-ptx`: dumping 125 MB
    of PTX text times out. And match `sm_[0-9]+[a-z]*` — a plain `sm_[0-9]+` silently truncates
    `sm_120a` to `sm_120` and hides exactly the finding below.)
  - **✅ C1 AMENDMENT VERIFIED 2026-07-16 (rebuilt with `90-virtual`, 38m55s).** `cargo clean -p
    llama-cpp-sys-2` first, then `CUDAARCHS="75-real;80-real;86-real;89-real;90-virtual;120-real"`:
    | | `120-virtual` (before) | `90-virtual` (after) |
    |---|---|---|
    | SASS | `sm_75 sm_80 sm_86 sm_89 sm_120a` | **identical** — full Blackwell coverage kept |
    | PTX | `sm_120a` — arch-specific, **cannot JIT forward** | **`sm_90`** — no `a` suffix, **forward-compatible** |
    | Size | 125.1 MB | **124.8 MB** (0.3 MB *smaller*) |
    The `a` suffix is gone, which is the whole point: `90-virtual` escaped ggml's `12X`→`12Xa`
    rewrite. **Forward-compat gained at zero cost.**
  - **⚠ C1 CORRECTION (the finding that prompted the amendment) — the `120-virtual` rationale does
    NOT hold.** C1 said
    "`120-virtual` PTX gives forward-compat to post-Blackwell GPUs". It does not: ggml's CMakeLists
    **rewrites every `12X` → `12Xa`** (`ggml-cuda/CMakeLists.txt:80-92`, "replace all instances of 12X
    with 12Xa … fine until Rubin is released"). So `120-real`→`120a-real` and `120-virtual`→
    `120a-virtual`, and the emitted PTX is **`sm_120a`** — an **architecture-specific** target.
    NVIDIA's `a` suffix means *not* compatible with other architectures, **including later ones**, so
    it cannot JIT forward. **This build therefore has NO forward-compatible PTX, and adding
    `120-virtual` cannot produce any.** Blackwell SASS coverage is unaffected (`sm_120a` is correct
    and is what a 5080 runs).
    - **DECISION NEEDED (owner):** either **(a) accept** — no forward-compat; a post-Blackwell
      (Rubin) GPU finds no usable kernel and falls back to Vulkan/CPU until a new knaif release
      rebuilds with its arch; or **(b) add a NON-12X virtual arch** — e.g. **`90-virtual`**, which the
      rewrite does not touch (its regex only matches `12[0-9]`) and whose PTX JITs forward to any
      later `sm_`. This is **upstream's own idiom** — ggml's default list carries `89-real 90-virtual`
      for exactly this purpose. Cost is PTX size only, no extra SASS. Recommended list under (b):
      `75-real;80-real;86-real;89-real;90-virtual;120-real`. **Not actioned — C1 is a locked decision
      and this changes it.**
  - **Machine note (2026-07-16):** the **dev box is the RTX 3070 Laptop (sm_86), driver 610.74** —
    which **clears C1's R580 CUDA-13 floor**, and on which a loadable CUDA backend has now really
    offloaded (C5a). **The RTX 5080 (sm_120) is a DIFFERENT machine**, so C4(b)'s Blackwell run must
    happen there — it cannot be done from here. C4(a)'s `cuobjdump` fatbin check is machine-independent
    and is the *only* thing that proves `CUDAARCHS` actually took (see C2 — an unset `CUDAARCHS`
    silently yields **no sm_120 SASS at all**).
  Owner has **RTX 3070 (Ampere sm_86)** + **RTX 5080 (Blackwell sm_120)**. (a) **Static proof:** run
  `cuobjdump --list-elf`/`--dump-elf` (or equivalent) on the built `ggml-cuda` lib to confirm SASS for
  **every** intended target (75/80/86/89/120) + the 120 PTX is actually present — a build-flag typo
  otherwise silently drops an arch. (b) **Runtime proof:** real inference on both the 3070 and 5080 from
  the same lib. **sm_75 and sm_89 are "built but UNVERIFIED"** — running on 86+120 does **not** validate
  75/89 (no interpolation claim); state exactly that in RELEASE.md. No card for them here.
- [x] **C5 — DECISION (2026-07-15): opt-in `GGML_BACKEND_DL` (Option 3), spike-gated; launcher
  (Option 1) as fallback.** Backend packaging model:
  - **Target — Option 3:** one `knaif` exe that `dlopen`s loadable ggml backends present at runtime
    (`ggml_backend_load_all_from_path`). **Default download = exe + core + `ggml-cpu` + `ggml-vulkan`**
    (covers every GPU vendor + CPU floor, ~small). **`ggml-cuda` + `cudart`/`cublas` are an opt-in
    payload only** — never shipped to non-NVIDIA users, so no ½ GB cost for them and **no non-NVIDIA
    crash by construction** (absent lib is never loaded). "Later activation" is native: drop the CUDA
    libs beside the exe (or in `~/.knaif/backends/`) and they're picked up next run — resolves the
    skip-at-install-then-want-it-later concern with no separate exe, launcher, or self-updater.
  - **This deliberately reopens the 2026-07-04 deferral** of `GGML_BACKEND_DL`. Legitimate: the
    deferral assumed a *heavy combined build shipped to everyone*; the **opt-in-DLL refinement**
    removes both original objections (everyone-pays-cublas and untestable-non-NVIDIA-crash). Record
    this in the dual-runtime plan so it doesn't read as drift.
  - **Binding support already exists (audit-corrected 2026-07-16 — NOT a fork/patch risk):**
    `llama-cpp-sys-2` **0.1.150** ships a **`dynamic-backends` feature** (`Cargo.toml:84`) that sets
    `GGML_BACKEND_DL=ON` + `GGML_CPU_ALL_VARIANTS=ON` (`build.rs:901`), and `llama-cpp-2` exposes
    **`load_backends_from_path`** (`llama_backend.rs:194`). So the spike is a **packaging /
    cross-platform integration** exercise (enable the feature; find and stage the produced backend
    libs; set the load path/RPATH; prove opt-in CUDA), **not** "will the binding cooperate."
  - **C5a — Windows CPU leg: PASSED 2026-07-16. The DL mechanism works.** Passthrough added
    (`knaif-llm: dynamic-backends`, `knaif-cli: dynamic-backends`) + loader wired
    (`load_dynamic_backends`/`backend_dirs` in `crates/knaif-llm/src/llama.rs`, called before
    `LlamaBackend::init`). **Proof:** a 43 MB staged artifact (exe + 4 core libs + 9 `ggml-cpu-*`)
    `dlopen`ed its backend **exe-relative** and ran **real CPU inference → a correct ffmpeg
    command**. `cargo clippy -p knaif-cli --features llama,dynamic-backends -- -D warnings` is
    clean (this closes **A1's deferred check**), workspace tests still 213 (static build unaffected).
    - **ROUTE THE FEATURE VIA THE WRAPPER, NOT `-sys` (audit correction).** The plan implied
      `llama-cpp-sys-2/dynamic-backends`. That **builds the backends but leaves the loader API
      cfg'd out** — `llama-cpp-2` gates `load_backends_from_path` on **its own**
      `dynamic-backends = ["llama-cpp-sys-2/dynamic-backends"]`. Empirically proven: the `-sys`
      route failed with `E0425: cannot find function load_backends_from_path`. Correct line:
      `dynamic-backends = ["llama", "llama-cpp-2/dynamic-backends"]`. (Aside: `llama-cpp-2` **does**
      forward `vulkan` at 0.1.150, so knaif-llm's "the wrapper does not forward it" comment is stale.)
    - **`dynamic-link` confirmed real, not theoretical** (`dynamic-backends → dynamic-link`, seen in
      `cargo tree`). The exe is **no longer self-contained**: `llama.dll`, `ggml.dll`,
      `ggml-base.dll`, `llama-common.dll` land in `$OUT_DIR/bin` and **must be staged beside the
      exe**; backends land in `$OUT_DIR/backends`. **D0/D2 must stage both sets** (+ `$ORIGIN` RPATH
      on Linux). Discovery is scriptable: `-sys` declares `links = "llama"` and emits
      `cargo:backends_dir` → `DEP_LLAMA_BACKENDS_DIR` → the wrapper bakes `GGML_BACKENDS_DIR`, read
      back at runtime as `llama_cpp_2::llama_backend::BACKENDS_DIR`.
    - **Bonus:** `GGML_CPU_ALL_VARIANTS=ON` emits **9** `ggml-cpu-*` libs (sse42 → alderlake, ~8 MB
      total) with **runtime CPU dispatch** — it picked `haswell` on this box. Strictly better than
      today's single static CPU build, at negligible size.
    - **Two bugs found by running it, both fixed:** (1) **double-load** — the compile-time
      `BACKENDS_DIR` dev fallback re-registered every backend a second time whenever the exe dir had
      its own; now skipped once the exe dir provides backends. Note "first dir wins" would be
      **wrong** — `~/.knaif/backends` must always also be scanned or opt-in CUDA can never load.
      (2) **quiet-by-default regression** — ggml logs `load_backend: …` during the scan, *before* any
      `LlamaBackend` exists to `void_logs`, and `void_logs` only sets `llama_log_set`, never
      `ggml_log_set`, so it could never have silenced it. Fixed with
      `send_logs_to_tracing(LogOptions::default().with_logs_enabled(false))` unless `--verbose`.
      Verified: silent by default, exactly one load under `--verbose`.
    - **CUDA OPT-IN PROVEN on Windows 2026-07-16 — all three required cases pass.** Built the
      loadable CUDA backend (`CUDAARCHS=86-real`, 12m17s — single arch deliberately, the spike tests
      the *mechanism*; C2 does C1's fatbin). Test used the **non-cuda exe** with a **separately
      built** `ggml-cuda.dll` — i.e. exactly the production shape, which also proves cross-build ABI
      compat between the default artifact and an independently produced payload:
      1. **Default (no cuda lib) → CPU.** 43 MB artifact, real inference. ✔
      2. **Drop the payload in → CUDA next run.** `ggml_cuda_init: found 1 CUDA devices` → `Device 0:
         RTX 3070 Laptop, compute capability 8.6` → `load_backend: loaded CUDA backend from
         …/cuda-backends/ggml-cuda.dll` → `load_tensors: layer N assigned to device CUDA0`. **Real
         GPU offload from a dir the exe never knew about at build time.** ✔
      3. **CUDA present but NO usable GPU** (`CUDA_VISIBLE_DEVICES=""`) → **clean silent fallback to
         CPU**, correct output, exit 0, no crash. This is the case the plan singled out
         ("test the CUDA-*present* path, not only CUDA-absent"). ✔
         - **RETRACTED 2026-07-30 — this recipe does not hide the GPU on Windows.** Re-running it
           against the packaged 1.1.0 payload, `ggml_cuda_init` still reported `found 1 CUDA devices`
           and every layer went to `CUDA0`: Windows treats an env var set to the empty string as
           unset, so the variable is deleted rather than emptied and the case passes without ever
           testing the fallback. `CUDA_VISIBLE_DEVICES="-1"` is the working form, and with it the
           case does pass. Read this ✔ as unproven; the real one is in
           [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md) U6.
      Also proven incidentally: **CPU (exe dir) and CUDA (backends dir) load together** — multi-dir
      scanning works, confirming "first dir wins" would have been the wrong dedupe fix; and the
      **NVIDIA redist resolves from beside `ggml-cuda.dll`** in the backends dir, so the opt-in
      payload is self-contained and needs no PATH/DLL-search games (a real risk that did not bite).
    - **SIZE DATA FOR THE C6 SPLIT DECISION (measured, not estimated):** `ggml-cuda.dll` =
      **30.9 MB at one arch**; the NVIDIA redist = **493 MB** (`cublasLt64_13` alone is **442 MB**).
      So even at C1's 5 archs the redist **dominates the payload**. A combined blob re-ships ~½ GB of
      byte-identical NVIDIA DLLs on **every** knaif release; the split re-ships only the
      ABI-coupled lib. **This is a much stronger argument for the split than when it was guessed at.**
    - **VULKAN WORKS — DEFAULT ARTIFACT PROVEN 2026-07-16.** `--features llama,dynamic-backends,vulkan`
      builds and runs. **98 MB** default artifact (exe + 4 core libs + 9 `ggml-cpu-*` + `ggml-vulkan`,
      **no CUDA**): cross-vendor detection found **both** GPUs (`0 = AMD Radeon(TM) Graphics`,
      `1 = NVIDIA GeForce RTX 3070 Laptop`), loaded **Vulkan + CPU from the artifact dir**, ran on
      `Vulkan1`, and finished in **12 s vs 73 s on CPU**. So the C5 "default = exe + core +
      `ggml-cpu` + `ggml-vulkan`, CUDA opt-in" shape is real on Windows.
      - **`CMAKE_GENERATOR=Ninja` is REQUIRED** (from a VS Developer shell, for Ninja on PATH + the
        MSVC env). Under the default MSBuild generator the build dies in `vulkan-shaders-gen` with
        `cannot find the batch label specified - VCEnd`. **This is a KNOWN, ALREADY-DOCUMENTED
        requirement**, not a new problem: `justfile:206-213` (the `native-vulkan` recipe) spells out
        the generator, the exact `VCEnd` error, and `just clean-vulkan-build` for a half-configured
        tree; `package.sh:23` and `NATIVE.md:266` both say "Vulkan also needs Ninja". A first pass
        here wrongly filed this as a new blocker + a VS-18 regression — **it was neither**; the
        recipe simply wasn't followed. Recorded so the same wrong turn isn't taken twice.
      - **D0/D1 consequence:** the Linux packaging path must force the **Ninja generator** for
        Vulkan too, and `package.sh` should set it rather than rely on the operator's shell.
    - **C5a — LINUX LEG: PASSED 2026-07-16 (WSL Ubuntu 24.04, RTX 3070 sm_86). All three legs pass;
      the DL mechanism works on Linux exactly as on Windows.** Proven against real packaged artifacts
      (`packaging/package.sh --kind=cpu|vulkan|cuda`, see D0–D4):
      1. **Default (CPU+Vulkan) artifact → CPU.** 15 MB (cpu) / 28 MB (vulkan) tarball, `$ORIGIN` RPATH
         resolves the staged core libs, real inference → correct ffmpeg command from OUTSIDE the
         checkout. The Vulkan backend **loads** but reports `ggml_vulkan: No devices found` and falls
         back to CPU — a **WSL environment limit** (no GPU-backed Vulkan ICD in this WSL; only the
         mesa/lavapipe ICD stubs, whose libs aren't installed), **not** a packaging defect. GPU Vulkan
         offload itself was already proven on Windows. ✔
      2. **Drop the CUDA payload into `$KNAIF_BACKENDS_DIR` → CUDA next run.** `ggml_cuda_init: found 1
         CUDA devices` → `Device 0: RTX 3070 Laptop, compute capability 8.6` → `loaded CUDA backend
         from …/libggml-cuda.so` → `layer N assigned to device CUDA0`. **Real GPU offload from the
         separately-built opt-in payload against the default (non-cuda) exe** — proves cross-build ABI
         compat, and that `libggml-cuda.so` finds its `.so.13` redist via `$ORIGIN` and `libggml-base`
         from the already-loaded exe process, and `libcuda.so` from `/usr/lib/wsl/lib`. ✔
      3. **CUDA present but no usable GPU** (`CUDA_VISIBLE_DEVICES=""`) → **clean silent CPU fallback**,
         correct output, exit 0, no crash. ✔ *(Linux, so the empty-string form is valid here — see the
         Windows retraction above, which applies only to Windows' unset-on-empty semantics.)*
    - **LINUX LOADER BUG FOUND + FIXED (`crates/knaif-llm/src/llama.rs`, `has_backend_libs`).** The
      dev-fallback dedupe checked `name.starts_with("ggml-")`, but Linux backends are `libggml-*.so`
      (Windows `ggml-*.dll`), so it returned **false on Linux** and the baked `BACKENDS_DIR` dev
      fallback **double-loaded every backend** (the exact bug C5a fixed on Windows, unfixed on Linux —
      only bites on a box where the build's `$OUT_DIR/backends` still exists, e.g. the build box).
      Fixed by normalising the `lib` prefix; verified the Vulkan backend now loads exactly **once**.
    - **Still open before C5b go/no-go:** nothing — **both OSes now pass all three legs.**
  - **C5a — SPIKE (½–1 day, go/no-go, before C2/C3):** with `--features …,dynamic-backends`, collect
    the emitted `ggml-*` backend libs and load them via `load_backends_from_path`. Prove on Windows
    **and** Linux: default (no cuda lib) runs on CPU/Vulkan; dropping the cuda lib in enables CUDA next
    run; **and a CUDA-present-but-no-compatible-GPU/driver machine falls back cleanly (test the
    CUDA-*present* path, not only CUDA-absent).** Fork/patch is not the expected path but remains the
    escape hatch if integration genuinely fights back.
  - **C5b — GO (RESOLVED 2026-07-16): Option 3 on both OSes.** The spike passed all three legs on
    **Windows and Linux**, so packaging is unified: one default artifact (exe + CPU + Vulkan backends)
    per OS, one Linux AppImage carrying the same, and the opt-in CUDA `.so` + redist loaded from
    `~/.knaif/backends/` (`$KNAIF_BACKENDS_DIR`) — never N per-backend artifacts. The Option-1 launcher
    fallback is **not needed** and is retired to the out-of-scope list.
  - Either way: **CUDA backend = `llama,cuda` (no Vulkan);** Vulkan+CPU is the always-installed default;
    CUDA opt-in via install-time NVIDIA-detected task **or** `knaif backend install cuda` anytime, plus
    a first-run nudge when an NVIDIA device is visible but the CUDA backend is absent.
- [x] **C6b — RESOLVED 2026-07-16 (owner): scan `~/.knaif/backends` FIRST.** Implemented in
  `backend_dirs` (`crates/knaif-llm/src/llama.rs`) — the opt-in payload dir is now scanned ahead of
  the artifact's own backends, so an installed `ggml-cuda` wins the PCI-id dedupe instead of losing
  to the bundled Vulkan. **Verified against the real default artifact — the roles are exactly
  reversed:**
  | Scenario | Before | After |
  |---|---|---|
  | default (CPU+Vulkan) **+ CUDA payload** | `skipping CUDA0 … using Vulkan1` — **payload inert** | `skipping Vulkan0 … using CUDA0` + `layer 0 assigned to device CUDA0` ✔ |
  | default, **no payload** (regression check) | Vulkan1 | **Vulkan1 — unchanged** ✔ (quiet-by-default intact) |
  **Payload staleness is explicitly NOT handled by ordering** (both dirs are scanned either way, so a
  mismatched lib loads regardless) — it is C6a's job, by pinning the payload to the exe's build.
  Original finding retained below.

  <details><summary>Original finding (2026-07-16)</summary>
  **The single most consequential finding of the C5a spike.** With the real default artifact
  (CPU+**Vulkan**) plus the CUDA payload installed, **llama.cpp does not use CUDA**:
  ```
  llama_prepare_model_devices: skipping device CUDA0 (RTX 3070) with id 0000:01:00.0
    - already using device Vulkan1 (RTX 3070) with the same id
  llama_prepare_model_devices: using device Vulkan1
  ```
  When two backends see the **same physical GPU**, llama.cpp dedupes by PCI id and the
  **first-registered wins** — and the exe dir (Vulkan) is scanned before `~/.knaif/backends` (CUDA).
  So `knaif backend install cuda` would download ~619 MB and **change nothing**, silently defeating
  C6's core promise ("install → present → auto-selected next run"). CUDA only won in the earlier
  proof because that stage dir had **no Vulkan** — a false positive that a CPU-only test cannot catch.
  - **Scan order is the mechanism — measured both ways** (experiment run, then reverted; the
    committed code is still exe-dir-first):
    | Scan order | Winner | Opt-in CUDA |
    |---|---|---|
    | exe dir first (**committed today**) | Vulkan1 | **inert** |
    | `~/.knaif/backends` first | CUDA0 | works |
  - **Options.** **(a) Scan `~/.knaif/backends` first** — one line, proven to work. It inverts the
    "the artifact's own backends beat a stale `~/.knaif/backends`" rationale in `backend_dirs`, but
    note that reasoning was **already weak**: both dirs are always scanned, so a stale/ABI-mismatched
    lib gets **loaded either way** — order only picks the *device*. Staleness is C6's problem to solve
    by pinning the payload to the exe's build (see C6a), not the loader's.
    **(b) Explicit device preference** (CUDA > Vulkan > CPU) instead of relying on implicit load
    order — cleaner and less fragile, but needs device-selection support through `llama-cpp-2`.
    **(c) Have `backend install cuda` remove/disable `ggml-vulkan`** — rejected: it destroys the
    non-NVIDIA fallback and the "just drop the lib in / take it out" property.
  - **Recommendation: (a) now** (it makes the opt-in actually work and is one line), **(b) later** if
    implicit ordering proves fragile. **Not actioned — it changes a documented design choice.**
  - **This gates C5b's value, not its viability:** Option 3 mechanically works; but without this,
    the opt-in CUDA payload buys nothing on any machine that also has the Vulkan backend — i.e.
    **every default install**.
  </details>

- [x] **C6a — PAYLOAD SHAPE + HOSTING — DECIDED 2026-07-16; EXECUTION DEFERRED 2026-07-17.** The
  decision below stands and is **not to be re-litigated** — it is the input C6 implements. Building
  and hosting the split artifacts moves with C6 to
  [2026-07-17-post-v1-ci-and-cuda-opt-in.md](2026-07-17-post-v1-ci-and-cuda-opt-in.md), so **v1.0.0
  publishes no CUDA assets and no `redist-cuda-13.3` tag**. Consequence: the URLs below get baked for
  the first time in the *follow-on* release, by which point the org move (OSS-prep) has already
  happened — so they are written against **`blackdeep-tech/knaif`**, and E0a's "transfer before you bake"
  rule is satisfied by ordering rather than by rushing the transfer.
  - **Measured sizes (C2's real 5-arch build, 2026-07-16):** `ggml-cuda.dll` = **125.1 MB**
    (30.9 MB at one arch); NVIDIA redist **493 MB** (`cublasLt64_13` = **442 MB**, `cublas64_13` =
    50 MB, `cudart64_13` = 0.5 MB). **Full opt-in payload = 619 MB.** **The redist is ~80% of it** —
    so a combined blob re-ships ~½ GB of byte-identical NVIDIA DLLs on every knaif release, while the
    split re-ships only the 125 MB ABI-coupled lib. **The split saves ~80% per upgrade.**
  - **DECIDED 2026-07-16 (owner): SPLIT.** Two separately-versioned artifacts —
    `ggml-cuda` keyed to the **exe's build** (ABI-coupled: its only contract with the exe is "same
    source tree", so it MUST be replaced every release) and the redist keyed to the **CUDA toolkit**
    (NVIDIA documents minor-version compat, so one copy is safely shared across releases). Both land
    in **one** backend dir — **proven safe**: the redist resolved fine from beside `ggml-cuda.dll`,
    so no separate dirs and no DLL-search/RPATH workaround are needed. Manifest carries a **per-file
    sha**; install/update fetches only files whose sha changed (the same shape `models update`
    already uses).
  - **The backend manifest is a BILL OF MATERIALS, not a catalog** — same reasoning as the model
    manifest (E0a) but for a *stronger* reason: a model mismatch fails cleanly on a versioned data
    format, whereas a backend mismatch is **undefined behaviour** (crash/corruption that reads like
    a GPU driver bug). It must ship **inside** the artifact and pin that release's exact files;
    resolution must never be "latest".
  - **HOSTING — DECIDED 2026-07-16 (owner): ALL ON GITHUB RELEASES.** Repo is public at v1.0.0, so
    assets are anonymously downloadable and the tokenless `HttpFetcher` works unchanged. Layout:
    | Artifact | Tag | Why |
    |---|---|---|
    | `ggml-cuda` (125 MB) | the **product release** (`v1.0.0`) | release-scoped; a tag-scoped URL **structurally cannot** serve a v1.1 lib to a v1.0 exe — ABI pinning for free |
    | `cudart`/`cublas`/`cublasLt` (493 MB) | a **dedicated `redist-cuda-13.3`** tag | NOT release-scoped: keyed to the CUDA toolkit and shared across releases. A dedicated non-product tag (pre-release, never deleted) matches that lifecycle inside GH's tag model — no back-pointing at an old product release, which would make that release undeletable |
    Rationale: free and on-label (they **are** release artifacts), one publish flow, no AWS
    dependency, no card that can lapse, and contributors can cut a release without the owner's
    cloud creds. Accepted costs: no cache/geo control, slow-or-blocked in some regions (e.g. CN),
    coarse per-asset download counts. Both artifacts are far under GH's ~2 GiB per-asset cap.
    An **S3/CloudFront mirror stays open as a fast-follow** if reach or speed disappoints — the
    fetcher has **no fallback logic today**, so that would need building.
    Cost note (informed the call): S3/CloudFront would have been **free** below ~1,650 installs/month
    (CloudFront's 1 TB/month perpetual free tier vs a 619 MB first install), ~$440/mo at 10k — so
    this was decided on ownership/simplicity, **not** on cost.
    **Do the org move BEFORE any of these URLs are baked (E0a).**
- [ ] ~~**C6 — CUDA opt-in surface + driver-aware detection**~~ — **DEFERRED 2026-07-17 (owner),
  not a close blocker.** Moved wholesale to
  [2026-07-17-post-v1-ci-and-cuda-opt-in.md](2026-07-17-post-v1-ci-and-cuda-opt-in.md). **Why it's
  safe to defer:** the payload works (C2/C4a/D4) and C6b's loader already scans `~/.knaif/backends`
  first, so a user who copies the payload in by hand gets CUDA today — what's missing is only the
  *convenience surface*. **What v1 gives up:** no `backend install cuda`, no driver gate, no
  first-run nudge; the installer's opt-in task has nothing to call, so **the Windows installer must
  not offer a CUDA component at v1** (verify in E2/H2). **What deferring buys:** nothing in a v1
  artifact names the GitHub org, which is what lets the transfer wait until after close.
  Original scope, moved verbatim to the follow-on plan: implement
  `knaif backend install cuda` / `backend remove cuda` (reuse the `HttpFetcher` + SHA-pinned manifest
  path; host is not a constraint) writing to the backend dir the loader scans; the installer's opt-in
  task calls the **same** command. **Detection gate (audit):** offer/nudge CUDA only when an NVIDIA
  GPU is present **and the driver is R580+** (CUDA 13 floor) — `nvcuda.dll` presence alone is
  insufficient; probe the driver version (e.g. NVML / `nvidia-smi`) and, if too old, tell the user to
  update rather than install a payload that will fail to load. First-run nudge fires only when NVIDIA
  + adequate driver + CUDA backend absent. Tests: install→present→auto-selected next run;
  absent→CPU/Vulkan, no fault; **old-driver→no offer + update hint**; nudge fires only when applicable.

---

## Workstream D — Linux packaging (Phase 9)

- [x] **D0-prep — Linux build environment — DONE (verified against WSL Ubuntu 24.04 on 2026-07-16;
  ticked 2026-07-17: D0–D4 all built and passed in this environment, which is the proof it stands
  up).** Every apt name below was confirmed present in that distro. Notably **`glslc` is in
  plain apt (2023.8) and `cmake` is 3.28.3** (≥3.20, so `CUDAARCHS` works) — **no LunarG SDK needed**.
  - **Code into Linux-native space.** Build on `/mnt/c` is ~10× slower (9p); clone into the WSL fs:
    `git clone /mnt/c/<windows-checkout>/knaif ~/knaif` — a local clone needs no SSH in WSL and
    carries every commit. **`models/` (3.7 GB) is NOT in git**: copy it to `~/.knaif/models`, or point
    `$KNAIF_MODELS_DIR` at `/mnt/c/<windows-checkout>/knaif/models` (works, but slow to load).
  - **apt — core build:** `build-essential pkg-config cmake ninja-build curl git ca-certificates`
    (**`ninja-build` is not optional for Vulkan** — see C5a's Ninja note).
  - **apt — Vulkan (D1):** `libvulkan-dev glslc glslang-tools spirv-tools vulkan-tools`
  - **apt — AppImage (D2):** `libfuse2t64 file desktop-file-utils patchelf`
    (Ubuntu 24.04 ships FUSE3; AppImages need the FUSE2 shim `libfuse2t64`.)
  - **apt — optional:** `musl-tools` (D1's static-musl CPU floor; plus
    `rustup target add x86_64-unknown-linux-musl`), `ffmpeg` (a real `run` in D3; the dep probe
    reports `[MISS]` without it, which is a legitimate pass).
  - **Rust:** rustup toolchain **1.96.0** (mise's pin, `mise.toml`), or `mise` + `just bootstrap`.
  - **CUDA (D4) — needs NVIDIA's repo, and has a WSL-specific trap.** Not in Ubuntu's archive, so
    register the keyring first (verified against the live repo 2026-07-16 — it carries **13-2 and
    13-3 only, no 13-0**; pick **13-3** to match the Windows toolkit so the `.so.13` redist lines up
    with C3):
    ```bash
    wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
    sudo dpkg -i cuda-keyring_1.1-1_all.deb && sudo apt-get update
    sudo apt-get -y install cuda-toolkit-13-3          # toolkit ONLY (~5 GB)
    ```
    The URL says **`wsl-ubuntu`**, not `ubuntu2404` — that repo deliberately omits driver packages.
    **NEVER** install `cuda`, `cuda-drivers`, or `nvidia-driver-*` inside WSL: the **Windows** driver
    already provides the GPU (`nvidia-smi` resolves to `/usr/lib/wsl/lib/nvidia-smi`), and a Linux
    driver breaks the passthrough.
  - **Resources:** disk is fine (944 GB free). **RAM is the risk — WSL had 7 GB**; the Windows CUDA
    multi-arch build took 33–39 min on 16 cores and nvcc is memory-hungry, so raise it via
    `.wslconfig` (`memory=12GB`) or cap parallelism (`CMAKE_BUILD_PARALLEL_LEVEL=8`) to avoid an OOM
    kill mid-build.
  - **ACTUAL PROVISIONING (2026-07-16) — the box was NOT pre-set-up, and there is no `sudo`.** Rust,
    the Vulkan dev packages (`libvulkan-dev`/`glslc`/`glslang-tools`/`spirv-headers`), **libclang**
    (llama-cpp-sys bindgen), and the AppImage tooling were all absent. All were installed **without
    root**: rustup toolchain into `~/.cargo`; the apt packages fetched with `apt-get download` (no
    sudo) and `dpkg-deb -x`'d into a `~/vklocal` prefix wired via `PATH`/`LD_LIBRARY_PATH`/
    `LIBRARY_PATH`/`CMAKE_PREFIX_PATH`/`LIBCLANG_PATH`; `linuxdeploy`/`appimagetool` downloaded as
    AppImages. **On a normal box with sudo, one `apt-get install` of the D0-prep list replaces all of
    this.** Gotchas hit and worth knowing: (a) bindgen needs the **full** clang stack —
    `libclang1-18` + `libllvm18` + `libclang-common-18-dev` (the builtin `stdbool.h`); (b) `-lvulkan`
    resolves at **link** time only via `LIBRARY_PATH` (not `LD_LIBRARY_PATH`); (c) Vulkan needs
    `spirv-headers` for its cmake `find_package`; (d) the Vulkan `mul_mm` shader OOM-kills `cc1plus` at
    16 jobs on 7 GB — cap with `CARGO_BUILD_JOBS` (cmake-rs reads `NUM_JOBS`, **not**
    `CMAKE_BUILD_PARALLEL_LEVEL`, from cargo); (e) `cargo clean -p llama-cpp-sys-2` does **not** reliably
    remove the build dir — wipe `target/release/build/llama-cpp-sys-2-*` directly when changing the
    feature set/arch (the `always_configure(false)` stale-cache trap), and also clear stale top-level
    `target/release/lib{ggml,llama}*.so*` copies or build.rs's hard-link step panics with `AlreadyExists`.

- [x] **D0 — `packaging/package.sh` Linux path DONE 2026-07-16.** The script now builds functional
  kinds directly on Linux (gcc + cmake + ninja, no MSVC gate) and stages the Option-3 dynamic layout:
  core libs (`libggml-base`/`libggml`/`libllama`/`libllama-common` `.so.N`) + the loadable `ggml-*`
  backends beside the exe, each ELF given an **`$ORIGIN` RPATH** with patchelf so the unpacked folder
  relocates. `--kind=cuda` on Linux emits an **opt-in payload** (only `libggml-cuda.so` + the
  `.so.13` redist collected from `$CUDA_PATH/targets/x86_64-linux/lib`, EULA hard-fail retained) for
  `~/.knaif/backends`, not a standalone app. Windows behaviour is unchanged (still gated to a VS Dev
  shell + `--no-build`). Verified end-to-end by D1/D4 below.
- [x] **D1 — Linux CPU + Vulkan builds DONE 2026-07-16.** `packaging/package.sh --kind=cpu` → 15 MB
  tarball (exe + 4 core libs + 14 `ggml-cpu-*` variants), real CPU inference from outside the checkout.
  `--kind=vulkan` → 28 MB tarball (adds `libggml-vulkan.so`) — the **default functional artifact**;
  E1 smoke passes, backend loads, clean CPU fallback (no Vulkan ICD in this WSL — see C5a Linux).
  `just`-style Ninja generator forced by `package.sh`; static-musl CPU floor not attempted here (a
  fast-follow, not a blocker).
- [x] **D2 — AppImage + tarball DONE 2026-07-16.** New `packaging/linux/build-appimage.sh` assembles
  an AppDir mirroring the tarball's exe-relative layout (exe+libs in `usr/bin`, `skills/`+`shared/` at
  `usr/`) and packages it with `appimagetool` → **23 MB AppImage** carrying the default CPU+Vulkan
  backends. Runs via FUSE, resolves skills from inside the mount. The opt-in `ggml-cuda.so` loads from
  `~/.knaif/backends/` outside the read-only mount — no per-backend AppImages. `dist/SHA256SUMS`
  covers all tarballs + the AppImage.
- [x] **D3 — Clean-env verification DONE 2026-07-16.** `packaging/smoke.sh` run against each tarball
  from an unrelated cwd with empty `KNAIF_SKILLS_ROOT` (exe-relative resolution under test): `--version`,
  `skills list` (ffmpeg + documents), `skills deps` probe, offline mock `plan --json` all pass; plus a
  real `run` from a temp dir outside the checkout produced a correct ffmpeg command. AppImage verified
  the same way through its FUSE mount.
- [x] **D4 — Linux CUDA opt-in DONE + PROVEN 2026-07-16 (IN for v1).** Built the payload with
  `CUDAARCHS=86-real` (single arch — the spike tests the mechanism; C2 owns the multi-arch fatbin):
  `libggml-cuda.so` 44.7 MB + redist (`libcublasLt.so.13` 517 MB, `libcublas` 55 MB, `libcudart`
  0.78 MB) + EULA, 412 MB tarball. **All three C5a Linux legs pass** (default→CPU; drop-in→CUDA offload
  on the RTX 3070 sm_86; CUDA-present-no-GPU→clean CPU fallback). glibc + vendor driver required; CPU
  floor stays static-musl-capable as a fast-follow.

---

## Workstream E — v1 release (manual for v1; automation fast-follows)

- [x] **E0a — DECISIONS (2026-07-16, owner): repo goes PUBLIC at v1.0.0; publish under the
  blackdeep-tech org; hosting split by artifact.** _(Decisions all made; ticked 2026-07-17. Their **execution**
  is tracked elsewhere: org move + public → OSS-prep pass; CUDA hosting → follow-on plan U1. The
  timing sub-bullet is amended below.)_
  - **Repo public from v1.0.0** — resolves E0's open call. GH Release assets are therefore
    anonymously downloadable, so the tokenless `HttpFetcher` works against them.
  - **PUBLISH TO NEW ORG — the repo is published fresh as `blackdeep-tech/knaif`. Timing AMENDED
    2026-07-17 (owner): AFTER this branch closes**, as part of the OSS-prep pass. The original rule
    was "NOW, before E2", because **E2 bakes URLs into the artifact** and a backend manifest inside
    v1.0.0 pointing at the old org would depend on GitHub's org redirect *forever* — a redirect that
    breaks the moment anyone recreates the old org/repo name.
    **That hazard is now gone, and the reason is C6's deferral, not a change of mind:** the only
    artifact-baked GitHub URL in the whole design was C6a's backend manifest, and C6 no longer ships
    in v1. **Verified 2026-07-17:** no code, manifest, or URL bakes a GitHub org — and the manifest
    that actually ships
    (`shared/models/model-manifest.yaml`, copied at `package.sh:241`) points at
    **`huggingface.co/blackdeep/knaif`** with commit-SHA-pinned URLs, i.e. already the right org and
    independent of GitHub entirely. So a v1 artifact built *before* the transfer is byte-for-byte as
    correct as one built after; **no rebuild is needed between transfer and publish.**
    The rest of the rationale is unchanged and still argues for moving early-ish: the repo is
    **private** (no forks/stars/clones to disturb), CI secrets/protections don't survive a transfer
    (the owner's "before CI" constraint — still satisfied, since CI is now a post-transfer plan), and
    it aligns GitHub with the **HF org `blackdeep`** that already hosts the models.
    **Order: E2 → H1/H2 → merge → OSS-prep → transfer → tag → publish → CI.**
    **Standing constraint:** if C6 is ever pulled back into a release, the transfer must precede that
    release's E2 — the manifest is what bakes the URLs.
  - **Hosting by artifact (all decided 2026-07-16):** **GGUFs → HF main** (visibility;
    `blackdeep/knaif`, already live and verified: anonymous 200, `Content-Length` == manifest
    `size_bytes` on both models), **S3+CloudFront also supported** (faster/more reliable;
    main-vs-fallback decided later). **Releases → GH main**, possible AWS mirror. **CUDA DLLs → GH
    Releases** (lib on the product tag, redist on a dedicated `redist-cuda-13.3` tag — see C6a).
  - **Models are BOUND to a knaif version (owner, 2026-07-16):** a model is only shipped against a CLI
    it was tested with. **Asymmetric:** a model bump forces a knaif release; a knaif release does not
    force a model bump (many knaif versions may ship `qwen3-4b-v1`). **Already how the code behaves** —
    the manifest ships *inside* the artifact (`package.sh:104`) and its URLs are commit-SHA-pinned, so
    no install can drift onto an untested model; the decision promotes that to intent. **No
    re-download on upgrade:** the store keys on the *model's own* name
    (`~/.knaif/models/knaif-qwen3-4b-v1-q4_k_m.gguf`, in the user profile, not the install dir), so
    a knaif upgrade with an unchanged recommendation downloads **zero bytes**. Binding ≠ renaming —
    this is exactly why the model keeps its own version line while the backend lib must inherit the
    exe's.
  - **Follow-ups this creates:** (a) the manifest header still frames itself as a live catalog
    ("`recommendations` are live now") — now contradicts the binding, fix in **A2**; (b) `docs/TODO.md`
    still claims uploading the GGUFs is the last owner action — **stale, it's done**, fix in **A2**;
    (c) **new guard needed:** nothing stops a release shipping a manifest whose recommended model has
    `url: TODO`. Harmless pre-B1 (only broke an explicit `models pull`); **post-B1 the recommended
    model IS the default path**, so it would break first-run for every user without `--model`. Add a
    test beside A4's version guard: *the recommended model must have a real url + sha*.
- [x] **E0 — DECISION (2026-07-15, superseded in part by E0a; ticked 2026-07-17): release home = GitHub Releases on `blackdeep-tech/knaif`**
  — the OSS-prep pass publishes a fresh repo in the new org
  before the tag is cut (a fresh repo, *not* a transfer), so the release home
  below is the *new* org. Its open "public vs unlisted" question was answered by E0a
  (**public**). Kept for the record; the live rule is E0a as amended.
  (the `origin` remote). Tag **`v1.0.0`** (semver — the `release.yml` `v*.*.*` pattern assumes it;
  avoid bare `v1`) on the **flatten commit** (the post-scrub initial commit), not the merge/release commit. The GitHub
  Release carries: Windows installer + portable zips, Linux AppImage + tarball, the **opt-in CUDA
  payloads**, and a `SHA256SUMS` file.
  **Models stay on HF** (`blackdeep/knaif`), pulled at runtime — never attached to the release.
  **Dependency:** public downloads need the repo public at release time (ties to OSS-readiness).
  Owner call whether v1.0.0 is public or an unlisted pre-release.
- [x] **E1 — Release smoke test — DONE 2026-07-16: `packaging/smoke.sh <artifact|staged-dir>`.**
  Unpacks a `.zip`/`.tar.gz`/staged dir into a temp dir and runs it from an unrelated cwd with an
  empty `KNAIF_SKILLS_ROOT`, so **exe-relative resolution is what's under test**. Checks: (1)
  `--version` matches the version `Cargo.toml` claims (ties to A4's guard); (2)+(3) `skills list`
  finds ffmpeg + documents from outside the checkout; (4) `skills deps` probe runs — a `[MISS]` is a
  **pass** (it tests the probe, not whether the box has ffmpeg); (5) one offline mock `plan --json`
  emits a JSON envelope. **Never downloads:** `--json` already forbids it (B1's `DownloadPolicy`)
  and `KNAIF_LLM_BACKEND=mock` opts out of auto-select, so a CI box with a GGUF installed still
  tests the mock path instead of loading 2.5 GB.
  **Validated:** run against the stale `dist/staging/knaif-0.1.0-windows-x64` it correctly **fails**
  — `FAIL: --version reported 'knaif 0.1.0', expected version 1.0.0` (exit 1) — and checks 2–5 were
  confirmed by hand against that same binary (`skills list` → both skills; `skills deps` → probe
  runs; `plan --json` → `{"plan":[]}`). **Still to do:** run it against a real freshly packaged
  1.0.0 artifact (needs E2 / a free cargo lock).
- [~] **E2 — LINUX HALF DONE 2026-07-17; Windows half needs the Windows box.**
  - **Built, E1-smoked, and SHA-256'd on Linux** (in `dist/`, gitignored — rebuild or copy them to
    the release box): `knaif-1.0.0-linux-x64.tar.gz` (15 MB, cpu),
    `knaif-1.0.0-linux-x64-vulkan.tar.gz` (28 MB, **the default**),
    `knaif-1.0.0-linux-x86_64.AppImage` (23 MB), and `dist/SHA256SUMS`. `packaging/smoke.sh` passes on
    each; a real `run` outside the checkout produces a correct ffmpeg command.
  - **REMAINING (Windows box):** the two Windows zips + the Inno installer, E1 on each, then fold them
    into a combined `SHA256SUMS`. **Assert the installer offers NO CUDA component** (C6 deferred).
  - **Artifacts are per-box build outputs, not committed.** Whoever cuts the release must produce
    both OSes' artifacts and generate `SHA256SUMS` over the *published* set — a Linux-only
    `SHA256SUMS` must not ship.
  Original spec: build+package
  every artifact (Win installer/zips, Linux AppImage/tarball), run E1 on each, generate `SHA256SUMS`.
  **Do NOT tag or publish here** — the `v1.0.0` tag belongs on the *merge* commit and publishing
  happens in **H3, after the merge** (audit: E0 said tag-on-merge-commit, so publishing before H3 was
  impossible). Staged artifacts wait; H3 tags the merge commit and uploads them.
  **`release.yml` automation is a follow-on** (was F5).
  - **CUDA payloads are NOT part of the v1 artifact set (2026-07-17, C6 deferred).** Don't stage or
    publish them: without `backend install cuda` there is nothing to fetch them, and publishing them
    would bake the org URLs this ordering exists to avoid. They move to the follow-on plan's U1.
  - **The Windows installer must ship with no CUDA component** for the same reason — its opt-in task
    would have no command to call. Verify that in the build, and again in H2.
  - **These artifacts publish unchanged after the org move** — no rebuild. That holds only while
    nothing in them names the org (E0a as amended); re-check for any baked GitHub org URL if C6 or any
    other URL-baking work lands before the tag.
- [x] **E3 — Artifact hygiene — VERIFIED 2026-07-16 (clean).** Enumerated every file in a staged
  artifact: binary + `LICENSE`/`README.txt` + `licenses/` + `shared/{runtime,models}` two YAMLs +
  runtime-only skill data (`skill.yaml`/`tools.yaml`/`prompt.yaml`/`vocab.yaml`/`profiles/`). A
  contraband sweep for `*.gguf`, `*.ipynb`, `*.jsonl`, `*.py`, and any `eval`/`sandbox`/`notebook`/
  `data` path returned **nothing**. Holds **by construction**, not by luck: `package.sh` copies an
  explicit **allowlist** (`package.sh:94-104`) rather than excluding known-bad paths, so a new
  stray directory in the repo cannot leak into a release. **Re-run on the real 1.0.0 artifact at E2**
  (checked here against the staged 0.1.0 tree; the packaging logic is unchanged).

---

## Workstream F — CI & governance — **MOVED OUT 2026-07-17 → [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md)**

> **This workstream no longer lives here.** F1/F2/F4/F5 moved verbatim to
> [2026-07-17-post-v1-ci-and-cuda-opt-in.md](2026-07-17-post-v1-ci-and-cuda-opt-in.md) (owner call
> 2026-07-17: "the CI if part of this plan should be moved to a new one"). It was already exempt from
> the Definition of Done as a fast-follow, so nothing about the merge gate changes — this only stops
> a closed plan from carrying open boxes it will never tick. **F3 stays below**: it is a *resolved
> decision* (the eval-parity GGUF), and decisions stay with the plan that made them; only its unbuilt
> implementation half travels.
>
> The stub is retained so cross-references to "F1"/"F5" from `NATIVE.md`, `TODO.md`, and this plan's
> own Definition of Done still land somewhere that explains itself. Tick nothing here.

CI does **not** block closing this branch. `.github/workflows/` is still the biggest
OSS-readiness gap, but it lands **after** the v1 merge/release, not before.
(A3/H1 keep the suites green **locally** at merge; CI just automates that afterward.)

- [ ] ~~**F1 — Split CI jobs**~~ — **MOVED 2026-07-17** → follow-on plan, task C1.
- [ ] ~~**F2 — Loader compatibility job**~~ — **MOVED 2026-07-17** → follow-on plan, task C2.
- [x] **F3 — RESOLVED (2026-07-15): eval-parity GGUF = `qwen3-4b-v1`** (the manifest
  `recommendations.cli`/`default`; file `knaif-qwen3-4b-v1-q4_k_m.gguf`, already on HF
  `blackdeep/knaif`). `qwen3-1.7b-v1` is the lighter/mobile lane. **Implementation still to do
  (fast-follow):** register a `rust-cli` backend shelling `knaif plan --skill X --json` in
  `eval_backends.yaml` + `just eval-parity` diffing `python-agent` vs `rust-cli` (±2%). Do **not**
  hard-code the retired lane — use these `knaif-*`/`qwen3-*-v1` names.
- [ ] ~~**F4 — Claim the `knaif` PyPI name**~~ — **MOVED 2026-07-17** → OSS-prep plan (it is a
  naming/ownership action, not CI).
- [ ] ~~**F5 — `release.yml`**~~ — **MOVED 2026-07-17** → follow-on plan, task C3.

---

## Workstream G — Docs

- [x] **G1 — `docs/RELEASE.md` WRITTEN 2026-07-17.** Version-bump surfaces (the three A4-guarded
  declarations + the manifest BOM guard), the artifact set, build+package per OS/kind, the **build
  traps** (stale cmake cache, stale lib copies → build.rs `AlreadyExists`, OOM → `CARGO_BUILD_JOBS`
  since cmake-rs reads `NUM_JOBS`), the **CUDA arch range as a built-vs-verified table** (86 + 120
  runtime-verified; 75/80/89 built but UNVERIFIED — no interpolation claim; R580+ driver floor; the
  `90-virtual` forward-compat rule and the `cuobjdump` check with the `sm_[0-9]+[a-z]*` gotcha),
  smoke/hygiene/clean-env verification, the strict merge→OSS-prep→transfer→tag→publish order, and a
  user-facing section (**SmartScreen "More info → Run anyway"** for the unsigned installer, checksum
  verification, AppImage FUSE, first-run, GPU). Records that **v1 publishes no CUDA assets and the
  installer must offer no CUDA component** (C6 deferred). Registered in `CLAUDE.md` + `AGENTS.md`.
- [x] **G2 — `NATIVE.md` UPDATED 2026-07-17.** §12 rewritten: CUDA distribution is now
  "mechanism DONE (both OSes), install surface DEFERRED to post-v1" rather than "pending Linux";
  added explicit CI / macOS / Linux-musl-floor limitation bullets pointing at the follow-on plans.
  §5.3, §9, §10 truthed-up with A2 (see A2 for the scan-order bug that pass caught).
- [x] **G3 — DONE 2026-07-17: `docs/TOOL_SCHEMA.md` → "Runtimes (Python / native / both)".**
  Documents the `runtimes:` block, the three shapes in a table (Python-only — including that
  **omitting the block is legal** and means Python-only, as `skills/io` does; native-only; both),
  where each runtime's handlers live, that the **declarative half is shared** so a tool is declared
  once, and the rule that **a tool implemented in only one runtime must still be declared in the
  shared `tools.yaml`** (else the other runtime's validator rejects a plan the model was told it
  could emit) — express the gap with `status`, not by hiding the tool. Disambiguated from the
  pre-existing, unrelated "Runtime models" section; cross-linked from `NATIVE.md` §7.
- [x] **G4 — Final status pass DONE 2026-07-17** (this pass): plan checkboxes reconciled with
  reality, `docs/TODO.md` + `docs/plans/README.md` set to the true state, and the items that
  **cannot** close on this box recorded as such rather than left ambiguous (see "Not closable here").

---

## Workstream H — Final verification & merge

- [x] **H1 — Full regression GREEN 2026-07-17 (Linux/WSL box). Counts recorded:**
  | Check | Result |
  |---|---|
  | `uv run pytest --tb=short` | **1496 passed, 40 skipped** |
  | `cargo test --workspace` | **213 passed** (= the A1 baseline; no regressions) |
  | `cargo clippy --workspace --all-targets -- -D warnings` | clean |
  | `cargo clippy -p knaif-cli --features llama,dynamic-backends --all-targets -- -D warnings` | clean |
  | `cargo fmt --all --check` | clean |
  | `python -m knaif.evalsuite regression --skill ffmpeg` | **No regressions above threshold=0.02. OK** |
  **Re-run on the Windows box — DONE 2026-07-19.** pytest **1501 passed / 38 skipped**, `cargo test
  --workspace` green, `cargo fmt --check` clean, both clippy passes clean (after two test-only lint
  fixes in `fetcher.rs`, commit `f59e5d4`), and `evalsuite regression --skill ffmpeg` reported no
  regressions above threshold=0.02. Gate observed on both boxes.
- [x] **H2 — Clean-env install smoke — Windows DONE 2026-07-19; Linux covered by D3.** Installed the
  real Inno installer to a dir outside any checkout: exit 0, per-user (no admin). **`smoke.sh` passes**
  (v1.0.0, both skills discoverable exe-relative, deps probe runs, mock `plan --json`). **`run` without
  `--model` works** — auto-selected the installed model and **offloaded all layers to Vulkan (RTX
  3070)**, producing a valid ffmpeg command, exit 0, no fault. **Installer offers NO CUDA component**
  — verified: zero cuda/cublas refs in `knaif.iss`, and the installed tree carries 9 `ggml-cpu-*` +
  `ggml-vulkan` but no `ggml-cuda`. Linux tarball/AppImage were verified from outside the checkout in
  D3. **Two notes recorded in RELEASE.md:** (1) an unattended install needs `/TYPE=full` or it can
  reuse a prior partial component selection (observed: ffmpeg-only); (2) a `/VERYSILENT` uninstall
  deletes `~/.knaif` by design. Manual-CUDA-payload hand-check (C6b) not done — no card here, optional.
  - Original spec: `run` must work **without `--model`** (B1) and land on CPU/Vulkan without a fault
    on a box with no NVIDIA card; assert the installer offers no CUDA component.
- [~] **H3 — DONE THROUGH STEP 2 (branch closed 2026-07-19); steps 3–7 are post-close.** Strict order;
  audit-corrected 2026-07-16, re-ordered 2026-07-17 for the post-close transfer. Steps 3–7 live in the
  follow-on plans (the OSS-prep pass, then tag/publish, then
  [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md)) and are the owner's to run.
  1. ✅ Opened PR #39, local suites green (H1, both boxes) — **not** "CI green" (CI is a follow-on).
  2. ✅ **Merged to `dev`** 2026-07-19 (merge `e74f82a`). ← *the branch is "closed" here; everything below is release mechanics*
  3. **OSS-prep pass + publish to the new org** — run the OSS-prep pass to completion, ending with the
     flattened tree pushed to a **fresh** `blackdeep-tech/knaif` (a fresh repo, not a transfer)
     and the repo public. **This is a hard gate on step 4:** the tag and every release URL must be
     born in the final org, never redirected into it.
  4. **Tag `v1.0.0` on the flatten commit** (the post-scrub initial commit) and push the tag to **`blackdeep-tech`**.
     **Not** the `dev` merge commit from step 2 — that predates the whole scrub, so tagging it would
     ship pre-prep source.
  5. **Publish** the GitHub Release on that tag, uploading the E2-staged artifacts + `SHA256SUMS`
     (draft → publish), **public** per E0a. The E2 artifacts stage *before* the transfer and are
     published *after* it **without a rebuild** — safe only because no v1 artifact names the org
     (E0a as amended). **Re-run `packaging/smoke.sh` on the staged set anyway** before upload: it is
     seconds, and it is the last chance to catch a stale artifact. **No CUDA assets and no
     `redist-cuda-13.3` tag at v1** (C6 deferred).
  6. **Verify** a fresh download of a published artifact installs + runs (independent of the build box).
  7. **Delete** `feature/native-implemetation` (the branch-name typo dies with it, A3).

---

## Not closable from the Linux dev box — what each one waits on (2026-07-17; resolved 2026-07-19)

The Windows-box items were completed 2026-07-19 and the branch merged. Of the original four, only
**C4(b)** remains, and it is **not a close blocker**:

| Item | Status |
|---|---|
| **E2** (Windows half) | ✅ **DONE 2026-07-19** — Windows zips + installer built, E1-smoked, and folded into a combined `SHA256SUMS` covering all four artifacts (rebuilt for the `knaif-` model-identifier rename). |
| **H2** clean-env install smoke | ✅ **DONE 2026-07-19** — Windows installer verified outside any checkout (`run` without `--model` → Vulkan; no CUDA component); Linux leg covered by D3. |
| **H3** merge → … | ✅ **Merged to `dev` (PR #39, `e74f82a`)** — the close. Steps 3–7 (OSS-prep → transfer → tag → publish) are post-close, in the follow-on plans. |
| **C4(b)** Blackwell runtime proof | ⏳ still needs the **RTX 5080** — C4(a)'s `cuobjdump` fatbin check is machine-independent and **done**; sm_86 is runtime-verified on the 3070; 75/80/89/120 stay "built, UNVERIFIED" (RELEASE.md says so). Not a close blocker. |

**Definition-of-done status:** Workstreams **A, B, C, D, E, G are fully closed**; **H1 green on both
boxes**, **H2 done**, **H3 closed at the merge**. **F is exempt** (follow-on) and **C6 is deferred**.
The branch is **CLOSED — merged to `dev` 2026-07-19**; all that remains is post-close release
mechanics (OSS-prep → transfer → tag → publish, then CI + CUDA) in the follow-on plans, plus the
non-blocking C4(b) Blackwell proof whenever the RTX 5080 is available.

---

## Explicitly out of scope (post-v1, record here so they aren't lost)

- **macOS packaging / notarization** — deferred; not a v1 blocker.
- **Persistent inference daemon** (amortize CUDA model load) — low value for Vulkan; own plan.
- **Combined "everyone-gets-CUDA" single bundle** (cublas shipped to all users) — **out.** Note:
  v1 **does** use `GGML_BACKEND_DL`, but as **opt-in** loadable backends (C5) — the default artifact
  carries only CPU+Vulkan; the CUDA lib is a separate download. What's out is the *heavy combined
  bundle*, not the DL mechanism itself. (Corrects the earlier "GGML_BACKEND_DL deferred" phrasing.)
- **Vulkan decode-speed investigation** (Blackwell/sm_120/coopmat2) — revisit after a
  llama.cpp update; tracked upstream (ggml-org/llama.cpp#16230).
- **First-class logging facility** — replace ad-hoc `eprintln!`/env-var gating; own plan.
- **Windows code signing (OV cert)** — v1 ships unsigned with documented bypass.
- **Python orchestrator `json_mode` honesty** — Python-side; stays in TODO Open/Next.

## Owner / hardware dependencies (surface early)

1. ~~**CUDA arch floor**~~ — DECIDED (C1: sm_75). ~~**Older-NVIDIA verification**~~ — RESOLVED
   (RTX 3070 sm_86 + RTX 5080 sm_120 both available, C4).
2. ~~**Linux clean-env box**~~ (D3) — **RESOLVED 2026-07-16: box available; Linux stays IN v1**
   (Definition of Done unchanged).
3. ~~**Repo public at release time?**~~ — **RESOLVED 2026-07-16: public from v1.0.0** (E0a).
4. ~~**PyPI name claim**~~ (F4) — **MOVED 2026-07-17** to the OSS-prep plan. Still an owner action.
5. ~~**ORG MOVE to `blackdeep-tech`, do NOW before E2**~~ — **RESCHEDULED 2026-07-17 (owner):
   after the branch closes**, inside the OSS-prep pass. The "every day after E2 costs
   a permanent redirect dependency" warning **no longer applies to v1**: deferring C6 removed the
   only artifact-baked GitHub URL, so a v1 artifact is org-agnostic (verified 2026-07-17). Still an
   owner action needing the Blackdeep org to exist + owner rights on both, and it is a **hard gate on
   H3.4** — tag and publish only after the transfer.
6. ~~**CUDA payload split + DLL host**~~ — **RESOLVED 2026-07-16: split; all on GH Releases** (C6a). Shape still contingent on the C5b
   go/no-go. If C5a fails, Option 1 ships CUDA as a whole separate artifact and neither question
   exists. Owner leaning: split (a knaif upgrade must re-fetch the ABI-coupled `ggml-cuda` but must
   NOT re-fetch the 493 MB NVIDIA redist, measured on CUDA 13.3 — 442 MB of it `cublasLt` alone).
