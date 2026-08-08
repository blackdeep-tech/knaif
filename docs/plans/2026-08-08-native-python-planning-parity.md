# Native/Python planning parity — the prompt gap, its contracts, and the eval-parity lane

**Status:** Active — not started · **Created:** 2026-08-08 · **Completed:** —
**Owner:** core · **Ref:** absorbs **C4** from
[post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md); complements
`scripts/parity_check.py`

**Goal:** Make the native runtime plan identically to Python on the same model, and pin that with
contracts CI can enforce without a GGUF — then measure what remains with an eval-parity lane.

---

## Why this exists

Owner observation, macOS, 2026-08-07: **the native runtime produces worse plans than Python on the
same model, and would not produce a multi-step plan at all.**

**It is not macOS-specific.** Nothing below is platform-dependent — the finding surfaced there
because that is where the CLI was being exercised by hand. Treat "macOS" as the reporting
location, not the scope.

This matters more than an eval delta. The native runtime is what ships: the installers, the
tarball, the AppImage. Python is the authoring and eval runtime. So a quality gap between them is
a gap between *what we measure* and *what users get*, in the direction that flatters the numbers.

---

## What is already established

A code read on 2026-08-08, **before any run**. Recorded separately from the workstreams because
these are findings, not tasks — and because the first instinct (blame the model, or the platform)
is wrong.

### Confirmed by reading the source

1. **Retrieval is not ported.** `retrieve_tools` is implemented in
   `native/crates/knaif-core/src/retrieval.rs`, re-exported at `lib.rs:30` — and **nothing calls
   it**. `registry.rs` says so outright: *"Retrieval (`retrieve_tools`) is ported in a later
   slice."* `apps/cli/src/main.rs` passes `&self.registry` — the whole thing — to `build_prompt`.

   Measured against the real ffmpeg registry:

   | | tools in the prompt |
   |---|---|
   | Python (`retrieve_tools`, `top_k=5`) | **5 of 26** |
   | Native | **26 of 26** |

   Two utterances, same result: `"trim clip.mp4 to 5 seconds then resize to 720p"` retrieves
   `trim_video, resize_video, compress_video, convert_video, strip_audio`.

2. **Example selection is not ported either.** Python's `build_prompt` filters the few-shot block
   per utterance — `select_examples()` ranks by how many *retrieved* tools appear in each
   example's plan, and always includes one clarify and one reject example. Native uses the static
   `examples_block` from `prompt.yaml` for every utterance.

3. **The generation budget differs.** Native hard-codes `max_tokens: 512`
   (`knaif-llm/src/llama.rs`, `$KNAIF_MAX_TOKENS` overrides); the promoted Python eval config uses
   `2048`. A multi-step plan is the longest output the model ever emits.

4. **`prompt.rs`'s module docstring is stale.** It claims the tool listing is alphabetical
   "because `Registry` is a `BTreeMap`"; the code sorts by `def.order` with a comment that the
   fine-tuned model is sensitive to that order. The divergence was fixed and the header never
   updated — worth fixing here because it is exactly the kind of note that stops the next reader
   looking.

### Ruled out by reading

`n_ctx` is 8192 on both. `/no_think` is applied on both. Both decode **greedily** — Python passes
`temperature=0.0`, native takes the argmax over logits. `normalize_path_separators` runs on both.

### Why the prompt is the prime suspect

Same GGUF, same greedy decode, identical prompt → near-identical tokens. So a *systematic* quality
gap has to come from something deterministic, and the prompt is where the two runtimes differ most.

The mechanism is specific rather than vague: **the shipped model is fine-tuned on Python-shaped
prompts.** A 26-tool listing with generic examples is off-distribution for it. Multi-step planning
is the hardest thing it does and the first thing to degrade — which is precisely the reported
symptom.

**This is a hypothesis with an obvious test, not a conclusion.** Workstream P is that test, and it
runs before anything is changed.

### The note that should have caught this

`prompt.rs` records its divergences as deliberate, with a stated safety net:

> *"Two intentional, prompt-only divergences from Python (not graded byte-for-byte; **Phase 10
> eval-parity measures end quality**)"*

Eval-parity is C4. **It was never built.** A divergence accepted on the strength of a check that
does not exist is an unmeasured divergence, and that is the structural lesson here — bigger than
any single fix below.

---

## Workstream P — Diagnose before fixing

- [ ] **P1 — Reproduce, with both prompts captured verbatim.** A fixed utterance set including at
  least three known-good multi-step cases from `skills/ffmpeg/data/eval.jsonl`. Dump the native
  prompt (`KNAIF_DEBUG`) and the Python prompt for the same utterance and diff them. **The diff is
  the deliverable** — every later claim rests on it.
- [ ] **P2 — Quantify.** Token counts for both prompts, tool counts, which examples each carries.
  Turns "the prompts differ" into a number that can be tracked.
- [ ] **P3 — Attribute, one variable at a time.** `KNAIF_MAX_TOKENS=2048` alone; then native
  against a hand-trimmed registry matching Python's retrieved set. If trimming restores multi-step
  plans, the diagnosis is settled and Q1 is the fix. If it does not, **stop and re-diagnose** —
  do not proceed to Q on a hunch.
- [ ] **P4 — Record the outcome here**, including whichever hypothesis fails. A ruled-out cause is
  worth as much to the next reader as the confirmed one.

## Workstream Q — Port what is missing

Only after P3 attributes the gap. Each item is a **port, not a rewrite** — same inputs, same
outputs, per `docs/NATIVE.md`.

- [ ] **Q1 — Call `retrieve_tools` in the native plan path.** The function is already ported and
  tested; the wiring is not. Match Python's defaults (`top_k=5`, `min_score=0`) rather than
  choosing new ones — a different `top_k` is a different prompt, which is the bug being fixed.
- [ ] **Q2 — Port `select_examples`.** Ranked by retrieved-tool overlap, plus one clarify and one
  reject example. Note Python falls back to the full block when no retrieved subset is supplied;
  the port must keep that behaviour, not just the happy path.
- [ ] **Q3 — Settle the generation budget deliberately.** Not "make native 2048 because Python is".
  Measure the longest plan the corpus actually produces, set the number from that, and make both
  runtimes read it from one place so they cannot drift again.
- [ ] **Q4 — Fix the stale `prompt.rs` docstring** and re-state which divergences remain
  intentional, if any survive Q1–Q3.

## Workstream R — Contracts, so it cannot drift silently again

**This is the durable half.** Q fixes today's gap; R is what stops the next one. All of it is
deterministic and needs **no GGUF**, so unlike C4 it can gate every PR in CI.

- [ ] **R1 — Prompt-parity contract.** Fixed utterances × fixed registries → both runtimes must
  produce the **same prompt string**. Extend `contracts/parity/` in the shape
  `planner_cases.json` already uses, consumed by a Python test and a Rust test.
  - Byte-for-byte, or an explicit allow-list of divergences with a reason attached to each. **No
    third option** — "roughly the same" is what got us here.
- [ ] **R2 — Retrieval-parity contract.** Same utterance + registry → same selected tool set and
  order. Separable from R1 and worth its own cases: retrieval is scoring logic with tie-breaks,
  and it is where CJK tokenization and diacritic handling live.
- [ ] **R3 — Settings-parity contract.** Assert the two runtimes' generation defaults agree —
  `max_tokens`, `n_ctx`, sampling, thinking suppression. A hard-coded `512` on one side and a
  `2048` on the other must fail a test, not wait for someone to read both files.
- [ ] **R4 — Gate them in CI.** They belong in the existing `python` and `native` jobs rather than
  a new one; both already run on every change to `skills/` and `contracts/`.

## Workstream S — The eval-parity lane (was C4)

Moved here from the CI plan, which deferred it with a design finding. **Read that finding at C4
before starting** — it is why the lane is not built the obvious way.

Short version: `eval_backends.yaml` entries substitute *token generation*
(`InferenceOrchestrator.infer()`), while `knaif plan --json` is the whole pipeline in Rust.
Registering `rust-cli` as a peer of `llama_cpp` claims a substitution it does not make, and
`run_corpus`'s `_build_registry_override(agent, utterance)` has no honest answer on that path —
the binary already retrieved, with its own prompt. **The lane would report a delta and mean
nothing by it.**

- [ ] **S1 — Build it as two independent lanes.** Python via `run_corpus`; native via
  `knaif plan --batch` (already implemented: one model load, one JSON envelope per line, order
  preserved). Same GGUF, same verifiers, diff the scored aggregates at ±2%. The
  `eval_backends.yaml` entry then configures *which binary and which GGUF* — which is what it
  honestly is.
- [ ] **S2 — Run it against the divergence Q closed**, and record both numbers. A parity lane
  whose first run is also its first green run has proved nothing; this one has a known gap to
  measure across.
- [ ] **S3 — Sequencing is not optional.** R must land first. Until the prompt is pinned, a delta
  here cannot be attributed to a planner bug rather than to one side's prompt having been edited —
  which is the same unmeasured-divergence trap that produced this plan.

**It cannot run in CI**, either lane: both need a GGUF and `models/` is gitignored. Local tooling,
like `just parity`.

**`scripts/parity_check.py` is the complement, not a duplicate** — its own docstring opens
"deliberately NOT an eval-suite". It diffs rendered argv per utterance; this compares scored
aggregates. Both are wanted.

---

## Definition of done

The same utterance, the same model and the same skill produce **the same plan on both runtimes**,
and the repo can prove it without anyone remembering to check:

- The native prompt is the Python prompt — same retrieved tools, same selected examples — or every
  remaining difference is enumerated with a reason.
- A PR that changes one runtime's prompt, retrieval or generation settings without the other
  **fails CI**, with no model required.
- The eval-parity lane runs locally and reports a delta inside ±2%, measured across a gap that was
  real before this plan.
- `docs/NATIVE.md` states the parity contract and how to run the lane.

## Explicitly out of scope

- **Improving planning quality on either runtime.** This plan makes them agree. Making them both
  better is fine-tuning work (`docs/FINE_TUNING.md`).
- **Porting `history`-based re-planning to native.** A real gap, separately scoped; single-shot
  planning is what the corpus and the shipped path exercise.
- **Changing the prompt format.** Any change here must move both runtimes together, and the model
  is fine-tuned on the current shape — so this plan matches native to Python, never the reverse.

## Open questions

- **Does the fine-tuning data generator build prompts through `build_prompt`?** If so, the
  training distribution is pinned to the Python path, and R1 protects the model's inputs as well
  as the runtime's. Worth confirming in P — it would raise R1's priority.
- **Is `top_k=5` right for native's larger skills?** Answer it with a measurement, after parity —
  not while fixing the gap, or the two changes become inseparable.
