# Terse "with no audio" — a phantom arg, a prompt rule, and eight untrained rows

**Status:** Draft · **Created:** 2026-09-08 · **Completed:** —
**Owner:** ffmpeg / training · **Ref:** eval row `ffmpeg_hard_014`; measured against
[evals/runs/2026-09-07_rtx5080-core-audit_success](../../evals/runs/2026-09-07_rtx5080-core-audit_success/)

> **Status note:** The diagnosis is complete and measured; **no fix is approved** — the owner
> chose "just the diagnosis" on 2026-09-08 and deferred the choice between the two paths in
> *Options*. Do not implement either workstream without that decision: they are mutually
> shaping (C changes `convert_video`'s contract, which would require re-authoring the same
> eight training rows that T would otherwise train as-is).

**Goal:** Make `convert clip.mov to mp4 with no audio` produce a silent mp4 instead of
`Tool 'convert_video' has unsupported args: ['audio_format']`, and record why the fix that
already exists in the corpus never reached a shipped model.

---

## The report

Owner, 2026-09-08, driving the CLI by hand:

```text
just cli ffmpeg "convert clip.mov to mp4 with no audio"
  Plan step 1 invalid: Tool 'convert_video' has unsupported args: ['audio_format']
```

> "I thought this was fixed long time ago."

It was — in `train.jsonl`, never in a model. See *Cause 3*.

---

## What is established

Measured 2026-09-08 against the live model (`knaif-qwen3-4b-v1`, backend `auto`, the
`models/knaif-qwen3-4b-v1-q4_k_m.gguf` the CLI resolves by default), by calling
`agent.infer(use_mock=False)` through the same `_build_orchestrator("auto", …)` path
[app.py:90](../../python/core/knaif/app.py#L90) uses — **not** the mock, which returns unrelated
junk for these utterances and will mislead anyone who reproduces this with `infer()`'s defaults.

All three utterances below are the **same eval row**, `ffmpeg_hard_014`
([eval.jsonl:311](../../skills/ffmpeg/data/eval.jsonl#L311)):

| utterance | emitted plan | result |
|---|---|---|
| `convert clip.mov to mp4 with no audio` | `convert_video{inputs, container:"mp4", audio_format:"none"}` | ✗ unsupported arg |
| `turn clip.mov into an mp4 with no sound` | `convert_video{inputs, container:"mp4", include_audio:false}` | ✗ unsupported arg |
| `convert clip.mov to mp4 and remove its audio` | `convert_video` → `strip_audio` | ✓ |

The invented key is **not stable** — two phrasings produced two different phantom args — so any
fix keyed on one literal key name is a patch, not a fix.

### Cause 1 — `convert_video` cannot express "no audio" *(read)*

[tools.yaml:30](../../skills/ffmpeg/tools.yaml#L30) allows
`container, video_codec, audio_codec, quality, crf, output, preview`. There is no arg that drops
the audio track, and `audio_codec` has no "none" value in the engine — the convert path only ever
chooses between `copy` and a real encoder
([_engine.py:627](../../skills/ffmpeg/python/_engine.py#L627)). The model has nowhere legitimate
to put the modifier, so it invents somewhere.

### Cause 2 — the prompt steers *against* chaining for modifier phrasing *(measured)*

The ffmpeg system prompt opens PARAMETERS with:

> *"Use ONE tool per plan unless the user explicitly requests two or more distinct operations
> (e.g. "trim … then append … then convert")."*

"…**with no** audio" reads as a modifier on one operation; "…**and remove** its audio" reads as
two. That is exactly the pass/fail split in the table above — the rule is doing what it says, and
what it says is wrong for this class.

### Not the cause — retrieval *(measured, ruled out)*

Worth stating because it is the first instinct and it is wrong here. Dumping the built prompt for
the failing utterance shows `strip_audio` present **three times over**:

- in the retrieved tool list (line 18 of the system prompt),
- with its own mapping rule — *"Remove audio" / "mute" / "strip audio" / "silent video" →
  strip_audio* (line 57),
- and as a few-shot example — *"Remove the audio track from clip.mp4."* → `strip_audio` (line 164).

The model is shown the right tool, told the rule, and given the example. It still folds the
modifier into `convert_video`. This is a **composition** failure, not a retrieval one — unlike the
`convert_document` phrasing gap, which is genuinely retrieval-side.

### Not the cause — the retry net *(measured, ruled out)*

`repair_invalid_plans` fires (`agent.last_retried == True`) and the second attempt, with the
concrete validator error injected, fails the same way.

### Cause 3 — the fix exists in data, never in weights *(read + measured)*

[train.jsonl:397-404](../../skills/ffmpeg/data/train.jsonl#L397-L404) holds **eight rows** tagged
`["v4", "hard_target", "chain", "chain2", "convert", "strip", "terse_no_audio"]` teaching
precisely this chain — "convert holiday.mov to mp4 with no audio", "convert intro.mov to mp4
without audio", "convert gameplay.avi to mp4, no audio", and five more.

But **both shipped models are `training_run: sft-v3-flat`**
([model-manifest.yaml:51](../../contracts/models/model-manifest.yaml#L51) and `:62`), and
`models/` contains no v4 GGUF. The v4 rows postdate the promoted model (promoted 2026-07-02). The
dataset fix was authored; the model was never retrained.

**Those eight rows are the entire v4 delta** — `grep '\"v4\"' skills/*/data/train.jsonl` returns 8,
all of them these, all ffmpeg. There is no larger untrained batch hiding behind this.

**This is not recoverable from git.** History is squashed into `1f8a16f knaif 1.0.1`, so
`git log -S` cannot date the rows or show that they postdate the promotion. The durable check is
the tag against the shipped model's `training_run` in the manifest.

### It is visible in the newest sweep *(measured)*

From [ffmpeg_qwen3-4b-sft-v3-flat-q4_success.json](../../evals/runs/2026-09-07_rtx5080-core-audit_success/ffmpeg_qwen3-4b-sft-v3-flat-q4_success.json)
(run 2026-09-07, the day before the report):

| tag | total | outcome_accuracy | note |
|---|---|---|---|
| `terse_no_audio` | 3 | **0.667** | only 2 of 3 produced an artifact |
| `chain2` | 9 | 0.889 | |
| `strip` | 21 | 0.905 | |
| `hard` | 56 | 0.929 | the average this hid inside |
| *(whole corpus)* | 847 | 0.902 | |

`avg_knaif_score` for `terse_no_audio` is **1.0** — the two rows that *do* plan are graded
perfect, so the failure is invisible in the score column and shows only in outcome accuracy over
three utterances. **A three-utterance tag cannot fail loudly enough to be noticed.** That is the
reporting lesson, independent of which fix lands.

---

## Options

Mutually shaping, not independent — see the status note.

### C — teach `convert_video` to drop audio (deterministic, no retrain)

Give `convert_video` the capability the model keeps assuming it has. Works with the **shipped**
sft-v3 model, because it makes the plan the model already emits *legal* rather than teaching it a
different plan.

- **C1** `- [ ]` Add `include_audio` (boolean) to `convert_video`'s `optional_args`
  ([tools.yaml:30](../../skills/ffmpeg/tools.yaml#L30)). Reuse `reverse_video`'s existing key
  ([tools.yaml:83](../../skills/ffmpeg/tools.yaml#L83)) rather than inventing a third name — it is
  already in the prompt vocabulary, and it is one of the two keys the model invented unprompted.
- **C2** `- [ ]` Cover the other invented key. `arg_aliases` renames keys but not values, so
  `audio_format:"none"` needs either an `arg_schema` alias map or an explicit string→bool
  coercion. **Decide deliberately:** normalize_plan's Pass 4
  ([planner.py:303](../../python/core/knaif/planner.py#L303)) coerces string/enum/int/number and
  **not** bool, so this may want a Pass-4 boolean arm in core (skill-agnostic, schema-driven)
  rather than an ffmpeg-local hack.
- **C3** `- [ ]` Wire it through the engine: a `drop_audio` option → `recipe.pop("audio")` for
  `mode == "convert"`, and emit `-an` in the generic flags branch
  ([_engine.py:861](../../skills/ffmpeg/python/_engine.py#L861)) when the recipe carries no audio
  block. Note the remux path: `strip_audio` already does the right thing with `-an -c:v copy`
  ([_engine.py:754](../../skills/ffmpeg/python/_engine.py#L754)), so a dropped-audio convert should
  follow it rather than forcing a needless re-encode.
- **C4** `- [ ]` Relax the ONE-tool prompt rule for modifier phrasing, or add a mapping line
  (*"convert … with no audio" → convert_video with include_audio=false*). `test_prompt_audit`
  holds the ffmpeg prompt under a size ceiling, so this competes for budget.
- **C5** `- [ ]` Port to native. `skills/ffmpeg/native/src/engine.rs` mirrors the same recipe
  builder (`copy_audio` at `:789`, the `-an` sites at `:254` / `:285` / `:332`);
  `just parity ffmpeg` must stay green.
- **C6** `- [ ]` Re-annotate `ffmpeg_hard_014`. Its `success_criteria`
  (`container: mp4, video_codec: h264, no_audio: true`) are already satisfied by a single-pass
  convert, but its `outputs` block encodes a **two-artifact chain** and `expected_tools` is
  `["convert_video", "strip_audio"]`. Both would become wrong. A re-authored baseline needs human
  validation before `outputs_validated_by` may say so.
- **C7** `- [ ]` Re-author the eight v4 rows to the new single-pass form — otherwise the corpus
  teaches a chain the tool no longer needs.
- **C8** `- [ ]` `just eval-fixtures ffmpeg` → `just eval-success ffmpeg`, then
  `just eval-regression ffmpeg <run>` against the committed snapshot. Re-lock only if adopting a
  measured improvement, in its own commit.

**Cost:** a contract change to a shipped tool, a corpus re-annotation, a native port, a parity run.
**Benefit:** fixes it today, on the model users already have.

### T — train v4 (the originally designed fix)

- **T1** `- [ ]` Build the union chat dataset including the eight existing v4 rows
  ([TRAINING_DATA_GENERATION.md](../TRAINING_DATA_GENERATION.md)).
- **T2** `- [ ]` Train the LoRA, merge to GGUF, quantize ([FINE_TUNING.md](../FINE_TUNING.md) —
  read its methodology rules first rather than re-deriving them).
- **T3** `- [ ]` Eval **every** active skill against its committed snapshot, not just ffmpeg.
- **T4** `- [ ]` Promote: `models.yaml` + `contracts/models/model-manifest.yaml` +
  the skill's `recommended_model:`. The public release name is **v2** while the FT-cycle name is
  the sft-v4 lineage — the two namespaces do not cross; see
  [MODELS.md](../MODELS.md) and the manifest's `training_run` bridge.
- **T5** `- [ ]` Publish the GGUF and update `url` / `sha256` / `size_bytes`
  ([RELEASE.md](../RELEASE.md)).

**Cost:** the full fine-tune loop — hours of GPU plus a cross-skill eval sweep, for eight rows.
**Benefit:** `convert_video`'s contract is untouched, the corpus stays as authored, and every
*other* untrained row of that vintage would ship at the same time (here: none).

---

## Guard, whichever path lands

- **G1** `- [ ]` Make "authored in the corpus but absent from the shipped model's lineage"
  *visible*. The failure this plan documents is a corpus fix that looks live in the repo and is
  not live at runtime, and history being squashed means git cannot tell anyone. A three-row tag
  averaging away inside `hard` (0.93) is the same blind spot from the reporting side.

---

## Related

- [complex-two-step-intents](2026-06-09-complex-two-step-intents.md) — **Superseded**, and directly
  relevant: producer-side strip-audio fusion was scoped there and *deliberately dropped*, with the
  chain-fidelity residual pushed to fine-tuning. Option C reopens that decision. Read it before
  approving C — the arguments against fusion are recorded there and are not restated here.
- [qwen3-finetuning-pass3](2026-07-02-qwen3-finetuning-pass3.md) — the pass that promoted the
  sft-v3 models this plan finds wanting.
