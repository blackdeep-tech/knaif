# Runtime parity process — Python authors, Rust ships, both must agree

**Status:** Active — not started · **Created:** 2026-09-10 · **Completed:** —
**Owner:** core · **Ref:** supersedes
[2026-08-08-native-python-planning-parity](2026-08-08-native-python-planning-parity.md);
builds on `scripts/parity_check.py` and `contracts/parity/`

> **Status note:** Replaces the prompt-parity plan, which asked *"why is native worse?"* — a
> question whose premise was measured false on 2026-09-09 (native and Python do not differ
> significantly; the reported symptom was the **executor** refusing chains, not the planner
> producing bad ones). That plan is **Kept, Superseded**: its Workstream P is the evidence base
> this plan stands on and should not be re-derived. What was missing was never a diagnosis — it
> was a *process* that keeps the two runtimes in agreement continuously, instead of discovering
> a drift months later by driving the CLI by hand.

**Goal:** Make Python/Rust behavioral agreement a measured, gated property of every skill — four
layers, explicit thresholds, and a gate that a skill cannot pass without meeting them.

---

## Why this exists

knaif's model is: **skills are authored in Python, the contract is shared, the skill is evaluated,
then it is implemented in Rust.** Python is the authoring and eval runtime. Rust is what ships —
the installers, the tarball, the AppImage. A divergence between them is therefore a gap between
*what we measure* and *what users get*, always in the direction that flatters the numbers.

The target is that **both runtimes behave the same ≥99% of the time**. Today that number has never
been computed for any skill.

### What the 2026-09-09 measurement established

**Carried here in full so this plan stands alone** — do not re-run it. The working record (method,
two corrected audit findings, and a chain-only sample that inverted under the full corpus) stays in
the superseded plan's *P1/P2 outcome* and *P3 full corpus*; read it before designing a new
comparison, because the ways it went wrong are reusable.

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

## The two rules

**1. Python is the reference; Rust moves.** The model is fine-tuned on Python-shaped prompts
(`build_dataset.py:132` builds every training row through `retrieve_tools` → `build_prompt`), and
Python is where skills are authored and graded. So a divergence is a Rust bug **by default**.

*The one exception, and it must be argued in writing with a measurement:* where Rust's behavior is
**measurably better**, Python moves instead. That is not hypothetical — see *Convergence
directions* below, where the evidence says Python should drop example selection rather than Rust
gain it. Without this escape hatch the rule would force known-worse behavior onto both runtimes in
the name of consistency.

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
| **L4 quality** | both runtimes within tolerance of the skill's eval snapshot | yes + exec | release | **within 2 pts** |

**Thresholds are per layer, deliberately.** L1/L2 are deterministic: a mismatch is a bug, never
noise, so anything below 100% is a failure. L3 is model-driven and floors at ~99.6% (above), so 99%
leaves room for genuine FP ties without hiding a port bug. A single blended "99%" would let a
deterministic port bug hide inside model noise — which is precisely how the prompt divergence
survived.

---

## Convergence directions — settled by measurement, per axis

Rule 1 says Rust moves. The 2026-09-09 data says that is right for two of the three known
divergences and **wrong for the third**. Converging each axis onto its measured-better side is what
makes L1 reachable *and* leaves both runtimes better than either is today.

| axis | today | direction | evidence |
|---|---|---|---|
| tool retrieval | Python retrieves 5, Rust sends 13 | **Rust adopts Python** | retrieval wins 8/1, p = 0.039 |
| example selection | Python selects 6, Rust sends 29 static | **Python adopts Rust** — delete `select_examples` from the prompt path | static wins 16/1 and 14/1, p ≤ 0.001 |
| path normalization | Rust rewrites every backslash, Python only path-shaped tokens | **Rust adopts Python** | no quality data; Python's rule is narrower and is what the model was trained on |

- [ ] **V1 — Port `retrieve_tools` into the native plan path, preserving rank.** The function is
  ported and tested in `knaif-core` but **never called**; `registry.rs` says "ported in a later
  slice". Wiring alone is insufficient: it returns a `BTreeMap` (`retrieval.rs:86`), discarding
  rank at the return, and `prompt.rs` re-sorts by `def.order`. Return
  `Vec<(String, &'a ToolDef)>` and emit in that order. Match Python's `top_k=5`, `min_score=0`,
  and its unusual tie-break (`scores.sort(reverse=True)` on `(score, name)` → equal scores order
  by name **descending**).
- [ ] **V2 — Decide and execute the example-selection direction.** The measurement says delete
  `select_examples` from Python's prompt path rather than port it to Rust: it costs 13–15
  utterances of outcome accuracy and buys 2 chain utterances, on a runtime that cannot execute
  chains anyway. **This contradicts the training distribution** (the model was tuned on selected
  examples), which is the argument for the opposite call — so decide it explicitly, with the
  chain-vs-corpus trade stated, and record the decision here. Do not let L1 force it silently.
- [ ] **V3 — Converge `normalize_path_separators` on Python's `_PATH_TOKEN_RE`** (`prompt.py:27`),
  replacing the blanket `replace('\\', "/")` at `main.rs:1185`. Existing Rust tests at
  `main.rs:1853-1869` already cover the cases and must be updated, not deleted.
- [ ] **V4 — One source of truth for generation settings.** `max_tokens` lives in `llama.rs:241`,
  `models.yaml` and `eval_backends.yaml`; all three currently agree at 512, so this is hygiene,
  not a fix — but the duplication already caused one wrong finding (a superseded stanza read as
  live). Canonical copy in `contracts/runtime/`, synced by `just sync-runtime` with a drift guard,
  as `core_tools.yaml` is.
- [ ] **V5 — Fix the two stale `prompt.rs` notes.** The module docstring claims the tool listing is
  alphabetical "because `Registry` is a `BTreeMap`" (it sorts by `def.order`), and the `def.order`
  comment claims the model was trained on that order (training prompts are in *relevance* order).
  Both are provably wrong today and both expire when V1 lands.

---

## Workstream L1 — Contract conformance (no model, every PR)

The durable half, and the only layer CI can run on every change.

- [ ] **L1a — Prompt-parity contract.** Fixed utterances × fixed registries → both runtimes
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
- [ ] **L1b — Retrieval-parity contract.** Same utterance + registry → same selected tool set **and
  order**. Separable from L1a and worth its own cases: retrieval is scoring logic with tie-breaks,
  and it is where CJK tokenization and diacritic handling live. **Order is the load-bearing half**
  — include cases with tied scores, where the `(score, name)`-descending tie-break is the only
  thing under test.
- [ ] **L1c — Settings-parity contract.** Assert both runtimes' generation defaults agree —
  `max_tokens`, `n_ctx`, sampling, thinking suppression. After V4 there is one canonical copy, so
  this becomes *"both runtimes read the canonical file"* — a weaker assertion over a stronger
  invariant. **Write it that way deliberately**; porting a three-way comparison onto a single
  source yields a test that can only ever pass. Keep one case reading each runtime's *effective*
  value at the point of use, so a hard-coded fallback shadowing the contract file still fails.
- [ ] **L1d — Gate in CI.** Belongs in the existing `python` and `native` jobs — both already run
  on changes to `skills/` and `contracts/`. **Both are `ubuntu-latest` only**, so CI green is not
  evidence the contract holds where the work is done: run L1 locally on Windows as part of V
  before calling this done, and state the real coverage in the contract file — **Ubuntu in CI,
  Windows locally, macOS unexercised**. Claiming three-platform coverage would be the same
  unmeasured assertion this plan exists to end.

## Workstream L2 — Deterministic pipeline (no model, every PR)

Same plan JSON in → same expansion, optimizer, validation verdict, rendered command out. No model
means no noise: this layer is either 100% or broken.

- [ ] **L2a — Extend `contracts/parity/planner_cases.json`** to cover expansion and rendering, not
  just planning: chain-intermediate linking, variable resolution, `apply_defaults`, and the
  hallucinated-filename gate. **The clarify gate belongs here** — it is deterministic, it fires on
  ~17% of the ffmpeg corpus in the native runtime, and nothing currently proves Python's
  equivalent fires on the same rows.
- [ ] **L2b — Assert validation *verdicts* agree, not just accepted plans.** A plan Python rejects
  and Rust accepts is the dangerous direction (Rust ships). Include known-bad plans with the
  expected error class on each side.
- [ ] **L2c — Run L2 in the same CI jobs as L1**, under the same 100% threshold.

## Workstream L3 — Behavioral parity (needs a GGUF; local + release)

**Largely already built — `scripts/parity_check.py` + `just parity <skill>`.** It pins both
runtimes to the identical GGUF, guards against comparing two configs of the same weights, compares
outcome type first and rendered argv second, and shlex-normalizes so quoting differences do not
register. What it lacks is a threshold, a record, and a trigger.

- [ ] **L3a — Give it a pass/fail threshold.** Today it reports; it does not gate. Adopt **≥99%
  per-row equivalence** using `plan_equiv_modulo_defaults` (`parity_check.py:523`) as the
  equivalence relation — it already treats native's materialized optional defaults as benign while
  keeping any difference in `_SIGNIFICANT_ARG_KEYS` (inputs, paths, outputs) a real divergence.
  **Report the count of non-equivalent rows and enumerate them**; an aggregate can hide offsetting
  changes in both directions.
- [ ] **L3b — Save every run under `evals/parity/`** with a `meta.json` pinning git SHA, corpus
  sha256, model sha256, **and the inference backend** — the last because greedy argmax over
  different FP accumulation can flip a near-tie, so a CPU→CUDA change between two runs is
  indistinguishable from the change being measured. `2026-09-09_p2b-prefix-baseline/meta.json` is
  the template. Add a row to `evals/INDEX.md`.
- [ ] **L3c — Resolve the chain blocker.** Native `run` rejects every multi-step plan
  (`decide_steps`, audit F5), so all 41 ffmpeg chain utterances compare as mismatches and L3
  cannot reach 99% on chains by construction. Either the executor lands first, or L3 launches
  with chains **explicitly excluded and the exclusion recorded as a known hole** — not silently
  skipped. See *Known blockers*.
- [ ] **L3d — Document the entry point on each side** in the harness output, per rule 2.

## Workstream L4 — Quality parity (needs a GGUF + execution; release)

- [ ] **L4a — Two independent lanes, not a fake backend.** Python via `run_corpus`; native via
  `knaif plan --batch`. **Do not register the native lane under `backends:`** — `_make_agent` hands
  any entry there straight to `InferenceOrchestrator(backend=…)` (`evalsuite/cli.py:130`), so a
  `rust-cli` key is not inert; it is a token-generation backend that will be constructed and fail,
  or half-work. Use a **separate top-level config section** (e.g. `lanes:`), so the config shape
  says what the thing is.
- [ ] **L4b — Write the scoring adapter, and label where it cuts.** `plan --batch` emits a
  validated plan and nothing else (`main.rs:580`); the executing verifiers need a rendered command
  and a produced file. Something must expand → optimize → resolve → execute each native envelope.
  **The honest option is Python-side execution, explicitly labelled** — the lane then measures *the
  native planner with Python execution*, not the shipped runtime end to end. Write that label into
  the lane's output **and** into `docs/NATIVE.md`, or the number will be quoted as end-to-end
  parity within a month.
- [ ] **L4c — Tolerance: within 2 points** of the skill's `eval_snapshot.json`, stated as an
  absolute, and **secondary** to L3's per-row criterion — it is a quality check against the
  committed bar, a different question from parity.

## Workstream G — The gate

Without this the layers are a checklist nobody is obliged to run.

- [ ] **G1 — Make `skill.yaml`'s `runtimes.native.status` mean something.** A skill may not move to
  `status: supported` until L1 and L2 pass at 100% and L3 passes at ≥99%, with the L3 run saved
  under `evals/parity/` and indexed. Until then it is `status: partial` (or whatever the existing
  vocabulary allows) — which is honest, and readable by both runtimes and the website extractor.
- [ ] **G2 — Add the gate to the skill lifecycle in `AGENTS.md`.** The bundle already documents
  four concerns (handlers, eval, training, native port); the native-port section should name L1–L3
  as its exit criteria rather than "same prompt, same validation, same expansion" as prose.
- [ ] **G3 — Add L1/L2 to `just check`** so the deterministic layers run locally by default, and
  L3/L4 to `docs/RELEASE.md` §4 as a release step with the run recorded.
- [ ] **G4 — State the release claim honestly.** Whatever L3 measures for each shipped skill is
  the number that may be quoted for runtime agreement — not L1's 100%, which proves only that the
  prompts match.

---

## Known blockers

- **Native has no multi-step executor.** `decide_steps` returns `Unsupported` for any plan with
  more than one step; `cmd_run` rejects it with *"this request needs N steps, but the native
  runtime executes one step at a time"*. Measured 2026-09-09: the native **planner** emits correct
  multi-step plans on **39/41 chain utterances (95.1%)**, so every one of those is refused at
  execution. This is the binding limit on native today and the most likely thing the 2026-08-07
  "native won't produce a multi-step plan" observation actually was. It blocks L3 on chain rows and
  needs its own plan (chain-intermediate binding exists in `apps/cli`; per-step confirmation,
  inter-step variable resolution and partial-failure semantics do not). Tracked in
  [../TODO.md](../TODO.md).
- **No generic native skill API.** `knaif-skill-api` ships shared `sandbox` helpers only;
  `HandlerContext` / `Step` / `Intent` equivalents do not exist, and native skills are dispatched
  by per-domain branches in `apps/cli` (audit F11). Every new native skill therefore re-implements
  the host wiring, which is the structural reason parity has to be *tested* rather than *typed*.
  Not a prerequisite for this plan, but it sets a ceiling on how cheap the L3 layer can get.

## Definition of done

- Every shipped skill has a **number** for Python/Rust agreement, produced by a command anyone can
  re-run, saved under `evals/parity/` and indexed.
- A PR that changes one runtime's prompt, retrieval, validation or rendering without the other
  **fails CI**, with no model required.
- A skill cannot be declared natively `supported` without meeting the thresholds.
- `docs/NATIVE.md` states the parity contract, the four layers, what each does and does not cover,
  and how to run them.

## Explicitly out of scope

- **Improving planning quality on either runtime.** This plan makes them agree and proves it. Both
  getting better is fine-tuning work (`docs/FINE_TUNING.md`).
- **Building the native multi-step executor.** A real gap, separately scoped — see *Known
  blockers*. This plan only decides how L3 behaves until it lands.
- **Porting `history`-based re-planning to native.** Single-shot planning is what the corpus and
  the shipped path exercise.

## Open questions

- **V2's direction** — delete `select_examples` (what the corpus measurement says) or port it to
  Rust (what the training distribution says)? The chain stratum and the corpus disagree, and the
  chain benefit is currently unreachable natively. Decide before L1a can go green.
- **Does `top_k=5` still suit larger skills?** Answer after parity, with a measurement — not while
  converging, or the two changes become inseparable.
- **Does macOS need its own contract run?** Nothing in L1d exercises it; the honest status is
  *unexercised*, not *passing*. Revisit if macOS packaging lands.
