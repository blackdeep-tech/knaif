#!/usr/bin/env bash
# R3a reference runs (docs/plans/2026-09-25-release-1.2.md): every model R5 compares a candidate
# against, re-measured under the grader, corpus and generation config the candidates will face,
# at the quantization it ships in. Python `success`, full corpus, both skills, plus safety.
#
#   sft-v4   qwen3-4b-sft-v4-flat-q4    (knaif-qwen3-4b-v2 candidate, not published) 4B Q4_K_M
#   v1-4b    qwen3-4b-sft-v3-flat-q4    published knaif-qwen3-4b-v1                  4B Q4_K_M
#   v1-1.7b  qwen3-1.7b-sft-v3-flat-q6  published knaif-qwen3-1.7b-v1                1.7B Q6_K
#
# Why now: documents grading changed at 76a0494 (the success verifier opens the produced file:
# rotation, page order/content, overlays, encryption, text layer, format, size). Historical
# documents scores are not comparable with it, so no promotion verdict may quote one. ffmpeg's
# grader is unchanged; it is re-measured so every reference number comes from one commit.
# All three share the aligned llama.cpp config (checked 2026-09-26) and the two published GGUFs
# match their manifest sha256.
#
# Then: sft-v4's documents run becomes the 4B documents baseline (snapshot re-lock, own commit).
# ffmpeg's snapshot stays as it is unless this run disagrees with it on the shared rows.
#
# PREDICTION (written before the run): documents outcome accuracy unchanged for every model (the
# grader changes scores, not outcomes); documents knaif drops for every model (sft-v4 ~0.96 per the
# 2026-09-26 probe, which showed 21 row-utterances newly below 1.0); ffmpeg sft-v4 reproduces its
# snapshot (0.93844 / 0.98401); safety 11/11 and 9/9 for both 4B models; the 1.7B v1 keeps its
# known ffmpeg safety miss (10/11, ffmpeg_safety_system_root_dir).
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-26_r3a-reference_success
git rev-parse HEAD > "$R/GIT_SHA"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES"
echo "START $(date)" > "$R/COMPLETE"

for skill in ffmpeg documents; do
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$R/fixtures.log" 2>&1
done

for arm in sft-v4:qwen3-4b-sft-v4-flat-q4 v1-4b:qwen3-4b-sft-v3-flat-q4 v1-1.7b:qwen3-1.7b-sft-v3-flat-q6; do
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
