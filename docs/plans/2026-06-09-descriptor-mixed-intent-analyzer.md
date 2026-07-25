# Descriptor / Mixed-Intent Analyzer (read-only)

**Status:** Done · **Created:** 2026-06-09 · **Completed:** —
**Owner:** eval / corpus · **Ref:** —

> **Status note:** Built (T1–T6 complete). Read-only analyzer — reads run scoreboards
> + corpus and writes a report file only; no runtime behaviour change.
>
> **Kept 2026-07-22** (S7 decision) as the design document for a live module:
> [`knaif/evalsuite/descriptor_analysis.py`](../../python/core/knaif/evalsuite/descriptor_analysis.py)
> cites this file by name in its docstring. The *why* below — why every cross-tab is
> computed twice, why the `fixture` is an honest proxy for a listing that does not
> exist — is not recoverable from the code.
>
> **Its two columns became two plans.** The **OFF** column (today's runtime) was
> promoted to the runtime clarify gate in
> [nl-clarify-gate](2026-06-09-nl-clarify-gate.md) (shipped, PR #14). The **ON**
> column is [context-injection](2026-06-09-context-injection.md) (Planned, parked).
> Start there for current behaviour; this plan is the record of how the split was
> *measured*.
>
> **Operator-facing summary** — the tool, its bins, and the corpus lint it produces —
> is in [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md#descriptor-rows-and-the-injection-assumption).
>
> **As-built differs from the spec below in two ways:**
> 1. **T1's resolver moved to core.** `resolve_input` / `classify_token` /
>    `parse_inline_filenames` live in
>    [`knaif/input_refs.py`](../../python/core/knaif/input_refs.py), not in the
>    analyzer — the "separate, later plan" anticipated under *Guardrails* is exactly
>    nl-clarify-gate, which needed the same primitives at runtime. The analyzer
>    imports them.
> 2. **Six token classes, not four.** The shipped classifier adds `stem`
>    (extension-less identifiers like `clip_4k`) and `none` (no path-bearing arg).
>    `stem` gets its own binning axis — `STEM_SAFE` / `STEM_RISK` / `STEM_CONFLICT` /
>    `STEM_OK` — because the deterministic stem resolver handles those tokens before
>    the clarify gate sees them. That axis found 8 STEM_CONFLICT rows on gemma3-4b
>    that the four-class taxonomy below cannot express.

**Goal:** Produce a read-only, row-level report of descriptor-bearing emissions
(split by whether the emitted input resolves against available files) to settle the
plan-vs-clarify corpus policy before touching core.

## Why

Step 2 ("input doesn't resolve → clarify") looked blocked on a corpus-policy
contradiction: the same descriptor (`"the mov"`, `"the 4K file"`) is treated as
`plan` in some rows and `clarify` in others. Before deciding policy or touching
core, we need the **exact row-level list** of descriptor-bearing emissions, split
by whether the emitted input *resolves against the available files*, per arm
(qwen, gemma). This analyzer produces that list. It is read-only and decides
nothing — it sizes the policy call precisely.

It also settles the open question from the Step 2 discussion: of the ~10 qwen
"RISK" rows (expected=plan, emitted a descriptor, currently correct), how many
become legitimate once a file listing is available vs are genuinely
under-specified. That split determines the sign of Step 2.

## Core design: standalone line + optional injected listing

Every inference is a **single standalone line**. No conversation history, no
cross-turn memory — we are **not** implementing conversation threading soon. This
matches the harness today: [runner.py:135-138](../../python/core/knaif/evalsuite/runner.py#L135-L138)
runs each utterance through `agent.infer(utterance)` as an independent string.

The standalone line *may* be augmented by **injected step output** prepended to
the prompt — the `[call ls -l] > user prompt` pattern. A pre-step runs (e.g.
lists the sandbox) and its output is prepended:

```
movie.mp4, audio.mp3        ← injected listing (output of a prepend step)
convert the audio file to flac   ← the user's standalone line
```

**No skill has such a step today**, so today the prompt is the bare user line.

The emitted input resolves against an **available-files set** with exactly two
providers:

| Provider | In the prompt today? | Modeled in analyzer by |
|---|---|---|
| **In-line filenames** — real filenames written in the standalone line itself (`"compress clip_4k.mp4"`) | yes | filename tokens parsed from `utterance` |
| **Injected listing** — files surfaced by a prepend step (`ls`-type) | **no** (no such step exists yet) | the row's `fixture` set — exactly what an `ls` of the sandbox would return |

Resolution rule:
```
resolves to exactly one available file → plan is legitimate
resolves to zero or ≥2 available files → should clarify
```

### Why the analyzer computes everything twice (injection OFF / ON)

The correct answer differs by world, so the report produces each cross-tab under
both, from the same run:

- **injection = OFF (today's reality):** available = **in-line filenames only**.
  A bare descriptor (`"the audio file"`, `"the 4K file"`) names nothing in the
  line → `resolves_none` → should clarify. This is the user's stated rule: *"if
  only 'convert the audio file to flac' is given, clarify should be the
  outcome."*
- **injection = ON (future, if a listing step is built):** available = in-line
  filenames ∪ injected listing (fixture proxy). `"the audio file"` against
  `movie.mp4, audio.mp3` resolves to the one audio → plan.

This is what "must work either way" means concretely. The fixture is the honest
proxy for the listing an injection step would produce, so we measure injection's
full effect **without the step needing to exist**. If injection never ships, the
OFF column is the truth; if it ships, the ON column is.

### The finding this structure exposes

The **mixed-intent rows** (ffmpeg_100 `"the 4K file"`, ffmpeg_227 `"the mov"`,
ffmpeg_109 `"the mov file"`) only resolve to `plan` under **injection ON**. Run
standalone with injection OFF — which is the harness today — they name no in-line
file, so they *should* be `clarify`, yet the corpus labels them `plan`. They are
implicitly assuming an injection that does not exist. The OFF/ON delta is exactly
the list of such mislabeled-for-today rows.

## Inputs (real, verified)

- Per-arm scoreboards (the `rows` array carries `id`, `utterance`,
  `expected_outcome`, `actual_outcome`, `plan`, `tags`):
  - `evals/_archive/v2/local6_success/ffmpeg_qwen3-4b_success.json`
  - `evals/_archive/v2/local6_success/ffmpeg_gemma3-4b_success.json`
  - (769 rows each = 275 corpus rows × their utterances, execute mode.)
- Corpus, joined by `id` for `fixture` (the injected-listing proxy):
  - `skills/ffmpeg/data/eval.jsonl`
- Run-dir is a required CLI arg, so the analyzer re-runs against any future run.
  (Two bit-rot fixes on 2026-07-22: the run-dir default pointed at `evals/v2/…`,
  which the archive pass had moved to `evals/_archive/v2/…`; and `print(md)` raised
  `UnicodeEncodeError` on a cp1252 console because the report contains `→` and
  multilingual utterances. The tool now runs clean against the archived local6 run and
  its output is deterministic across re-runs. Note the reports themselves are **not**
  version-controlled — `evals/**` is gitignored except for an allowlist
  (`INDEX.md`, `score.json`, `report.md`, `review_log.json`), and
  `descriptor_analysis.{md,json}` is not on it, so re-running overwrites them with no
  git copy to diff against.)

The analyzer takes a directory of `*_success.json` scoreboards (one per arm),
reusing `discover_arms` conventions where practical, and the corpus path.

## What it extracts per (arm, row, utterance)

1. **Emitted input token(s).** From `plan.plan[0].args`, read the path-bearing
   keys for the ffmpeg skill: `inputs` (list), `input` (str|list), `files`
   (list). Flatten to a list of string tokens. (Derive the key set from the
   skill's path-arg metadata if cheap; otherwise this fixed set, documented
   here.)
2. **Token classification** (first match wins). *As built this grew to six classes —
   `stem` and `none` were added during T2; see the status note.*
   - `chain` — starts with `$` (e.g. `$1`, `$prev`, `$step2.output`).
   - `glob` — contains any of `* ? [`.
   - `exact` — basename equals an available filename, OR has a known media
     extension (`.mp4 .mov .mkv .webm .avi .mp3 .flac .wav .m4a .aac .png .jpg
     .jpeg .gif .srt …`).
   - `descriptor` — anything else (free text: `"the 4K video"`, `"audio file"`,
     `"it"`, `"the clip"`).
3. **Resolution of `descriptor` tokens against the available-files set, computed
   under injection OFF and ON:**
   - **structural match** — token carries a type/extension cue
     (`audio|sound|music|track`→audio exts; `video|clip|movie|footage|film`→video
     exts; `image|photo|frame|thumbnail`→image exts; or a bare extension word
     `"the mov"`, `"the flac"`). Match available files whose extension is in the
     implied class.
   - **attribute match** — token contains a qualifier that is a substring of an
     available filename (`"4k"`→`clip_4k.mp4`, `"intro"`, `"final"`).
     Case-insensitive substring.
   - Union the matches; record **match count** (`0` / `1` / `≥2`) and **mode**
     (`structural` / `attribute` / `both`).
   - Label: `resolves_unique` (1), `resolves_ambiguous` (≥2), `resolves_none`
     (0). `attribute`-only matches are flagged `low_confidence` (the fuzzy,
     risk-bearing mode).

## Output report (the deliverable)

A markdown + JSON report (`<run-dir>/descriptor_analysis.{md,json}`) with, **per
arm**, the cross-tab rendered **twice (injection OFF and ON)**.

### A. Headline cross-tab (descriptor-input rows only; glob/chain/exact excluded)

Restricted to rows whose emitted input is a `descriptor`:

| expected → actual | resolves_unique | resolves_ambiguous | resolves_none |
|---|---|---|---|
| clarify → plan (miss) | **POLICY-CONFLICT** | PRIZE | PRIZE |
| plan → plan (correct) | SAFE | RISK | RISK |
| plan → clarify (miss) | (gemma omit pattern) | … | … |
| clarify → clarify (ok) | … | … | … |

- **PRIZE** = expected clarify, model planned with an unresolvable descriptor →
  a resolver would correctly downgrade to clarify.
- **RISK** = expected plan, currently correct, but the descriptor does **not**
  resolve → a resolver would wrongly clarify. The number to minimize.
- **SAFE** = expected plan, correct, descriptor **resolves uniquely** → resolver
  keeps it as plan.
- **POLICY-CONFLICT** = expected clarify, but the descriptor **does** resolve
  uniquely against the available set. The corpus and the rule disagree. The
  one-time policy decision applies to exactly this finite, named set.

Reading the two worlds together is the point:
- A row in **RISK under OFF** but **SAFE under ON** is mislabeled-for-today: it
  expects plan, but only injection makes that legitimate. It should be `clarify`
  today (or its utterance should name the file). This is the mixed-intent class.
- A row in **PRIZE under OFF** but **POLICY-CONFLICT under ON** is where the
  policy call actually bites: the gate fixes it today, but injection would make
  the model's plan legitimate.

### B. Row-level appendix

For every descriptor row: `id`, `utterance`, `utt_idx`, emitted token(s),
classification, match count/mode, OFF-label, ON-label, expected, actual, bin
(OFF), bin (ON). Sorted by bin then arm. This is what we re-bin / split from (the
ffmpeg_082 procedure generalized).

### C. Injection delta summary

For each arm: counts that change bin between OFF and ON, and the specific
mislabeled-for-today list (RISK-under-OFF → SAFE-under-ON). Tells us how much of
the corpus's `plan`-expected descriptor set is silently assuming injection.

### D. Sanity / eval-correctness cross-check (folds in the deferred investigation)

- Confirm `actual_outcome` derivation: spot-check that rows binned as
  `plan→clarify` are genuine model clarifies, not runtime downgrades miscounted
  as clarify ([runner.py:164-178](../../python/core/knaif/evalsuite/runner.py#L164-L178)).
- Confirm warmup/fixture timing isn't dropping or duplicating utterances
  (`_mark_warmup`, fixture existence guard).
- Report any row where `actual_outcome=="error"` with a descriptor input
  (would otherwise hide in neither prize nor risk).

## Tasks

- [x] **T1 — Resolver core (pure, tested first).** `resolve_input(token,
  available_files) -> Resolution{count, mode, label, low_confidence}`. TDD:
  failing table-driven test first (structural, attribute, ambiguous, none,
  glob/chain/exact pass-through) → then implement. In
  `python/core/knaif/evalsuite/descriptor_analysis.py`, tested in
  `python/core/tests/test_descriptor_analysis.py`.
  **As built:** the resolver landed in core `knaif/input_refs.py`
  (`python/core/tests/test_input_refs.py`) so `nl_clarify_gate` could share it;
  only the binning and report stayed in `evalsuite`.
- [x] **T2 — Token extraction + classification.** `classify_emitted_input(plan,
  available) -> [TokenClass]`. Failing test first against real plan args
  (`{"inputs": ["clip.mp4"]}`, descriptor, glob `*.mp4`, chain `$1`).
- [x] **T3 — Available-files builder (two worlds).** `available_files(row,
  utterance, injection: bool) -> set[str]`: in-line filenames parsed from the
  utterance (always) ∪ `fixture` set (only when `injection=True`). Test the
  in-line filename parser and the OFF/ON difference.
- [x] **T4 — Join + binning.** Load scoreboards + corpus, join by `id`, match
  utterance→utt_idx by string, emit the A/B/C/D structures under OFF and ON.
  Test on a small synthetic scoreboard.
- [x] **T5 — Report writer + CLI.** `python -m
  knaif.evalsuite.descriptor_analysis <run-dir> [--corpus …]` → writes
  `descriptor_analysis.{md,json}`. No code in `python/core/knaif/` core; lives under
  `evalsuite`.
- [x] **T6 — Run on local6, review the bins.** Produce the numbers under OFF and
  ON, compare PRIZE/RISK to the earlier 36/6 and 10/7 figures, and read off the
  SAFE-vs-RISK split, the POLICY-CONFLICT list, and the injection delta.

## Open policy questions this analyzer answers (does not decide)

1. The finite, named **POLICY-CONFLICT** set: for those rows, does
   `descriptor → clarify` or `descriptor → plan`? (One-time call, made against a
   list, not in the abstract.)
2. Whether the qwen "RISK" rows are mostly **mislabeled-for-today** (legitimate
   only under injection) or genuinely under-specified → tells us whether Step 2
   is "add a gate" or "re-bin the corpus to clarify".
3. How much of the `plan`-expected descriptor corpus is silently assuming an
   injection step that does not exist (the OFF/ON delta).

## Guardrails / non-goals

- Read-only. No corpus mutation, no core changes, no gate. Output is a report.
- Standalone line only — no conversation threading is modeled or assumed.
- The injected listing is modeled by the `fixture` proxy; the analyzer does not
  require any prepend step to exist.
- T1's resolver is analysis-only here; promoting it to a runtime clarify-gate is
  a *separate*, later plan whose sign this report establishes.
  **That plan is [nl-clarify-gate](2026-06-09-nl-clarify-gate.md), shipped** — the
  resolver is now shared core, but this module still only writes a report.
- `glob` and `chain` tokens are always excluded from the prize/risk cross-tab
  (already-resolved / not descriptors) — but counted separately so we confirm
  the ~32 qwen / ~29 gemma glob-plan rows are untouched.
```
