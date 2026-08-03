# iOS Model Feasibility — which model or API can plan on-device, at what quality and speed

**Status:** Planning · **Created:** 2026-08-03 · **Completed:** —
**Owner:** eval · **Ref:** [MODELS.md](../MODELS.md) · [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md) · [PERFORMANCE.md](../PERFORMANCE.md) · [`orchestrator.py`](../../python/core/knaif/orchestrator.py)

> **Status note.** Not started. Executed **entirely on a Mac** — no iOS device, no app, no
> Swift/Rust FFI, no packaging work. This plan answers only *which model or API could do knaif's
> planning job on iOS, and how well*. The iOS **skills** are a separate track and are out of scope
> (§13) — the existing ffmpeg and documents corpora are used here as **instruments for measuring
> models**, not as candidates for iOS.
>
> **⚠️ Self-contained by design.** This plan is executable on its own: it changes no shipped
> configuration, depends on no other plan, and nothing in the repository has to be fixed before it
> can start (§2 explains why the eval regression gate's known defects do not block it). It does
> **not** produce, and must not wait for, any macOS *release* work — packaging, signing and
> notarization are a different effort with a different owner. The only resource it shares with other
> work is **the Mac itself**, and §5 P1 states why that matters for timing runs.

**Goal:** Produce a per-model go/no-go table for iOS — knaif-qwen3 (llama.cpp Metal and MLX), Apple Foundation Models on-device, and Apple Private Cloud Compute — giving measured planning quality against knaif's existing corpora, measured Mac-side latency, and the structural constraints (context, footprint, determinism) that decide viability.

---

## 1. What this plan answers, and what it cannot

| Answers | Does **not** answer |
|---|---|
| Relative planning quality of each model/API on knaif's real task | **iPhone/iPad performance** — needs a device (D5) |
| Mac-side latency and cold-start per arm | What the iOS skills are (user's separate track) |
| Whether knaif's prompt fits each context window | The Swift-host / Rust-core FFI design |
| What each API actually exposes (greedy, token counts, guided generation) | Whether to ship an iOS product at all |
| Whether an arm can ever hold a committed snapshot | Anything about shipping a macOS CLI — packaging, signing, notarization |

> **⚠️ The single most important limit.** Every number produced here is a **Mac** number. An
> M-series Mac has more GPU cores, no phone thermal envelope, and no jetsam memory limit. Mac
> figures are an **upper bound** for iPhone and no iPhone claim follows from them. See D5.

---

## 2. Why `cheap` is the right primary instrument here — and where it stops being one

[AGENTS.md](../../AGENTS.md) and [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md) are emphatic:
*`cheap` is an iteration instrument, never an acceptance bar.* That rule governs **locking a
skill's snapshot**. This plan locks nothing, gates nothing, and re-baselines nothing — it compares
**models to each other**, on the same corpus, on the same day.

Under that use, `cheap` is not merely acceptable, it is the **correct** instrument:

- It isolates the model. The question is *routing and arg-fill*, not whether ffmpeg produced a
  conformant file — and the iOS skills will be different code anyway, so artifact-level grading of
  *these* skills carries less signal than usual.
- Its known distortion is **common-mode**. The `verifier_kind` `plan → output` flip that moved the
  ffmpeg aggregate 0.973 → 0.928 with no behavior change applies identically to every arm measured
  on the same corpus, so it **cancels in a comparison**. It does *not* cancel against a snapshot.

Two consequences follow, and both are load-bearing:

- **No result here is ever compared to a committed snapshot.** Measured 2026-08-02: ffmpeg's bar is
  a **`cheap`** snapshot at **297 rows against a 314-row corpus**; documents' is `success` at **129
  against 143**. Both are stale, and ffmpeg's is locked on a verifier the docs forbid as a bar.
  Comparing to either would produce a false regression that says nothing about the model.
- **The repository's regression gate is separately known to be broken, and this plan does not
  depend on it.** For the record, so nobody here re-derives it: `cmd_regression` defaults
  `current = baseline` and so compares the snapshot to itself; `just eval-regression skill:` takes
  no `*args` and cannot forward `--current`; and `just eval-success` persists no scoreboard without
  `--save`. **None of that blocks this plan**, because no `--current` diff happens anywhere in it.
  Do not fix it as part of this work — it is repository work with its own owner, and pulling it in
  would couple a research spike to a release-gating change.

But `cheap` never executes anything, and this plan's deliverable is a *quality expectation that
will drive a product decision*. So the finalists are re-run with an executing verifier (§9).

---

## 3. Decision log

**D1 — `cheap` for the full matrix, `success` for the finalists. Nothing is locked or gated.**
Per §2. No snapshot diff, no regression gate, no re-lock.

**D2 — arms are equalized on constrained decoding, and never compared across that boundary.**
Guided generation makes structural validity and unknown-tool errors *structurally impossible*, so
a guided arm scores better partly for reasons that are not model quality. Pair them:
FM free-form ↔ qwen3 `json_mode: false`; FM guided ↔ qwen3 `json_mode: true`. A guided-vs-free-form
comparison measures the constraint, not the model.

**D3 — greedy everywhere.** `GenerationOptions` greedy sampling for FM/PCC (existence to be
confirmed — B2), `temperature=0.0` for llama.cpp (already the case in `orchestrator.py`) and for
mlx-lm. Sampled output makes run-to-run variance a confound indistinguishable from model quality.

**D4 — `knaif-qwen3-1.7b-v1` is *the* iOS candidate; the 4B is a reference ceiling, not a
candidate.** *Confirmed with the owner 2026-08-03.* For an iOS app the 4B is not viable — 2.5 GB of
weights against an iOS memory budget — and [MODELS.md](../MODELS.md) §4.2 already recommends the
1.7B (Q6_K, **1.32 GB**) for footprint-constrained surfaces. **The headline comparison this plan
must produce is `qwen3-1.7b` vs Apple FM**; the 4B arms exist only to say how much quality the
footprint costs, and the stock 4B only to say what fine-tuning buys.

> **The comparison is fairer than the parameter counts suggest.** The 1.7B ships at **Q6_K, 1.32 GB**;
> Apple's on-device model is ~3B at **2-bit QAT**. Those land in the same footprint class, so this is
> a genuine like-for-like on the axis that matters for a phone — not a small model against a large
> one. Report footprint alongside every quality number (B4) so the trade is visible.

⚠️ Two things follow for reporting. MODELS.md §4.2 records that the 1.7B's weakness **concentrates
in the hard slice** (0.691 at Q4, holding at Q6), so an aggregate hides exactly the difference that
decides this — hence M5. And the 1.7B is the arm whose MLX requant matters (A3); a degraded 4B MLX
requant would waste effort on a model that is not a candidate.

**D5 — ⚠️ every latency number is a Mac number and is labelled as such.** This extends
[PERFORMANCE.md](../PERFORMANCE.md) §1's standing rule (*never quote a latency number without
naming the machine*) to a new axis: the machine is not the target device. State plainly in the
deliverable that no iPhone claim follows from this plan.

**D6 — an FM/PCC scoreboard is dated to an OS build, not pinned to a checksum.** Apple's greedy
determinism holds only *within a model version*, and the base model moves with the OS. Record the
OS build and `contextSize` in every FM/PCC run's metadata and in its `evals/INDEX.md` row. This is
a permanent property of these arms and the reason they may never be able to hold a snapshot (§13
Q4) — a fact about *evidence*, independent of how well they score.

**D7 — free-form FM first; the `DynamicGenerationSchema` work is gated behind the pilot.** Guided
generation fixes *structure*, not tool choice: if free-form FM routes badly, guided will not save
it. But if free-form FM emits prose or refusals instead of a plan envelope, guided becomes
**mandatory** rather than optional. The pilot (§7) decides which world we are in. **Do not build
the schema mapping first.**

**D8 — this plan changes no shipped configuration.** No snapshot re-locked, no
`recommended_model:` repointed, no `models.yaml` edit. Producing a *finding* — about the 1.7B, about
FM, about footprint — is the deliverable; acting on one is a separate, deliberate commit.

**D9 — ⚠️ the Mac→iOS transfer is valid for QUALITY and invalid for SPEED and REACH, and that
asymmetry is the entire licence for this plan.** *Added 2026-08-03, verified against Apple's own
material.* Apple ships **one** on-device model — the same ~3B, 2-bit-QAT model across iOS, iPadOS,
macOS and visionOS — so an FM quality number measured on a Mac is a claim about the same weights an
iPhone runs. **Without that fact this plan would not be worth executing.** Three things do *not*
transfer, and each becomes a task rather than an assumption:

- **Speed.** Same weights, different silicon and thermal envelope. Per D5.
- **`contextSize`.** A *queryable* property, not a constant — it already moved 4,096 → 8,192 between
  OS 26 and 27, so it may also vary by device. Read it, never hard-code it (B2); record it per run
  (D6).
- **Reach.** FM requires an Apple-Intelligence-capable device, so **an FM-backed iOS app addresses a
  subset of iPhones**, while a bundled `knaif-qwen3-1.7b` runs wherever the app installs. No quality
  number offsets that; it belongs in G1's table (B5).

> The asymmetry runs the other way for the qwen3 arms: their *quality* transfers trivially
> (identical GGUF, greedy decode) but their *feasibility* does not — 1.32 GB against an iOS memory
> budget is the open question B4 can only partially answer.

---

## 4. The arms

**The headline pair is `qwen3-1.7b-metal` vs `apple-fm`** (D4). Everything else is a reference
point, a control, or a variant — and if time runs short, the table below is also the drop order,
bottom-up.

| Arm key | Model | API / runtime | Role |
|---|---|---|---|
| `qwen3-1.7b-metal` | `knaif-qwen3-1.7b-v1` Q6_K (**1.32 GB**) | llama-cpp-python, Metal | **candidate** — the iOS model (D4) |
| `apple-fm` | Apple on-device (~3B, 2-bit QAT) | Python SDK, free-form | **candidate** — the zero-download alternative |
| `apple-fm-guided` | same | Python SDK + `DynamicGenerationSchema` | candidate variant, conditional on §8 (D7) |
| `qwen3-4b-metal` | `knaif-qwen3-4b-v1` Q4_K_M (2.50 GB) | llama-cpp-python, Metal | **ceiling** — what the footprint costs. Not an iOS candidate |
| `qwen3-4b-stock` | stock Qwen3-4B Q4_K_M | llama-cpp-python, Metal | control — what fine-tuning buys (MODELS.md §4.1: 0.905 full / 0.909 hard) |
| `apple-pcc` | Apple server model (32K ctx) | Python SDK | option — no download, no API keys; **not on-device** |
| `qwen3-1.7b-mlx` | `knaif-qwen3-1.7b-v1` requantized | mlx-lm | control — does MLX beat llama.cpp **for the candidate** |
| `qwen3-4b-mlx` | `knaif-qwen3-4b-v1` requantized | mlx-lm | optional — drop first (D4) |

---

## 5. Workstream P — prerequisites and baseline

- [ ] **P1. Record the machine.** Chip (M-series generation), P/E core counts, GPU core count,
      **unified memory**, macOS build. Becomes a row in [PERFORMANCE.md](../PERFORMANCE.md) §1;
      every number this plan produces is quoted against it (D5).
      > **⚠️ Timing runs need the machine to themselves.** If anyone else is using this Mac —
      > compiling llama.cpp, running an eval, building an installer — §11's latency figures are
      > measured against a contended CPU and GPU and are simply wrong. Quality runs (§9, §10)
      > tolerate sharing; **§11 does not**. Agree exclusive windows for F1/F2 before measuring, and
      > record in the results whether the box was idle. This is the one resource this plan shares
      > with any other work.
- [ ] **P2. ⚠️ Confirm macOS 27 and Apple Intelligence.** The on-device context is **4,096 tokens
      shared input+output on macOS 26** and **8,192 on macOS 27**. knaif's ffmpeg prompt is **3,938
      tokens** and the native path budgets 512 output tokens
      ([`llama.rs:241`](../../native/crates/knaif-llm/src/llama.rs#L241)) — so on macOS 26 the
      on-device arm is **structurally impossible**, not merely tight. Confirm the build, that Apple
      Intelligence is enabled, that model assets are downloaded, and that the availability API
      reports available, **before** any other work.
- [ ] **P3. Python SDK and `fm` CLI.** Confirm both are present (macOS 27), record versions, and
      confirm the SDK's Python requirement coexists with `mise.toml`'s pinned interpreter. If it
      needs its own environment, record how the eval suite reaches it.
- [ ] **P4. Fixtures and external tools.** `brew install ffmpeg` (ffmpeg **and** ffprobe), then
      `just eval-fixtures ffmpeg` and `just eval-fixtures documents`. ⚠️ Missing fixtures do not
      error — they **silently score correct plans near zero** (a documents baseline once landed at
      0.55 for this reason). Needed for §9; harmless to do first.
- [ ] **P5. GGUFs present and arms pinned.** `knaif-qwen3-4b-v1-q4_k_m`, `knaif-qwen3-1.7b-v1-q6_k`,
      stock `qwen3-4b`. ⚠️ `eval_backends.yaml`'s header warns that most stanzas' GGUFs are
      deliberately absent and score **~0.0**, which reads as catastrophic quality loss. **Always
      pass `--backends` explicitly** — omitting it runs every stanza.
- [ ] **P6. Darwin baseline.** `uv run pytest` and record pre-existing failures. Some Python tests
      have never run on Darwin; a latent failure must not be discovered later and blamed on this
      plan. If someone else has already recorded a Darwin baseline on this machine, reuse it rather
      than re-running — but do not *assume* one exists.

---

## 6. Workstream A — the two new eval backends

- [ ] **A1. `backend: apple_fm` in [`orchestrator.py`](../../python/core/knaif/orchestrator.py).**
      Three touch points: the `__init__` dispatch ([line 113](../../python/core/knaif/orchestrator.py#L113)),
      `infer` ([line 399](../../python/core/knaif/orchestrator.py#L399)), `infer_stream`
      ([line 475](../../python/core/knaif/orchestrator.py#L475)). Must satisfy the existing
      `InferenceBackend` protocol so nothing downstream changes. Options:
      `variant: on_device | pcc`, `guided: bool`, `max_tokens`, greedy sampling (D3).
- [ ] **A2. `backend: mlx`** via `mlx-lm`, same three touch points. No Apple SDK involved —
      `mlx_lm.load()` + `generate()` is the whole surface.
- [ ] **A3. Requantize the two knaif fine-tunes to MLX — from the merged f16, not the GGUF.**
      Record quant and on-disk size. ⚠️ If only the Q4/Q6 GGUF survives, requantizing from it is a
      **double quantization**: either label the arm as such in every result, or drop it. Do not
      silently produce a degraded arm and attribute the loss to MLX.
- [ ] **A4. `eval_backends.yaml` stanzas for every arm in §4.** Per that file's own convention,
      every stanza sets `json_mode` and `thinking_enabled` **explicitly** — D2 depends on those
      being deliberate rather than inherited.
- [ ] **A5. ⚠️ Keep the non-Mac suite green.** Both backends must import lazily and skip cleanly
      when the SDK / `mlx` is unavailable, so `just check` still passes on the Windows and Linux
      boxes. Add unit tests that exercise the dispatch and the skip path.

---

## 7. Workstream B — Gate 0: constraints, before any quality measurement

- [ ] **B1. ⚠️ Count knaif's prompts with Apple's own tokenizer.** The 3,938-token figure is a
      **Qwen3** count; Apple's tokenizer differs, and knaif's prompt is unusually dense in tool
      names, JSON and flag strings — exactly where tokenizers diverge. Measure both skills' prompts,
      then record **headroom = `contextSize` − prompt − `max_tokens`**. If headroom ≤ 0, the
      on-device arm is dead as-is, and the only honest fallback is a retrieval-shrunk registry
      applied to **every** arm — otherwise the comparison is rigged in FM's favour (§13 Q5).
- [ ] **B2. API capability audit — write down what the Python SDK actually exposes.** Greedy
      sampling; `contextSize`; token counting; guided generation and `DynamicGenerationSchema`;
      streaming; per-request latency; an OS/model build identifier. **Each "no" invalidates a
      decision above** — no greedy breaks D3, no build identifier breaks D6.
- [ ] **B3. PCC reachability and terms.** Does the server arm need an account, an entitlement, or
      network permissions? Record exactly what a knaif user would have to accept, since "no API
      keys" is the arm's main selling point and must be verified rather than repeated.
- [ ] **B4. Footprint facts for D4.** On-disk size per arm and peak RSS during a run. ⚠️ Report as
      **Mac RSS**; an iOS app's budget is smaller and this plan cannot measure it (§14 Q6). The
      number that matters is the candidate's: `knaif-qwen3-1.7b-v1` at 1.32 GB on disk versus
      Apple FM at zero.
- [ ] **B5. Device reach for FM (D9).** Record which Apple silicon generations expose the on-device
      model, i.e. what fraction of installed iPhones an FM-backed app would exclude. This is a
      product constraint that sits beside the quality numbers in G1 and cannot be traded against
      them — a bundled 1.7B runs wherever the app installs.

---

## 8. Workstream C — the pilot (20 rows) and its kill gates

`just eval ffmpeg --limit 20 --backends <arm>` per arm, before spending the full matrix.

- [ ] **C1. Output-shape check on free-form FM.** Three outcomes, each with a different consequence:
      - **parseable plan envelopes** → proceed free-form (D7);
      - **prose or preamble instead of JSON** → guided generation becomes **mandatory**; build C2
        before the matrix;
      - **guardrail refusals on legitimate rows** → record the rate *now*. This is the
        Phi-4-mini failure mode: MODELS.md §4.1 records a candidate dropped for rejecting 47
        legitimate plan rows to win the safety tags. It can disqualify FM independently of routing
        quality, and Apple's guardrails are not tunable by us.
- [ ] **C2. (conditional) `tools.yaml` → `DynamicGenerationSchema` mapping.** ⚠️ The shape knaif
      needs is a **heterogeneous array** — each step's arg schema depends on which tool it is — so
      it needs an `anyOf`-style choice plus schema references. That is the least commonly exercised
      corner of the API. **Prove it with a two-tool toy schema before mapping the real registry.**
- [ ] **C3. Record a go/no-go per arm.** An arm that fails here does not enter §9's matrix, and the
      reason is written down — a negative result is a deliverable, not a gap.

---

## 9. Workstream M — the full matrix (`cheap`)

- [ ] **M1. Both skills × every surviving arm, full corpora.** `--backends` pinned (P5),
      `--save evals/runs/2026-XX-XX_ios-feas-<arm>_cheap/`, one `evals/INDEX.md` row per run per
      the naming convention in `evals/INDEX.md`.
- [ ] **M2. ⚠️ Compare arms to each other, never to a committed snapshot.** Per §2 and D1. Every
      arm measured on the same corpus, same machine, same day.
- [ ] **M3. Safety corpora.** `data/safety_test.jsonl` per skill — must produce `reject`. Report
      separately from the routing aggregate; a model can win one and lose the other.
- [ ] **M4. Over-refusal on *legitimate* rows, as a first-class metric.** Per C1. This is the number
      that killed a previous candidate, and for FM it is a property of a guardrail layer we do not
      control.
- [ ] **M5. Hard-slice breakdown.** MODELS.md §4.2 shows the 1.7B's weakness concentrates there
      (0.691 at Q4 vs 0.869 full). Report full **and** hard per arm — an aggregate hides exactly
      the difference that matters for a small on-device model.

---

## 10. Workstream E — finalists (`success`)

- [ ] **E1. The headline pair, executing verifier, fixtures regenerated first.** `cheap` never runs
      the command; the deliverable is a quality expectation that will inform a product decision, so
      it must be grounded in produced artifacts at least once. **Run `qwen3-1.7b-metal` and the best
      surviving FM arm regardless of where they rank** (D4) — those two are the decision, and a
      ranking that promoted the 4B ceiling over the candidate would answer a question nobody asked.
      Add a third arm only if it is genuinely in contention. Save and index as in M1.
- [ ] **E2. If an FM or PCC arm is a finalist, run it twice — different days, and after any OS
      update.** Sizes the drift D6 warns about, and turns "the model can change underneath you"
      from an assertion into a measurement.

---

## 11. Workstream F — performance

- [ ] **F1. Per-arm latency, PERFORMANCE.md methodology**, so rows are comparable to the existing
      ones: ffmpeg skill prompt, 32-token generation, fresh process, median of warm reps,
      `KNAIF_TIMING=1`. Report per-phase (load, prompt decode, generation) where the backend
      exposes it; FM/PCC will likely expose only wall time — say so rather than inventing phases.
- [ ] **F2. Cold start vs warm.** First call after boot vs steady state, per arm. llama.cpp pays
      model load; FM may pay an assets/compile cost; PCC pays network. The first-run experience is
      a product property, not a footnote — Vulkan's 38.3 s first run (PERFORMANCE.md §2) is the
      precedent for why this gets measured.
- [ ] **F3. Write the rows.** A machine row in [PERFORMANCE.md](../PERFORMANCE.md) §1 and an arm
      table, **every row labelled with the Mac** (D5), plus one explicit sentence that these are not
      iPhone numbers.

---

## 12. Workstream G — the deliverable

- [ ] **G1. The answer table.** One row per model/API, every cell filled or explicitly marked
      *"not measurable on a Mac"*:

      | Model / API | Usable on iOS? | `cheap` ffmpeg full / hard | `cheap` documents | `success` (finalists) | Over-refusal | Mac p50 | Cold start | Download | Device reach | Context headroom | Determinism | Blocking caveat |

- [ ] **G2. The headline verdict: `knaif-qwen3-1.7b` vs Apple FM**, stated as a direct comparison
      before the per-row detail (D4) — how much quality the zero-download option costs, or buys, on
      the hard slice as well as the aggregate, at a comparable footprint (1.32 GB Q6 vs ~3B at
      2 bits).
- [ ] **G3. A one-paragraph verdict per model** — *usable / not usable / usable-with-caveat* — and,
      for each, **the one thing that would change the answer**. A verdict with no falsifier attached
      is an opinion, not a finding.
- [ ] **G4. Docs.** [PERFORMANCE.md](../PERFORMANCE.md) rows (F3), `evals/INDEX.md` rows,
      [MODELS.md](../MODELS.md) **only if** a finding warrants recording (D8 — a finding, not a
      change), plus [plans/README.md](README.md) and [TODO.md](../TODO.md) entries.

---

## 13. Out of scope

- **The iOS skills.** A separate track with its own owner. The existing corpora are instruments
  here, not candidates — neither ffmpeg nor documents can execute on iOS (both shell out to external
  binaries), and this plan does not attempt to change that.
- **iPhone / iPad measurement.** Needs a device (D5).
- **The Swift-host / Rust-core FFI bridge and the app architecture.** Needed for any iOS app
  regardless of model, and not attributable to the model choice.
- **All macOS *release* work — packaging, signing, notarization, installers.** A separate effort
  with a separate owner. This plan builds nothing that ships and touches no file under
  `installers/`.
- **Re-locking any snapshot or changing `recommended_model:`** (D8).
- **Training an adapter on Apple's base model.** Only becomes a question if FM lands close; then it
  is its own plan, with the base-version coupling as its central problem.
- **Repairing the eval regression gate.** Real and known (§2), owned elsewhere, and **not a
  prerequisite** — nothing here diffs against a snapshot. Do not absorb it into this work.

---

## 14. Open questions to resolve during execution

1. **Does the Python SDK expose greedy sampling and token counting?** (B2.) No greedy breaks D3; no
   build identifier breaks D6.
2. **Does the merged f16 of each knaif fine-tune still exist**, or only the quantized GGUF? (A3.)
   Decides whether the MLX arms are honest or must be labelled double-quantized.
3. **What does the PCC arm require of a user** — account, entitlement, network policy? (B3.)
4. **Can an FM/PCC arm ever hold a committed snapshot**, given the base model moves with the OS?
   (D6.) A methodology question, not a measurement — but it decides whether these models can be
   part of a regression-gated product at all.
5. **If on-device headroom is tight, is a retrieval-shrunk registry acceptable** — and does it then
   become the shape *every* arm is measured on? (B1.)
6. **Does an iOS app's memory budget admit the 1.7B at all?** Not answerable on a Mac (B4).

---

## 15. Acceptance criteria

This plan is done when **all** of the following hold:

1. **G1's table exists**, with every cell filled or explicitly marked not-measurable-on-a-Mac, and
   G2's verdict-plus-falsifier written for every model.
2. Every arm ran with **`--backends` pinned**, fixtures regenerated first, and a row in
   `evals/INDEX.md` per saved run.
3. **No result is compared to a committed snapshot** (D1/§2), and free-form and guided arms are
   never compared across the constraint boundary (D2).
4. Every latency number **names the Mac**, and the deliverable states in its own words that **no
   iPhone claim follows** (D5).
5. Every FM/PCC run records the **OS build** and `contextSize` (D6), and at least one finalist FM
   arm was measured twice to size drift (E2).
6. **Over-refusal on legitimate rows** is reported per arm, separately from the safety-corpus
   `reject` rate (M3/M4), and the hard slice is reported alongside the aggregate (M5).
7. **Nothing shipped changed**: no snapshot re-locked, no `recommended_model:` repointed, no
   `models.yaml` edit (D8).
8. A negative result is recorded as a result — an arm killed at C3 has its reason written down,
   not an empty row.
