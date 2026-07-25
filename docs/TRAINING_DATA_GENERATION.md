# TRAINING_DATA_GENERATION.md — Fine-tuning Dataset Generation Contract

> Part of the fine-tuning docs. For the **end-to-end procedure** (train → quantize → eval →
> promote), the methodology rules, and what's already known to work/fail, see the canonical
> [FINE_TUNING.md](FINE_TUNING.md). **This** doc is only the `train.jsonl` generation contract.

## Purpose

This document is the handoff contract for **generating `train.jsonl`** — the
fine-tuning dataset for a skill. Hand the launch prompt below (see
"How to launch") to a big-LLM coding agent (Claude, GPT-4, Gemini, etc.).

`train.jsonl` is the dataset a small model **learns from** during fine-tuning.
It is distinct from `eval.jsonl`, which the model is **measured against**:

| File | Role | Consumed by |
|---|---|---|
| `data/train.jsonl` | fine-tuning input (utterance → plan pairs) | the fine-tuning job |
| `data/eval.jsonl` | benchmark (expected outcomes, baselines, criteria) | `just eval <skill>` |

The two never feed each other automatically. A change to routing (a new tool, a
removed tool, a new argument pattern) must be reflected in **both** or they drift
— which is exactly how `train.jsonl` went stale after `batch_convert` was removed.

---

## When to run this

Generate the training set **only after the corpus is trusted**. The prerequisite
gates, in order:

1. `eval.jsonl` rows are authored and human-validated
   (see [CORPUS_AUTHORING_STEPS.md](CORPUS_AUTHORING_STEPS.md)).
2. Baselines verified — ffmpeg outputs produce correct files
   (`just eval-honest <skill>`).
3. Routing is healthy on the current prompt — `just eval <skill> --verbose`
   shows the small model picking the right tools.

Only then does it pay to spend a big model generating a large, high-quality
training set. Generating earlier bakes in whatever routing mistakes the corpus
still has.

---

## How to launch

Hand the task below to a big-LLM coding agent (Claude, GPT-4, …). The agent reads
the **live** `tools.yaml` / `prompt.yaml`, so the generated data tracks the
current skill definition and cannot drift the way a one-off generator script
does. Substitute `<skill>` (e.g. `ffmpeg`).

> Generate the fine-tuning dataset for the `<skill>` skill.
>
> Read, in order:
> - `docs/TRAINING_DATA_GENERATION.md` — this contract (read first)
> - `skills/<skill>/tools.yaml` — authoritative tools + args
> - `skills/<skill>/prompt.yaml` — routing rules + canonical examples
> - `skills/<skill>/data/train.jsonl` — existing examples (style + dedup)
> - `skills/<skill>/data/eval.jsonl` — validated corpus (coverage reference)
>
> Produce an expanded `skills/<skill>/data/train.jsonl` per the contract,
> then verify with `just test-skill <skill>` and `just eval <skill> --verbose`.

---

## The generation task

Produce an expanded `skills/<skill>/data/train.jsonl` where each line is one
training example.

### Row schema

```json
{
  "utterance": "Convert all mp4 files in this folder to mkv.",
  "plan": {"plan": [{"tool": "convert_video", "args": {"inputs": ["*.mp4"], "container": "mkv"}}]},
  "tags": ["convert", "batch"]
}
```

- `utterance` — **required.** One natural-language request. One utterance per row
  (unlike `eval.jsonl`, which groups rephrasings — here each phrasing is its own
  training example).
- `plan` — **required.** The exact `{"plan": [...]}` object the model should learn
  to emit. Tools and args MUST be valid per `tools.yaml`.
- `tags` — optional, for slicing/inspection. Not used by training.

### Hard requirements

1. **Valid tools only.** Every `tool` must exist in `tools.yaml`, or be `clarify`
   / `reject`. Never invent tools or use a removed one.
2. **Valid args only.** Use only the `required_args` / `optional_args` declared
   for that tool. Match the argument shapes in `prompt.yaml`'s examples (e.g.
   `inputs` is a list; `trim_video` takes a single `input`).
3. **Mirror the prompt's routing rules.** The plans must agree with the routing
   guidance in `prompt.yaml` — same tool for the same intent. If `prompt.yaml`
   says "convert all/every <ext> → convert_video with a glob input", training
   rows for that intent must do exactly that.
4. **Ground every path in the utterance.** Never introduce filenames the user did
   not mention. Batch phrasing → a glob (`["*.mp4"]`) or folder path.

### Coverage requirements

- **Every model-visible tool** in `tools.yaml` gets multiple examples. Audit the
  existing `train.jsonl` first — tools with zero examples are the priority.
- **`clarify`** rows: vague intent, missing file, ambiguous operation
  (e.g. "apply the same settings to every file" → clarify, because the operation
  is undefined even though the input is a batch).
- **`reject`** rows: unsafe / out-of-scope requests (bulk delete, disk wipe,
  anything not media processing).
- **Multilingual** phrasings (EN, DE, ES, BG; FR/RU where natural), matching
  the language mix already used in `eval.jsonl`. **ZH/CJK rows are unblocked** (2026-07-02):
  `retrieve_tools` used to whitespace-tokenize, so Chinese keywords never matched and the
  right tool was never surfaced — and training on inputs whose tools aren't retrieved teaches
  a mapping the model can't use at inference. CJK-aware tokenization has since shipped in both
  runtimes (ffmpeg CJK recall@5 0.429 → 0.857), so ZH rows are now worth authoring. The
  general rule that produced the old hold still applies to **any** language: check retrieval
  first with `uv run -m knaif.evalsuite retrieval`, and do not author training rows for
  phrasings whose expected tool is not in the top-5 — fine-tuning is downstream of retrieval
  and cannot recover a tool the model never sees. Note that only Chinese keywords are
  authored in `tools.yaml` today; the tokenizer also handles kana and Hangul, but JP/KO
  keyword *coverage* does not exist yet.
- **Edge cases**: typos, informal phrasing, boundary values, multi-step requests
  (output of one step feeding the next via `$var`).
- **Quality/platform variety**: every platform profile, every quality level.

Target a few hundred rows for a meaningful fine-tune — scale to the budget.

---

## After generation — validate before fine-tuning

```bash
uv run pytest python/core/tests/test_train_data_integrity.py   # the automated guard
just test-skill ffmpeg          # schema validity (utterance + plan present)
just eval ffmpeg --verbose      # routing sanity on the current prompt
```

`test_train_data_integrity.py` is the guard that catches what `validate_plan` cannot. It
structurally validates every row, then adds three value-level checks: **cross-skill
contamination** (a documents enum or tool name appearing in an ffmpeg plan, and vice versa —
the union fine-tune's characteristic failure), **canonical enum values** (`validate_plan`
checks arg *keys*, never the *values*, so `quality: "small"` passes structural validation and
teaches the wrong mapping), and **holdout integrity** (no training utterance copied verbatim
from `eval.jsonl`, which would turn the benchmark into a memorization test). Run it after
every regeneration; a new skill should extend its value-space constants rather than opt out.

A generated row is only useful if its plan is something the skill would actually
accept and execute. Spot-check a sample of rows by hand: do the tool + args match
what `prompt.yaml` prescribes for that utterance?

---

## Multi-skill fine-tuning loop (one shared model)

The deployed model is **one shared model serving every skill** (each skill builds its
own skill-scoped agent around the *same* weights — see
`docs/plans/2026-06-25-cross-skill-eval-monitoring.md`). Fine-tuning is therefore on the
**union** of every skill's `train.jsonl`, never one fine-tune per skill — a per-skill
fine-tune would forget the other skills.

The promotion loop for a new model build:

```bash
# 1. Train on the union of all skills' train.jsonl (your fine-tuning pipeline).

# 2. Sweep every active skill for the new build (cheap = fast routing gate):
uv run -m knaif.evalsuite run --all-skills --verifier cheap \
  --config eval_backends.yaml --backends <new-build> \
  --label <build-tag> --save evals/runs/<YYYY-MM-DD>_<build-tag>_cheap

# 3. Gate against every skill's committed snapshot. Non-zero exit = a skill regressed:
uv run -m knaif.evalsuite regression --all-skills \
  --current-run evals/runs/<YYYY-MM-DD>_<build-tag>_cheap
```

**Block promotion on any per-skill regression.** Step 3 is the catastrophic-forgetting
detector: training on skill X's utterances (or adding a new skill to the union) can
silently regress skill Y, and this gate is what catches it. Use `--verifier success`
for the promotion-grade sweep once `cheap` is clean (real execution is slower). Inspect a
skill's trajectory across builds with `trend --skill <name>`.

---

## Checklist for the big LLM

- [ ] Read `tools.yaml`, `prompt.yaml`, existing `train.jsonl`, `eval.jsonl`.
- [ ] List the tools with zero or few existing training examples.
- [ ] Generate rows covering every tool, plus `clarify` / `reject` / multilingual
      / edge cases, all consistent with the prompt's routing rules.
- [ ] Use only valid tools and args; ground every path in the utterance.
- [ ] Write one JSON object per line to `skills/<skill>/data/train.jsonl`.
- [ ] Run `just test-skill <skill>` and `just eval <skill> --verbose`.
- [ ] Spot-check a sample for tool/arg correctness against `prompt.yaml`.
