#!/usr/bin/env bash
# R5a round 3 (docs/plans/2026-09-25-release-1.2.md): FT cycle sft-v8-flat.
#
#   4b    qwen3-4b-sft-v8-flat-q4    knaif-qwen3-4b-v2 candidate    4B Q4_K_M
#   1.7b  qwen3-1.7b-sft-v8-flat-q6  knaif-qwen3-1.7b-v2 candidate  1.7B Q6_K
#
# Data: ffmpeg rows exactly as before R3 (content-identical to 5443298's train.jsonl; the R3 and
# R5a ffmpeg blocks are held back in scripts/gen_train.py `ffmpeg_rows_held_back`), documents with
# R3's page-label fixes and rows + the R5a inspect rows. The 1.7B also trains on 14 extra rows
# (named-file stills, "extract X"), its own training_run as the plan allows.
#
# Why a third candidate, and the guard against it: the first two (sft-v6, sft-v7) failed. Their
# documents changes worked both times; their ffmpeg changes moved the plan/clarify/reject boundary
# back and forth. This candidate keeps only what held. It is still chosen after seeing eval
# results, so promotion ALSO needs an independent probe (P1/P2), written below before training:
# 40 fresh utterances (probe_ffmpeg.jsonl, probe_documents.jsonl), each below 0.8 similarity to
# every eval, train and held-back row, and each retrievable.
#
# DECISION RULES — written 2026-09-26 before sft-v8 was trained.
#   A1-A4 and B1-B2: unchanged from ../2026-09-26_r5a-candidates_success/run_all.sh.
#   P1  4B: probe outcome-correct count >= sft-v4's on the same probe - 1 (both skills together),
#       and every probe `reject` row correct.
#   P2  1.7B: probe outcome-correct count >= published v1-1.7b's on the same probe - 1, and every
#       probe `reject` row correct.
#   FALLBACK: if the 4B fails any of A1-A4 or P1, sft-v4 ships as knaif-qwen3-4b-v2 (it passed
#   R3a and is the committed snapshot); no fourth 4B candidate this release. If the 1.7B fails,
#   its floors are already at the guardrail minimum (v1), so it goes to the owner.
#
# PREDICTION (not a rule): the 4B's ffmpeg lands within noise of sft-v4 (same ffmpeg data) with
# safety 11/11, and documents at or near sft-v7's (0.98 / 1.00). The 1.7B keeps 11/11 safety only
# if it came from the rebalance rows, which are held back; that is the likeliest 1.7B failure.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-26_r5a-v8-candidates_success
git rev-parse HEAD > "$R/GIT_SHA"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES"
echo "START $(date)" > "$R/COMPLETE"

for skill in ffmpeg documents; do
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$R/fixtures.log" 2>&1
done

# Candidates: full corpus + safety + probe.
for arm in 4b:qwen3-4b-sft-v8-flat-q4 1.7b:qwen3-1.7b-sft-v8-flat-q6; do
  name="${arm%%:*}"; B="${arm##*:}"
  mkdir -p "$R/$name" "$R/$name/probe"
  for skill in ffmpeg documents; do
    uv run python -m knaif.evalsuite run --skill "$skill" --verifier success --verbose \
      --config eval_backends.yaml --backends "$B" --save "$R/$name" > "$R/$name/$skill.log" 2>&1
    uv run python -m knaif.evalsuite safety --skill "$skill" \
      --config eval_backends.yaml --backends "$B" --save "$R/$name/${skill}_safety.json" > "$R/$name/${skill}_safety.log" 2>&1
    uv run python -m knaif.evalsuite run --skill "$skill" --verifier success --verbose --corpus "$R/probe_$skill.jsonl" \
      --config eval_backends.yaml --backends "$B" --save "$R/$name/probe" > "$R/$name/probe/$skill.log" 2>&1
  done
  echo "DONE $name $(date)" >> "$R/COMPLETE"
done

# Probe baselines for P1/P2: the same probe on the models the candidates are compared against.
for arm in ref-sft-v4:qwen3-4b-sft-v4-flat-q4 ref-v1-1.7b:qwen3-1.7b-sft-v3-flat-q6; do
  name="${arm%%:*}"; B="${arm##*:}"
  mkdir -p "$R/$name"
  for skill in ffmpeg documents; do
    uv run python -m knaif.evalsuite run --skill "$skill" --verifier success --verbose --corpus "$R/probe_$skill.jsonl" \
      --config eval_backends.yaml --backends "$B" --save "$R/$name" > "$R/$name/$skill.log" 2>&1
  done
  echo "DONE $name $(date)" >> "$R/COMPLETE"
done
echo "ALL DONE $(date)" >> "$R/COMPLETE"
