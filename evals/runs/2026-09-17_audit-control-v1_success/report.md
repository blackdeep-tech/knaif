# Incumbent control and trust audit

Status: control complete, both snapshots reproduced exactly; candidate experiment in progress.

The control uses the staged 4B v2 (`qwen3-4b-sft-v4-flat-q4`), unchanged runtime,
corpora and prompts, and freshly generated fixtures in a separate sandbox.
`meta.json` records the model/input hashes, git state and exact commands.

## Confirmed audit findings

1. **Python acceptance does not enforce evidence completeness/integrity.**
   `audit_instrument.py` reproduces acceptance of an otherwise accepted scoreboard
   with coverage changed to zero, fixture corruption recorded, or aggregate scores
   changed to NaN. A safety record with a different backend and empty population
   also passes if its old pass rate is retained. These demonstrate missing guards;
   they do not establish that any historical run actually had those defects.
2. **Coverage is capability coverage over returned records, not corpus completion.**
   Scoring one of two expected utterances reports total 1, coverage 1.0. Independently
   compare the exact `(id, utterance_idx, utterance)` population before accepting
   experiments; do not use coverage alone as that proof.
3. **Some artifact criteria do not prove the requested transformation.**
   An unchanged fixture passes documents_010's rotation criteria at 1.0 because they
   check the planned tool and file existence, not rotation. ffmpeg_044 has empty
   criteria and scores 1.0 even without an artifact. Thus the quality average must
   not be interpreted as complete semantic correctness.
   The criteria inventory finds 73 documents rows / 77 utterances whose only criteria
   are tool identity and, when requested, output existence. A separate semantic replay
   checks 22 saved plans, with unchanged-source negative controls rejected in all 22.
   It finds an actual hidden failure: documents_036 asks to rotate sample.pdf 90 degrees,
   but the model specifies only page 1. The output rotations are [90, 0, 0]; the original
   benchmark grades it 1.0. The other 21 selected checks pass. This spot-check does not
   establish complete semantic coverage for the skill.
4. **One exact train/eval overlap:** documents_079, `Do something with a file.`
   This is a clarify example, not a transformation. No normalized verbatim overlap
   was found for ffmpeg. The existing leakage test covers ffmpeg but not documents.
5. **Training targets can name tools omitted by retrieval:** 51 ffmpeg and 44
   documents training rows have at least one target outside the retrieved inventory.
   Eval has 34/10 such utterances when all expected chain tools are checked.
   This is broader than the retrieval CLI's first-tool recall. Full-registry validation
   still accepts learned tool names, so omission is a prompt inconsistency, not proof
   the model cannot recover.
6. **Executing Python evaluation could stop at preview confirmation.** It captured
   commands with `confirmed=False`, truncating preview-enabled workflows before their
   full batch or subsequent intents. A frozen-plan replay reproduces grading an earlier
   compression output instead of the final WhatsApp output. The narrow runner fix grants
   confirmation only for executing evaluations. See the separate preview-replay run;
   the completed paired control measures both models with this correction.
7. **Not every apparent artifact failure is model failure.** ffmpeg_237 requires a
   `scale` filter for a 4K thumbnail even though its source is already 4K; the produced
   image has the requested dimensions. Conversely, ffmpeg_003's "trim first five seconds
   off" is a real direction error (the model extracts those seconds), not just a command
   formatting mismatch. The exploratory failure-map labels are provisional.
8. **The auxiliary schema-validity metric counts parse failures as valid.**
   `_intent_result` uses `outcome != "error"`, so the separate `parse_error` outcome
   receives a valid-schema flag. `schema_metric_probe.json` demonstrates invalid JSON
   with outcome accuracy 0.0 but schema validity 1.0. The main outcome score correctly
   fails it; do not use this auxiliary metric as proof of schema correctness. This
   finding is recorded separately rather than expanding this change into a metric redesign.
9. **Symbolic `last` is approximate in both renderers.** The current implementation
   seeks to duration minus 0.1s, which is not the exact final frame at 30/25 fps.
   The original 10fps thumbnail fixtures hid that distinction. Before evaluating
   the candidate, a stronger v4 probe was calibrated with mixed frame rates,
   distinctive per-frame colors, and exact last-frame references from reverse
   decoding. All 20 adjacent-frame negative controls fail. Hand-specified supported
   plans pass 18/20; the two `last` cases fail because of this runtime limitation,
   not model interpretation. This is documented, not folded into the speed fix.
10. **Offline merge can report success without an artifact.** The existing Unsloth
    `save_pretrained_merged` path warned that the model was not found, returned without
    writing a checkpoint, and `merge_to_hf.py` still printed its success message and
    exited zero. The independent verification stopped conversion. A separate, local
    PEFT merge of the same adapter passed the base-plus-LoRA slice check and was used
    for the candidate. Preserve output existence and weight checks in future pipelines.
11. **The thumbnail prompt understates the implemented argument contract.** It tells
    the model to emit `at_time as HH:MM:SS`, without documenting the supported relative
    tokens. The new SFT rows teach those tokens under the unchanged prompt. A separate,
    predeclared in-memory prompt diagnostic tested both models: incumbent correct frames
    improved from 6/20 to 11/20, candidate stayed at 18/20. One incumbent wrong-frame
    output became an execution error; the prompt was not adopted or full-corpus validated.
12. **Some measured candidate gains remain semantically wrong.** ffmpeg_174#1 asks for
    500 kbps but the candidate emits `target_size_mb: 500` and receives artifact score 1.0.
    ffmpeg_271#1 asks for quieter audio but emits positive `6dB` and scores 1.0. Existing
    criteria check properties too weak to reject these unit/direction errors. The full
    [candidate row review](../2026-09-17_sft-v5-symbolic-pair-v1_success/review-v1.md)
    distinguishes these apparent gains from actual capability improvements.

The full control scored ffmpeg 0.937720329 / 0.977944540 on 851 utterances and documents
0.981707317 / 1.0 on 164. Both S2 bars, all exact populations and fixture checks passed;
safety was 11/11 and 9/9 with no breaches. These metrics use the original runner.

## Checks that passed

- Rebuilding the union into a new file produced exactly the existing 742 rows.
- Token lengths are 865–2615 (median 2232), below the configured 3072 limit.
- Response masking covers the complete rendered assistant suffix on all 742 rows;
  no prompt-label leakage or fully masked row was found. The template inserts an
  empty thinking block. `mask_and_merge_audit.json` initially compared labels to raw
  JSON and therefore flags that expected prefix; use the corrected interpretation
  in `mask_and_merge_audit_v2.json`, which preserves the original probe evidence.
- A 64×64 slice of the merged first-layer query projection differs from the cached
  base in 2477 elements. This disproves an untouched-base merge for that slice; it
  is not a full tensor-by-tensor provenance proof.
- Retrieval CLI recall@5: ffmpeg 0.9619, documents 0.9470.

## Experimental implications

Preserve the current scoring instrument for matched comparisons, but independently
check populations, finite values, fixture integrity, safety identity, and the actual
output properties targeted by an improvement. Fixing the gate or strengthening the
corpus changes what can be certified, not the model's performance. Such changes need
their own measured control and must never be credited as a model gain.

The source inspection also identifies a concrete runtime defect: quarter-speed audio
renders `atempo=0.25`, outside FFmpeg's supported per-filter minimum. The same defect
exists in Rust. This is a renderer issue, not a reason to teach the model another speed.
