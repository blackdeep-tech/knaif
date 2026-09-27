#!/usr/bin/env bash
# Python `success` arm after the illegal-filename binding fix, batch naming, the max_size_kb
# criterion and target_size_mb. Measures whether the committed snapshot (outcome 0.93655,
# knaif 0.98127, 851 utterances, sft-v4) still describes the code.
#
# Prediction on record BEFORE the run, so it can be wrong: `ffmpeg_268#4` flips error -> plan
# (the colon fix applies to Python too), which is +1/851 on outcome_accuracy. Nothing else is
# expected to move — `ffmpeg_020`/`ffmpeg_093` gained a `max_size_kb` criterion their commands
# already satisfy (gold 206 KB, model 217 KB, ceilings 1024/500 KB), and `target_size_mb` is
# inert because the model never emits it.
#
# WAITS for the L3 parity run to finish first: both want the GPU, and two llama.cpp contexts
# on one card skew each other's timings and risk VRAM exhaustion.
#
# `documents` is deliberately NOT re-run. Every change in this window is under skills/ffmpeg/
# or apps/cli (native); no core module moved, so the anchor skill cannot have shifted.
set -uo pipefail
cd "$(dirname "$0")/../../.."
L3=evals/parity/2026-09-16_l3-ffmpeg-command-legal-names/COMPLETE
while [ ! -f "$L3" ]; do sleep 30; done
R=evals/runs/2026-09-16_python-legal-names_success
B=qwen3-4b-sft-v4-flat-q4
uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose \
  --config eval_backends.yaml --backends $B --save "$R" > "$R/ffmpeg.log" 2>&1
echo "SUITE DONE $(date)" >> "$R/COMPLETE"
uv run python -m knaif.evalsuite safety --skill ffmpeg \
  --config eval_backends.yaml --backends $B --save "$R/ffmpeg_safety.json" > "$R/safety.log" 2>&1
echo "ALL DONE $(date)" >> "$R/COMPLETE"
