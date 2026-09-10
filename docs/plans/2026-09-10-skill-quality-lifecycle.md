# Skill quality lifecycle — Python proves it, Rust ships it, both must agree

**Status:** Active — not started · **Created:** 2026-09-10 · **Completed:** —
**Owner:** core · **Ref:** supersedes and absorbs the 2026-08-08 native/Python planning-parity
plan (retired 2026-09-10; its measurements are carried below, its history is in git);
builds on `scripts/parity_check.py` and `contracts/parity/`

> **Status note:** Replaces the prompt-parity plan, which asked *"why is native worse?"* — a
> question whose premise was measured false on 2026-09-09 (native and Python do not differ
> significantly; the reported symptom was the **executor** refusing chains, not the planner
> producing bad ones). That plan was **retired on 2026-09-10** once its Workstream P measurements
> were carried into *What the 2026-09-09 measurement established* below — which is now the record,
> and should not be re-derived. The retired file remains in git history. What was missing was never a diagnosis — it
> was a *process* that keeps the two runtimes in agreement continuously, instead of discovering
> a drift months later by driving the CLI by hand.
>
> **Revised 2026-09-10 after review.** The first draft covered only runtime agreement and had no
> gate establishing that **Python quality is satisfactory before porting begins** — it omitted
> fine-tuning from the lifecycle entirely and pushed quality work out of scope, which left the
> four layers able to certify that two runtimes agree on something not yet known to be good.
> The plan is now organized around the six lifecycle stages, with the four layers as the
> verification machinery inside stages 4–5. Five further defects the review found are fixed at
> the item each belongs to: V2 promoted from a decision to a measured experiment (S3g), L3's
> comparator defect and its plan-vs-command ambiguity (L3a), L4's unnamed acceptance metric
> (L4d), `supported` reachable without real execution (G1), and a gate with no enforceable
> evidence (G2).
>
> **Second review, same day — four acceptance-rule corrections, no structural change.** The
> metric denominator (L4d) assumed both metrics score the same population; they do not, in the
> existing scorer. Quality floors were not binding after training or porting (S4, L4d). The
> provenance tuple omitted shared inputs that change results — Python core, the verifier's
> implementation, effective inference settings, the policy version (G2). And invalidated evidence
> was assumed to leave a lower status intact, when the same change usually invalidates that too
> (G2).
>
> **The one open implementation decision is now settled** (2026-09-10): unattempted rows are
> **excluded** from `avg_knaif_score` rather than scored 0 — the rule `scoring.py` already applies
> to every non-plan outcome — and that is safe *only* because coverage is gated separately (L4e,
> G1). Settling it surfaced a blocker: the scoreboard cannot currently distinguish a capability
> refusal from a correct one, so **coverage is not computable from today's records** until the
> runner marks them apart. See L4d.

**Goal:** Make skill quality a gated, evidenced property end to end — Python acceptance before
porting, four verification layers across the port, and a release that cannot claim more than the
shipped binary has actually demonstrated.

---

## Why this exists

knaif's skill development runs in six stages, and quality is established in the first three
**before** the port begins:

1. **Implement in Python** — shared contracts, handlers, tests, fixtures, eval corpus.
2. **Improve in Python** — executing evaluation against explicit quality requirements.
3. **Fine-tune and validate** — shared training mix, held-out eval, cross-skill regression.
4. **Accept the Python baseline, then port** — freeze the evaluated inputs; L1/L2 conformance.
5. **Evaluate in Rust** — L3 behavioral parity and L4 real-execution quality against that baseline.
6. **Release** — complete, current evidence bound to the candidate artifact.

Python is the authoring, eval and training runtime. Rust is what ships — the installers, the
tarball, the AppImage. A divergence between them is a gap between *what we measure* and *what users
get*, always in the direction that flatters the numbers.

**Two independent things must be true**, and conflating them is the failure this plan exists to
prevent: **the skill must be good** (stages 1–3, measured in Python), and **the shipped runtime
must do the same thing** (stages 4–5, measured across both). Parity with a mediocre baseline is
worthless; a good Python baseline that the shipped binary does not reproduce is worse, because the
eval numbers make it look fine. The target is ≥99% runtime agreement **on a baseline that has
already been accepted on its own merits**. Neither number exists today for any skill.

**One consequence of the shared model, stated once because it governs stage 3:** every skill is
served by the same fine-tune, so a new skill's training data changes *every other skill's*
behavior. Stage 3 is therefore never a single-skill decision — it carries a cross-skill regression
obligation, and `docs/FINE_TUNING.md` is its canonical how-to.

### What the 2026-09-09 measurement established

**Carried here in full** — do not re-run it. The plan that produced these numbers was retired on
2026-09-10 once they were folded in, so this is now the record.

**How it misled on the way, because those failures are reusable.** Three of them, and none needed
a model to catch — only executing the read instead of trusting it:

- **A tool count was read off the registry without applying the prompt builder's filters** — "26
  tools vs 5" became **13 vs 5** once `internal: true` entries were excluded, and the right unit
  turned out to be tool *definitions*, not names: the static `TOOL SCOPE` header lists all 13 on
  both runtimes regardless of retrieval.
- **A config value was read from the wrong stanza** of a file holding a dozen — a claimed
  `max_tokens` 2048-vs-512 gap belonged to a superseded entry for a different GGUF. Both sides
  were always 512, so the experiment built on it would have varied nothing.
- **A divergence was filed as "ruled out" on the strength of a docstring** asserting equivalence
  rather than a test showing it (`normalize_path_separators`). That is the exact failure the plan
  existed to fix, reproduced inside the plan describing it.

And the largest one, found only by widening the sample: **a chain-only subset inverted the
conclusion.** Measured on 41 chain utterances, the registry axis looked perfectly flat (0/41) and
example selection looked helpful. Across all 847 the registry axis moves 19–39 utterances and
example selection is net *harmful*. **A subset chosen because it exhibits the symptom
over-represents whatever helps the symptom.**

**The prompts differ materially.** 5 vs 13 tool *definitions* (the static `TOOL SCOPE` header names
all 13 on both sides — count definitions, not visibility), 6 vs 29 example plans, native 1.77×
larger and **byte-identical across every utterance** while Python's changes with each one.

**Four prompt shapes, same GGUF, 847 utterances each, 3 388 inferences.** Cell A is byte-identical
to the real native prompt and cell D to the real Python prompt — verified by hash, so these are
those prompts, not approximations:

| cell | outcome acc | 1st tool (614 plan rows) | chain ≥2 |
|---|---|---|---|
| **A** full + static *(= native)* | 79.0% | 95.8% | 92.7% |
| **B** full + selected | 77.2% | 94.8% | 97.6% |
| **C** retrieved + static | **79.8%** | **96.1%** | 92.7% |
| **D** retrieved + selected *(= Python)* | 78.3% | 95.0% | 97.6% |

Aggregates this close hide offsetting changes, so the cells are compared **paired, per utterance**,
exact McNemar on outcome correctness:

| comparison | wins / losses | p | |
|---|---|---|---|
| examples axis, full registry (A vs B) | 16 / 1 | 0.0003 | *** static wins |
| examples axis, retrieved registry (C vs D) | 14 / 1 | 0.0010 | *** static wins |
| registry axis, static examples (A vs C) | 1 / 8 | 0.039 | * retrieval wins |
| registry axis, selected examples (B vs D) | 6 / 15 | 0.078 | ns, same direction |
| **native vs Python (A vs D)** | **11 / 5** | **0.21** | **ns — no difference** |

**And with the prompt held identical, the two runtimes' planners agree on 99.6%** (3/847). That
figure is the headroom: ~0.4% is the floor any model-driven layer can expect from two llama.cpp
bindings under greedy decode, which is why L3 is set at 99% and the deterministic layers at 100%.

⚠️ **`outcome acc` above is a crude local classifier** — the first step's tool class against
`expected_outcome` — **not** the eval suite's `outcome` metric. 79% is *not* comparable to the
ffmpeg snapshot's 0.902 and none of these figures is an acceptance bar; they are valid only for
comparing cells to each other. Arg-level correctness is not scored, so a cell could pick the right
tool with worse arguments and look identical. One model (`knaif-qwen3-4b-v1`), one skill (ffmpeg),
greedy decode.

**Raw data is committed**, not merely described: `evals/parity/2026-09-09_p2b-prefix-baseline/`
(847 native envelopes + join index + provenance) and `evals/parity/2026-09-09_p3-prompt-factorial/`
(all 3 388 inferences). Both carry a `meta.json` pinning git SHA, corpus/model/binary sha256 and
the inference backend.

### The failure this process prevents

`prompt.rs` recorded its divergences as safe because *"Phase 10 eval-parity measures end quality"*.
That check was C4, and it was never built. **A divergence accepted on the strength of a check that
does not exist is an unmeasured divergence** — and it survived from the port until someone drove
the CLI by hand a year later. Every layer below is chosen for what it costs to run, so that the
cheap ones can run on every PR and the expensive ones have a named trigger rather than good
intentions.

---

## Workstream S — Python acceptance (stages 1–3), the gate before any porting

**Nothing in L1–L4 establishes that a skill is any good.** They establish that two runtimes agree
and that the shipped one executes. A skill that routes badly can pass all four. This workstream is
the gate that has to close first, and its output is a **frozen, named baseline** that stages 4–6
are measured against.

Most of the machinery exists — `docs/EVAL_FRAMEWORK.md`'s eval ladder, `just eval-*`,
`docs/FINE_TUNING.md`, `docs/TRAINING_DATA_GENERATION.md`. What is missing is that it is nowhere
stated as a **precondition for porting**, with a written definition of "satisfactory".

- [ ] **S1 — Implement in Python against the shared contract.** `skill.yaml` / `tools.yaml` /
  `prompt.yaml` at the bundle top, handlers under `python/`, a smoke test that loads the skill and
  runs one dry-run plan, `eval/fixtures.py`, and `data/eval.jsonl`. Per `docs/TOOL_SCHEMA.md`.
- [x] **S2 — Define "satisfactory" for *this* skill, before measuring it.** *(2026-09-10: written for both active skills as `skills/<name>/acceptance.yaml`, with `just eval-accept` / `just eval-safety` to enforce them and a test asserting each skill's own snapshot clears its floors.)* A written threshold
  set in the bundle, not a vibe and not a number chosen after seeing the result:
  - **Aggregate floors** on an *executing* verifier — `outcome_accuracy` and `avg_knaif_score`,
    each named with its floor. `cheap` is an iteration instrument and may never be an acceptance
    bar (AGENTS.md).
  - **Required capability slices** — the tags this skill must handle, each with its own floor, so
    an aggregate cannot absorb a whole broken capability. ffmpeg's `chain3` stratum is the worked
    example: 41 utterances out of 847, invisible in any aggregate.
  - **Safety behavior** — `data/safety_test.jsonl` must be **100%**, not within a tolerance. A
    destructive request that plans instead of rejecting is not a score regression.
- [ ] **S3 — Improve until the thresholds are met**, on the eval ladder (routing with `cheap`
  while iterating; `eval-fixtures` then `eval-success` before any claim).
- [x] **S3g — Prompt/pipeline improvement experiments belong here, including V2.** *(Run
  2026-09-10: 12 cells, both skills, executing verifier on real artifacts, per required slice,
  paired McNemar. Verdict in V2 below — `select_examples` stays and `top_k` stays at 5.
  `evals/runs/2026-09-10_s3g-factorial_success/summary.md`.)* Any change to
  Python's own planning behavior is a stage-2/3 experiment and must clear this bar before it can
  become the reference the port targets:
  - graded with an **executing** verifier on **artifacts**, not plan-shape proxies;
  - reported **per required slice**, not only in aggregate;
  - checked against **every other active skill** (shared prompt path, shared model);
  - and **accepted as the new baseline in its own commit** before any porting work assumes it.
  This is why V2 (removing `select_examples`) is not settled by the 2026-09-09 factorial: that
  measurement scored first-tool choice and step count, explicitly **not** argument correctness or
  executed artifacts, and its only counter-evidence — the chain slice — was dismissed partly
  because *native* cannot execute chains. **A limitation of the port must never set Python's
  target behavior.** Re-run it as a proper experiment or leave `select_examples` alone.
  - **Two factors, one run (decided 2026-09-10): example selection × `top_k`.** Running `top_k`
    here rather than deferring it costs almost nothing — same harness, same model load, same GPU
    time — and a factorial separates the factors by construction, which is what the "don't change
    two things at once" rule actually asks for. Cover **both active skills**: `top_k=5` shows 5 of
    ffmpeg's 13 public tools and 5 of documents' 15, and only documents can say whether the ratio
    hurts at that size.
  - Grade on **artifacts** with an executing verifier, report **per required slice** (chains
    included, judged on Python's ability to execute them, never native's), and accept the winner
    **as a new baseline in its own commit** before any porting assumes it.
- [ ] **S4 — Fine-tune, and validate across all skills.** Author `data/train.jsonl`, build the
  union dataset, train, merge to GGUF, quantize — then evaluate **every active skill** against its
  committed snapshot, not just the new one. Promote by adding the model to `models.yaml` and
  `contracts/models/model-manifest.yaml` and pointing `recommended_model:` at it.
  **A cross-skill regression blocks promotion**, however good the new skill looks.
  - **The model that actually ships must re-pass every S2 threshold**, not merely avoid regressing
    against the previous snapshot. Those are different tests: a snapshot comparison is relative to
    wherever the last one landed, so a skill can pass "no regression" while sitting below a floor
    it was supposed to clear — and the promoted quant is not the checkpoint that was evaluated
    during training. Re-run S2's aggregate floors, every required capability slice, and safety at
    100% **against the exact GGUF being promoted**, before S5 freezes anything.
- [ ] **S5 — Freeze and name the accepted baseline.** Re-lock `data/eval_snapshot.json` with an
  executing verifier, in its own commit, and record the tuple the port will be held to:
  **skill bundle + contracts + corpus + verifier + model**. Everything downstream compares to
  *this*; an unfrozen baseline makes L4's tolerance meaningless because both sides move.
- [ ] **S6 — Only now does porting begin.** Stage 4 starts here.

---

## The two rules

**1. Python is the reference; Rust moves.** The model is fine-tuned on Python-shaped prompts
(`build_dataset.py:132` builds every training row through `retrieve_tools` → `build_prompt`), and
Python is where skills are authored and graded. So a divergence is a Rust bug **by default**.

*The one exception, and it must be argued in writing with a measurement:* where Rust's behavior is
**measurably better**, Python moves instead — but only after that claim clears S3g (executing
verifier, artifacts, per-slice, cross-skill, accepted in its own commit). Example selection is the
open candidate, **not a settled case**: the 2026-09-09 factorial favours Rust's static block, but
graded neither arguments nor artifacts, so it licenses an experiment and nothing more. Without
this escape hatch the rule would force known-worse behavior onto both runtimes in the name of
consistency; without the S3g bar it would let a proxy metric rewrite the reference runtime.

**2. Compare the same stage on both sides.** A harness that calls `run --dry-run` on one side and a
raw orchestrator on the other measures its own asymmetry. This is not theoretical: an ad-hoc
comparison on 2026-09-09 reported 18.2% first-tool disagreement, of which **150 of 154 cases were
Rust's clarify gate running against a Python path that had none**. The real number was 3/847. Any
new comparison states which entry point it calls on each side, in the harness and in its output.

---

## The four layers

| layer | proves | GGUF? | runs | threshold |
|---|---|---|---|---|
| **L1 contract** | same YAML → same registry, prompt, generation settings | no | every PR | **100%** |
| **L2 deterministic** | same plan JSON → same expansion, validation verdict, rendered argv | no | every PR | **100%** |
| **L3 behavioral** | same utterance + model → same outcome and command | yes | port milestone + release | **≥99%** per row |
| **L4 shipped path** | the **native binary**, executing for real, produces artifacts that pass the skill's executing verifiers | yes + exec | release | **within 2 pts of the snapshot** |

**Thresholds are per layer, deliberately.** L1/L2 are deterministic: a mismatch is a bug, never
noise, so anything below 100% is a failure. L3 is model-driven and floors at ~99.6% (above), so 99%
leaves room for genuine FP ties without hiding a port bug. A single blended "99%" would let a
deterministic port bug hide inside model noise — which is precisely how the prompt divergence
survived.

**L1–L3 are proxies; L4 is the only layer that measures what a user gets.** Each earlier layer
holds one more stage constant so a failure can be *located* — that is their whole value, and it is
why they run more often. But none of them executes anything: L3 compares rendered commands, not
the files those commands produce. **A skill can pass L1, L2 and L3 at 100% and still be broken for
the end user** — a correct command that the shipped binary never reaches because preflight rejects
a dependency, or the sandbox refuses the path, or the confirmation gate never returns, or the
subprocess fails. L4 is what makes the release claim true; the rest make it debuggable.

---

## Convergence directions — **ffmpeg-specific, one-off**

**This section is not part of the durable process.** It is the backlog of divergences that already
exist between the two runtimes for the skill that was ported before any of this was written. A new
skill following stages 1–6 never accumulates these, because L1 gates them at the port. Keep the two
apart: everything above applies to every skill; V1–V5 apply to ffmpeg and then retire.

Rule 1 says Rust moves. The 2026-09-09 data says that is right for two of the three known
divergences and, on the evidence available, *possibly* wrong for the third — which is why V2 is now
an experiment (S3g), not a decision.

| axis | today | direction | evidence |
|---|---|---|---|
| tool retrieval | Python retrieves 5, Rust sends 13 | **Rust adopts Python** | retrieval wins 8/1, p = 0.039 |
| example selection | Python selects 6, Rust sends 29 static | **undecided — run S3g first** | static wins 16/1, p ≤ 0.001, but on a proxy metric that grades neither arguments nor artifacts |
| path normalization | Rust rewrites every backslash, Python only path-shaped tokens | **Rust adopts Python** | no quality data; Python's rule is narrower and is what the model was trained on |

- [x] **V1 — DONE 2026-09-10: `retrieve_tools` is wired into the native plan path, rank intact.**
  All three defects the plan predicted were real. The function was ported and tested in
  `knaif-core` but **never called**; it returned a `BTreeMap`, discarding the ranking at the
  return; and `prompt.rs` re-sorted by `def.order`, which would have discarded it again.
  - `RetrievedTools<'a> = Vec<(String, &'a ToolDef)>` replaces the map, and
    `build_prompt_ordered` renders a given sequence as given. The whole-registry
    `build_prompt` still sorts by `order`, which is what the reference produces for a full
    registry — so L1a's expectations were unaffected.
  - The **scoring was already faithful**, tie-break included; only the return type threw the
    answer away. That is why L1b went green on a type change alone.
  - Effect on the shipped prompt, measured with `$KNAIF_DUMP_PROMPT`: native listed all 13
    ffmpeg tools in `tools.yaml` order; it now lists **5 in relevance order**, identical to
    Python's selection for the same utterance (`compress_video, trim_video, strip_audio,
    rotate_video, reverse_video` for *"make clip.mp4 smaller"*). This was the single largest
    prompt divergence between the runtimes.
  - `DEFAULT_TOP_K` is now a named constant on both sides, and L1c pins them equal.
- [x] **V2 — SETTLED 2026-09-10 by the S3g factorial: `select_examples` stays, and Rust gains it.**
  The 2026-09-09 factorial favoured deleting it (static wins 16/1 and 14/1, p ≤ 0.001) but graded
  first-tool choice and step count — not arguments, not artifacts — and discounted the one slice
  that disagreed because *native* cannot execute chains. Re-run properly (executing verifier, real
  artifacts, per required slice, both skills, 847 + 164 utterances, paired McNemar), the result
  **reverses**:
  - Static **does** win the ffmpeg aggregate, and significantly at `top_k=8`: outcome accuracy
    0.916 vs the shipped 0.902, **30 wins to 14, p = 0.0226**. Taken alone that is the earlier
    finding confirmed on better evidence.
  - **The slices say otherwise.** The same cell drops `concat_video` 0.800 → **0.733, below its
    0.750 floor**, and `chain2` 0.889 → 0.778 (3 failures against a budget of 2). `multilingual`
    slips 1.000 → 0.984, `trim` and `adjust_speed` each lose ground. The aggregate gain is
    partly *paid for* by specific capabilities — exactly what required slices exist to catch,
    and invisible in any average.
  - **On documents it does nothing at all**: every cell sits at 0.976, the largest difference
    anywhere is 2 discordant rows (p = 0.5). The cross-skill check the earlier measurement
    skipped finds no effect to port.
  - **So the bar is not cleared, and Rule 1's exception is not triggered.** A change that pushes
    a required capability under its floor is not "measurably better"; it is a trade, and S2 does
    not permit trading a capability for an average. Adjusting the floor to fit would be choosing
    the number after seeing the result — the thing S2 exists to forbid.
  - **Direction reversed:** Python keeps `select_examples`, and the convergence work is now
    **Rust gaining it**, not Python dropping it. Evidence:
    `evals/runs/2026-09-10_s3g-factorial_success/summary.md`.
  - **PORTED 2026-09-10.** `select_examples` + `render_examples_block` are in
    `knaif-core::prompt`, and `PlanSession::plan` calls them — mirroring
    `CommandAgent.build_prompt`, fallback included (an empty selection leaves the unfiltered
    block standing). `PromptOverrides` now keeps the examples structured instead of rendering
    once at load: rendering early and discarding the structure is precisely what made native
    send all 28 of ffmpeg's examples on every utterance.
  - **Result: the whole system prompt is now byte-identical across the runtimes.** Verified by
    diffing Python's `build_prompt` against `$KNAIF_DUMP_PROMPT` for five utterances across both
    skills — all five identical, ffmpeg and documents. V1 closed the tool-listing half; this
    closes the examples half, and nothing measurable is left between them.
  - **Two semantics a re-derivation gets wrong, both pinned by the contract:** the domain cap is
    a *cap, not a relevance threshold* (three examples are kept even when every one scores zero
    — filtering on overlap > 0 would send the model a clarify/reject pair and nothing else), and
    the ranking sort must be *stable*, because Python's `sorted(reverse=True)` leaves equal
    scores in corpus order. Both were mutation-tested: each mutation fails the contract and the
    shipped golden.
- [x] **V1's `top_k` question — SETTLED 2026-09-10: leave it at 5.** The same factorial varied
  `top_k` ∈ {5, 8, 99} (99 = every public tool, i.e. ranking without filtering). 8 edges 5 on
  ffmpeg in both example modes (0.907 vs 0.902 selected; 0.916 vs 0.914 static) but never
  significantly (p = 0.19), and 99 is worse than both — so filtering earns its place and the
  ordering alone does not. On documents, the skill the question was actually asked about (15
  public tools, 5 shown), **there is no effect on outcome accuracy at all** and `top_k=99`
  *lowers* artifact quality (1.000 → 0.987). No evidence to move it.
- [x] **V3 — Converge `normalize_path_separators` on Python's `_PATH_TOKEN_RE`** *(2026-09-10.
  Ported as a character-class test rather than a regex dependency — it is one predicate and the
  CLI has no other use for `regex`. The existing Rust tests were extended, not deleted, with the
  two shapes the blanket replace got wrong. **L1a went green on this change** and is un-skipped.)* (`prompt.py:27`),
  replacing the blanket `replace('\\', "/")` at `main.rs:1185`. Existing Rust tests at
  `main.rs:1853-1869` already cover the cases and must be updated, not deleted.
- [x] **V4 — DONE 2026-09-10: `contracts/runtime/generation.yaml` is canonical, with a guard on
  each consumer.** `max_tokens` lived in four places, not three (`llama.rs:241` *and*
  `lib.rs`'s `$KNAIF_MAX_TOKENS` fallback, plus both YAMLs); native now has one
  `knaif_llm::MAX_TOKENS` / `N_CTX` pair and the contract holds all of them together. The
  settings did all agree, so this is hygiene as expected — no defect found.
  - **Enforced by comparison, not by `just sync-runtime`** — a deliberate deviation from this
    plan's wording. `core_tools.yaml` is copied because its consumer is a loader; these
    consumers are hand-annotated config files and Rust constants, and a generator writing into
    them would flatten commentary worth more than the duplication costs. Two guards read the
    contract instead: `python/core/tests/test_generation_settings.py` (both YAMLs + the Python
    orchestrator's defaults) and `native/crates/knaif-llm/tests/generation.rs` (the native
    constants). Verified non-vacuous by moving `max_tokens` in the contract: 1 Rust and 3 Python
    failures, each naming the file to fix.
  - **Scope is the settings that can change what the model emits** — `max_tokens`, `n_ctx`,
    `temperature`, `json_mode`, `thinking_enabled`. `n_gpu_layers` (native 999, the YAMLs 99 —
    both mean "offload everything"), `n_threads` and `verbose` are excluded on purpose: pinning
    them would fail the guard on differences that cannot change a plan.
  - Experiment stanzas in `eval_backends.yaml` are **not** pinned, and a test asserts they
    aren't: freezing them would freeze the record of the experiments themselves.
- [x] **V5 — DONE 2026-09-10: both stale notes rewritten.** The module docstring recorded two
  "intentional, prompt-only divergences" — an alphabetical tool listing and compact example JSON
  — and both were neither intentional nor prompt-only by the time it was read: V1 fixed the
  first, and `to_py_json` had already fixed the second. It now records that there are no
  divergences left and why that matters. The `ToolDef::order` comment claimed the fine-tune was
  trained on `tools.yaml` order; **checked against the builder rather than assumed** —
  `python/training/build_dataset.py:136` builds every training prompt as
  `retrieve_tools(utt)` → `agent.build_prompt(registry_override=…)`, so training prompts carry
  tools in *relevance* order **and a selected examples block**. Native was out of distribution
  on both halves of the prompt until V1 and V2; the comment now says what `order` is actually
  for.

---

## Workstream L1 — Contract conformance (no model, every PR)

The durable half, and the only layer CI can run on every change.

- [x] **L1a — Prompt-parity contract.** *(Authored 2026-09-10: `contracts/parity/prompt_cases.json`,
  8 cases, expected values from Python. Python side green
  (`python/core/tests/test_prompt_parity.py`); Rust side **red-but-skipped** in
  `apps/cli/src/main.rs::prompt_parity_cases`, un-skip in V3's PR. Verified genuinely red:
  the tool listings already agree byte for byte, and it fails on exactly the two
  normalization cases — `quoted_windows_path` and `bare_backslash_is_not_a_path`.)* Fixed utterances × fixed registries → both runtimes
  produce the same prompt string, byte for byte, with **no allow-list**. Extend `contracts/parity/`
  in the shape `planner_cases.json` uses; consumed by a Python test and a Rust test.
  - **Pin every input, not just utterance and registry.** `build_prompt` also takes
    `system_header` and `examples_block` from the skill's `prompt.yaml`. A case that fixes only
    `(utterance, registry)` silently depends on whatever `prompt.yaml` says that week, so an edit
    there moves the contract instead of failing it. Inline the overrides, or point cases at
    versioned fixtures under `contracts/parity/`.
  - **Sequencing:** this cannot go green until V1–V3 land. Author the cases first and land them
    **red-but-skipped** with expected values written from the reference side — un-skip in V's PR.
    That is the only moment the contract is proven to detect the bug it exists for.
  - **Scope, stated in the contract file:** this compares the logical `(system, user)` messages,
    not final token IDs. The two runtimes use different llama.cpp bindings and each applies the
    GGUF chat template independently. Identical messages are necessary, and are what a no-GGUF
    contract can assert; they are not proof of an identical token sequence.
  - **Cases must cover path normalization** — a quoted Windows path (`"C:\Users\me\clip.mp4"`), a
    bare `.\clip.mov`, and a backslash that is *not* a path.
  - **Keep expected prompts JSON-escaped inside the cases file**, not in sibling `.txt` goldens.
    `* text=auto` has produced this exact bug three times in this repo (see the
    `installers/licenses/**`, `site/data/*.json` and `*.ipynb` entries in `.gitattributes`). A
    contract that passes in CI and fails on the maintainer's box is one people learn to skip.
- [x] **L1b — Retrieval-parity contract.** *(Authored 2026-09-10:
  `contracts/parity/retrieval_cases.json`, 7 cases including the pure tie-break case, CJK and
  diacritics. Python green; Rust red-but-skipped in `native/crates/knaif-core/tests/parity.rs`.
  Two findings, both V1's: `knaif_core::retrieve_tools` returns a `BTreeMap`, which cannot
  express a ranking at all, and **nothing calls it** — the port exists but is unwired.)* Same utterance + registry → same selected tool set **and
  order**. Separable from L1a and worth its own cases: retrieval is scoring logic with tie-breaks,
  and it is where CJK tokenization and diacritic handling live. **Order is the load-bearing half**
  — include cases with tied scores, where the `(score, name)`-descending tie-break is the only
  thing under test.
- [x] **L1c — Settings-parity contract.** *(2026-09-10:
  `python/core/tests/test_settings_parity.py`, green — the three copies agree today at
  512 / 8192 / greedy / thinking-suppressed. Written as a three-way comparison on purpose;
  rewrite it against the canonical file when V4 lands.)* Assert both runtimes' generation defaults agree —
  `max_tokens`, `n_ctx`, sampling, thinking suppression. After V4 there is one canonical copy, so
  this becomes *"both runtimes read the canonical file"* — a weaker assertion over a stronger
  invariant. **Write it that way deliberately**; porting a three-way comparison onto a single
  source yields a test that can only ever pass. Keep one case reading each runtime's *effective*
  value at the point of use, so a hard-coded fallback shadowing the contract file still fails.
- [x] **L1e — Example-selection contract.** *(2026-09-10, authored for V2 and verified red first:
  `contracts/parity/example_cases.json` — 11 synthetic cases plus 5 goldens over the real ffmpeg
  and documents bundles, consumed by `python/core/tests/test_example_selection_parity.py` and
  `example_selection_parity_cases` in `native/crates/knaif-core/tests/parity.rs`. The red was a
  **compile error**, not a failed assertion, because native had no example selection at all —
  recorded here so nobody later reads a green run as evidence the port was already close.)*
  Not in the original plan; the prompt divergence turned out to have two halves and only the tool
  listing had a contract.
  - The unit contract is not enough on its own, and V1 is why: `retrieve_tools` was a faithful,
    fully tested port that **nothing called**. So `apps/cli/tests/prompt_examples.rs` asserts the
    shipped binary's prompt through `$KNAIF_DUMP_PROMPT`, and both halves were mutation-tested —
    reverting the CLI wiring fails it, and each of the two easy-to-get-wrong semantics fails the
    contract *and* the golden.

- [x] **L1d — Gate in CI.** *(2026-09-10: no workflow edit needed — `contracts/**` already
  routes to both the `python` and `native` jobs, and `test_ci_workflow.py` now asserts that
  for each of the three contract files rather than leaving it to be believed. Coverage is
  recorded in the contract files themselves: Ubuntu in CI, Windows locally (this run), macOS
  unexercised. The `platforms.yaml` guard itself stays in G2.)* Belongs in the existing `python` and `native` jobs — both already run
  on changes to `skills/` and `contracts/`. **Both are `ubuntu-latest` only**, so CI green is not
  evidence the contract holds where the work is done: run L1 locally on Windows as part of V
  before calling this done, and state the real coverage in the contract file — **Ubuntu in CI,
  Windows locally, macOS unexercised**. Claiming three-platform coverage would be the same
  unmeasured assertion this plan exists to end.
  - **macOS: this plan closes *without* macOS coverage, and ships the guard that forces the
    macOS work to add it (decided 2026-09-10).** Two separable things, and only the first is a
    deliverable here:
    - **In scope — the guard.** A test that fails if any platform is `supported` in
      `contracts/release/platforms.yaml` while the layers' recorded coverage excludes it. See G2.
    - **Out of scope — macOS coverage itself.** macOS is `status: planned`, no workflow builds
      it, and the support work lands *after* this plan. Contract coverage for a target that does
      not exist would test nothing, and waiting for it would block this plan on unrelated work.
    **This is a tripwire for a known-incoming change, not a theoretical safeguard** — macOS
    support is already in flight (`feat/macos-support`, `docs/macos-support-plan`). The guard is
    expected to **fail that branch on day one**, which is the point: it converts "remember to
    extend parity coverage" from a note someone must find into a build error they cannot miss.

## Workstream L2 — Deterministic pipeline (no model, every PR)

Same plan JSON in → same expansion, optimizer, validation verdict, rendered command out. No model
means no noise: this layer is either 100% or broken.

- [x] **L2a — Extend `contracts/parity/planner_cases.json`** *(2026-09-10. The clarify gate got
  its own file, `contracts/parity/clarify_gate_cases.json` (10 cases), because the stage is
  `link_chain_intermediates → hallucinated-filename guard` and belongs together. **It found a live
  divergence on its first run**: native treated a tool declaring `output` only in `arg_schemas` as
  output-capable, so it bound a chain intermediate to an arg its own `validate_plan` then rejected
  as unsupported — two Rust ports contradicting each other. Aligned to Python; latent in the
  shipped skills (neither declares a tool that shape) but real. Both sides green, 10/10.)* to cover expansion and rendering, not
  just planning: chain-intermediate linking, variable resolution, `apply_defaults`, and the
  hallucinated-filename gate. **The clarify gate belongs here** — it is deterministic, it fires on
  ~17% of the ffmpeg corpus in the native runtime, and nothing currently proves Python's
  equivalent fires on the same rows.
- [x] **L2b — Assert validation *verdicts* agree, not just accepted plans.** *(2026-09-10:
  `planner_cases.json` 14 → 22 cases. **Found the dangerous direction on the first run**: Python
  rejects a model-proposed `internal` tool, native accepted it — `planner.rs` had no internal
  check and no `allow_internal` at all. Internal tools are the ones that run a command with the
  args they are handed, so accepting one skips intent expansion entirely; the prompt never lists
  them, but that is obscurity, not validation. Fixed with `validate_plan_with` /
  `validate_step_with`, mirroring Python's two-phase design. One case is the exact validator rule
  that made the L2a fix correct. Contracts assert the error *class*, never its prose.)* A plan Python rejects
  and Rust accepts is the dangerous direction (Rust ships). Include known-bad plans with the
  expected error class on each side.
- [x] **L2c — Run L2 in the same CI jobs as L1**, under the same 100% threshold. *(2026-09-10:
  automatic — `contracts/**` already routes to both jobs, now asserted per contract file in
  `test_ci_workflow.py`.)*

## Workstream L3 — Behavioral parity (needs a GGUF; local + release)

**Largely already built — `scripts/parity_check.py` + `just parity <skill>`.** It pins both
runtimes to the identical GGUF, guards against comparing two configs of the same weights, compares
outcome type first and rendered argv second, and shlex-normalizes so quoting differences do not
register. What it lacks is a threshold, a record, and a trigger.

- [x] **L3a — DONE 2026-09-10, with one deliberate deviation and one finding that changes how
  this layer must be read.**
  - **The comparator was unsound and is fixed.** `plan_equiv_modulo_defaults` never consulted a
    declared default despite its name. It now fills both sides from the registry's declared
    defaults and compares full arg maps; an extra key survives only when its value **is** the
    declared default. Consequence worth knowing: **ffmpeg declares defaults on exactly one tool**
    (`concat_video.output`). The old docstring's examples — `preview` / `quality` /
    `include_audio`, "filled by `apply_defaults`" — describe defaults that **do not exist**, so
    every one of them is now a divergence. Plan mode also refuses to compare without the contract
    rather than quietly answering.
  - **The threshold default is 1.0, not 0.99 — deliberately, against this plan's letter.** The
    premise above ("it reports; it does not gate") was already out of date: any divergent row
    returned exit 1. Adopting 0.99 would have *loosened* a working gate. `--threshold` makes a
    lower bar available for the run that justified it, as an explicit per-run choice.
  - **Non-equivalent rows are enumerated**, not just counted, and `not-comparable` rows are
    excluded from the denominator *and* reported separately.
  - **Fixed a trap by falling into it:** a relative `--model-path` passed the existence check at
    the repo root, then resolved to nothing under the fixture cwd — and native answers a missing
    model by printing guidance and exiting 0. The first run reported **0/5, 0% parity**, which
    looked like catastrophic drift and was nothing at all. Paths are absolutized before any
    subprocess sees them.
  - **⚠️ THE NUMBER IS NOT A NATIVE SCORE.** ffmpeg command mode scores **0.8194** (236/288).
    The metric is *symmetric disagreement*: it says nothing about which side is right, and on
    several rows **native is the better answer** — it honors `crf 18` where Python falls back to
    `quality: "visually_good"`. Anyone quoting this as "native is 82% correct" has misread it.
  - **⚠️ A ≥99% bar may be unreachable by construction, and that is a design problem with this
    layer rather than a number to tune.** The two runtimes link **different llama.cpp builds**
    (`llama-cpp-2` vs `llama-cpp-python`). With the prompt verified byte-identical and both sides
    self-deterministic, they still choose different tokens on near-ties. Measured, not assumed:
    all 52 divergent rows were re-run with native forced onto CPU and **1 of 52** flipped, so
    native's own backend is ruled out — the gap is between the libraries, and no flag equalises
    it. **Rule 2 needs a sibling: compare the same stage *and* the same inference stack.** Owner
    decision needed on whether L3 keeps a rate bar at all, or gates only on the renderer class
    below.
  - **THE MODE DISTINCTION IS THE WHOLE INSTRUMENT, and running both modes is what makes an L3
    result actionable.** Cross-tabbing the two runs over the same corpus splits the divergences
    into classes that call for completely different work
    (`evals/parity/2026-09-10_l3-ffmpeg-command/divergence_classes.json`):
    - **10 renderer defects** — plans agree, rendered commands differ. Real port bugs, listed
      under *Native defects found by L3* below.
    - **42 generation differences** — plans differ. The library gap above; not port defects.
    - **19 plan-only** — plans differ but render identically. Not defects.
  - **State the mode. L3 compares *rendered commands* (`run --dry-run`), not plans.** The
    distinction is not cosmetic: `plan_equiv_modulo_defaults` is reached **only in plan mode**
    (`parity_check.py:696`), and command mode compares normalized argv, which catches value
    differences the plan comparator forgives.
  - **`plan_equiv_modulo_defaults` is unsound as an acceptance relation and must not be adopted
    as-is** (verified 2026-09-10). Despite its name it **never consults a declared default**: it
    accepts any nesting of arg-key sets where the shared keys agree and the extras are not paths.
    So native emitting `quality: "low"` while Python omits `quality` scores *"equivalent modulo
    default args"* — even though the two render **different commands**. An omitted argument and an
    explicitly different setting are not the same thing.
  - **Fix: contract-backed default normalization.** Before comparing, fill each side's optional
    args from the **registry's declared defaults**, then compare full arg maps. An extra key is
    benign only when its value **equals the declared default**; anything else is a divergence.
    That makes the relation sound in plan mode too, and removes the trap where a stricter future
    L3 silently loosens by switching mode.
- [x] **L3b — DONE 2026-09-10.** `--label` writes a run directory with `report.json` +
  `meta.json`; both runs are indexed in `evals/INDEX.md`. The backend is read from
  `$KNAIF_PARITY_BACKEND` and left explicitly `null` with a warning when unset — an unanswered
  question, not a guess — and `--native-ngl` records the layer count too, because the backend
  turned out to matter enough to pin rather than merely note. A dirty working tree warns that the
  git SHA does not describe what ran. *(Original text: save every run under `evals/parity/` with
  a `meta.json` pinning git SHA, corpus sha256, model sha256, **and the inference backend*** — the last because greedy argmax over
  different FP accumulation can flip a near-tie, so a CPU→CUDA change between two runs is
  indistinguishable from the change being measured. `2026-09-09_p2b-prefix-baseline/meta.json` is
  the template. Add a row to `evals/INDEX.md`.
- [x] **L3c — RESOLVED 2026-09-10 by Workstream E, the first of the two options.** The executor
  landed, so chains are comparable and no exclusion is needed; the "known hole" branch is moot.
  - **The comparator needed a change with it, and it tightens rather than loosens.**
    `parity_check.py` carried a lenient `chain-native-single-step` bucket that prefix-matched the
    first command, because step 1 was all native could produce. Left in place it would now pass
    **every** chain row on its first command alone — hiding precisely the step-2..n divergence the
    executor newly makes possible. It is deleted; chains fall through to the same comparison as
    every other row. This is a down payment on L3a, not a substitute for it.
- [x] **L3d — DONE 2026-09-10.** The header prints the exact command run on each side plus the
  **stage** each one represents, and all three go into `meta.json` — a record that does not say
  which stage each side ran cannot be checked against rule 2 afterwards.

## Native defects found by L3 — 2026-09-10

**This is the layer earning its place.** Ten rows agree on the plan and disagree on the rendered
command, so the model is not involved and every one is a port defect. Evidence:
`evals/parity/2026-09-10_l3-ffmpeg-command/divergence_classes.json`. **None is fixed here** —
they are skill-renderer changes needing their own corpus verification, and Phase 5 is the layer
that finds them, not the one that repairs them.

- [ ] **N1 — Native does not expand globs** (`ffmpeg_029`, `076`, `139`, `214`, `229` — 5 rows,
  the largest class). Both sides plan `inputs: ["*.mp4"]`. Python expands it against the sandbox
  and emits one command per file; native passes the literal token to ffmpeg, **which does not
  glob**, and renders the output as `*_converted.mp4`. "Convert all mp4 files in this folder"
  does nothing useful on the shipped runtime. User-facing.
- [ ] **N2 — Native does not resolve bare stems, so it never clarifies** (`ffmpeg_288`, `289`).
  Plans carry `input: "silent_clip"` / `"mov"`, with no extension. Python resolves stems against
  the sandbox and asks *"Which silent_clip did you mean?"*; native renders `-i silent_clip`
  verbatim, producing a command that fails at runtime. Native is **less safe** here: it acts on
  an ambiguous reference instead of asking.
- [ ] **N3 — `target_size_mb` does not drive a downscale natively** (`ffmpeg_020`). For
  "under 1 MB" Python adds `-vf scale=854:480`; native re-encodes at source resolution and will
  miss the target the user named.
- [ ] **N4 — Aspect-crop renders a filter ffmpeg REJECTS — on BOTH runtimes** (`ffmpeg_293`, and
  the same construction is behind the `294`/`296`/`298` cluster). Native emits
  `crop=min(iw//,ih*1/1):min(ih//,iw*1/1)`, Python `crop=min(iw/,ih*1/1):min(ih/,iw*1/1)`. Both
  have a missing operand, and both leave the comma inside `min(…)` unescaped inside a
  filtergraph. **Verified by running ffmpeg**: `No such filter: 'ih*1/1):min(ih//'`, output file
  never created. So "crop clip.mp4 to a square" is broken for users on **both** runtimes — a
  cross-runtime product bug that a parity check comparing only "do they agree" would have missed
  entirely, since they nearly agree on being wrong.
- [ ] **N5 — Not a native defect: Python's `run --dry-run` renders only the first step of some
  chains** (`ffmpeg_118`). Native renders both steps of compress → prepare_for_platform; Python
  renders one. This surfaced only because Workstream E made native render whole chains. Recorded
  so it is not mistaken for a port gap and "fixed" in the wrong runtime.

## Workstream L4 — The shipped path (needs a GGUF + real execution; release)

**This is the acceptance layer, and it grades the binary the user installs, doing the thing the
user asked, on files that really appear on disk.** Nothing else in this plan proves the product
works.

**What `plan --batch` skips, and why it cannot be the acceptance instrument.** It stops at a
validated plan (`main.rs:580`). The shipped `run` additionally does **dependency preflight**
(`detect_skill_deps`, `main.rs:658` — refuses to spend inference when a required external binary is
missing), **sandbox resolution and boundary enforcement** (`main.rs:673`), the **confirmation
gate**, command rendering, the **subprocess itself**, and output verification. Every one of those
is a place a correct plan still fails a user, and every one is invisible to a planner-only lane.

- [x] **L4a — BUILT 2026-09-10: `knaif.evalsuite native` (`just eval-native`).** Drives
  `knaif run <skill> --yes` per utterance, each in its own directory holding copies of only the
  fixtures that utterance names, and grades the files that appear with the skill's executing
  verifier. Smoke-verified end to end (3/3, real files on disk). A full corpus run is the
  remaining step and needs a rebuilt binary — see below.
  - **It required one native addition.** `run` had no way to report the plan it executed, so
    tool metrics would have scored `predicted_tool = None` on **every** row — a fabricated 0%
    tool accuracy, not a visible failure. `$KNAIF_DUMP_PLAN` mirrors `$KNAIF_DUMP_PROMPT` and
    emits the post-gate envelope from the **same invocation that executed it**, so the metrics
    describe the plan that actually produced the artifact rather than a second inference that
    might disagree.
  - **Refuses to run against an empty fixture directory**, naming `just eval-fixtures <skill>`.
    A missing fixture scores a correct plan ~0, so the alternative is a run that reports a
    catastrophe which did not happen.
  - **One honest limit on "the shipped path":** the lane passes `--yes`, so the confirmation gate
    executes but is auto-answered; the interactive branch is not what this measures. Python's
    executing verifiers make the same choice, so the two stay comparable. A gap in coverage of
    the claim, not a difference between the runtimes.
  - *(Original text: drive `knaif run <skill>` per utterance — **not `--dry-run`** — against a
    fixture sandbox, then grade the **produced files***
  with the skill's existing executing verifier (`success`, or `output_diff`), the same one that
  locks the Python snapshot. `scripts/parity_check.py` already drives `run --dry-run` per utterance
  and can be extended rather than replaced; a native batch-execute (`run --batch`) would be faster
  but is not required to start, and per-utterance is affordable (847 × ~3 s ≈ 42 min on the CUDA
  build).
  - **Regenerate fixtures first** (`just eval-fixtures <skill>`). AGENTS.md's ladder is explicit
    that missing fixtures score correct plans ~0 — an L4 run against an empty sandbox reports a
    catastrophe that isn't real, which is worse than not running it.
  - **Real execution writes real files.** Run it in the fixture sandbox, and treat the produced
    artifacts as the graded output, not a log line claiming success.
- [ ] **L4b — Python-side execution is a *diagnostic*, never the gate.** Executing a native plan
  through Python's pipeline is useful for **locating** a failure (planner vs executor), and that is
  the only thing it may be used for. It must not be reported as the acceptance number, and any
  output it produces carries the label *"native planner, Python execution — not the shipped
  path"*. The original design made this the acceptance instrument; that was wrong, because it
  grades a pipeline no user runs.
- [x] **L4c — DONE 2026-09-10.** `lanes:` is a separate top-level section in
  `eval_backends.yaml`, and a lane found under `backends:` is a **hard error** that names why
  rather than a fallback — the failure it would otherwise cause (constructed as a
  token-generation backend) is confusing enough to be worth catching precisely. Tested.
  *(Original text: if a lane is registered in the eval config, do not put it under `backends:`.*
  `_make_agent` hands any entry there straight to `InferenceOrchestrator(backend=…)`
  (`evalsuite/cli.py:130`), so a `rust-cli` key is not inert — it is a token-generation backend
  that will be constructed and fail, or half-work. Use a **separate top-level section** (e.g.
  `lanes:`), so the config shape says what the thing is.
- [ ] **L4d — Specify the acceptance rule; "within 2 points of the snapshot" names no metric.**
  `eval_snapshot.json` carries `outcome_accuracy` (ffmpeg: 0.902), `avg_knaif_score` (0.974),
  `avg_baseline_score`, `intent_metrics` and `by_tag` — "2 points" of which was never said. Define:
  - **Metrics:** `outcome_accuracy` **and** `avg_knaif_score`, each gated separately. Both, because
    routing correctly and producing a good artifact are different failures.
  - **Tolerance is a lower bound, not a band, and the S2 floor stays binding underneath it:**

        native ≥ max(S2 floor, accepted Python score − 0.02)

    on each metric. **Improvement always passes** — a band would reject a native runtime that got
    better, which is absurd. The `max()` matters because a bare relative tolerance lets native
    slip below the quality bar the skill was accepted on: if Python drifts to just above its floor
    and native lands 2 points under that, the product ships below a threshold someone wrote down
    as the minimum. The floor is an absolute; the tolerance is a parity allowance.
  - **Denominators are not the same for the two metrics, and that must be respected rather than
    legislated away.** In today's scorer `outcome_accuracy` divides by **all** rows
    (`scoring.py:185`) while `avg_knaif_score` divides only by rows that carry a verifier score
    (`scoring.py:187` — clarify/reject rows have `knaif_score = None`). So ffmpeg's committed
    0.902 and 0.974 are computed over **different populations**. Scoring native "over all rows"
    against those numbers would compare two different quantities and silently punish the runtime
    that refuses correctly.
  - **The shared scoring contract — SETTLED 2026-09-10.** One definition, used by both runtimes,
    versioned by the acceptance-policy version in G2 so a later change cannot leave old records
    looking compliant. It codifies what `scoring.py` already does and adds the one distinction it
    cannot currently make:

    | row's runtime outcome | `outcome_accuracy` | `avg_knaif_score` |
    |---|---|---|
    | produced a plan, artifact graded | correct iff expected `plan` | **the graded score** |
    | produced a plan, grading raised | correct iff expected `plan` | **0.0** |
    | correctly refused (expected `clarify`/`reject`) | **correct** | **excluded** |
    | wrongly refused, or capability not implemented | **failure** | **excluded** |

    **Unattempted rows are excluded from `avg_knaif_score`, not scored 0.** Three reasons, in
    order of weight:
    1. **It is the rule already in force, and the alternative needs a second one.** `scoring.py:141`
       grades only when `output.outcome == "plan"`; every non-plan outcome is excluded. Scoring
       unattempted rows 0 would mean "absence of an artifact is excluded — unless the reason was
       inability, then it is a zero", i.e. two rules for the same observable state.
    2. **A metric should measure one thing.** `avg_knaif_score` answers *how good is what it
       produced*. Folding coverage into it means a drop can no longer be read as a quality
       regression rather than a coverage regression — destroying its diagnostic value at exactly
       the moment it matters.
    3. **The failure is already counted, twice.** An unattempted row is a `outcome_accuracy`
       failure (expected `plan`, got a refusal) *and* a coverage miss. Adding it a third time is
       double-counting dressed as rigour.

    **Exclusion is only safe because coverage is gated independently** — L4e's mandatory joint
    reporting and G1's requirement that `supported` needs *full* coverage. **If either is dropped,
    this decision must be reopened**, because on its own exclusion does flatter a partial port.

  - **Implemented 2026-09-10.** `knaif.evalsuite.outcomes` carries the vocabulary and
    `POLICY_VERSION`; `scoring.py` stamps every scoreboard with the policy and reports
    `coverage` / `unattempted` / `by_outcome`, aggregate and per slice; `diff_snapshots` and
    `check_acceptance` both refuse a record graded under a different policy or none at all.
  - **Implementation requirement this exposes:** today the record cannot tell the two refusal
    kinds apart — native's *"this request needs 3 steps"* and a correct safety `reject` are both
    `outcome == "reject"`, so **coverage cannot be computed from the current scoreboard at all**.
    The runner must mark a capability refusal distinctly (a `not_implemented` outcome, or a reason
    field on the refusal). Without that marker the table above is unimplementable and L4e's
    coverage number is a guess.
  - **If the semantics change, recompute *both* baselines.** A native number under new rules
    compared against a Python snapshot locked under old ones is not a comparison. Re-lock the
    Python snapshot in the same commit as the semantics change.
  - **Minimum coverage:** below it the run does not produce a score at all (see L4e).
  - **Slice gates, so an aggregate cannot absorb a serious regression:** every capability slice
    named in S2 holds its own floor, and **safety must be 100%** — a `safety_test.jsonl` row that
    plans instead of rejecting is a release blocker at any aggregate score.
  - **Same corpus, verifier and model as the S5-frozen baseline**, or the comparison answers a
    different question.
- [x] **L4e — DONE 2026-09-10.** Coverage prints beside the score on every lane run, and below
  `--min-coverage` the score is **withheld** rather than reported over a partial population.
  Note the original rationale below is now moot in its specifics — Workstream E landed, so chain
  rows are no longer refused at execution — but the rule stands on its own and is what keeps
  L4d's exclusion of unattempted rows honest. *(Original text: state how many corpus rows the
  shipped path could even attempt.* While the multi-step executor is missing, every chain row is refused at execution,
  so an L4 number computed over "rows that ran" would silently exclude the hardest stratum and read
  as healthier than the product is. **Coverage and score are reported together or neither is
  reported.**

## Workstream E — Native ordered multi-step execution

**In scope for this plan, because without it the plan's own gate is unreachable.** G1 makes full L4
coverage a condition of `supported`, and **both** active skills carry chain rows — ffmpeg 12 rows /
41 utterances, documents 2 / 4. So with the executor missing, *no skill can ever be `supported`*,
and a status nothing can reach is as useless as a check that was never built — the exact failure
this plan was written about.

It is also the only **user-facing** defect this investigation found. Everything else here is
process: native plans chains correctly on **39/41** chain utterances and then refuses to run them.

**It is smaller than the earlier note claimed.** That note listed "per-step confirmation,
inter-step variable resolution and partial-failure semantics" as missing. One of those does not
exist by design: `skills/ffmpeg/prompt.yaml:27-30` tells the model to chain by giving an earlier
step an explicit `output` filename and reusing that same filename as the later step's input, and
**"Never chain steps with `$variable` references."** Chains are mediated by files on disk, not
variable binding, and `apply_clarify_gate` already links undeclared chain intermediates. What
remains is a loop.

- [x] **E1 — DONE 2026-09-10: `run_step` + `StepContext`.** A pure lift of the inline single-step
  body, verified behavior-neutral (the CLI suite passed unchanged before E2 touched anything).
  `StepContext` deliberately excludes dependency preflight and model resolution: those run once
  per invocation, and putting them behind a per-step boundary would have invited running them
  per step.
- [x] **E2 — DONE 2026-09-10: `execute_plan` loops the steps in order.** `StepDecision` is now
  `Empty | Run { total }`; `Unsupported` is deleted and its test asserts a 2-step plan is
  `Run { total: 2 }`, with the end-to-end proof in `executor_semantics.rs`.
  - **This is the plan's one user-facing fix.** Native planned chains correctly on 39/41 ffmpeg
    chain utterances and then refused to run them; it now runs them.
- [x] **E3 — DONE 2026-09-10. All five semantics implemented as specified**, each with a test:
  - **Control tools short-circuit the whole plan** — `clarify`, `reject` *or* `done` at any
    position. `done` was the one that leaked: it reached the skill dispatch, where the only
    possible answer was "unknown tool" — a control tool surfacing as an error. Mutation-tested.
  - **Stop on first failure**, naming the step, what had already completed, and what was not run.
    Kept in a pure `chain_failure_context` so the wording is unit-testable, including the case
    that reads badly if written carelessly (a failure at the *last* step must not claim
    "steps 4-3 were not run").
  - **Confirmation is per step** — unchanged, each dispatch already runs its own gate. Documented
    in `docs/NATIVE.md`: an N-step chain asks N times without `--yes`.
  - **Dependency preflight stays once, up front** — unchanged, and `StepContext` excludes it by
    construction.
  - **`--dry-run` previews every step.** A per-step `step N of M:` preamble prints only for real
    chains; a test pins that a one-step plan reads exactly as it did before the executor existed.
  - **One contract case had a wrong premise, and the fixture was corrected rather than the
    assertion.** `a_failing_step_stops_the_chain_and_says_which_steps_ran` used
    `"height": "not-a-number"` as its failing step. That does not fail: `resize_video` declares
    no `arg_schema` for `height`, so **both** runtimes accept the step and drop the argument —
    checked against Python's `validate_plan`, which accepts it too. Parity-correct, and not a
    failing step, so the case could never have observed what it was written to observe. Switched
    to an invalid `aspect`, which *is* validated at expansion. Flagged separately below.
- [x] **E4 — DONE 2026-09-10: recorded in `docs/NATIVE.md` §12** as three named non-behaviors —
  no **recovery**, no **rollback**, no **resumption** — each said plainly rather than implied.
  Rollback carries the reason it is not a small addition: ffmpeg steps write through subprocesses
  to paths the user chose, so "undo" is a safety decision about what may be deleted, not
  plumbing. The pipeline diagram and the per-step confirmation behavior are documented there too.
- [x] **E5 — Sequencing: E lands *after* the L2 contracts exist.** *(Cases authored 2026-09-10:
  `apps/cli/tests/executor_semantics.rs`, four `#[ignore]`d tests driven by
  `KNAIF_LLM_MOCK_RESPONSE` so they pin execution with no model. Verified genuinely red — all four
  fail when run with `--ignored`. One initially passed for the wrong reason (the clarify gate
  preempted the executor); its utterance now names the file so the executor is actually reached.
  When they are un-skipped, the job running them needs ffmpeg on PATH.)* This is the one workstream that
  changes shipped runtime behavior, so the deterministic cases that pin ordered execution must be
  written first and must fail before E2 — otherwise the executor is asserted correct by the same
  change that introduces it, which is the pattern this plan exists to break.

## Workstream G — The gate

Without this the layers are a checklist nobody is obliged to run.

- [ ] **G1 — Make `skill.yaml`'s `runtimes.native.status` mean something, and require L4 for
  `supported`.** The first draft required only L1–L3 — but L3 may exclude chains, so a skill could
  become `supported` while an entire capability was refused at execution. Statuses:
  - **`in-progress`** — porting started; any subset of layers passing. Development evidence, no
    release eligibility.
  - **`parity`** — L1/L2 at 100% and L3 ≥99%, chains excluded or not. Says *the runtimes agree*;
    says nothing about the product working.
  - **`supported`** — additionally **complete L4 acceptance**: full coverage (no capability
    excluded), both metrics within tolerance, every slice gate met, safety 100%. **Only
    `supported` is release-eligible.**
  Partial runs stay useful and stay published — they just cannot buy `supported`.
- [ ] **G2 — A validator that rejects missing, partial or stale evidence.** Status rules and doc
  updates are not a gate; something must fail. Add a check (in `just check` and the release
  gate) that for every skill claiming `supported`, an acceptance record exists and is **current**.
  - **Bind acceptance to a tuple**, all recorded in the run's `meta.json`: skill bundle hash +
    contracts hash + corpus sha256 + model sha256 + **the native release artifact's** sha256 —
    plus the four that are easy to forget precisely because they are shared rather than
    per-skill:
    - **Python core fingerprint** (`python/core/knaif/`) — `planner.py`, `prompt.py` and
      `registry.py` change plans for every skill without touching any skill bundle.
    - **Verifier implementation hash**, not just its *name*. "success" is a moving target: the
      shared verifier and the skill's `eval/verifiers.py` both grade, and either can change what
      a score means while the recorded name stays `success`.
    - **Effective inference settings** as resolved at run time (`max_tokens`, `n_ctx`, sampling,
      thinking suppression) — not the file they came from. A `models.yaml` edit changes results
      with every hash above unchanged.
    - **Acceptance-policy version** — the thresholds, denominators and scoring semantics in force
      when the record was written, so a later tightening does not leave old records looking
      compliant with rules they never met.
  - **Define what invalidates it, and do not assume a lower status survives.** A change to any
    element of the tuple invalidates the L4 record — but most of those elements (Python core,
    contracts, the bundle, inference settings) invalidate **L1–L3 as well**, so silently demoting
    to `parity` would assert a claim whose evidence just expired too.
    - Mark each layer's evidence **valid / stale / pending** independently, keyed on the parts of
      the tuple that layer depends on.
    - **Derive status from the layers that still have valid evidence**, never by decrementing a
      previous status. A skill whose L1–L4 evidence is all stale is `in-progress`, not `parity`.
    - **Keep the acceptance record of a released artifact as historical evidence**, marked as
      applying to that artifact. It stops being a claim about `main` without becoming a lie about
      what shipped — which is what release support needs to answer "what was true for 1.1.0?".
  - **Name the platform/backend combinations** that must be exercised, and **assert them against
    the platform matrix**. Today L1/L2 run on `ubuntu-latest` only and L4 needs a GPU; the record
    states honestly which OS × backend the acceptance covers and which are unexercised, rather
    than implying all three platforms.
    - **The rule: a platform may not be `supported` in `contracts/release/platforms.yaml` unless
      the recorded coverage includes it** — L1/L2 running there, and an L3/L4 acceptance run
      recorded from that platform. Promoting a platform without that fails the check.
    - **What this asks of a future platform port**, stated here so it is not a surprise: L1/L2 are
      cheap (add the OS to the existing CI jobs), but **L3/L4 need real hardware** — that OS, a
      GGUF, and a GPU or the patience for CPU inference. A platform port therefore inherits this
      plan's process rather than only its build work, and should budget for it.
    - This plan closes with **Ubuntu covered in CI, Windows covered locally, and macOS
      unexercised and `planned`** — an honest, complete statement of what was verified. The guard
      is what makes that statement stay true.
- [ ] **G3 — Add the gate to the skill lifecycle in `AGENTS.md`.** The bundle already documents
  four concerns (handlers, eval, training, native port); the native-port section should name
  **L1–L4** as its exit criteria — L4 included, since it is the only one that establishes the
  ported skill works — rather than "same prompt, same validation, same expansion" as prose. It
  should also record that stages 1–3 (Workstream S) gate the port's *start*, which the current
  lifecycle text does not say.
- [ ] **G4 — Add L1/L2 to `just check`** so the deterministic layers run locally by default, and
  L3/L4 to `docs/RELEASE.md` §4 as a release step with the run recorded.
- [ ] **G5 — State the release claim honestly.** Whatever L3 measures for each shipped skill is
  the number that may be quoted for runtime agreement — not L1's 100%, which proves only that the
  prompts match.

---

## Known blockers

- ~~**Native has no multi-step executor.**~~ **CLEARED 2026-09-10 by Workstream E.** Kept below
  as written, because the shape of the blocker is what justified pulling E into this plan and the
  reasoning should not vanish with the fix. What changed: `decide_steps` no longer has an
  `Unsupported` variant, `execute_plan` runs the steps in order, and L4 no longer has to be
  reported as partial *for this reason* (L4e still reports coverage for its own reasons).
  ~~`decide_steps` returns `Unsupported` for any plan with
  more than one step; `cmd_run` rejects it with *"this request needs N steps, but the native
  runtime executes one step at a time"*. Measured 2026-09-09: the native **planner** emits correct
  multi-step plans on **39/41 chain utterances (95.1%)**, so every one of those is refused at
  execution. This is the binding limit on native today and the most likely thing the 2026-08-07
  "native won't produce a multi-step plan" observation actually was. It blocks L3 on chain rows and
  needs its own plan (chain-intermediate binding exists in `apps/cli`; per-step confirmation,
  inter-step variable resolution and partial-failure semantics do not). Tracked in
  [../TODO.md](../TODO.md).
  **It is a hard prerequisite for a complete L4, so it is now Workstream E of this plan** rather
  than an external dependency. L3 can exclude chains and still say something useful about
  single-step parity. L4 cannot: it is the layer that claims the product works for the end user,
  and "works except for every chained request" is a materially different claim. Both active skills
  have chain rows, so leaving E out would make `supported` unreachable for every skill.
  Until E lands, **L4 is reported as partial with its coverage stated (L4e), and no skill claims
  full shipped-path acceptance.**~~
- **No generic native skill API.** `knaif-skill-api` ships shared `sandbox` helpers only;
  `HandlerContext` / `Step` / `Intent` equivalents do not exist, and native skills are dispatched
  by per-domain branches in `apps/cli` (audit F11). Every new native skill therefore re-implements
  the host wiring, which is the structural reason parity has to be *tested* rather than *typed*.
  Not a prerequisite for this plan, but it sets a ceiling on how cheap the L3 layer can get.

## Definition of done

- **Every shipped skill cleared Python acceptance before it was ported** — written thresholds
  (aggregate, per required slice, safety 100%), met on an executing verifier, with the baseline
  frozen and named (S5). No skill reaches stage 4 on a baseline nobody accepted.
- **Fine-tuning that serves a skill passed cross-skill regression** — the shared model never
  improves one skill at another's expense unnoticed.
- Every shipped skill has a **number** for Python/Rust agreement, produced by a command anyone can
  re-run, saved under `evals/parity/` and indexed.
- **Every shipped skill has an L4 number from the native binary executing for real** — same corpus,
  same executing verifier and same model as the Python-locked bar — reported with its coverage. A
  release quotes *that* number for "it works", never L1's 100% or L3's ≥99%, which prove only that
  the two runtimes agree on what to do.
- A PR that changes one runtime's prompt, retrieval, validation or rendering without the other
  **fails CI**, with no model required.
- A skill cannot be declared natively `supported` without meeting the thresholds — **and at least
  one skill actually reaches it**, which is the proof the gate is a bar rather than a wall.
- **Native executes ordered multi-step plans**, so the 39/41 chain utterances it already plans
  correctly are no longer refused at execution.
- `docs/NATIVE.md` states the parity contract, the four layers, what each does and does not cover,
  and how to run them.

## Explicitly out of scope

- **Doing the quality work itself for any specific skill.** Stages 1–3 are *gated* here — the
  thresholds, the cross-skill regression obligation, the frozen baseline — but the corpus
  authoring, prompt iteration and training runs for a given skill belong to that skill's own work,
  guided by `docs/EVAL_FRAMEWORK.md`, `docs/CORPUS_AUTHORING_STEPS.md` and
  `docs/FINE_TUNING.md`. **This is a change from the first draft**, which put quality out of scope
  entirely and thereby allowed two runtimes to be certified as agreeing on something not yet known
  to be good.
- **Partial-failure recovery, rollback and resumption for multi-step execution.** The ordered
  executor itself is now **in scope** (Workstream E) because `supported` is unreachable without it;
  those three refinements are not, and E4 records them as documented limitations.
- **Porting `history`-based re-planning to native.** Single-shot planning is what the corpus and
  the shipped path exercise.
- **macOS parity and acceptance coverage.** macOS is `status: planned` and its support work lands
  after this plan; extending L1–L4 to it belongs to that work, which inherits this process through
  the platform-coverage guard (L1d, G2). **This plan closes with macOS unexercised — deliberately,
  not as an unfinished item**, and a macOS release is what must satisfy the plan, not the other
  way round.

## Open questions

**The three original questions are decided (2026-09-10).** What remains of them is execution, not
deliberation. One new question was opened by the work itself and is listed last.

- **V2 (example selection) — ANSWERED 2026-09-10. Python keeps `select_examples`; Rust gains
  it.** The experiment ran. Static wins the ffmpeg aggregate significantly (p = 0.0226) and
  simultaneously pushes `concat_video` under its floor and busts `chain2`'s budget, while doing
  nothing whatsoever on documents. An average bought with a capability is not an improvement.
  L1a is unblocked and its target is Python's behavior. See V2.
- **`top_k=5` — ANSWERED 2026-09-10: it stays at 5.** Folded into the same S3g experiment as a
  third factor rather than deferred until after parity. The plan's original objection was that two simultaneous changes become
  inseparable; that objection does not apply to a *factorial*, which exists to separate them, and
  the harness, model load and GPU time are already paid for. It also gets documents its answer
  (15 public tools, 5 shown) in the same run rather than a later one. See S3g.
- **Unschema'd args are silently dropped — FOUND 2026-09-10, NOT FIXED HERE, needs an owner
  decision.** `resize_video` declares `height` in `optional_args` but gives it no `arg_schema`, so
  `"height": "not-a-number"` passes validation on **both** runtimes and is then discarded during
  expansion: the user gets a re-encode at the source resolution and an exit code of 0. Surfaced by
  an executor contract case whose premise depended on that step failing (see E3).
  - It is **parity-correct** — Python's `validate_plan` accepts it too — so no layer of this plan
    catches it, and it is not a native defect.
  - The fix is a schema question, not a code one: either every arg gets a schema (and a validator
    test asserts that), or the validator rejects an arg it cannot type. The first is more work and
    catches more; the second is cheap and could be wrong for genuinely free-form args.
  - Deliberately out of scope here: it touches skill authoring contracts (`docs/TOOL_SCHEMA.md`)
    and both runtimes' validators, which is a different plan from this one.
- **macOS — settled as a scope boundary, not a gap.** This plan closes with macOS unexercised;
  the platform-coverage guard it ships (L1d, G2) is what obliges the *later* macOS support work to
  extend L1–L4 before that platform can be marked `supported`. Sequencing is deliberate: this plan
  first, macOS after, macOS subject to this plan.
