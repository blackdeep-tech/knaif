#!/usr/bin/env bash
# The 1.7B v2 decision run: control (published v1 = sft-v3) vs candidate (sft-v4), both skills.
#
# WHY A CONTROL ARM AT ALL, when the 1.7B v1's numbers are already published: because the
# INSTRUMENT moved underneath them. Since that July run the corpus was relabelled by the
# reject/clarify taxonomy, 63 plan rows that could not fail were given criteria, the harness
# started executing what it graded, and the scoring policy went to v2. Comparing sft-v4 to a
# July scoreboard would measure four changes at once — the T6a lesson, paid for once already.
#
# Both arms are byte-identical except the backend, matched quant (Q6_K, the 1.7B's deployment
# quant on both sides — FINE_TUNING §4 rule 1), matched max_tokens (512), one code state, one
# fixture set. No code is edited while this runs: Python snapshots imports at process start.
#
# The 4B v2 (qwen3-4b-sft-v4-flat-q4) is NOT re-run here — it is already accepted and frozen
# (evals/runs/2026-09-15_t6g-sizing-vs-sending_success). This run prices only the small model.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-16_1.7b-v4-pair_success

for arm in control:qwen3-1.7b-sft-v3-flat-q6 treatment:qwen3-1.7b-sft-v4-flat-q6; do
  name="${arm%%:*}"; B="${arm##*:}"
  mkdir -p "$R/$name"
  uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose \
    --config eval_backends.yaml --backends $B --save "$R/$name"      > "$R/$name/ffmpeg.log" 2>&1
  uv run python -m knaif.evalsuite run --skill documents --verifier success --verbose \
    --config eval_backends.yaml --backends $B --save "$R/$name"      > "$R/$name/documents.log" 2>&1
  uv run python -m knaif.evalsuite safety --skill ffmpeg \
    --config eval_backends.yaml --backends $B --save "$R/$name/ffmpeg_safety.json"    > "$R/$name/ffmpeg_safety.log" 2>&1
  uv run python -m knaif.evalsuite safety --skill documents \
    --config eval_backends.yaml --backends $B --save "$R/$name/documents_safety.json" > "$R/$name/documents_safety.log" 2>&1
  echo "DONE $name $(date)" >> "$R/COMPLETE"
done
echo "ALL DONE $(date)" >> "$R/COMPLETE"
