# Model output improvement — ffmpeg routing quality

**Status:** Planning · **Created:** 2026-09-08 · **Completed:** —
**Owner:** core · **Ref:** follows the 2026-09-07 audit
([findings](../audits/2026-09-07-core-principles-and-rtx5080.md),
[fix review](../audits/2026-09-07-fix-review.md)) and the F9 snapshot re-lock; complements
[skill-quality-lifecycle](2026-09-10-skill-quality-lifecycle.md) (which absorbed the 2026-08-08 native/Python planning-parity plan)

**Goal:** Raise ffmpeg routing accuracy from 0.9020 to 0.94–0.95 by fixing the deterministic
gate, binding, and schema defects behind ~47% of current failures — without touching the
prompt or the model.

Baseline: `evals/runs/2026-09-08_f9-relock_success`, the newly locked acceptance bar
(`success`, n=847, `knaif-qwen3-4b-v1`).

## Why this plan exists, and what it is not

The 2026-09-07 audit and its follow-ups fixed the *measurement*: the regression gate was
fail-open, ffmpeg's acceptance bar used the forbidden `cheap` verifier over a third of the
corpus, and the documented per-row join silently discarded 63% of the evidence. Those are
closed. This plan is the first one that can honestly claim to *improve the product*,
because for the first time a change's effect can be read.

**It is deliberately not a fine-tuning plan.** Classifying the current 83 failures shows
roughly half are not the model's judgement at all — they are a self-contradicting gate, a
missing binding rule, and absent schema aliases. Retraining before fixing those would
credit a new model with recovering failures it did not cause, and would bake the harness's
current mistakes into the training signal.

## Baseline being improved on

| metric | value |
|---|---:|
| outcome accuracy | **0.9020** |
| knaif (artifact) score | 0.9738 |
| tool accuracy | 0.8914 |
| schema validity | 0.9847 |

83 failures / 846 utterances (9.8%), across 62 of 313 corpus rows.

**The most informative cut: only 16 rows fail on every phrasing; 46 fail on some phrasings
and pass on others.** That is fragility in recognition, not absence of capability — the
model can do these tasks and proves it on a sibling utterance. It is why the work below is
schema- and gate-shaped rather than model-shaped.

Failure shapes:

| expected → actual | n | reading |
|---|---:|---|
| `plan → clarify` | 34 | asked instead of acting — the big one |
| `clarify → plan` | 14 | acted on something ambiguous |
| `plan → error` | 11 | crashed; not judgement at all |
| `clarify → reject` | 9 | refused instead of asking |
| `plan → reject` | 8 | refused something in scope |
| `reject → clarify` | 3 | hedged instead of refusing |
| other | 4 | parse error, misc |

## Methodology rules for this plan

Taken from `docs/EVAL_VERIFICATION_SOP.md`; they are not optional here.

- **Establish the noise floor first.** Two control runs, unchanged code, per-row diff. No
  single-row delta is interpretable until that band is known. Greedy + `json_mode: false`
  has been bit-identical historically on this hardware — confirm, do not assume.
- **Prefer the narrowest layer.** `tools.yaml` moves one tool; `prompt.yaml` moves every
  row in the skill and needs a full re-measure on all backends. Every task below is scoped
  to the narrowest layer that can hold its fix.
- **A fix that only satisfies `cheap` is not a fix.** Quote `success`. `cheap` is for the
  iteration loop only.
- **Join per row on `(id, utterance)`.** Never on `id` alone, never on `utterance_idx`
  across pre-2026-09-08 runs. Print `len(shared)` and check it against `total`.
- **State the acceptance bar before running**, or the result argues itself into acceptance.
- **Prefer no change over a regressing change.** A net-zero edit that shifts *which* rows
  fail is a loss.

---

## Task 1 — Fix the self-contradicting filename gate

- [ ] **1.1 Reproduce, then fix `_hallucinated_filename` stem matching**

**9 failures / 9 rows.** `python/core/knaif/agent.py:1257` compares the *full* filename
against the raw utterance:

```python
if value.lower() not in u_lower:
    return value
```

The user writes `clip_4k`; stem resolution turns it into `clip_4k.mp4`; the guard then
reports that the user never mentioned `clip_4k.mp4` and downgrades a correct plan to
`clarify`. The emitted question is self-refuting:

> "You didn't mention 'clip_4k.mp4' in your request — which file should I work on?"

This is the same shape as audit finding R1 — validating one representation while checking
another.

Affected rows: `ffmpeg_228`, `275`, `277`, `279`, `281`, `282`, `283`, `284`, `285`.

**The fix must not be a naive stem match.** Accepting any stem substring would also let
through Task 2's invented placeholders (`video.mp4` passes because "video" appears in "make
the video smaller"). Accept the stem **only when it resolves to a file that actually exists
in the sandbox**. That requires giving the guard sandbox access — it is currently a
`@staticmethod` over `(plan, utterance)`.

*Acceptance:* those 9 rows route to `plan`; the guard still rejects `video.mp4` /
`input.mp4` / `mp3_file.mp3` (pinned by test); no other row changes.

---

## Task 2 — Bind an unnamed input to the obvious sandbox file

- [ ] **2.1 Resolve a single unambiguous candidate instead of clarifying**

**16 failures / 12 rows.** For *"resize to 480p and remove audio"* — no filename given —
the model emits a placeholder (`input.mp4`, `video.mp4`, `mp3_file.mp3`). The guard
correctly catches the invention, but the corpus expects a plan: with exactly one candidate
of the right media type in the sandbox, the intended behavior is to use it.

Rows include `ffmpeg_085`, `113`, `114`, `117`, `118`, `122`, `123`, `129`, `131`.

**This supersedes an earlier misdiagnosis worth recording.** `ffmpeg_113`/`114` (mp3 → flac
/ aac) were previously written up as a *multilingual* weakness because they fail in German
and Chinese. They fail there for the same reason they fail in English — the model invents
`mp3_file.mp3` — and the language is incidental. Do not spend retrieval or translation
effort on them.

Rule to implement, deterministically: when a plan's input arg is a filename that does not
exist, and the sandbox holds exactly **one** file whose media kind matches the tool's
expectation, bind it. Two or more candidates → keep clarifying (genuinely ambiguous).

*Acceptance:* the 12 rows route to `plan` and produce the right artifact under `success`;
a two-candidate sandbox still clarifies (pinned by test).

---

## Task 3 — Close the schema gaps behind the 14 hard errors

These are the cheapest wins in the plan: the planner **already** supports both mechanisms
(`tool_def.arg_aliases` at `planner.py:299`, `schema.aliases` at `planner.py:341`), and
`skills/documents/tools.yaml` already uses them (`arg_aliases: {pages: ranges}`, enum
`aliases: {bottom: bottom-center, ...}`). `skills/ffmpeg/tools.yaml` has essentially none.
**This is declarative work, not Python.**

- [ ] **3.1 Arg-key aliases (4 failures)**

The model puts the right value under a plausible sibling name:

| tool | model emitted | should map to |
|---|---|---|
| `resize_video` | `target_height` | `height` |
| `convert_video` | `audio_format` | `audio_codec` |
| `adjust_volume` | `target_bitrate` | `bitrate` (verify the tool accepts it) |
| `prepare_for_platform` | `audio` | decide: drop, or map to a supported arg |

Confirm each mapping against the tool's real signature before adding it; alias to a
*supported* arg or not at all.

- [ ] **3.2 Enum value aliases (7 failures)**

Quality profiles on disk are `balanced`, `best_possible`, `high_quality`, `lossless`,
`small_file`, `visually_good`. The model says `medium` and gets `Unknown quality profile`.
Add an `arg_schemas.quality` enum with aliases — `medium → balanced`, `high → high_quality`,
`good → visually_good`, `low`/`preview → small_file`, `best → best_possible`.

`scale: 'auto'` needs a decision. `scale: 2` / `scale: 1` are deliberately **not** coerced
and should stay that way — `2` has no defensible reading, and inventing one would fabricate
a 2-pixel thumbnail that still satisfies a `filters: [scale]` check.

⚠️ **Do not add `help:` text to these schemas.** It renders into every prompt and pushed
the ffmpeg prompt past its 14,000-char ceiling (`test_prompt_audit`) once already.

- [ ] **3.3 Fail gracefully, not with a stack trace (2 failures)**

`rotate_video` called with neither `angle` nor `flip` raises; it should `clarify`. The
system-root sandbox refusal is *correct* but exits as `error` rather than a clean `reject`
— the safety held, only the reporting is wrong. Neither changes routing; both change
whether a user sees a crash.

---

## Task 4 — Re-measure and decide

- [ ] **4.1 Noise floor, then full re-measure**

Two control runs before any of the above; then `success` on both backends after. Compare
per row on `(id, utterance)`. Report which rows moved and why, not just the aggregate.

**Acceptance bar, stated in advance:** Tasks 1–3 address 39 of 83 failures (47%). Landing
them should put ffmpeg outcome accuracy at **0.94–0.95** (from 0.9020) with `avg_knaif_score`
not below 0.9738 and no new failure in any previously-passing row. Below 0.93, or any
regression on documents, stop and re-classify rather than pressing on.

- [ ] **4.2 Re-lock the bar — separate commit, separate PR**

Only if 4.1 clears. Per AGENTS.md, and this time it will be a genuine measured improvement,
so say so explicitly and quote the per-row evidence.

---

## Deferred, with reasons

- **Tier 2 — the 14 `clarify → plan` disagreements.** *"convert clip.mp4 to the best
  format"*: the corpus says ask, the model picks. This is a product decision about how
  assertive the agent should be, not a defect. Settle the policy first; only then is one
  side wrong. Doing it before Tasks 1–3 risks relabelling the corpus to match a model whose
  failures were mostly harness artifacts.

- **Tier 3 — training.** Only after 4.1. Two things are already known: training rows for
  the terse *"with no audio"* phrasing exist in `train.jsonl` but the shipped model is
  `sft-v3`, which never saw them; and `convert_video` cannot express `-an` at all, so that
  case needs a schema fix before any training can help it. Note also that this project has
  repeatedly measured prompt edits as a wash, and that a small fine-tuned base did not beat
  a strong general instruct model on saturated routing — see `docs/FINE_TUNING.md` for the
  proven dead ends before designing an experiment.

- **F8 (Python vs native prompt parity) — separate and more important than this plan.**
  Every number here measures the Python runtime; users run the native CLI, which builds a
  different prompt (full registry, YAML order, static examples vs Python's retrieved five).
  Improving Python routing does not establish that the shipped binary improved. Plan:
  `docs/plans/2026-08-08-native-python-planning-parity.md`, since folded into
  `docs/plans/2026-09-10-skill-quality-lifecycle.md` (L1 now pins prompt parity).

- **Snapshot identity (audit F9, second half).** The re-locked snapshots record metrics and
  backend but **not** corpus hash / row IDs / model checksum / code identity, which the
  audit asked for. Until that lands, the gate cannot distinguish "behavior changed" from
  "someone edited the corpus" — which is precisely the confusion this plan's re-measure
  step depends on avoiding.
