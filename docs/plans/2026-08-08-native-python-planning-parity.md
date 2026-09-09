# Native/Python planning parity — the prompt gap, its contracts, and the eval-parity lane

**Status:** Active — not started · **Created:** 2026-08-08 ·
**Revised:** 2026-08-08 (audit), 2026-09-09 (decisions) · **Completed:** —
**Owner:** core · **Ref:** absorbs **C4** from
[post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md); complements
`scripts/parity_check.py`

> **Status note:** Still not started — no code has moved. What changed on 2026-09-09 is that the
> eight decisions left open inside the workstreams are **taken**, so P0 can start without stopping
> to settle them mid-flight. They are collected in *Decisions taken* below and inlined at the item
> each one governs. Two questions remain open, both deliberately deferred: `top_k` for larger
> skills (answer after parity) and macOS contract coverage (unexercised, not passing).

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
quoted Windows paths, and backslashes that are not paths. **Decided 2026-09-09 — native adopts
Python's rule; see Q5.**

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

## Decisions taken (2026-09-09)

Eight decisions were left open inside the workstreams — some flagged as such, some only visible as
a "decide" verb in a bullet. They are settled here so no one has to stop mid-workstream to make
them, and each is repeated at the item it governs, with the alternatives that were rejected.

| Item | Decision |
|---|---|
| **Q5** | Path normalization: **native adopts Python's `_PATH_TOKEN_RE` rule** |
| **R1** | Prompt-parity contract is **byte-for-byte, with no allow-list** |
| **R4** | **Ubuntu in CI, Windows run locally**; matrix not widened; macOS stays a stated gap |
| **P2b** | Baseline is the **committed `plan --batch` envelopes**, not an archived binary |
| **Q3** | `max_tokens` canonical copy lives in **`contracts/runtime/`** |
| **S1b** | Native lane gets a **separate top-level config section**, never under `backends:` |
| **DoD** | Secondary aggregate tolerance: **within 2 points of `eval_snapshot.json`** |
| **Q1** | `retrieve_tools` returns **`Vec<(String, &'a ToolDef)>`** |

**Two of these are coupled, and the coupling is now load-bearing.** R1 byte-for-byte with no
allow-list means Q5 is a *prerequisite* rather than cleanup: there is no way to record the
path-normalization divergence as permitted, so R1 cannot go green until it is converged. That is
the intended shape — it removes the option that produced this plan — but it also means a
divergence found in P1 that resists convergence reopens R1 rather than slipping into a list.

**Q3 changes R3's job** rather than merely relocating a constant; see R3.

Still open, deliberately: `top_k` for larger skills, and macOS contract coverage. Both are in
*Open questions* with the reason each is deferred.

## Workstream P — Diagnose before fixing

- [x] **P0 — Build the prompt dump first. There is currently no way to do P1.** **Done
  2026-09-09** — `$KNAIF_DUMP_PROMPT` (an env gate, not a `--dump-prompt` flag: the prompt is
  built inside `PlanSession::plan`, which `plan`, `plan --batch` and `run` all share, so one gate
  covers every path including the batch capture R1 needs). Writes the framed `(system, user)` to
  **stderr**, leaving stdout's JSON envelope machine-readable. `prompt_dump` is pure with the gate
  as a parameter, mirroring the existing `debug_dump`; three tests, one of which asserts both
  messages survive **byte for byte** through trailing spaces, blank lines, a lone CR, tabs and
  non-ASCII — the property P1 and R1 depend on.
  `$KNAIF_DEBUG` does **not** print the prompt: its only caller is the failure path, and it emits
  the raw model output plus extracted JSON on a parse/validation error (`main.rs:1084`, `1114`,
  `1129`). On a *successful* plan — which is most of the corpus, and the interesting case — it
  prints nothing at all. Add a real prompt-dump: a `--dump-prompt` flag on `plan`, or an env gate
  that writes `(system, user)` to stderr before inference at `main.rs:1051`. Keep it a **plain
  read-only dump**, not a formatter — anything that reshapes the string makes the P1 diff a diff of
  the dumper.
  - This lands in `apps/cli` ahead of Q and stays afterwards: R1 needs a way to produce the
    native side of a golden, and a contributor debugging parity needs the same thing.
- [x] **P1 — Reproduce, with both prompts captured verbatim.** **Done 2026-09-09** — see
  *P1/P2 outcome* below. The diff is two hunks and nothing else. A fixed utterance set including at
  least three known-good multi-step cases from `skills/ffmpeg/data/eval.jsonl`. Dump both prompts
  for the same utterance and diff them. **The diff is the deliverable** — every later claim rests
  on it.
  - Write both captures with `newline="\n"` / `eol=lf` before diffing. A PowerShell redirect gives
    CRLF and a UTF-16 BOM, which turns every line of the diff red and hides the real one.
  - Diff the **logical `(system, user)` messages**. Neither runtime's dump is the final token
    sequence — see R1's scope note.
- [x] **P2 — Quantify.** **Done 2026-09-09** — numbers in *P1/P2 outcome* below. Reported as
  characters and whitespace tokens, **not BPE tokens**: `transformers` is not in the default env
  and an exact count needs the GGUF tokenizer. The ratio is what matters here and both proxies
  agree on it (1.7–1.8x chars, 1.6x whitespace tokens); take a real token count before quoting
  one against `n_ctx`.
- [ ] **P2b — Save the pre-fix corpus run before touching anything.** S2 promises a
  before-and-after across a real gap, and Q destroys the "before". Run `knaif plan --batch` over
  the full corpus on today's binary and commit the envelopes under `evals/parity/`.
  **This is the only irreversible step in the plan** — after Q lands, no amount of care
  reconstructs it, and S2 degrades to a single green number that proves nothing.
  - **Decided 2026-09-09 — commit the envelopes, do not archive the binary.** The JSONL is
    diffable, reviewable in a PR, survives a toolchain or driver change, and is exactly what S2
    re-scores. Archiving the built binary would keep the ability to re-run *arbitrary* future
    utterances, but it rots against llama.cpp and driver changes and cannot be read in review — a
    baseline nobody can inspect is not a baseline.
### P1/P2 outcome (2026-09-09) — measured, on this box

Four utterances: three `chain3` rows from `skills/ffmpeg/data/eval.jsonl` (`ffmpeg_hard_001`,
`_002`, `_005` — the multi-step cases the symptom is about) plus one single-step control. Native
captured through P0's dump on the **mock** backend (the prompt is built before inference, so no
GGUF is needed to compare prompts); Python through
`agent.build_prompt(utt, registry_override=retrieve_tools(utt, agent.registry))`, the call the
eval runner makes at `evalsuite/runner.py:110`.

| | Python | Native |
|---|---|---|
| tool **definitions** in the prompt | **5** | **13** |
| `TOOL SCOPE` header names | 13 | 13 |
| example plans | **6** | **29** |
| system-message chars | 7 831 – 8 105 | **13 994 (constant)** |
| whitespace tokens | 1 123 – 1 149 | 1 806 – 1 816 |
| system prompt varies by utterance | **yes** (4 distinct hashes) | **no** (1 hash, all four) |
| user message | — | **identical to Python's** |

**The hypothesis is confirmed, and the divergence is narrower than feared.** The user message is
byte-identical on both sides; *every* difference is in the system message, and it is **exactly two
diff hunks** — the `Available tools:` block and the examples block. Those are precisely the two
ported-but-unwired features (Q1, Q2). No third divergence appeared.

**Two corrections to this plan's own framing, both from executing the read:**

1. **"13 model-visible tools against Python's 5" is the wrong unit.** The static `TOOL SCOPE`
   header from `prompt.yaml` enumerates **all 13 tool names on both runtimes**, retrieval or not.
   What retrieval changes is which tools carry a **definition** (description + arg list): 5 vs 13.
   So the model always sees 13 *names*; Python shows it 5 *schemas*. The gap is real and still
   2.6x — quote it as definitions, not visibility.
2. **The examples gap is bigger than the tool gap and was never quantified: 6 vs 29 example
   plans.** Finding 2 called it "static instead of chosen per utterance" without a number. Native
   carries ~5x the examples, and that hunk is the same size as the tool hunk (+43 vs +42 lines).
   Q2 is not the junior partner of Q1.

**A third fact, not previously stated anywhere:** native's system prompt is **byte-identical
across all four utterances** (one md5). It is not merely unranked — it is utterance-invariant.
Whatever the model is asked, it gets the same 13 schemas and the same 29 examples, while the model
was fine-tuned on a prompt that changes with every utterance.

**Not yet established:** that any of this *causes* the multi-step failure. That is P3, and it
needs the GGUF. Captures, diffs and `p2_quantified.json` are reproducible with the P0 gate; the
capture harness is scratch tooling, not committed.

- [ ] **P3 — Attribute factorially, not one-at-a-time.** Retrieval and example selection are
  *coupled* in Python (finding 2: the example filter only fires when a `registry_override` is
  passed), so testing them singly cannot separate them. Run all four cells:

  | | static examples | selected examples |
  |---|---|---|
  | **full registry** | today's native | — |
  | **retrieved registry** | — | Python-equivalent |

  Fill both `—` cells too; they are the whole point. Add tool *order* as a fifth cell if the four
  do not settle it — relevance order vs `tools.yaml` order, holding the tool set fixed.
  - **Do not run the `max_tokens=2048` experiment.** Finding 4: both sides are already 512, so it
    varies nothing. If output length is ever suspected, first check whether any plan actually
    reaches the 512 cap — if none does, the cap is not in the causal path at all.
  - If no cell restores multi-step plans, **stop and re-diagnose.** Do not proceed to Q on a hunch.
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
  re-sorts by `def.order`. Change the return to an ordered type and have `build_prompt` emit in
  that order when a retrieved subset is supplied.
  - **Decided 2026-09-09 — return `Vec<(String, &'a ToolDef)>`**, mirroring Python's
    insertion-ordered dict directly. The alternative was a `Vec` of names alongside the existing
    map, which keeps two structures a later edit can desynchronize — the same class of defect as a
    rank discarded at a return.
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
  - **Decided 2026-09-09 — the canonical copy lives in `contracts/runtime/`**, read by both
    runtimes, synced by `just sync-runtime` and held by a drift-guard test, exactly as
    `core_tools.yaml` already is. Making `models.yaml` canonical was the alternative: rejected
    because it couples a *runtime* default to model resolution and still leaves
    `eval_backends.yaml` holding its own copy. This changes what R3 asserts — see there.
- [ ] **Q4 — Fix both stale notes in `prompt.rs`** — the module docstring's "alphabetical because
  `BTreeMap`" (false: it sorts by `def.order`) *and* the `def.order` comment's claim that the
  fine-tuned model was trained on that order (finding 5: training prompts are in relevance order).
  State which order is canonical after Q1 and why, and re-state which divergences remain
  intentional, if any survive Q1–Q3.
- [ ] **Q5 — Converge `normalize_path_separators` on Python's rule. Decided 2026-09-09.**
  Not ruled out (see above): native rewrites every backslash, Python only path-shaped tokens.
  **Native adopts Python's rule** — port `_PATH_TOKEN_RE` (`prompt.py:27`) to Rust and rewrite only
  tokens that match it, replacing the blanket replace at `main.rs:1142`.
  - **Why this direction.** It is the rule the plan already commits to (*Explicitly out of scope*:
    match native to Python, never the reverse), and the shipped model is fine-tuned on
    Python-shaped prompts — so a backslash native rewrites and Python would have left alone is
    off-distribution input to the model.
  - **Rejected:** moving Python to the blanket replace (changes the trained-on prompt and
    invalidates the current eval bars), and keeping both behind a documented divergence (exactly
    the accepted-but-unmeasured shape this plan exists to close).
  - **This is now a prerequisite for R1, not cleanup** — R1 is byte-for-byte with no allow-list,
    so there is nowhere to record this divergence as permitted.
  - The decision is a line of code **and** a contract case (R1), not a docstring assertion —
    which is what got us here.

## Workstream R — Contracts, so it cannot drift silently again

**This is the durable half.** Q fixes today's gap; R is what stops the next one. All of it is
deterministic and needs **no GGUF**, so unlike C4 it can gate every PR in CI.

- [ ] **R1 — Prompt-parity contract.** Fixed utterances × fixed registries → both runtimes must
  produce the **same prompt string**. Extend `contracts/parity/` in the shape
  `planner_cases.json` already uses, consumed by a Python test and a Rust test.
  - **Byte-for-byte, with no allow-list. Decided 2026-09-09.** The allow-list variant was the
    alternative and is deliberately not taken: entries in such a list grow quietly, and without one
    a divergence either fails CI or does not exist. **This makes Q5 a prerequisite rather than
    cleanup** — with no escape hatch, R1 cannot go green until path normalization converges.
  - **If P1's diff surfaces a divergence that resists convergence, that reopens this decision**
    rather than being waved through: the escape hatch has been spent, so re-deciding in the open
    is the only honest move. Record the outcome here if it happens.
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
- [ ] **R2 — Retrieval-parity contract.** Same utterance + registry → same selected tool set **and
  order**. Separable from R1 and worth its own cases: retrieval is scoring logic with tie-breaks,
  and it is where CJK tokenization and diacritic handling live. **Order is the load-bearing half**
  — finding 3 is precisely a case that passes a set comparison and fails a real one, so include
  cases with tied scores, where the `(score, name)`-descending tie-break is the only thing under
  test.
- [ ] **R3 — Settings-parity contract.** Assert the two runtimes' generation defaults agree —
  `max_tokens`, `n_ctx`, sampling, thinking suppression. Had this existed, the original finding 4
  would have been impossible to write: the test names its sources, so nobody can compare a live
  value against a superseded stanza.
  - **Q3's decision changes what this test does.** With one canonical copy in `contracts/runtime/`
    there are no longer three values to compare, so R3 becomes *"both runtimes read the canonical
    file"* — a weaker assertion over a stronger invariant. **Write it that way deliberately;**
    porting the three-way comparison onto a single source yields a test that can only ever pass.
  - Keep at least one case that reads each runtime's *effective* value at the point of use
    (`llama.rs`'s default, the Python orchestrator's), so a hard-coded fallback shadowing the
    contract file still fails the gate.
- [ ] **R4 — Gate them in CI.** They belong in the existing `python` and `native` jobs rather than
  a new one; both already run on every change to `skills/` and `contracts/`.
  - **Both jobs are `ubuntu-latest` only**, so CI green is not evidence the contract holds where
    the work is being done. Run R1–R3 locally on Windows as part of P/Q before calling R done,
    and if the two disagree, that difference is itself a parity bug — fix it in the contract, do
    not skip the case.
  - **Decided 2026-09-09 — Ubuntu in CI, Windows exercised locally, matrix not widened.** Adding
    Windows runners was the alternative and is not taken: it changes two existing jobs and buys
    runner minutes for cases the operator already runs by hand on the box where the work happens.
    **The load-bearing condition is that R4 is not done until R1–R3 have actually been run on
    Windows** — this decision rests entirely on that discipline, so skipping the local run makes
    the coverage claim false rather than merely thin. Revisit, and note it in `docs/TODO.md`, if a
    Windows-only divergence ever reaches a release.
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
    fail, or worse, half-work. This is the same design finding C4 recorded, one level down: the
    config shape has to describe what the thing *is*.
  - **Decided 2026-09-09 — a separate top-level config section** (e.g. `lanes:`), not a `type:`
    discriminator inside the existing list. A distinct key states at the config level that this is
    a whole-pipeline binary rather than a token-generation backend; a discriminator still lets a
    mistyped entry reach code that assumes a backend, which is the failure being designed out.
    Keeping the lane out of the eval config entirely was considered and rejected — it forfeits the
    shared `--save` / `evals/INDEX.md` plumbing.
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
  - The aggregate score stays as a **secondary** number, because that is a quality check against
    the committed bar — a different question from parity, and worth not conflating with it.
    **Decided 2026-09-09: within 2 points of `eval_snapshot.json`**, stated as an absolute. That
    sits inside the noise floor of the re-locked `success` bars (ffmpeg 0.902 outcome, documents
    0.976). A 1-point bound was rejected as tight enough to turn benign variation into a blocker
    on a number that is explicitly *not* the acceptance criterion.
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
