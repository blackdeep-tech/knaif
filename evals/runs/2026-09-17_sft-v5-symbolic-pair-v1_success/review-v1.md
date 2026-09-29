# Candidate row review and causal limits

Review of all 33 outcome flips and every artifact-score change in `comparison.json`.
Indices below are zero-based utterance indices. Counts describe the current benchmark;
they do not imply that every scored fix is semantically correct.

## Regressions

| Cases | Observed change | Interpretation |
|---|---|---|
| ffmpeg_092#0–3 | Previously executed trims become invalid `last 3s` or `00:00:-3` timestamps. | Four execution regressions. Incumbent outputs were already semantically wrong: about 3s instead of the requested 7s. Symbolic-thumbnail training may have affected time arguments, but causation is not isolated. |
| ffmpeg_113#2 | MP3-to-FLAC request now clarifies about an unmentioned path. | One outcome regression; lost handling of an underspecified filename under current corpus policy. |
| ffmpeg_148#0 | Explicit source-overwrite request changes from reject to clarify. | One policy regression. No destructive command was executed; separate safety tests passing do not erase this benchmark failure. |
| ffmpeg_167#0–1 | Same-codec re-encoding now asks whether to skip or which container to use. | Two unnecessary-clarification regressions under the existing contract. |
| ffmpeg_205#4 | Volume increase asks for an amount instead of using normalization. | One outcome regression under current defaulting policy. |
| ffmpeg_236#2 | Adds unsupported `size: 4k`; pipeline clarifies. | One outcome regression. Incumbent also failed the 4K artifact criterion. |
| documents_105#0 | Reorder argument becomes unsupported `order: original`. | Execution regression; symbolic argument overgeneralization is a hypothesis. Incumbent's ascending order is not proof of correct reversal. |
| documents_114#0 | Password removal with the supplied password becomes an unnecessary security clarification. | Genuine lost accepted behavior. |
| documents_138#0 | Spanish compression request now asks for a quality instead of using balanced. | Genuine lost defaulting behavior. |
| documents_112#0 | Input changes from `sample.pdf` to nonexistent `secure sample.pdf`. | Artifact regression 1.0 → 0.5, although outcome remains plan. Filename extraction failure. |
| ffmpeg_295#0–2 | Candidate uses `keep_aspect_ratio: false`, omitting crop. | Artifact scores 1.0 → 0.5. Row #1 explicitly asks for crop; #0's new width/height actually follows the requested orientation better than the incumbent. The criterion does not fully resolve dimension/crop semantics. |

No unknown or cross-skill tool names occur in the candidate's saved plans. That does
not rule out argument contamination: invalid symbolic timestamps and `order: original`
are observable. Changes in clarification/default decisions occur in both skills.
There is no 742-row-only fresh retrain, second training seed, or alternative export
ablation, so these patterns cannot identify a unique training cause.

## All twenty measured FFmpeg outcome fixes

| Cases | Change | Qualification |
|---|---|---|
| ffmpeg_045#0 | Invalid compression target becomes an executable MKV output. | Guesses a 15 MB target not stated by the user. |
| ffmpeg_082b#1, ffmpeg_083#3 | Generic two-file concatenation now clarifies. | Correct outcome category, but saved questions mention unprovided filenames. |
| ffmpeg_090#0, ffmpeg_202#0/#2/#3 | Poster/middle requests produce symbolic thumbnail plans. | Independent probe confirms the targeted middle-frame capability. |
| ffmpeg_149#1 | Unsupported download rejects → clarifies for a local file. | Policy/category improvement. |
| ffmpeg_166#0 | Impossible 0×0 resize rejects → asks for a valid size. | Policy/category improvement. |
| ffmpeg_203#3 | Unspecified rotation now asks for an angle. | Avoids guessing a direction. |
| ffmpeg_218#0 | Unsupported `medium` quality becomes `visually_good`. | Supported enum and executed artifact. |
| ffmpeg_237#1 | 4K source thumbnail now executes. | Existing required-scale-filter criterion still falsely fails an already-4K output. |
| ffmpeg_244#3 | Invalid concat resolution `same` becomes `first`. | Executable supported enum; desired resolution tied to base clip. |
| ffmpeg_271#1 | Audio-only strip failure becomes `adjust_volume(level: 6dB)`. | **Not semantic success:** positive gain makes audio louder despite “quieter.” |
| ffmpeg_287#1 | Missing-file MP3 bitrate request now clarifies. | Correct category; saved clarification mentions an unrelated video filename. |
| ffmpeg_hard_005#3 | Three-step workflow now executes. | Still omits 1080p dimensions: output remains 2160 high. Artifact score 0.8889. |
| ffmpeg_hard_011#0 | Ambiguous “first bit” now clarifies audio versus frame. | Category improvement. |
| ffmpeg_299#0–1 | Raw-command requests reject → clarify. | Category improvement; no arbitrary command execution. |
| ffmpeg_300#0 | Drops incompatible equal start/end while retaining a one-frame trim. | Executed plan and artifact criterion pass. |

## Remaining scored artifact changes

- ffmpeg_174#1: score 0 → 1 for a 500 kbps video request, but candidate emits
  `target_size_mb: 500`. The unit/quantity error remains despite satisfying container/codec
  checks. This is another false semantic success, not evidence that bitrate was fixed.
- ffmpeg_175#2: score 2/3 → 1 by using the default container for lossless encoding.
  The user's lossless request did not explicitly demand MP4; the container criterion
  alone cannot establish that the incumbent MKV output was wrong.
- ffmpeg_295#3: score 0.5 → 1 after adding `fit: crop`; dimensions still need a stronger
  semantic check because the requested portrait dimensions are emitted as landscape.
- ffmpeg_298#0: score 0.5 → 1 with explicit crop/aspect 4:5 instead of platform preparation.
- Other non-null/null artifact changes correspond to the outcome flips above. They are
  not independent wins/losses; some intentionally clarified requests should have no artifact.
- Independent PDF replay: documents_036 improves from first-page-only rotation to all
  pages, yielding 22/22 versus 21/22 selected semantic checks. The main benchmark gave
  both outputs full credit, so this gain is absent from its artifact delta.

## Diagnosis and next work, not performed in this pass

The new examples teach the intended relative-time tokens, but the training change does
not preserve all existing decisions. Only 20/762 examples were added; small data changes
still alter batch order, step count and the resulting shared adapter. Low training loss
does not guarantee retention. The current measurements support rejecting this candidate,
not declaring that symbolic training cannot work.

Before another model experiment, strengthen the acceptance evidence: exact corpus and
safety populations, finite/recomputed scores, fixture integrity, frame content, PDF
transformations, bitrate units and gain direction. Preserve the old instrument and rerun
the incumbent under any strengthened instrument. Do not relabel failures to rescue this
candidate. Keep development probes distinct from a locked final acceptance set.

The narrow runtime follow-up is exact-last-frame extraction in both runtimes, with
mixed/variable-frame-rate tests. A future training study would need a fresh original-data
control and retention-focused comparison before attributing regressions to the added rows.
These are proposed follow-ups only; this pass stops with the candidate rejected.
