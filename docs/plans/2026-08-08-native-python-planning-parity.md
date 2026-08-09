# Native/Python planning parity — the prompt gap, its contracts, and the eval-parity lane

**Status:** Active — not started · **Created:** 2026-08-08 · **Revised:** 2026-08-08 (audit) ·
**Completed:** —
**Owner:** core · **Ref:** absorbs **C4** from
[post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md); complements
`scripts/parity_check.py`

**Goal:** Make the native runtime plan identically to Python on the same model, and pin that with
contracts CI can enforce without a GGUF — then measure what remains with an eval-parity lane.

---

## Why this exists

Owner observation, 2026-08-07: **the native runtime produces worse plans than Python on the same
model, and would not produce a multi-step plan at all.** Found by driving the CLI by hand, not by
an eval run.

**It is not platform-specific.** Nothing below depends on the OS — every divergence identified is
in the prompt the two runtimes build, from code that is identical on every target. The reporting
machine is not the scope.

**The work runs on Windows.** Diagnosis, ports, contracts and the eval lane are all exercised
there, so the commands in this plan are PowerShell. That is the operator's box, not a constraint
on the fix: nothing here is Windows-specific either, and R's contracts must pass on all three
release platforms.

This matters more than an eval delta. The native runtime is what ships: the installers, the
tarball, the AppImage. Python is the authoring and eval runtime. So a quality gap between them is
a gap between *what we measure* and *what users get*, in the direction that flatters the numbers.

---

## What is already established

A code read on 2026-08-08, **before any run**, **corrected 2026-08-08 after an audit** that
re-measured every number against a live registry load. Recorded separately from the workstreams
because these are findings, not tasks — and because the first instinct (blame the model, or the
platform) is wrong.

**The audit found three errors: two of the four original findings, plus one entry in the
ruled-out list.** They are shown corrected below rather than quietly replaced, because the *shape*
of the errors is instructive. A tool count was read off the registry without applying the filters
the prompt builder applies. A config value was read from the wrong stanza of a file that holds a
dozen. A divergence was filed under "ruled out" on the strength of a docstring that asserted
equivalence rather than a test that showed it — which is, exactly, the failure this plan exists to
fix, reproduced inside the plan describing it.

None of the three needed a model or a run to catch; they needed *executing the read*. So:
**everything below marked *measured* was run, everything marked *read* was not**, and P re-checks
the read ones. The audit also **strengthened** the central hypothesis while shrinking its headline
number — see *Why the prompt is the prime suspect*.

### Confirmed

1. **Retrieval is not ported.** *(read)* `retrieve_tools` is implemented in
   `native/crates/knaif-core/src/retrieval.rs`, re-exported at `lib.rs:30` — and **nothing calls
   it**. `registry.rs` says so outright: *"Retrieval (`retrieve_tools`) is ported in a later
   slice."* `apps/cli/src/main.rs` passes `&self.registry` — the whole thing — to `build_prompt`.

   *(measured, 2026-08-08, live `CommandAgent.from_skill("skills/ffmpeg")`)*

   | | tools in the prompt |
   |---|---|
   | Python (`retrieve_tools`, `top_k=5`) | **5 of 13** |
   | Native | **13 of 13** |

   **Not 26.** The ffmpeg registry holds 26 entries and 30 once core control tools merge, but
   **13 are `internal: true`** and both prompt builders skip them (`prompt.py`'s `_SYSTEM_TOOLS` +
   `internal` filter, `prompt.rs:167`'s `is_system_tool(name) || def.internal`). The original
   26-vs-5 table counted registry entries on one side and prompt lines on the other. The gap is
   real and still 2.6×, but quote 5-of-13.

   `"trim clip.mp4 to 5 seconds then resize to 720p"` retrieves, **in this order**:
   `resize_video, trim_video, convert_video, compress_video, strip_audio` (plus the always-include
   `done`/`clarify`/`reject`, which the prompt then filters out). The order matters — see finding 3.

2. **Example selection is not ported either.** *(read)* Python's `CommandAgent.build_prompt`
   filters the few-shot block per utterance — `select_examples()` ranks by how many *retrieved*
   tools appear in each example's plan, and always includes one clarify and one reject example.
   Native uses the static `examples_block` from `prompt.yaml` for every utterance. Note the
   trigger: Python only filters when `prompt_examples` exist **and** a `registry_override` was
   passed, so retrieval and example selection are coupled — porting Q1 without Q2 changes the
   examples too, by making the override non-empty.

3. **Tool *order* diverges, and the port cannot fix it without a type change.** *(read; this
   finding is new to the audit and is the one that makes Q1 more than wiring.)* Python emits tools
   in **relevance order** — `retrieve_tools` builds `selected` from `scores.sort(reverse=True)`
   (`registry.py:201`) and `build_prompt` iterates `registry.items()` (`prompt.py:232`), so dict
   insertion order *is* the ranking. Native's `retrieve_tools` returns a **`BTreeMap`**
   (`retrieval.rs:81`), which discards rank at the moment of return, and `prompt.rs:163` then
   sorts by `def.order` — `tools.yaml` order. **Calling `retrieve_tools` in the native path
   therefore still fails R1 and R2.** Q1 must return an ordered result.

4. **The generation budget does *not* differ — the original finding was wrong.** *(measured)*
   Native hard-codes `max_tokens: 512` (`knaif-llm/src/llama.rs:241`, `$KNAIF_MAX_TOKENS`
   overrides) and so does the promoted model, on **both** sides: `models.yaml:56`
   (`knaif-qwen3-4b-v1`) and its promoted eval stanza `qwen3-4b-sft-v3-flat-q4` in
   `eval_backends.yaml:196`. The `2048` in the original finding belongs to `qwen3-4b-ft-q4`, a
   **superseded** stanza for a different GGUF. Q3 survives as hygiene — one source of truth for a
   value duplicated in three files — but **it is not a candidate cause**, and the P3 experiment
   that set `2048` would have measured nothing.

5. **`prompt.rs`'s module docstring is stale, and its replacement comment may be too.** *(read)*
   The docstring claims the tool listing is alphabetical "because `Registry` is a `BTreeMap`";
   the code sorts by `def.order`, with a comment that the fine-tuned model was trained on that
   order and is sensitive to it. **That rationale is not supported by the training path** —
   `python/training/build_dataset.py:132` builds every training prompt as
   `retrieve_tools(utt, agent.registry)` → `agent.build_prompt(utt, registry_override=override)`,
   i.e. in *relevance* order, not `tools.yaml` order. The comment is defensible as a description
   of what was available: with the **whole** registry in the prompt there is no ranking to sort
   by, so `def.order` was the only stable choice. It expires the moment Q1 lands. Fix both notes
   in Q4 and say which order is now canonical, and why.

### Ruled out

`n_ctx` is 8192 on both. `/no_think` is applied on both. Both decode **greedily** — Python passes
`temperature=0.0`, native takes the argmax over logits. *(all read, not measured.)*

**`normalize_path_separators` is not ruled out** — it was listed here in error. Both runtimes have
one, and they do different things: native replaces **every** backslash in the utterance
(`main.rs:1142`), Python only backslash-bearing tokens that match `_PATH_TOKEN_RE`
(`prompt.py:27`). `prompt.py`'s own docstring records the divergence and calls it "same result on
any file-path utterance" — which is a claim about the corpus, not about the function, and it is
**exactly** the shape of accepted-but-unmeasured divergence this plan exists to close. It is also
the one most likely to surface on Windows, where utterances carry backslashes. R1 must cover it:
quoted Windows paths, and backslashes that are not paths.

### Why the prompt is the prime suspect

Same GGUF, same greedy decode, identical prompt → near-identical tokens. So a *systematic* quality
gap has to come from something deterministic, and the prompt is where the two runtimes differ most.

The mechanism is specific rather than vague, and the audit made it **more** specific, not less.
**The shipped model is fine-tuned on Python-shaped prompts** — and that is now confirmed at the
source rather than assumed: `python/training/build_dataset.py:132` builds every training row's
prompt through `retrieve_tools` → `agent.build_prompt(registry_override=…)`.

So the training distribution is: **five tools, in relevance order, with examples selected for
those tools.** Native serves it **thirteen tools, in `tools.yaml` order, with a static example
block** — three simultaneous divergences, not one. The tool-count gap shrank from the original
26-vs-5 to 13-vs-5, but the hypothesis got *stronger*: count is only one of the three axes, and
ordering and examples were not being counted at all.

Multi-step planning is the hardest thing the model does and the first thing to degrade — which is
precisely the reported symptom.

**This is a hypothesis with an obvious test, not a conclusion.** Workstream P is that test, and it
runs before anything is changed. Note what the audit already cost this section: the one finding
that was *measured* rather than read (the tool count) was also the one that was wrong by 2×.

### The note that should have caught this

`prompt.rs` records its divergences as deliberate, with a stated safety net:

> *"Two intentional, prompt-only divergences from Python (not graded byte-for-byte; **Phase 10
> eval-parity measures end quality**)"*

Eval-parity is C4. **It was never built.** A divergence accepted on the strength of a check that
does not exist is an unmeasured divergence, and that is the structural lesson here — bigger than
any single fix below.

---

## Workstream P — Diagnose before fixing

- [x] **P0 — Build the prompt dump first. There is currently no way to do P1.** *(done 2026-08-08)*
  `$KNAIF_DEBUG` does **not** print the prompt: its only caller is the failure path, and it emits
  the raw model output plus extracted JSON on a parse/validation error (`main.rs:1084`, `1114`,
  `1129`). On a *successful* plan — which is most of the corpus, and the interesting case — it
  prints nothing at all.
  - **Built:** `knaif plan --skill X --dump-prompt [--batch FILE]` and its Python counterpart
    `scripts/dump_prompt.py`, emitting the identical banner format so the two diff line by line.
    Both are **model-free** — the prompt is a pure function of the bundle and the utterance, so
    neither side loads a GGUF, and the capture runs in under a second.
  - `PlanSession` was split: a new `PromptContext` holds the registry and overrides, and *both*
    the dump and `PlanSession::plan` call its `build()`. A dump produced by a parallel code path
    could disagree with the prompt actually sent, which would be worse than no dump.
  - Format is banner-delimited, not JSON: JSON escaping collapses each message onto one line where
    every difference reads as "the line changed". Both writers force LF (Rust never translates
    newlines; the Python side opens stdout with `newline="\n"`), so a Windows redirect cannot
    inject CRLF. Verified on the captures below — both files came out LF, no BOM.
  - `python/core/tests/test_dump_prompt.py` is the drift guard: it **parses the format literal out
    of `main.rs`** and asserts the Python function reproduces it, since there is no shared source
    to generate both from. Confirmed non-vacuous against a deliberately drifted format.
- [x] **P1 — Reproduce, with both prompts captured verbatim.** *(done 2026-08-08)* Four utterances
  — `ffmpeg_hard_001/002/003` (the `chain3` tag, which is how multi-step rows are marked; there is
  no `len(plan) >= 2` field to filter on) plus `ffmpeg_001` as a single-step control.
  - **Result: the user message is byte-identical on both runtimes for all four. The entire
    divergence is in the system message**, and it is 1.77× longer on native (13,994 vs 7,893
    characters for `ffmpeg_hard_002`).
  - The system header is **identical** (5,395 chars both) — so the divergence is exactly the two
    ported-but-unwired pieces, and nothing else. That is a stronger result than "the prompts
    differ": it rules out the header, the rules block and the user turn in one measurement.
- [x] **P2 — Quantify.** *(done 2026-08-08)*

  | per utterance | Python | Native |
  |---|---|---|
  | tools listed | **5**, varying per utterance, relevance order | **13**, identical every utterance, `tools.yaml` order |
  | few-shot examples | **5**, varying per utterance | **28**, identical every utterance |
  | system message | 7,592–7,907 chars | 13,994 chars, *identical for every utterance* |
  | user message | — | byte-identical to Python |

  Character breakdown of the gap (`ffmpeg_hard_002`): header +0, tool listing **+2,033**, examples
  **+4,068**. Total +6,101.

  - **The examples block is two-thirds of the excess — the bigger half of the divergence.** The
    plan had retrieval (Q1) as the headline and example selection (Q2) as secondary; by volume it
    is the other way round. Q2 is not a follow-up to Q1, and P3's factorial design is what will
    say which one actually moves plan quality.
  - Native's system prompt being **byte-identical across four different utterances** is the whole
    finding in one line: no retrieval, no example selection, nothing per-utterance at all.
  - **Token counts were not measured** — no tokenizer is installed in the venv (`transformers`,
    `tokenizers`, `llama_cpp`, `tiktoken` all absent) and the GGUF's tokenizer is only reachable
    through a `--features llama` build. Characters are reported instead, and they are exact.
    Worth closing when the llama-backed binary is next built; the ratio is what matters and
    characters track it closely for near-ASCII English.
- [x] **P2b — Save the pre-fix corpus run before touching anything.** *(done 2026-08-08)*
  847 utterances (all 314 rows) through `plan --batch` on a **planner-identical** v1.1.0 binary,
  saved to `evals/parity/2026-08-08_prefix-baseline/` with `score.json` + `report.md` committed.
  - **No build was needed, and that is verified rather than assumed.** `git diff v1.1.0..HEAD~3`
    shows zero `.rs` changes under `native/crates/`, two `--help` strings as the only `apps/cli`
    delta, and `tools.yaml` / `prompt.yaml` untouched. The staged release ships `ggml-vulkan.dll`,
    so the run took ~35 min on the 3070 instead of CPU's measured 63 s/utterance ≈ 15 hours.
  - **`evals/**` is gitignored**, so the raw envelopes are not committed — only `score.json` and
    `report.md`, which the ignore rules allow. **This is a real conflict with what P2b asked for.**
    For this run the envelopes *are* the durable artefact: S2 must re-grade them with the S1b
    adapter, which does not exist yet, and a summary cannot be re-graded. 224 KB total. Either
    add a narrow negation (`!evals/parity/**/*.jsonl`) or accept that S2 re-runs the baseline
    from the still-tagged v1.1.0 binary — **decide before Q lands**, because after that only the
    tag makes it reproducible.
- [x] **P2c — The founding symptom does not reproduce.** *(2026-08-08 — unplanned, and the most
  important result so far)*

  This plan was opened on: *native "would not produce a multi-step plan at all."* Measured across
  the corpus, on the shipped binary and the promoted GGUF, with the full 13-tool / 28-example
  prompt P1 characterized:

  | chain-tagged utterances | result |
  |---|---|
  | total | 41 |
  | produced ≥2 steps | **39 (95.1%)** |
  | produced the full expected length | **38** |

  All four repetitions of every `chain3` row chained correctly. Corpus-wide routing: outcome
  **0.908**, expected tool present **0.861**, against the committed Python snapshot's 0.933 /
  0.845 — *indicative only*, since that snapshot is 297 rows on a `cheap` verifier and this is
  847 utterances scored on plan envelopes. Close, not comparable.

  - **The three non-full chains are not refusals to chain.** Two are validation errors and one is
    a correct 2-step where 3 was expected. All five error envelopes corpus-wide are **one bug**:
    the model emitting an undeclared argument (`adjust_speed.include_audio`,
    `convert_video.bitrate`, `convert_video.audio_format`, `adjust_volume.target_sample_rate`).
    That is schema coverage, unrelated to retrieval or examples, and it deserves its own item —
    "with no sound" and "500 kbps Bitrate" are reasonable requests with no argument to carry them.
  - **What this does and does not overturn.** The prompt divergence is measured and real (P1/P2);
    nothing here touches it. What it overturns is the *causal story* — that an off-distribution
    prompt degrades multi-step planning specifically, which was the plan's stated mechanism and
    its justification for urgency. On this evidence the divergence is not costing multi-step
    capability at all.
  - **What is still unexplained:** why the original hand-driven session saw no multi-step plans.
    Possibilities not yet ruled out — a different skill, a different or absent `--model` (the
    mock backend), `run` rather than `plan`, or an older binary. Worth asking the observer before
    spending more on it, because the answer decides whether P3 is chasing anything.
  - Recorded as its own item rather than folded into P4 because it changes what the rest of the
    plan is *for*: R's contracts stand on their own merits, Q remains correct as a port, but
    neither is now a fix for a quality emergency.
- [ ] **P3 — Attribute factorially, not one-at-a-time.** **Re-scope before running: P2c removed
  the effect this was designed to attribute.** The question is no longer "which change restores
  multi-step planning" — nothing needs restoring — but "does the prompt divergence cost measurable
  quality at all, in either direction". Same four cells, different success criterion: a routing
  delta against the P2b baseline, not the presence of chains. If every cell lands inside noise,
  that is a *result* — it says the divergence is cosmetic and Q is a tidiness port, not a fix.

  Retrieval and example selection are *coupled* in Python (finding 2: the example filter only
  fires when a `registry_override` is passed), so testing them singly cannot separate them. Run
  all four cells:

  | | static examples | selected examples |
  |---|---|---|
  | **full registry** | today's native | — |
  | **retrieved registry** | — | Python-equivalent |

  Fill both `—` cells too; they are the whole point. Add tool *order* as a fifth cell if the four
  do not settle it — relevance order vs `tools.yaml` order, holding the tool set fixed.
  - **Do not run the `max_tokens=2048` experiment.** Finding 4: both sides are already 512, so it
    varies nothing. If output length is ever suspected, first check whether any plan actually
    reaches the 512 cap — if none does, the cap is not in the causal path at all.
  - **P2c already triggered the stop condition this bullet was written for.** The original wording
    said to stop and re-diagnose if no cell restored multi-step plans; the baseline restored
    nothing because nothing was broken. Re-diagnosis happened — that is P2c. Do not treat a null
    P3 as a failure to find the bug; treat it as evidence there is no quality bug to find.
  - **P3 needs a Python-side run to compare against, and that is currently blocked:**
    `llama-cpp-python` is not installed (`just install-llama`). Until it is, native can be
    measured but not measured *against* anything, which is the whole question.
- [ ] **P4 — Record the outcome here**, including whichever hypothesis fails. A ruled-out cause is
  worth as much to the next reader as the confirmed one. Two are already recorded above — write
  them the same way.

## Workstream Q — Port what is missing

Only after P3 attributes the gap. Each item is a **port, not a rewrite** — same inputs, same
outputs, per `docs/NATIVE.md`.

- [ ] **Q1 — Call `retrieve_tools` in the native plan path, and make it return ranked order.**
  The function is already ported and tested; the wiring is not — but wiring alone is **not
  sufficient**, per finding 3. `retrieve_tools` returns a `BTreeMap<String, &ToolDef>`
  (`retrieval.rs:81`), which throws the ranking away at the return, and `prompt.rs:163` then
  re-sorts by `def.order`. Change the return to an ordered type (`Vec<(String, &ToolDef)>`, or a
  `Vec` of names alongside the map) and have `build_prompt` emit in that order when a retrieved
  subset is supplied.
  - Match Python's defaults (`top_k=5`, `min_score=0`) rather than choosing new ones — a different
    `top_k` is a different prompt, which is the bug being fixed.
  - Match Python's tie-break too: `scores.sort(reverse=True)` sorts `(score, name)` tuples
    descending, so equal scores order by name **descending**. That is unusual enough to get wrong
    by writing the obvious thing, and R2 is the test that catches it.
- [ ] **Q2 — Port `select_examples`.** Ranked by retrieved-tool overlap, plus one clarify and one
  reject example. Note Python falls back to the full block when no retrieved subset is supplied
  **or when `prompt_examples` is empty** (`agent.py:752`) — the port must keep both fallbacks, not
  just the happy path.
- [ ] **Q3 — De-duplicate the generation budget.** **Not a fix — hygiene.** Finding 4: native's
  `512` and the promoted model's `512` already agree, so nothing changes behaviourally. The defect
  is that the number lives in three places (`llama.rs:241`, `models.yaml`, `eval_backends.yaml`)
  and the original code read picked the wrong copy. Give it one source both runtimes read, so the
  next reader cannot repeat that mistake. If the corpus's longest plan justifies a different value,
  that is a separate, measured change.
- [ ] **Q4 — Fix both stale notes in `prompt.rs`** — the module docstring's "alphabetical because
  `BTreeMap`" (false: it sorts by `def.order`) *and* the `def.order` comment's claim that the
  fine-tuned model was trained on that order (finding 5: training prompts are in relevance order).
  State which order is canonical after Q1 and why, and re-state which divergences remain
  intentional, if any survive Q1–Q3.
- [ ] **Q5 — Decide `normalize_path_separators` deliberately.** Not ruled out (see above): native
  rewrites every backslash, Python only path-shaped tokens. Converge them or write down which one
  is canonical and why the other is acceptable — but the decision must be a line of code or a
  contract case, not a docstring assertion. That is what got us here.

## Workstream R — Contracts, so it cannot drift silently again

**This is the durable half.** Q fixes today's gap; R is what stops the next one. All of it is
deterministic and needs **no GGUF**, so unlike C4 it can gate every PR in CI.

- [x] **R1 — Prompt-parity contract.** *(done 2026-08-09)* `contracts/parity/prompt_cases.json` +
  `python/core/tests/test_prompt_parity.py` + `native/crates/knaif-core/tests/prompt_parity.rs`.
  Expected strings are generated from the Python reference, never hand-written.
  - **Result: the renderers agree byte-for-byte.** With the same utterance, registry and explicit
    overrides, `knaif_core::build_prompt` reproduces `knaif.prompt.build_prompt` exactly — header,
    tool listing, arg labels, assembly. The prompt *rendering* is a faithful port; only retrieval,
    example selection and the built-in defaults diverge. That is a stronger result than the plan
    assumed and narrows Q considerably.
  - **It found a new divergence on its first run.** The built-in fallback blocks differ: Python's
    `_EXAMPLES` carries **9** examples, native's `DEFAULT_EXAMPLES` carries **4** — missing the
    `reject` example, both history/`completed:` examples, and the variable-binding example. The
    system *headers* match byte-for-byte, so this is examples only.
    - **Latent, not shipped:** documents, ffmpeg and io each provide a `prompt.yaml` examples
      block, so no released skill reaches the default. It is live for a newly authored skill and
      for SDK apps with no `prompt.yaml` — the case nobody would think to measure, which is why a
      contract found it and three months of use did not.
    - Held as an `#[ignore]`d Rust test with the reason inline, so Q has a green-able target.
  - **It also pinned an asymmetry the plan had mislocated.** `normalize_path_separators` is
    *library* code in Python (`knaif/prompt.py`, called inside `build_prompt`) but *CLI-private* in
    native (`apps/cli/src/main.rs`, not exported from `knaif-core`). So the two `build_prompt`
    functions have different contracts, and any non-CLI consumer of `knaif-core` — an embedder, a
    GUI, the skill API — gets un-normalized input while every Python caller gets normalized input.
  - **And it exposed two bugs in Python's normalizer**, both now pinned as fixtures:
    - `what does A\B mean` **is** rewritten to `A/B`. The docstring claims "only path-shaped
      tokens", but `A\B` matches `_PATH_TOKEN_RE`.
    - `convert "C:\My Videos\clip.mov" to mp4` is **not** rewritten. The path contains a space, so
      splitting on `" "` yields fragments that no longer match — meaning the exact case the
      function exists to prevent (a Windows path reaching the model as an illegal JSON escape)
      survives untouched whenever the path has a space in it. Native's replace-every-backslash
      rule handles it. Q5 decides which is canonical; this is now evidence rather than opinion.
  - Byte-for-byte, or an explicit allow-list of divergences with a reason attached to each. **No
    third option** — "roughly the same" is what got us here.
  - **Pin every input, not just the utterance and the registry.** `build_prompt` also takes
    `system_header` and `examples_block`, both of which come from the skill's `prompt.yaml` and
    both of which Q2 makes utterance-dependent. A case that fixes only `(utterance, registry)`
    silently depends on whatever `prompt.yaml` says that week, so an edit there moves the contract
    instead of failing it. Inline the overrides in the case file, or point cases at versioned
    `prompt.yaml` fixtures under `contracts/parity/`.
  - **Cases must cover the path-normalization divergence** — quoted Windows paths
    (`"C:\Users\me\clip.mp4"`), a bare `.\clip.mov`, and a backslash that is *not* a path, where
    the two implementations provably differ rather than merely might.
  - **Scope: this compares the logical `(system, user)` messages, not the final token IDs.** The
    two runtimes use different llama.cpp bindings and each applies the GGUF chat template
    independently. Identical messages are necessary and are what a no-GGUF contract can assert;
    they are not proof of an identical token sequence. Say so in the contract file, or the next
    reader will over-trust a green test — which is the same mistake as trusting the `prompt.rs`
    note that started this.
  - **A byte-for-byte fixture has to survive a Windows checkout.** Keep the expected prompts
    JSON-escaped inside the cases file rather than in sibling `.txt` goldens; if they ever move to
    their own files, pin them `text eol=lf` in `.gitattributes`. `* text=auto` has produced this
    exact bug three times already in this repo — see the `installers/licenses/**`,
    `site/data/*.json` and `*.ipynb` entries and the reasons recorded above each. A contract that
    passes in CI and fails on the maintainer's box is a contract people learn to skip.
- [x] **R2 — Retrieval-parity contract.** *(done 2026-08-09)*
  `contracts/parity/retrieval_cases.json` + tests on both sides, expectations generated from the
  Python reference.
  - **The tool *set* already agrees; the *order* does not** — confirmed by the contract, which
    fails with `["compress_video","gamma_tool","resize_video","strip_audio","trim_video"]`
    (alphabetical, from the `BTreeMap`) against Python's relevance order
    `["trim_video","resize_video","compress_video","strip_audio","gamma_tool"]`. Split into two
    tests so the set assertion passes today and the order assertion is an `#[ignore]`d target that
    turns green the moment Q1 lands. Verified it genuinely fails rather than passing vacuously.
  - Cases cover the `(score, name)`-**descending** tie-break, `min_score` filtering, `top_k`
    larger than the registry, CJK and diacritic queries, the empty query, and an internal tool
    carrying matching keywords so the exclusion cannot pass by accident.
  - `expected_always_include` is asserted as a **set**, deliberately: Python appends those by
    iterating a `frozenset`, whose order is not a language guarantee, and `build_prompt` filters
    them out before the model sees anything. Pinning that order would encode an accident.
  - Original R2 wording follows. Same utterance + registry → same selected tool set **and
  order**. Separable from R1 and worth its own cases: retrieval is scoring logic with tie-breaks,
  and it is where CJK tokenization and diacritic handling live. **Order is the load-bearing half**
  — finding 3 is precisely a case that passes a set comparison and fails a real one, so include
  cases with tied scores, where the `(score, name)`-descending tie-break is the only thing under
  test.
- [x] **R3 — Settings-parity contract.** *(done 2026-08-09)*
  `contracts/parity/generation_settings.yaml` declares the shipped configuration once;
  `python/core/tests/test_generation_settings_parity.py` asserts `models.yaml`, the promoted
  `eval_backends.yaml` stanza **and the Rust literals** all agree with it. Reading Rust source from
  a Python test is deliberate — the alternative is trusting that whoever edits one side remembers
  the other, which is exactly what failed when finding 4 was written from a superseded stanza.
  Mutation-tested: injecting `max_tokens: 2048` into `llama.rs` fails the suite with the intended
  message.
  - **Two corrections it forced.** `$KNAIF_MAX_TOKENS` is in `knaif-llm/src/lib.rs`, **not**
    `llama.rs` as the plan claimed. And native holds `max_tokens` **twice** — the struct default in
    `llama.rs` and the env fallback in `lib.rs` — so the shipped cap would depend on whether the
    variable happened to be set if they ever disagreed. Both are now asserted.
  - Original R3 wording follows. Assert the two runtimes' generation defaults agree —
  `max_tokens`, `n_ctx`, sampling, thinking suppression — reading each from the file that actually
  holds it. Had this existed, the original finding 4 would have been impossible to write: the test
  names its sources, so nobody can compare a live value against a superseded stanza.
- [ ] **R4 — Gate them in CI.** They belong in the existing `python` and `native` jobs rather than
  a new one; both already run on every change to `skills/` and `contracts/`.
  - **Both jobs are `ubuntu-latest` only**, so CI green is not evidence the contract holds where
    the work is being done. Run R1–R3 locally on Windows as part of P/Q before calling R done,
    and if the two disagree, that difference is itself a parity bug — fix it in the contract, do
    not skip the case. Widening the CI matrix to Windows is a bigger call than this plan should
    make; note the outcome in `docs/TODO.md` if it turns out to be warranted.
  - **Say what is actually exercised: Ubuntu in CI, Windows locally, and macOS not at all.** The
    path-normalization and line-ending cases are the ones with any platform surface, and neither
    is macOS-specific. Claiming three-platform coverage would be the same unmeasured assertion
    this plan is about — so the contract file states its coverage, and macOS stays an open gap
    rather than an assumed pass.

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
  preserved). Same GGUF, same verifiers.
- [ ] **S1b — Write the adapter, and be honest about where it cuts.** `plan --batch` emits a
  **validated plan and nothing else** (`main.rs:580`) — `{"plan": [...]}`, or
  `{"plan": [], "error": …}` for a failed line. The executing verifiers need much more:
  `run_corpus` gets there by calling `agent.execute_plan(...)` and deriving `artifact` (the
  rendered command string) and `artifact_path` from the results (`runner.py:170-202`), and
  `success` grades that string plus ffprobe on the produced file. So something must expand →
  optimize → resolve → execute each native envelope and build an `AgentOutput`.
  - **The honest option is Python-side execution, explicitly labelled.** The lane then measures
    *the native planner with Python execution* — not the shipped runtime end to end. Write that
    label into the lane's output and into `docs/NATIVE.md`; a number whose scope is implicit will
    be quoted as end-to-end parity within a month.
  - That is defensible precisely because it does not stand alone: `scripts/parity_check.py`
    already covers the deterministic native half (expansion and rendered argv). **State the
    division in both files** — planner quality here, deterministic port there — so the pair is
    legible as coverage rather than as two overlapping tools.
  - **Do not register the native lane under `backends:`.** `_make_agent` hands any entry there
    straight to `InferenceOrchestrator(backend=cfg["backend"], …)` (`evalsuite/cli.py:130`), so a
    `rust-cli` key is not inert — it is a token-generation backend that will be constructed and
    fail, or worse, half-work. Use a separate config section, or explicit lane-type dispatch
    before the orchestrator is reached. This is the same design finding C4 recorded, one level
    down: the config shape has to describe what the thing *is*.
- [ ] **S2 — Score the saved pre-fix run and the post-fix run with the same scorer**, and record
  both numbers. P2b is what makes this possible — without those envelopes there is no "before",
  and a parity lane whose first run is also its first green run has proved nothing. Grade both
  sides with the adapter built in S1b, so the delta is a change in plans and not a change in how
  plans were graded.
- [ ] **S3 — Sequencing is not optional.** R must land first. Until the prompt is pinned, a delta
  here cannot be attributed to a planner bug rather than to one side's prompt having been edited —
  which is the same unmeasured-divergence trap that produced this plan.

**It cannot run in CI**, either lane: both need a GGUF and `models/` is gitignored. Local tooling,
like `just parity` — and it runs on the operator's Windows box, so follow that recipe's shape:
resolve the binary through `{{EXE}}` and pass absolute paths, never a bare `knaif` off `PATH`.

**`scripts/parity_check.py` is the complement, not a duplicate** — its own docstring opens
"deliberately NOT an eval-suite". It diffs rendered argv per utterance; this compares scored
aggregates. Both are wanted.

---

## Definition of done

The same utterance, the same model and the same skill produce **the same plan on both runtimes**,
and the repo can prove it without anyone remembering to check:

- The native prompt is the Python prompt — same retrieved tools, **in the same order**, same
  selected examples — or every remaining difference is enumerated with a reason.
- A PR that changes one runtime's prompt, retrieval or generation settings without the other
  **fails CI**, with no model required.
- **Per-row plan parity is the acceptance criterion, not an aggregate.** Two runs can score
  identically while disagreeing on half the corpus in offsetting directions, so "±2% aggregate"
  proves less than it sounds like. Grade **row by row**, reusing
  `parity_check.py:523`'s `plan_equiv_modulo_defaults` as the equivalence relation — it already
  encodes the distinction this needs, treating native's materialized optional defaults as benign
  while keeping any difference in `_SIGNIFICANT_ARG_KEYS` (inputs, paths, outputs) a real
  divergence. Report the count of non-equivalent rows and enumerate them.
  - The aggregate score stays as a **secondary** number, with a tolerance stated as an absolute
    (e.g. "within 2 points of `eval_snapshot.json`"), because that is a quality check against the
    committed bar — a different question from parity, and worth not conflating with it.
- `docs/NATIVE.md` states the parity contract, what the eval lane does and does not cover
  (S1b: native planner, Python execution), and how to run both.

## Explicitly out of scope

- **Improving planning quality on either runtime.** This plan makes them agree. Making them both
  better is fine-tuning work (`docs/FINE_TUNING.md`).
- **Porting `history`-based re-planning to native.** A real gap, separately scoped; single-shot
  planning is what the corpus and the shipped path exercise.
- **Changing the prompt format.** Any change here must move both runtimes together, and the model
  is fine-tuned on the current shape — so this plan matches native to Python, never the reverse.

## Open questions

- **Is `top_k=5` right for native's larger skills?** Answer it with a measurement, after parity —
  not while fixing the gap, or the two changes become inseparable.
- **Does macOS need its own contract run?** Nothing in R exercises it (R4). The two cases with any
  platform surface — path normalization and line endings — have no macOS-specific behaviour, so
  the honest status is *unexercised*, not *passing*. Revisit if macOS packaging lands.

### Answered, moved out of this section

- **Does the fine-tuning data generator build prompts through `build_prompt`?** ~~Open.~~
  **Yes — confirmed 2026-08-08 by reading `python/training/build_dataset.py:132`**, which calls
  `retrieve_tools(utt, agent.registry)` then `agent.build_prompt(utt, registry_override=override)`
  for every training row. So the training distribution *is* pinned to the Python path, R1 protects
  the model's inputs as well as the runtime's, and its priority rises accordingly. It also settles
  finding 5: training prompts are in relevance order, not `tools.yaml` order.

---

## Recommended sequence

The dependencies are not obvious from the workstream letters, and two of them are one-way doors:

1. **P0** — prompt diagnostics. Nothing in P1–P3 is possible without it.
2. **P2b** — save the pre-fix corpus. *Irreversible if skipped;* Q destroys the baseline.
3. **P1–P4** — re-measure, then attribute factorially.
4. **R1–R3** — pin prompt, retrieval (with order) and settings. **Author the cases before Q**, so
   Q's change is *observed by a contract* rather than asserted. They will fail on today's code —
   that is the point, and it is the only moment the contract is proven to detect the bug it
   exists for. Land them red-but-skipped with the expected values written from Python's output,
   and un-skip in Q's PR.
5. **Q1–Q5** — port retrieval with ranked order, example selection, the rest.
6. **S1b** — the native-plan scoring adapter.
7. **S2** — score the saved pre-fix run and the current one with that same adapter.
8. **R4 / done** — gate in CI, apply the per-row acceptance criterion.
