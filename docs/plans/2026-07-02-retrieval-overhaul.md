# Retrieval Overhaul — robust multilingual tool retrieval

**Status:** Done · **Created:** 2026-07-02 · **Completed:** 2026-07-02
**Owner:** retrieval · **Ref:** `python/core/knaif/registry.py::retrieve_tools`,
[../audits/2026-07-02-ffmpeg-retrieval-miss-triage.md](../audits/2026-07-02-ffmpeg-retrieval-miss-triage.md),
[../FINE_TUNING.md](../FINE_TUNING.md)

> **Kept 2026-07-23** (S7 decision — cited-from-source tier, genuinely: `FINE_TUNING.md`'s
> fix-retrieval-first rule points here by path, as does `docs/TOOL_SCHEMA.md`'s keyword
> guidance. It is also the surviving home for the deleted CJK plan's analysis.)
>
> **Found and fixed a publication blocker.** Task 8 advertises a CI gate —
> `test_retrieval_no_regression_vs_baseline`, run by `just check` — that reads
> `evals/retrieval/2026-07-02_phase1.json` **by path, with no existence guard**. That file was
> covered by the `evals/**` ignore rule and untracked: `git archive HEAD` produced no
> `evals/retrieval/` directory at all, so on any fresh clone the gate did not merely skip, it
> raised `FileNotFoundError` and broke `just check` for every new contributor. The two 1 KB
> baselines are now committed via an `!evals/retrieval/*.json` allowlist entry — they are a
> committed acceptance bar in the same sense as `skills/*/data/eval_snapshot.json`, which has
> always been tracked, and they were only in the wrong directory to inherit that treatment.
>
> **Corrections:** Task 1's spike checkbox was unticked although Task 2 *resolved it by doing*
> (Option A shipped and cleared the gate with no new dependency, so the FTS5 spike was never
> needed). Line-number references (`registry.py:129`, `agent.py:1227`) replaced with names.
> `FINE_TUNING.md` §4 "rule 8" is now **rule 10** after two rules were inserted ahead of it
> during this same review pass — both citations were changed to name the rule instead of
> numbering it, since a numbered cross-doc reference rots on the next insertion. The promoted
> model key and the suite count were refreshed.
>
> **Status note:** **Done (lexical track).** Motivated by the pass-3 finding that retrieval, not
> the model, capped accuracy. Delivered Phases 0+1+3: a model-independent recall@k/MRR harness
> with a CI gate, CJK n-gram tokenization, df-weighted scoring (shared keywords), and a
> multilingual+CJK keyword pass. **Retrieval recall@5: ffmpeg 0.853 → 0.954, documents
> 0.914 → 0.947** (ascii unchanged — no regression). Phase 2 (embeddings) deliberately **not
> pursued** — lexical hit ~0.95, not worth the mobile-footprint cost. Follow-on (separate): a
> ZH fine-tune, now unblocked. Absorbs/supersedes the CJK segmentation plan.

**Goal:** Replace the whitespace-keyword retriever with a tokenization-robust, multilingual
tool retriever that surfaces the correct tool in the top-k for CJK and non-English utterances,
without regressing European languages or the mobile footprint — and make retrieval quality a
first-class, CI-gated metric.

---

## Why now (evidence)

The pass-3 retrieval triage ([audit](../audits/2026-07-02-ffmpeg-retrieval-miss-triage.md))
replayed the real inference retrieval over the ffmpeg eval corpus:

- **114 utterances never retrieve their expected tool** (90 after a keyword band-aid). These
  are not model failures — the correct tool is absent from the top-5, so no amount of
  fine-tuning can recover them (fine-tuning is strictly downstream of retrieval).
- **44 are CJK** and are *structurally* unfixable by keywords (see the bug below).
- The remaining non-CJK misses are multilingual synonym gaps (DE/ES/RU/BG) and genuine
  lexical ambiguities that keyword-whack-a-mole cannot close, and the keyword approach is
  already constrained (see "keyword uniqueness" below).

The model side is near its data-curation ceiling. Retrieval is the next accuracy lever, it is
cross-cutting (every skill), and it unblocks the ZH/CJK market the project keeps deferring.

## The current retriever and its structural limits

`retrieve_tools` (`registry.py`) scored each tool by **whitespace token-set intersection**:

```python
tokens = set(_normalize(query).replace("_", " ").split())   # split on whitespace
kw_score = 3 * len(tokens & {_normalize(kw) for kw in tool.keywords})
desc_score = len(tokens & set(_normalize(text).replace("_", " ").split()))
```

Top-k = 5, `min_score = 0` at inference (`agent.py`, the `retrieve_tools` call in the
prompt-build path). Four structural limits:

1. **Whitespace tokenization kills CJK.** `将clip.mp4裁剪为9:16` is one token; it can never
   equal the keyword `裁剪`. Every CJK keyword in `tools.yaml` is dead weight, and a Chinese
   query returns 5 tools by *arbitrary score-0 sort order*, not relevance.
2. **Lexical-only → multilingual + paraphrase gaps.** A tool is found only if the query
   literally contains a listed keyword/description word. Every language/synonym must be
   hand-enumerated per tool; coverage is a treadmill (pass-3 fixed 24 by hand, 46 remain).
3. **Keyword uniqueness is enforced** (`load_registry`): a keyword may belong to exactly one
   tool. Real language isn't exclusive — "reduce" fits both volume and size — so the model is
   forced to under-cover. This is an artifact of additive integer scoring.
4. **No retrieval quality metric.** Retrieval is only measured indirectly, through end-to-end
   model evals, so retrieval regressions hide behind model noise.

## Design principles

- **The search corpus is tiny** (≤26 tools/skill). This is a *semantic-matching* problem, not
  an IR-scale problem — compute is a non-issue; correctness across scripts/phrasings is the job.
- **Mobile budget is real.** The 1.7B lane exists for footprint. A per-query embedding model is
  a genuine on-device cost → **lexical-first; embeddings are Phase 2 and gated on need + cost.**
- **Must not regress European languages** (DE/ES/FR/RU/BG tokenize fine today).
- **Deterministic** (retrieval feeds a greedy planner; results must be reproducible).
- **Measure before and after** every change against a held-out retrieval benchmark.

## Approach (phased, with decision gates)

### Phase 0 — Retrieval eval harness (do first)
Make retrieval quality measurable and CI-gated, independent of the model.

- A `retrieval` eval command that, per skill corpus, computes **recall@k** (is `expected_tool`
  in the top-k?) and **MRR**, sliced by script/language (ASCII / Latin-multilingual / CJK).
- Seed from the pass-3 triage script (a scratchpad one-off, not retained — the harness it
  seeded is `knaif.evalsuite.retrieval` and supersedes it).
- Lock a baseline; wire a regression gate like the model snapshots.

**Gate:** baseline recall@5 reported per language slice; CI fails on regression.

### Phase 1 — Fix tokenization + lexical scoring (the cheap, high-value fix)
- **CJK-aware tokenization:** segment CJK spans into character n-grams (bi/tri-gram) or match
  keywords by substring so `裁剪`/`压缩`/… score again. (Absorbs the CJK plan.)
- **Relax keyword uniqueness:** move to per-tool relevance so a term can inform multiple tools;
  replace additive-integer scoring with a normalized score (e.g. BM25-style or weighted
  overlap) that tolerates shared terms.
- **Multilingual keyword pass** for the remaining Latin-script misses (DE/ES/RU/BG), now cheap
  because uniqueness no longer blocks it.
- **Tune `min_score`/top-k** so a no-match query degrades gracefully (clarify) rather than
  returning 5 arbitrary tools.
- **Option A (in-process):** normalize + n-gram tokenize + weighted overlap, zero new deps.
  **Option B (SQLite FTS5):** build a small in-memory FTS5 index per skill using the
  `trigram` tokenizer (CJK substring matching for free) + BM25. Decide in Phase 1 task 1 on a
  spike; prefer the lower-dependency option that hits the recall target.

**Gate:** CJK recall@5 → ~1.0; non-CJK misses materially down; **no European regression**;
then re-run the ffmpeg model eval to confirm the routing metric improves (retrieval-miss rows
were excluded before — they should now convert).

### Phase 2 — Semantic / hybrid layer (GATED — only if Phase 1 plateaus)
- Only if Phase 1 recall stalls below target on paraphrase/synonym misses.
- Small **multilingual sentence-embedding** model encodes the query and each tool's
  description+keywords; hybrid rank = lexical ⊕ cosine.
- **Mandatory on-device cost assessment first** (model size, per-query latency, RAM) — this
  competes directly with the 1.7B lane's footprint budget. May ship desktop-only.

**Gate:** hybrid beats lexical on the paraphrase slice by a margin that justifies the added
footprint/latency; otherwise stay lexical.

### Phase 3 — Land + clean up
- Make the new retriever the shared path (`retrieve_tools` callers in `agent.py`).
- Remove the keyword-uniqueness enforcement hack once scoring tolerates shared terms.
- Update `docs/TOOL_SCHEMA.md` (keyword guidance) and `FINE_TUNING.md` §4's *fix-retrieval-before-blaming-the-model* rule.
- Re-lock the retrieval baseline; note CJK/multilingual now trainable → a follow-on ZH
  fine-tune becomes worthwhile (previously blocked here).

## Risks

- **Embedding footprint vs mobile budget** — the main reason Phase 2 is gated, not default.
- **Regressing European languages** — Phase 0 harness + per-language slices guard this.
- **Over-fitting keywords to the eval corpus** — measure recall on held-out phrasings, not just
  the corpus that motivated the change.
- **Dependency creep** (FTS5/embeddings) — prefer the lowest-dependency option meeting the gate.

## Tasks

- [x] 0. Retrieval eval harness — `knaif.evalsuite retrieval` (recall@k + MRR, per-script slices); baseline `evals/retrieval/2026-07-02_baseline.json`; tests in `python/core/tests/test_retrieval_harness.py`. **Baseline:** ffmpeg recall@5 ascii 0.939 / latin 0.836 / **cjk 0.429**; documents 0.927 / 0.769 / (cjk n=2). (CI gate wiring: deferred to Phase 3.)
- [x] 1. Spike Option A (in-process n-gram) vs Option B (FTS5 trigram) — **resolved by doing**: Task 2 shipped **Option A**, which hit the recall gate (CJK 0.429 → 0.857) with zero new dependencies, so the FTS5 spike was never needed. *(Checkbox corrected 2026-07-23; the decision is in `docs/TOOL_SCHEMA.md`.)*
- [x] 2. CJK-aware tokenization (n-grams for CJK/kana/Hangul runs in `_query_tokens`) — absorbs the 2026-06-28 CJK plan. **ffmpeg CJK recall@5 0.429 → 0.857** (MRR 0.32 → 0.77); ASCII/Latin **byte-identical** (no regression, by construction); overall 0.853 → 0.907. Full suite green (1503 at the time; 1532 as of 2026-07-23).
- [x] 3. Relax keyword uniqueness → **df-weighted scoring** (a keyword's weight ÷ #tools claiming it): no-op for today's unique keywords, safe for shared terms, self-mutes over-generic keywords (hard error only at >4 tools). Enables shared terms like намали (reduce) across compress+volume.
- [x] 4. Multilingual + CJK keyword pass (BG/RU/ES + Chinese). **ffmpeg recall@5 0.907 → 0.954** (latin 0.836 → 0.992, cjk 0.857 → 0.974, ascii unchanged 0.939); **documents 0.914 → 0.947** (latin 0.769 → 1.0). New baseline: `evals/retrieval/2026-07-02_phase1.json`.
- [~] 5. `min_score`/top-k tuning — deferred: recall@5 now ≈0.95, remaining misses are chain rows (expected tool = first of several verbs) + genuine ambiguities, not threshold issues. Revisit only if a no-match graceful-clarify case surfaces.
- [x] 6. Re-run ffmpeg model eval (promoted model, then keyed `qwen3-4b-v3`, renamed
  `knaif-qwen3-4b-v1` 2026-07-20), CJK fix end-to-end. Chinese outcome **0.952 → 0.971**, full 0.903 → 0.905, hard/chain3 unchanged — no regressions. **Nuance:** recall rose +43pt but 4B routing only +1.9pt — the strong model was compensating for poor retrieval; the recall/robustness win matters more for the 1.7B and for training signal. Run: `evals/runs/2026-07-02_4b-v3-cjkfix_success`.
- [ ] 7. (gated — not pursued) Semantic/hybrid spike + on-device cost assessment. Lexical recall reached ~0.95; not justified against the mobile budget. Revisit only if paraphrase misses matter.
- [x] 8. Landed: `retrieve_tools` is the shared path (no separate retriever); uniqueness hack removed (df-weighting); docs updated (TOOL_SCHEMA keyword guidance, FINE_TUNING's fix-retrieval-first rule); baseline re-locked (`2026-07-02_phase1.json`); **CI gate** via `evalsuite retrieval --check` + `test_retrieval_no_regression` (runs in `just check`) + `just retrieval` / `just retrieval-check`.
- [ ] 9. (follow-on, separate) ZH/CJK fine-tune — now unblocked (retrieval surfaces CJK tools). A small ZH training-row pass + re-tune; tracked as a future item, not part of this plan.

## Supersedes

The standalone 2026-06-28 CJK-segmentation plan, whose fix is Phase 1 / Task 2 here. That
plan was **deleted 2026-07-23**: none of its four tasks ran, its bug analysis is reproduced
in *The bug* above, and the two things it uniquely held moved to `docs/TOOL_SCHEMA.md` —
why n-grams beat the substring branch it had recommended, and the fact that kana/Hangul
tokenize but no JP/KO keywords are authored.
