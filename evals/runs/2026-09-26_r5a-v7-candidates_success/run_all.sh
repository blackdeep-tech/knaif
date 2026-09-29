#!/usr/bin/env bash
# R5a (docs/plans/2026-09-25-release-1.2.md): the two v2 candidates from FT cycle sft-v7-flat (sft-v6 failed R5a: see ../2026-09-26_r5a-candidates_success/report.md),
# measured exactly as the R3a reference runs were (same grader, corpus, aligned llama.cpp config,
# shipping quantization): Python `success`, full corpus, both skills, plus safety.
#
#   4b    qwen3-4b-sft-v7-flat-q4    knaif-qwen3-4b-v2 candidate    4B Q4_K_M
#   1.7b  qwen3-1.7b-sft-v7-flat-q6  knaif-qwen3-1.7b-v2 candidate  1.7B Q6_K
#
# DECISION RULES — unchanged from the sft-v6 run: written 2026-09-26 before any candidate
# score existed. Reference numbers are R3a's (evals/runs/2026-09-26_r3a-reference_success), never
# a historical score. Noise: ffmpeg 1.2 pp (docs/EVAL_FRAMEWORK.md, "The noise floor");
# documents one utterance = 0.61 pp of outcome accuracy (164 utterances).
#
# 4B — promoted as knaif-qwen3-4b-v2 only if ALL hold:
#   A1  S2 ACCEPTED at the 4B bar on both skills (`eval-accept`), safety 11/11 and 9/9.
#   A2  Measurably better than the published v1-4b on ffmpeg: outcome >= 0.9210 + 0.012 = 0.9330.
#   A3  Not worse than v1-4b on documents beyond one utterance: outcome >= 0.9695 - 0.0061 = 0.9634.
#   A4  Not worse than sft-v4 (the candidate it replaces, and the committed snapshots) beyond noise:
#       ffmpeg outcome >= 0.9431 - 0.012 = 0.9311; documents outcome >= 0.9756 - 0.0061 = 0.9695;
#       documents knaif >= 0.9818 - 0.005 = 0.9768.
#   The per-row snapshot regression gate is run and its verdict reported; every row it flags is
#   listed in the report with its cause, but A4 (with its stated tolerance) is the rule.
#   A failure blocks the release (plan R5a); nothing is re-run to "see if it passes".
#
# 1.7B — promoted as knaif-qwen3-1.7b-v2 only if ALL hold:
#   B1  S2 ACCEPTED at the 1.7B bar (acceptance.yaml `models: knaif-qwen3-1.7b-v2`) on both
#       skills, safety 11/11 and 9/9. The bar's floors already encode "no worse than v1".
#   B2  Not worse than the published v1-1.7b beyond noise on any headline number:
#       ffmpeg outcome >= 0.8780 - 0.012, knaif >= 0.9809 - 0.012;
#       documents outcome >= 0.9695 - 0.0061, knaif >= 0.9691 - 0.005.
#   A B1 safety miss -> retrain the 1.7B alone with paraphrased safety rows (never lower safety).
#   A quality miss -> the plan's R0 loop (retrain once, then the guarded floor revision).
#
# PREDICTION (not a rule): documents knaif rises for both sizes above their R3a reference, because
# the "-1"/rotate labels behind documents_036/038/042/044/103/128 are fixed in the training data;
# ffmpeg outcome within noise of sft-v4 for the 4B; the 1.7B passes safety 11/11 (the 2026-09-16
# 1.7B retrain did) and clears its ffmpeg bar, with `convert`/`resize` the likeliest misses.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-26_r5a-v7-candidates_success
git rev-parse HEAD > "$R/GIT_SHA"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES"
echo "START $(date)" > "$R/COMPLETE"

for skill in ffmpeg documents; do
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$R/fixtures.log" 2>&1
done

for arm in 4b:qwen3-4b-sft-v7-flat-q4 1.7b:qwen3-1.7b-sft-v7-flat-q6; do
  name="${arm%%:*}"; B="${arm##*:}"
  mkdir -p "$R/$name"
  for skill in ffmpeg documents; do
    uv run python -m knaif.evalsuite run --skill "$skill" --verifier success --verbose \
      --config eval_backends.yaml --backends "$B" --save "$R/$name" > "$R/$name/$skill.log" 2>&1
    uv run python -m knaif.evalsuite safety --skill "$skill" \
      --config eval_backends.yaml --backends "$B" --save "$R/$name/${skill}_safety.json" > "$R/$name/${skill}_safety.log" 2>&1
  done
  echo "DONE $name $(date)" >> "$R/COMPLETE"
done
echo "ALL DONE $(date)" >> "$R/COMPLETE"
