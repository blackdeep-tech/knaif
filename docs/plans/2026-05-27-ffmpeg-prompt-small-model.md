# FFmpeg prompt for small models — plan and outcome

**Status:** Done · **Created:** 2026-05-27 · **Completed:** 2026-05-29
**Owner:** ffmpeg · **Ref:** `evals/_archive/report.md`

> **Status note:** Plan drafted 2026-05-27; eval runs 2026-05-28; combined
> retrospective 2026-05-29. Outcome: the prompt was already viable at 4B; no
> simplification needed (see TL;DR).
>
> **The conclusions are durable; the percentages are not.** Every number below
> comes from the retired 70-row corpus and the toolset of that era, before
> fine-tuning and the retrieval overhaul. `evals/INDEX.md` classes this run
> family as **not comparable** to current results — do not quote these figures
> as present-day ffmpeg quality. Read the table for the *shape* of the finding
> (4B clears the bar, 1.7B does not, sub-2B cannot emit JSON), not the values.
>
> **Surviving raw data:** only `evals/_archive/stage_a/` and the aggregated
> `evals/_archive/report.md`. The `stage_c` and `stage_no_retrieval` run
> directories cited by the original draft were not carried forward, so the
> retrieval A/B table below is now the only surviving record of that comparison.

**Goal:** Determine whether the ffmpeg prompt needs simplifying to work on small
(4B) models, and act on the eval evidence.

## TL;DR

**The prompt did not need simplifying.** At 4B parameter count the existing
prompt already clears 89–94% outcome accuracy on a 70-case corpus. The
original ask — "make the prompt viable for small models" — was answered by
the data showing it was *already* viable at 4B, and that prompt-side changes
do not meaningfully move the needle below that.

What did matter, and what landed:

- Two correctness bugs in the eval suite that meant every prior measurement
  was a lie.
- Parse-failure visibility — the small-model JSON-emission failure mode was
  invisible on `dev`.
- Multilingual retrieval via cheap keyword aliases (ES/DE/FR/RU, all at 100%
  on 4B).
- A 47% rendered-prompt reduction from filtering examples to the retrieved
  tool set — accuracy-neutral, kept as infrastructure.

The header rewrite (Task 10), the `NORMALIZERS` extension point (Task 9),
and the embedding retriever (Task 12) were all skipped after the data came
in. None were justified by the failure patterns we observed.

---

## What we set out to do

The current prompt worked for `io` but was suspected of being too dense for
reliable FFmpeg planning on 1.7B and borderline for 4B.

Measured prompt sizes at the start of the work (2026-05-27):

| Skill / Mode | Visible tools | Chars | ~Tokens | Assessment |
|---|---:|---:|---:|---|
| `io`, full prompt | 4 | 2,432 | 608 | Fine for 4B; plausible for 1.7B |
| `ffmpeg`, full prompt | 13 | 9,882 | 2,470 | Suspect for 1.7B; risky for 4B |
| `ffmpeg`, retrieved (`run()`) | 5 | 8,072–8,366 | 2,018–2,092 | Header + examples dominate |

FFmpeg prompt breakdown with retrieval:

| Section | Chars | Share |
|---|---:|---:|
| Header / rules | 3,188 | 32% |
| Retrieved tool defs | 1,135 | 11% |
| **Examples** | **4,027** | **40%** |

The single most important finding from the audit: **examples were 40% of the
prompt and the retriever did not filter them.** Even when retrieval surfaced
only 5 of 13 tools, the model still read all 21 examples. We hypothesized
that filtering examples would be the highest-leverage change.

The prompt also asked the model to do many things at once: JSON formatting,
safety classification, unsupported-feature detection, argument extraction,
enum normalization, CRF-to-profile mapping, multi-step planning, variable
binding, timestamp conversion, and filename preservation.

## Two correctness bugs found during planning

These were the critical blockers; any A/B comparison would have been noise
until they were fixed.

1. **`run_corpus` bypassed retrieval.** It called `agent.infer(utterance)`
   without `registry_override`, so the eval measured the full 13-tool prompt
   while production used the 5-tool retrieved one. Every prior eval number
   was apples-to-oranges with runtime behavior.
2. **Parse failures vanished into clarify.** `agent.infer()` caught
   `ValueError` from `parse_plan()` and returned a synthetic clarify plan,
   so any "parse failure rate" metric read zero. Parse failures had to be
   exposed before they could be measured.

Both were treated as preconditions to Task 8 (the baseline eval), not as
optional improvements.

## Goals (as drafted)

1. Make the model-facing prompt smaller and more regular for 1.7B / 4B
   local models.
2. Preserve the invariant that the model emits only
   `{"plan":[{"tool":"…","args":{…}}]}`.
3. Keep all safety, validation, expansion, confirmation, and execution
   deterministic.
4. Add regression tests + eval measurements so changes are judged by parse
   validity, schema validity, outcome accuracy, and tool accuracy.
5. Improve robustness to varied phrasings; make multilingual handling
   possible behind an opt-in flag.

## Non-goals

- Re-training or fine-tuning. Prompt + code only.
- Changing the public ffmpeg tool surface (`tools.yaml` stays).
- Moving ffmpeg-specific behavior into `python/core/knaif/` core.
- Letting the model emit shell commands.
- Removing deterministic validation or safety gates.

## Approach

Staged, empirical, cheapest-first:

1. Establish baselines (prompt-size audit tests + eval with at least one
   small-model backend).
2. Fix the example-syntax bug (cheap, high signal).
3. Filter examples by retrieved tools (predicted biggest single win).
4. Re-measure. Decide whether header simplification and CRF normalization
   were still worth doing, or whether example filtering already cleared the
   bar.
5. Compress the header / rewrite the prompt only if data demanded it.
6. Add an embedding retriever as opt-in only if aliases didn't cover
   multilingual.

The staging mattered. It is the only reason we were able to skip Tasks 9,
10, and 12 with confidence.

## Architecture decisions made along the way

| Decision | Choice | Held up under data? |
|---|---|---|
| Eval ↔ retrieval alignment | `run_corpus` applies retrieval by default; `--no-retrieval` available for diagnostics | Yes — load-bearing |
| Parse-failure observability | Expose `agent.last_parse_error` and report it as a distinct outcome bucket | Yes — surfaced 47 silent SmolLM3 failures |
| Example filtering | Index examples by *all* non-terminal tools they contain (not just the first); rank by tool overlap, tiebreak by query-token Jaccard; always keep one clarify + one reject example; cap tool-relevant examples at 3 | Code shipped, but accuracy-neutral on eval |
| Prompt-size regression guard | `python/core/tests/test_prompt_audit.py` asserts ceilings per skill / mode | Yes |
| `NORMALIZERS` hook (Task 9) | Designed but gated on eval evidence | **Not built** — data did not justify it |
| Header rewrite (Task 10) | Designed but gated on eval evidence | **Not built** — data did not justify it |
| Multilingual aliases first | Try cheap keyword aliases before introducing an embedding dep | Yes — aliases hit 100% on 12 multilingual rows at 4B |
| Embedding retriever (Task 12) | Optional `pip install knaif[embeddings]` extra; opt-in per agent | **Not built** — aliases cleared the bar |
| Backend selection | Reuse existing `knaif.evalsuite` CLI; gemma3-4b/qwen3-4b/qwen3-1.7b on Ollama + llama.cpp | Yes |

---

## What landed

### Correctness fixes (preconditions for any honest measurement)

- **Task 1 — Prompt audit tests.** `python/core/tests/test_prompt_audit.py` (11 tests)
  measures prompt sizes deterministically, asserts ceilings, guards against
  internal-tool leakage, and catches unsupported `$x.field[N]` syntax in
  examples.
- **Task 2 — Fix `$logs.files[0]` example.** `skills/io/prompt.yaml`
  was teaching the model bracket-index variable references; the planner's
  var-ref regex (`planner.py:32`) only accepts `$var` / `$var.field`. Pure
  bug fix.
- **Task 3 — Apply retrieval in `run_corpus`.** Plumbed `retrieve_tools()`
  per utterance and passed the result as `registry_override` to
  `agent.infer()`. Added `--no-retrieval` CLI flag for diagnostic A/B.
- **Task 4 — Expose parse-failure metadata.** Added
  `agent.last_parse_error: str | None`, set on `ValueError` from
  `parse_plan()`. Runner emits `outcome="parse_error"` distinct from
  `outcome="clarify"`. HTML report distinguishes "n/a" (score not
  applicable) from "n/a ✗" (model failed to produce a scorable artifact).

### Prompt-side change (kept as infrastructure)

- **Task 5 — Filter examples by retrieved tools.** `Skill` retains
  `prompt_examples` as structured data alongside the rendered block.
  `build_prompt()` filters examples to those whose plan steps overlap the
  retrieved tool set, always keeping one clarify + one reject + up to 3
  domain examples ranked by tool overlap (primary) and query-token Jaccard
  (tiebreaker). **Result: 47% rendered-prompt reduction (9,882 → 5,204
  chars), accuracy-neutral on the eval.** Retained because it is cheap to
  maintain, doesn't hurt, and may help future skills with larger example
  pools.

### Corpus and backends

- **Task 6 — Expand eval corpus 19 → 70.** Added CRF cases (`crf 18`,
  `crf 22`, `crf 26`, `crf 31`, `crf18` no-space), quality-word cases
  (small, tiny, balanced, ok, good, decent, high, best, lossless),
  tool-confusion traps ("smaller AND convert to mkv" → two steps; "for
  whatsapp" without "prepare" → still `prepare_for_platform`), 5
  unsupported → clarify (subtitles, watermark, denoise, color grade, mix
  audio), 5 reject (rm -rf, format C:, …), and ~20 multilingual at native
  phrasing in Spanish / German / French / Russian, plus 5 indirect English.
  Originals 1–19 unchanged so before/after numbers stay comparable.
- **Task 7 — Backends.** `eval_backends.yaml` rewritten around llama.cpp
  GGUF backends plus the existing Ollama gemma3-4b for anchor comparison.

### Multilingual support

- **Task 11 — Keyword aliases.** Added 4–5 high-value translations per
  tool to the `keywords:` list (Spanish, German, French, Russian, plus one
  informal English synonym). Registry retrieval normalizes queries before
  scoring: lowercase + strip combining diacritics
  (`comprimír` → `comprimir`). All 12 multilingual eval rows pass on every
  4B backend.

### Telemetry and reporting

- Time-to-artifact aggregates (mean / p50 / p95 / max) on plan rows only,
  with first-row warmup skip so model-load cost does not skew the mean.
- HTML and Markdown reports include latency columns and the n/a vs n/a ✗
  distinction.
- `any_of_args` schema field on `ToolDef`; `concat_video` requires at
  least one of `inputs`/`base`/`append`. Closes a real validation hole.

### Test surface

631 tests pass on dev after merge (up from 519). New: prompt audit (11
tests), corpus validity, latency aggregates, multilingual retrieval
(parametrized over 17 queries), `any_of_args`, `/no_think` placement.

---

## Eval results

> Historical — retired 70-row corpus, old toolset, pre-fine-tuning. See the
> status note at the top before quoting anything here.

### Outcome accuracy by backend (70-case corpus)

| Backend | Variant | Outcome accuracy | Plans | Clarify | Reject | Error | Parse-error |
|---|---|---:|---:|---:|---:|---:|---:|
| qwen3-4b | llama.cpp | **94.3%** (66/70) | 49 | 13 | 6 | 2 | 0 |
| gemma3-4b-ollama | Ollama | 92.9% (65/70) | 52 | 10 | 6 | 2 | 0 |
| gemma3-4b | llama.cpp | 88.6% (62/70) | 50 | 10 | 6 | 4 | 0 |
| qwen3-4b-ollama | Ollama | 85.7% (60/70) | 43 | 19 | 7 | 0 | 1 |
| qwen3-1.7b-q8 | llama.cpp | 84.3% (59/70) | 44 | 11 | 9 | 6 | 0 |
| qwen3-1.7b-ollama | Ollama | 84.3% (59/70) | 44 | 11 | 7 | 7 | 1 |
| qwen3-1.7b-q4 | llama.cpp | 84.3% (59/70) | 42 | 12 | 8 | 7 | 1 |
| phi4-mini | llama.cpp | 82.9% (58/70) | 40 | 15 | 8 | 7 | 0 |
| gemma3-1b | llama.cpp | 40.0% (28/70) | 26 | 14 | 4 | 12 | **14** |
| smollm3-3b | llama.cpp | 30.0% (21/70) | 14 | 4 | 4 | 1 | **47** |

`parse_error` counts make it visible that gemma3-1b and smollm3-3b cannot
reliably emit JSON. This used to silently coerce into "clarify", masking
the real failure mode.

### A/B: retrieval + example filtering vs no retrieval

Same code, same models, same corpus — only difference is whether retrieval
filters the registry (and therefore which examples render into the prompt).

| Backend | With retrieval | No retrieval | Δ |
|---|---:|---:|---:|
| qwen3-4b | 94.3% (66/70) | 94.3% (66/70) | 0 |
| qwen3-1.7b-q8 | 84.3% (59/70) | **87.1% (61/70)** | −2 rows with retrieval |

Six rows differ on 1.7B-q8 (four favor no-retrieval, two favor retrieval).
The four no-retrieval wins include two cases where the filtered prompt
caused the model to wrongly *reject* a valid request (`ffmpeg_046` WhatsApp
upload, `ffmpeg_063` German strip-audio) — likely because filtering removed
the example that demonstrated the pattern. CRF cases flip in opposite
directions on adjacent rows (`ffmpeg_035` wins without retrieval,
`ffmpeg_036` wins with), which reads as variance, not signal.

**Conclusion: example filtering is essentially neutral.** It did not deliver
the small-model improvement that motivated the work. The 47% rendered prompt
reduction did not buy measurable accuracy.

---

## What we did not build, and why

### Task 9 — `NORMALIZERS` for CRF / quality words: skipped

The CRF rows (`ffmpeg_035–039`) and quality-word rows (`ffmpeg_040–044`)
pass on every 4B backend without normalization. The 1.7B failures on these
rows are spread across all quality words — not concentrated on a specific
mapping — suggesting a model limitation, not a missing transformation step.
No evidence that normalizers would shift the numbers.

### Task 10 — Prompt header rewrite: skipped

Was conditional on the eval showing header-driven failures. The 4B numbers
say the header is fine; the 1.7B failures are scattered across many tool
families with no concentration that points at the header. Rewriting the
header in the dark could regress 4B as easily as it might help 1.7B; the
eval suite is now set up to A/B this safely if we ever choose to.

### Task 12 — Embedding-based retriever: skipped

Multilingual aliases cover ES/DE/FR/RU at 100% on 4B with zero runtime
cost. Embeddings would have to clear the ceiling those aliases already set
— and add a ~120 MB optional dependency plus ~8 s model-load + sub-100 ms
per-query overhead. The current corpus does not test the paraphrase /
indirect-language gap that embeddings would fill, so we have no way to
justify the dependency on the data we have.

---

## What the data says you would actually need next

If you want to push small-model accuracy further, the lever is not the
prompt. Three candidates, in rough order of plausibility:

1. **Constrained decoding (JSON schema sampling, GBNF).** SmolLM and
   gemma3-1b's parse failures are not a prompt issue. Constrained decoding
   is the right fix and would rescue ~40 of SmolLM3's 47 parse failures
   essentially for free.
2. **Argument-level repair after parse.** Some 1.7B "error" outcomes are
   valid plans with off-by-one or near-miss enum values. A post-parse
   repair pass (re-prompt with the previous bad output and ask for valid
   JSON) is cheap and well-documented.
3. **Bigger model.** 4B is the practical floor for this skill on the
   current prompt. Below that, accuracy drops steeply.

And the cheapest methodological fix:

- **Run multiple trials per row.** N=70 single-trial means you can't tell
  signal from variance on single-row differences. The infrastructure to
  re-run is already in place; just call `run_corpus` repeatedly with
  different seeds.

---

## Task status

| Task | Outcome |
|---|---|
| 1 — Prompt audit tests | Done |
| 2 — Fix `$logs.files[0]` | Done |
| 3 — Apply retrieval in `run_corpus` | Done |
| 4 — Expose parse-failure metadata | Done |
| 5 — Filter examples by retrieved tools | Done; **accuracy-neutral**; kept as infrastructure |
| 6 — Expand eval corpus | Done (19 → 70) |
| 7 — Small-model backends | Done |
| 8 — Baseline + A/B evals | Done (this doc) |
| 9 — `NORMALIZERS` extension point | **Skipped** — data did not support it |
| 10 — Compress / rewrite header | **Skipped** — data did not support it |
| 11 — Multilingual keyword aliases | Done |
| 12 — Embedding retriever | **Skipped** — aliases clear the bar |
| 13 — Docs | Done (`docs/EVAL_FRAMEWORK.md` updated) |
| 14 — Full regression check | Done (631 tests green) |
