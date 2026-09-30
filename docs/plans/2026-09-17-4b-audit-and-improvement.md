# 4B evaluation trust audit and measured improvement

**Status:** Done · **Created:** 2026-09-17 · **Completed:** 2026-09-17
**Ref:** [4B handoff](2026-09-17-4b-model-improvement-handoff.md)

**Goal:** Improve the incumbent 4B planner on trustworthy, matched executing evaluations of both active skills, without weakening safety or the accepted bar.

> **Status note:** This audit/experiment pass is closed under the user's stopping rule.
> Deterministic execution improved; the retrained model failed documents nonregression
> and was rejected. The broader model-improvement goal remains unmet. Nothing committed
> or promoted. Final evidence: [experiment report](../../evals/runs/2026-09-17_sft-v5-symbolic-pair-v1_success/report.md)
> and [row review](../../evals/runs/2026-09-17_sft-v5-symbolic-pair-v1_success/review-v1.md).

## Constraints

- Stay on this branch. Do not commit without explicit user approval.
- Preserve existing ignored artifacts: use new dataset, cache, fixture, run, adapter, and model paths.
- Keep changes narrow; distinguish instrument fixes from model improvements.
- Leave snapshots, published recommendations, and releases unchanged.
- Use the staged `qwen3-4b-sft-v4-flat-q4` as incumbent; compare control and candidate on the same instrument.

## Progress

### [x] Establish tool access

Python/native CUDA inference and real media/PDF execution work. A disposable 4B bf16 LoRA step completed. Conversion tools and native build tools are accessible. The training-data integrity suite passed (17 tests). These are readiness checks, not a quality verdict.

### [x] Audit evaluation and training trust

Trace representative rows end to end; probe incorrect artifacts and incomplete records; inspect criteria, coverage, safety, training leakage, prompt construction, masking, truncation, and artifact provenance. Record reproducible findings, including limitations.

Findings and counterexamples: [control audit report](../../evals/runs/2026-09-17_audit-control-v1_success/report.md).
The existing acceptance gate needs supplementary population/integrity checks; these passed for the reproduced control.
Recomputed aggregate and per-slice metrics also match the saved row records. A 22-plan
documents semantic replay passes 21 checks and exposes one hidden partial-rotation error;
all unchanged-source negative controls fail. Existing corpus criteria remain unchanged.

### [x] Reproduce the incumbent and classify failures

Generate separate fixtures and run both skills plus safety. Record provenance and retrieval results. Rank failure causes using row-level evidence, including sampled apparent successes.

Both accepted snapshots reproduced exactly. The independent thumbnail probe gets only 6/20 correct frames.

### [x] Run isolated improvements

Choose experiments from the audit. Use focused probes first, then matched full comparisons. Fine-tune only where the failure evidence supports a data/weight change; keep new artifacts separate.

- Speed renderer fixed in both runtimes; quarter/eighth/0.4-speed execution tests pass. Frozen-plan replay fixes ffmpeg_282. Full Python suite: 2313 passed, 1 skipped; native workspace: 371 passed; clippy/fmt pass.
- Preview confirmation in the executing Python evaluator was truncating workflows. Fixed with a real-agent regression; full Python suite now 2315 passed, 1 skipped. A separate five-plan replay isolates its measurement effect (artifact average 0.87 → 0.92). The fresh model pair uses the corrected runner for both arms.
- Symbolic-thumbnail candidate: 20 additional examples, original 742 rows unchanged, new 762-row dataset. Attempt v1 stopped at step 2 because Windows spilled ~6.7 GB of GPU allocations into shared memory. Attempt v2 kept the data/optimizer recipe and capped the PyTorch allocator at 80% device memory; all 288 steps / 3 epochs completed successfully in about 61 minutes, with finite logged losses/gradients. No CUDA illegal-memory-access error occurred.
- The existing offline Unsloth merge helper printed success without producing a checkpoint. The verification caught this. A separate PEFT merge accumulated updates in FP32 from the cached base, then exported BF16; the checked 64×64 weight slice exactly matches base + LoRA. F16 GGUF and Q4_K_M conversion succeeded. The full matched control/candidate sweep completed.
- Corrected incumbent versus original control: all 1,015 predictions identical; ffmpeg_282 now executes successfully, three other artifact scores improve through complete confirmation capture, no outcome or artifact-score regressions observed. Documents is unchanged.
- Candidate versus corrected incumbent: FFmpeg outcome 93.8895% → 95.0646% (20 fixes, 10 regressions); documents 98.1707% → 96.3415% (three outcome regressions and one artifact regression). All exact populations, integrity checks, S2 thresholds and 20 safety cases pass. Candidate rejected under documents nonregression.
- Independent exact-frame probe: incumbent 6/20, candidate 18/20; the two remaining candidate failures are the renderer's approximate `last`. PDF semantic replay: 21/22 versus 22/22. Neither targeted gain cancels full-corpus regressions.
- One in-memory prompt clarification: incumbent 11/20 correct frames, candidate 18/20. Five incumbent fixes and no lost correct frames, but one wrong-frame output becomes an execution error. No full-corpus validation; prompt not adopted. Stopped further experiments and reviewed every outcome/score flip.

The 80% allocator cap is a stability workaround, not a proven throughput optimum. It does
not itself offload model weights. The inherited Unsloth checkpointing recipe offloads
activations; Windows counters still show some shared-memory allocation. Do not infer
tensor residency or transfer cost from VRAM usage alone. A controlled cap/performance
comparison has not been run.

### [x] Validate and hand back

Require both skills, required slices, complete coverage, and safety. Report fixes/regressions and unisolated factors. Run focused and full tests for code changes. Present reviewable changes and measured results; ask before any commit.

Python: 2,315 passed / 1 skipped; Rust: 371 passed; fmt/clippy and changed-source Python
lint/format checks passed. Reports distinguish executing outcomes, conditional artifact
averages and semantic checks. Full/hard/chain3 are reported separately. No native model
promotion or L3/L4 acceptance is claimed. Original ignored artifacts remain preserved.

### Follow-ups, outside this closed pass

- Harden production acceptance against incomplete/mismatched evidence and nonfinite scores.
- Strengthen semantic criteria (including PDF transformations, bitrate units and gain direction).
- Fix exact-last-frame extraction in Python/Rust against mixed and variable frame rates.
- If training resumes, use a fresh original-data control and explicit retention checks;
  the current single-seed experiment does not isolate the cause of the regressions.
