# Plans Index

Durable implementation plans, ordered by date. Each plan is a single self-contained
file with inline `- [ ]` / `- [x]` checkboxes tracking its own progress. This index is
the at-a-glance status map; the individual plan headers remain the source of truth.

**Authoritative queue.** There is exactly one live work queue: the **Open / Next** section of
[../TODO.md](../TODO.md). This index and the plan headers are the *record* — what each plan is and
whether it shipped — **not** a backlog. To pick what to work on next, read TODO.md Open / Next; to
learn the state or rationale of a specific plan, read its header. The header lint
([../../python/core/tests/test_plan_headers.py](../../python/core/tests/test_plan_headers.py), run by
`just check`) keeps this index's status column and every plan header conformant, so the three
surfaces can't silently disagree.

Status legend: **Done** (shipped/closed) · **Active** (in progress) ·
**Draft** (written, not approved — do not implement) · **Planning** (not started) ·
**Superseded** (replaced by a later plan). "Complete" and "Implemented" both fold into
**Done** — use Done.

## Plan header format

Every plan opens with the same block directly under its `# Title`, so status, dates,
and goal are scannable at a glance:

```markdown
# <Title>

**Status:** Done · **Created:** YYYY-MM-DD · **Completed:** YYYY-MM-DD
**Owner:** <area> · **Ref:** <PR # / branch / related plan, or —>

> **Status note:** <free-form current state — supersession, parking rationale,
> what's left, risks/dependencies. Optional, but where the nuance lives.>

**Goal:** <one sentence: what this plan delivers.>
```

Rules: **Created** is the plan's filename date. **Completed** is the date the plan
reached its terminal status (Done/Superseded), or `—` for open plans or when no date
was recorded. Use the legend statuses only. Keep the one-line `**Goal:**` even when a
detailed `## Goal` section follows.

| Date | Plan | Status | Notes |
|---|---|---|---|
| 2026-05-24 | [eval-suite](2026-05-24-eval-suite.md) | Done | `knaif.evalsuite` package + ffmpeg eval plugin; absorbs the superseded v1 draft and the T12–T14 report implementation. |
| 2026-05-27 | [ffmpeg-prompt-small-model](2026-05-27-ffmpeg-prompt-small-model.md) | Done | Eval-correctness fixes + retrospective. |
| 2026-06-06 | [deterministic-clarify](2026-06-06-deterministic-clarify.md) | Done | T1–T17. **Kept** — records the static-planning-over-re-planning decision. |
| 2026-06-06 | [eval-quality-fixes](2026-06-06-eval-quality-fixes.md) | Done | T1–T3 (+7.6pp); T4 won't-fix. **Kept** — the T4 refusal-routing argument and two corrected triage hypotheses; durable rules extracted to EVAL_FRAMEWORK / EVAL_VERIFICATION_SOP / REQUIREMENTS. |
| 2026-06-08 | [local3-outcome-fixes](2026-06-08-local3-outcome-fixes.md) | Done | Eval iteration (qwen3-4b outcome fixes). **Kept** — per-row forensics; carries the still-open Corpus Trim. |
| 2026-06-09 | [complex-two-step-intents](2026-06-09-complex-two-step-intents.md) | Superseded | Retired (2026-06-27), never implemented. **Kept** — the only record that producer-side strip-audio fusion was scoped and deliberately dropped; chain-fidelity residual → fine-tuning. |
| 2026-06-09 | [context-injection](2026-06-09-context-injection.md) | Planning | **Kept — live future work (parked).** Parked for the ffmpeg-UI work; dependencies shipped, design refreshed 2026-07-22 (hook re-scoped to `Skill.provide_context()`). |
| 2026-06-09 | [descriptor-mixed-intent-analyzer](2026-06-09-descriptor-mixed-intent-analyzer.md) | Done | Read-only analyzer (T1–T6). **Kept** — design doc for a live module that cites it by name; its OFF column became [nl-clarify-gate](2026-06-09-nl-clarify-gate.md), its ON column [context-injection](2026-06-09-context-injection.md). |
| 2026-06-09 | [nl-clarify-gate](2026-06-09-nl-clarify-gate.md) | Done | Shipped (PR #14). **Kept** — design doc for `nl_clarify_gate.py`; holds the utterance-not-args constraint and the T7 numbers. Its T4 injection-ON seam is only half-wired (see the audit box). |
| 2026-06-09 | [verify-output](2026-06-09-verify-output.md) | Draft | Kept, ready (2026-06-21); queued behind OSS P0s. Highest-leverage draft. **Gap re-verified live 2026-07-22** — `verify_outputs` still asserts nothing; see [ARCHITECTURE.md](../ARCHITECTURE.md#known-gap-validation-stops-at-dispatch). |
| 2026-06-16 | [oop-skill-architecture](2026-06-16-oop-skill-architecture.md) | Done | Phases 0–5 (PR #15). io rebuild is the one scoped-out remainder. **Kept** — design record for the `Step`/`Intent`/`Skill` model; repointed 2026-07-22 for the `contracts/` restructure. |
| 2026-06-17 | [ffmpeg-geometry-and-thumbnail-merge](2026-06-17-ffmpeg-geometry-and-thumbnail-merge.md) | Done | **All three phases** — Phase 3 eval ran 2026-06-17/18 (was wrongly marked deferred until 2026-07-22). **Kept** — its comparison methodology is now in [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md). |
| 2026-06-17 | [monorepo-dual-runtime](2026-06-17-monorepo-dual-runtime.md) | Done | Phases 0–9 shipped; v1 merged (PR #39). Phase 10's CI remainder → [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md). **Kept — the design record for the dual-runtime architecture**; 10 files (incl. `Cargo.toml`, `pyproject.toml`, 7 READMEs) cite it *as* their rationale. |
| 2026-06-18 | [ffmpeg-prompt-optimization](2026-06-18-ffmpeg-prompt-optimization.md) | Done | qwen3-4b A/B prompt loop; winners merged (PR #18), 0.862→0.933 outcome. **Kept** — the measured record incl. negative results; method extracted to [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md). Step 5 (leakage check) never ran. |
| 2026-06-19 | [knaif-cli-sdk](2026-06-19-knaif-cli-sdk.md) | Done | `knaif.cli` developer SDK (PR #24). **Kept** — decision record behind [SDK.md](../SDK.md); its cross-track `arg_schema` prediction was verified against the Rust port. Three v1 deferrals tracked in [TODO.md](../TODO.md). |
| 2026-06-21 | [documents-skill](2026-06-21-documents-skill.md) | Done | knaif's 2nd skill (general document toolkit); unblocks the monorepo plan. Vector/search split into its own future plan. |
| 2026-06-22 | [documents-productionization](2026-06-22-documents-productionization.md) | Done | Implemented → production (2026-06-27): hands-on CLI/notebook testing, corpus growth, baseline lock, prompt/step iteration. **Kept** — how a *second* skill matured, and where ffmpeg-shaped assumptions broke; plan-shaped-skill and corpus-fork rules extracted to EVAL_FRAMEWORK / CORPUS_AUTHORING_STEPS. |
| 2026-06-25 | [cross-skill-eval-monitoring](2026-06-25-cross-skill-eval-monitoring.md) | Done | One shared model, per-skill eval; cross-skill regression detection; skill-aware run history (all 4 phases). **Kept** — the `--all-skills` commands are now in [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md), and its two gate-design traps in [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md). |
| 2026-06-26 | [skill-package-loader](2026-06-26-skill-package-loader.md) | Done | Skills are importable Python packages; `vocab.yaml` Tier-1 extraction. Precursor to the dual-runtime plan. |
| 2026-06-27 | [big-llm-comparison](2026-06-27-big-llm-comparison.md) | Done | Premium-agent vs. local arm; opus-4-8 0.989 vs 4B 0.967. Contract: `docs/BIG_LLM_HANDOFF.md`. Task 6 (more providers) open. |
| 2026-06-27 | [fine-tuning](2026-06-27-fine-tuning.md) | Done | One multi-skill LoRA on both base sizes; size×precision×tuned matrix + held-out hard stratum. **Result:** fine-tuning generalized to the held-out hard slice (ffmpeg hard-outcome +.036–.091 every cell), small 1.7B gained most (full +.080/+.135), and the 1.7B quant tax shrank −.064→−.009; no per-skill forgetting. |
| 2026-06-29 | [training-subsystem](2026-06-29-training-subsystem.md) | Planning | **Kept — live future work**, none of 10 tasks built. The maintainer path exists (`python/training/` scripts + [FINE_TUNING.md](../FINE_TUNING.md)); the gap is packaging it for *third-party* skill authors (config + `just train-*` + author flow). Task 2 is obsolete — the restructure isolates training by layout. |
| 2026-06-30 | [best-skill-model](2026-06-30-best-skill-model.md) | Done | Data v2 did **not** beat v1 (hurt the hard slice); Gemma3 not competitive (worse + 4× slower). **Quant sweep result:** 1.7B-ft **Q6 (1.32 GB)** is the quality-per-byte pick — 1 GB smaller + 1.9× faster than 4B-Q4, docs matched, ffmpeg within ~2.4/3.6pt. **Rec: 4B-Q4 default; adopt 1.7B-ft-Q6 when footprint/latency matter.** |
| 2026-07-01 | [qwen3-ffmpeg-max-results](2026-07-01-qwen3-ffmpeg-max-results.md) | Done | ffmpeg-v3 targeted data + SFT grid. Produced `sft-v3-flat` (best 1.7B candidate); weighted SFT / tiny DPO / synthetic distill all failed. Open items (quant pass, production lane) finished in pass 3. |
| 2026-07-02 | [qwen3-finetuning-pass3](2026-07-02-qwen3-finetuning-pass3.md) | Done | Corrected the Q6-inflated headline (honest v3 effect +3.6pt hard/+6.2pt chain3); **promoted 4B-v3** for ffmpeg+documents (eliminated the old enum-bleed); retrieval keyword fixes (+0.48pt); planner-diversity hypothesis **not** supported. Canonicalized in [../FINE_TUNING.md](../FINE_TUNING.md). |
| 2026-07-02 | [retrieval-overhaul](2026-07-02-retrieval-overhaul.md) | Done | Lexical track shipped: recall@k/MRR harness + CI gate, CJK n-gram tokenization, df-weighted scoring (shared keywords), multilingual+CJK keyword pass. **Retrieval recall@5 ffmpeg 0.853→0.954, documents 0.914→0.947**, ascii unchanged. Phase 2 (embeddings) not pursued (lexical sufficient vs mobile budget). Absorbs the CJK plan. Follow-on: ZH fine-tune, now unblocked. |
| 2026-07-06 | [model-hosting-cdn-research](2026-07-06-model-hosting-cdn-research.md) | Done | **Research findings.** Cost/vendor analysis behind the hosting decision: **HF kept** (the client-side fetcher fix — now shipped in `knaif-models/src/fetcher.rs` — closed the speed gap, not a host change; R2 the vetted fallback), and the swap is URL-only because models are content-addressed. Conclusion in [MODELS.md §7](../MODELS.md); referenced only from docs. |
| 2026-07-07 | [inference-backend-performance](2026-07-07-inference-backend-performance.md) | Done | **Research findings.** The inspectable primary evidence behind [PERFORMANCE.md](../PERFORMANCE.md) §2 and [NATIVE.md](../NATIVE.md) backend guidance (5080 backend ordering, Blackwell Vulkan collapse, first-run shader tax). **Kept** — only here is the negative-result method (`VkPipelineCache` dead end). |
| 2026-07-15 | [native-branch-finalization](2026-07-15-native-branch-finalization.md) | Done | **Merged to `dev` 2026-07-19 (PR #39).** Closeout plan for `feature/native-implemetation`: merge-readiness (local green), default-model auto-select, CUDA multi-arch build (opt-in `GGML_BACKEND_DL`), Linux packaging, **manually-cut** v1 release → merge to `dev`. **A, B, C, D, E, G closed; H1 green on both boxes; H2 Windows install smoke done; H3 closed at the merge.** **Scope narrowed 2026-07-17:** C6 (CUDA opt-in surface) + Workstream F (CI) moved out; org move moved to the OSS-prep pass, after close. **Post-close (follow-on plans):** OSS-prep → transfer → tag `v1.0.0` → publish, then CI + CUDA. **Non-blocker:** C4(b) Blackwell proof (RTX 5080). |
| 2026-07-19 | [repo-restructure](2026-07-19-repo-restructure.md) | Done | Four-pillar layout — `python` / `native` / `skills` / `apps` (+ `contracts/`) — merged 2026-07-20 (PR #40). **Kept** — the canonical old→new path mapping that dated closeout records (e.g. native-branch-finalization) translate against; sequenced **before** the OSS-prep pass's X2 flatten. |
| 2026-07-22 | [document-corpus-search](2026-07-22-document-corpus-search.md) | Planning | Extracted from documents-skill's *Out of scope*. North-star: "find me the invoice to company X from august" — a hybrid query needing ingest-time structured extraction, not a vector index. Keyword-first; cache location is the open blocker. |
| 2026-07-25 | [windows-installer-polish](2026-07-25-windows-installer-polish.md) | Done | **Shipped 2026-07-27 (rides 1.1.0).** Eleven findings — six from a live v1.0.1 install session, five from a follow-up script cross-read. All three P0s fixed: the undeclared `deps` parent task that defeated three `unchecked` flags (AGPL Ghostscript + a ~350 MB LibreOffice shipped pre-checked); a missing `_is1` key leaving no Add/Remove row and no upgrade detection (rescue added — the cause is still unknown, so this is recovery, not prevention); and **`NOTICE` never distributed** (Apache-2.0 §4(d)) — fixed in `package.sh`, so it covers the Linux tarball and macOS too, **but not the AppImage**, which is assembled separately. Plus `[InstallDelete]`, `AppMutex`/`SetupMutex`, a runtime-equivalent dependency probe (`PATHEXT`, `$KNAIF_<CMD>_BIN`, `all_required`), icon + VERSIONINFO, and `test_installer_iss.py` — a lint verified by injecting all 14 mutations it claims to catch. **W4 (signing) split out** to [code-signing](2026-07-27-code-signing.md). Closed rather than left hanging: the upgrade assertion moved to `RELEASE.md` §4 as a recurring release check, ARM64 warn-and-allow moved to `TODO.md` (no hardware to test on), `WizardSmallImageFile` is won't-do. The F11 "published exposure" question was **based on a false premise** — no GitHub Release ever existed, so nothing was ever downloadable without `NOTICE`. |
| 2026-07-27 | [code-signing](2026-07-27-code-signing.md) | Planning | Extracted from windows-installer-polish's W4 — the only workstream there gated on an external party. Carries the certificate landscape (**EV no longer buys instant SmartScreen trust**; Microsoft pulled the privilege in 2024), the SignPath Foundation eligibility finding, and the payload + installer signing tasks. **Deferred 2026-07-27**: the Foundation application waits for more release history. **Deliberately not bound to the CI plan** — every path except SignPath signs fine from a dev machine, and signing may be wanted sooner than CI lands. S0 (Defender submission) is unblocked today and needs no certificate. |
| 2026-07-27 | [portable-builds](2026-07-27-portable-builds.md) | Done | **Shipped 2026-07-27.** Artifacts inherited a runtime floor from whatever machine built them; both OSes had a verified P0. **Windows:** every binary imported `VCRUNTIME140`/`MSVCP140`/`VCOMP140` with none staged — a clean Windows 11 image died at process start with `0xC0000135` printing nothing (proven in Windows Sandbox before and after the fix). **Linux:** built on 24.04 → glibc 2.39; now built in a fully-pinned `ubuntu:22.04` container (apt via snapshot.ubuntu.com, LunarG Vulkan 1.4.313, appimagetool by checksum). Also fixed: the AppImage shipped without `NOTICE` and with a 1×1 icon, and `libgomp.so.1` — the Linux twin of `VCOMP140` — was unstaged. **The measured floor is not what anyone assumed:** the artifact needs `GLIBC_2.34` (below the build base) and is really bound by `GLIBCXX_3.4.30`/`CXXABI_1.3.13`, so RHEL 9 misses by one libstdc++ version; bundling it was rejected because `libggml-vulkan.so` dlopens a host driver built against a newer one. Adds `check_pe_imports.py`, `check_elf_deps.py` and a two-sided `check-floor.sh`. **Through-line: a check that runs on the build box tests staging, never portability** — now a rule in RELEASE.md §4. |
| 2026-07-17 | [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md) | Active | **Workstream U is release-blocking.** The two things v1 consciously skipped: CI + `release.yml` + eval-parity (was Workstream F) and the CUDA opt-in surface (`backend install cuda` + R580 driver gate; was C6). **Workstream U became release-blocking 2026-07-28**, reversing the C6 deferral: `PERFORMANCE.md` §2 measures Vulkan at **~5.7 tok/s on Blackwell against ~80 on CUDA** — CPU speed on the newest NVIDIA cards — so a Vulkan-only artifact cannot be the first thing anyone downloads, and with no users yet there is nothing to protect by shipping sooner. Added **U6** (Windows `--kind=cuda` still emits the historical static app, not a payload — mechanism proven 2026-07-16, packaging missing) and **U7** (a CUDA build image, since the release image deliberately carries no toolkit). U3's nudge now keys on **compute capability**, because CUDA is correctness on Blackwell and a ~3% optimisation on Ampere. Workstream C stays not-started and non-blocking. |


## Open threads (not yet a plan, or spanning plans)

- **Live backlog** — the open roadmap (OSS-readiness blockers, orchestrator hardening,
  validator-feedback retry, ffmpeg consolidation, etc.) lives in the **Open / Next**
  section of [../TODO.md](../TODO.md). None of these have a dedicated plan yet.
- **io skill rebuild** — the OOP migration left `executor.py`'s bare-`CommandAgent` io
  implementation in place; the rebuild is its own effort (see oop-skill-architecture Phase 5).
- **Second skill** — prerequisite for the monorepo plan; planned as the **documents**
  skill ([2026-06-21-documents-skill.md](2026-06-21-documents-skill.md)). The `input_refs`
  media-vocab extraction moves to the future vector/search plan, not this skill.
- **Document corpus search** — "find me the invoice to company X from august" over a folder
  of local files. Now has its own plan:
  [document-corpus-search](2026-07-22-document-corpus-search.md) (Planning, not started),
  extracted 2026-07-22 from the documents-skill plan's *Out of scope* section.

  > **Corrected 2026-07-22.** This thread previously read "Local vector DB / similarity
  > search … replaces the broken whitespace `retrieve_tools`", pointing at
  > a standalone CJK-segmentation plan as the near-term fix. All three parts were stale: `retrieve_tools` was fixed lexically by
  > [retrieval-overhaul](2026-07-02-retrieval-overhaul.md) (recall@5 ffmpeg 0.853 → 0.954),
  > that plan **deliberately declined** its embedding phase, and the CJK plan was folded
  > into the same overhaul (and deleted 2026-07-23). **Tool retrieval is solved and is a
  > separate problem from document search** — the first ranks a few dozen tool descriptions
  > at prompt-build time, the second ranks thousands of user files at query time.
