# Symbolic thumbnail times: matched 4B experiment

**Status: complete; candidate rejected. Experiments stopped after regression review.**

The deterministic speed fix improves the incumbent without measured regressions. The
new model learns symbolic thumbnail positions, but loses documents accuracy and has
ten FFmpeg outcome regressions. Keep the incumbent weights and the existing prompt.
The separate prompt diagnostic is promising on thumbnails but is not accepted for
integration. No model, prompt, snapshot, recommendation, or release was promoted.

## Results and decision

Both arms completed all 1,015 utterances on the same corrected instrument and fixture
bytes. Independent population, score recomputation, integrity and safety checks passed.
Both arms meet S2, but the candidate fails the stricter documents nonregression rule.

| Measure | Incumbent | Candidate |
|---|---:|---:|
| FFmpeg outcome, 851 utterances | 93.8895% | 95.0646% |
| FFmpeg conditional artifact average | 98.0123% | 98.4705% |
| Documents outcome, 164 utterances | 98.1707% | 96.3415% |
| Documents conditional artifact average | 100% | 99.6689% |
| FFmpeg hard outcome, 56 utterances | 96.4286% | 100% |
| FFmpeg hard artifact average | 100% | 99.7904% |
| FFmpeg chain3 outcome, 32 utterances | 96.875% | 100% |
| FFmpeg chain3 artifact average | 100% | 99.6528% |
| FFmpeg safety | 11/11 | 11/11 |
| Documents safety | 9/9 | 9/9 |
| Independent frame-content probe | 6/20 | 18/20 |
| Selected PDF semantic replay | 21/22 | 22/22 |

FFmpeg has 20 outcome fixes and 10 regressions; documents has zero fixes and three
regressions, plus one artifact-only regression. The cluster-bootstrap 95% interval
for FFmpeg outcome delta is -0.56 to +2.85 percentage points, spanning zero. This is
one trained candidate, not established general improvement. The PDF replay gain is
real in its selected scope and does not cancel the full-corpus documents regressions.

Artifact averages are conditional: FFmpeg includes 584 incumbent versus 581 candidate
scores; documents includes 154 versus 151. Failed plans can drop out of that average.
For example, four incorrect three-second trims become execution errors in the candidate.
Do not read the artifact average as an end-to-end correctness rate.

The full row review is in [review-v1.md](review-v1.md); machine-readable evidence is
`comparison.json`, the per-arm scoreboards and `independent_verdict.json` files.

## Isolated deterministic result

Comparing the original incumbent control with the corrected incumbent control gives
**identical predictions on all 1,015 utterances**. FFmpeg outcome rises from 93.7720%
to 93.8895% because ffmpeg_282 now executes its already-correct quarter-speed plan.
Its artifact score becomes 1.0. No outcome or scored-artifact regressions were observed.
Documents is unchanged. The Python and Rust renderers now compose valid audio tempo
factors; real-execution tests cover quarter/eighth/0.4 speed and preserve both streams.

The evaluation confirmation fix independently improves artifact scores for ffmpeg_118#0
and ffmpeg_hard_016#0/#1 by capturing the complete approved workflow. This is a measurement
correction, not a model gain. `instrument_comparison.json` lists every change.

Validation: full Python suite 2,315 passed / 1 skipped; Rust workspace 371 passed;
native fmt/clippy and changed-source Python lint/format checks passed. These checks
do not replace future native behavioral/shipped-path acceptance before release.

## Prompt-only diagnostic and stopping point

One predeclared in-memory clarification documents `first`, `middle`, and `last` in the
FFmpeg header. Repository prompt files are unchanged. With identical probe fixtures:

| Model | Original prompt | Clarified prompt |
|---|---:|---:|
| Incumbent: correct frames | 6/20 | 11/20 |
| Candidate: correct frames | 18/20 | 18/20 |

Five incumbent failures become correct frames. No previously correct frame is lost,
but probe_08 changes from the wrong frame at 2.5 seconds to an execution error at
25 seconds on a 12-second video. Both models now use `last` on the two final-frame
requests, which still fail because of the renderer's duration-minus-0.1s approximation.
All four arms use identical case/fixture hashes and reject all adjacent-frame negative
controls. See the two `2026-09-17_thumbnail-prompt-*_v1_success` folders.

This diagnostic improves a narrow capability but has a worse failure mode on one row
and no full-corpus nonregression evidence. It is not a production prompt change.
Following the requested stopping rule, no further fine-tunes, prompt variants, or broad
runtime edits were attempted. The accepted result of this pass is the tested deterministic
speed improvement and the evaluation correction; the model-improvement goal remains unmet.

The incumbent often guesses numeric timestamps for requests such as "the middle of the
video", although the renderer already accepts symbolic `middle`, `first`, and `last`.
An independent frame-content probe found only 6/20 correct outputs: numeric 4/4, first
2/2, middle 0/12, last 0/2. The candidate teaches those existing arguments without
changing the prompt, schema, or original training rows.

## Experiment

- Base checkpoint: `unsloth/Qwen3-4B`, BF16 LoRA, r16/alpha16, 3 epochs, lr2e-4,
  seed3407, batch1, gradient accumulation8, max sequence3072.
- Data: original 742 union examples plus 20 FFmpeg examples (12 middle, 4 first,
  4 last), yielding 428 FFmpeg + 334 documents rows. Original examples are unchanged.
  Token lengths are 865–2615; no example reaches the truncation limit.
- Supplemental source: `skills/ffmpeg/data/train_sft_v5_symbolic.jsonl`.
  Frozen chat dataset: `python/training/sft-v5-symbolic-audit-v2_chat.jsonl`.
- Training/adapter identity: `qwen3-4b-sft-v5-symbolic-audit-v2`. Attempt v1 was stopped
  after heavy Windows shared-memory spill at step2. Attempt v2 caps PyTorch allocation
  at 80%; data, precision, optimizer and other training settings are unchanged.
- Deployment comparison: both arms Q4_K_M, same context/retrieval/prompt settings and
  512-token generation budget. Candidate backend is experimental; no public version assigned.

Training logs and metadata are under `python/training/output/qwen3-4b-sft-v5-symbolic-audit-v2/`.
`finish_experiment_v2.py` merges, checks a base-plus-adapter tensor slice, converts/quantizes,
then invokes this folder's `run_pair.py` and `compare.py`. The original offline Unsloth
helper returned success but created no checkpoint; that failed attempt and its logs are
preserved. The continuation uses PEFT on the cached training base, FP32 accumulation
followed by BF16 export. Its checked 64×64 slice matches base + LoRA exactly (2,556
elements changed from base). This is a slice check, not an exhaustive weight comparison.
All 288 training steps completed with finite logged losses and gradients; runtime was
about 61 minutes. Current conversion tool files and DLLs are hashed in the metadata.
`export_audit.json` confirms identical tokenizer, architecture and quantization metadata,
plus matching names/shapes/quantization types for all 398 tensors, against the incumbent.
This checks export compatibility; it does not assert that the model weights are equal.

## Measurement

Both models run on the same corrected code state and shared, regenerated fixture bytes.
The control and candidate each execute all 851 FFmpeg and 164 documents utterances and
both safety corpora. `meta.json` records model, source, config and fixture hashes.
The independent checker verifies exact populations, aggregate/per-slice recomputation,
finite scores, fixture integrity and safety identity, in addition to the written S2 bar.

The following are separate from the model comparison:

- The quarter-speed renderer fix is isolated by a frozen-plan replay (error → valid
  quarter-speed media) and Python/Rust tests.
- Approved confirmation during executing evaluation fixes truncated preview workflows.
  Its five-plan replay changes artifact average 0.87 → 0.92 with no model change.
- The thumbnail probe checks actual frame content. Its initial PNG/JPEG calibration
  error was corrected before evaluating the candidate; preserved versions explain this.
  The final matched probe uses v4: mixed 30/25/10fps fixtures with distinctive per-frame
  colors and exact final-frame references. All 20 adjacent-frame negative controls fail.
  A hand-specified-plan replay passes 18/20 and identifies a separate runtime limitation:
  `last` resolves to duration minus 0.1s, missing the exact final frame at 30/25fps.
  Thus those two failures must not be attributed to model interpretation when it emits
  the supported symbolic argument. The original 10fps probe did not expose this issue.
- A 22-plan documents replay checks PDF semantics beyond the corpus's weak criteria.
  The initial incumbent passes 21/22; documents_036 rotates only the first page.

## Limits and decision rules

This is a single training seed. There is no freshly retrained 742-row-only ablation, so
the experiment establishes the candidate's measured behavior, not an isolated causal
estimate for each added row. Training order/step count change with dataset size. The
allocator cap is a stability measure, not a demonstrated throughput optimum.
The export used a verified PEFT fallback rather than the failing offline Unsloth helper;
the actual candidate build is what is compared, not an assumed identical export process.

The existing benchmark informed the targeted capability choice; treat it as a development
benchmark. The new thumbnail phrases are held out from training, but the small authored
probe is not broad deployment evidence. Some existing artifact criteria are incomplete,
especially in documents; a high quality average is not proof that every transformation
is correct. The auxiliary schema-validity metric has a separately recorded parse-error bug.

Decide using both full skills, hard and chain3 separately, required slice floors, exact
safety at 100%, named row regressions, and no documents regression. No snapshots,
recommendations, releases, or commits are changed. Native L3/L4 revalidation belongs to
integration before any eventual promotion.
