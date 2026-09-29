#!/usr/bin/env bash
# T6a control arm, second take: the shipped model on the fixed clarify gate and the
# corrected corpus. Same backend, same verifier, same policy as 2026-09-12 — only the
# instrument and the corpus moved, which is exactly what this re-run exists to re-measure.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-13_t6a-control-v4_success
B=qwen3-4b-sft-v3-flat-q4

uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose \
  --config eval_backends.yaml --backends $B --save $R           > $R/ffmpeg.log 2>&1
uv run python -m knaif.evalsuite run --skill documents --verifier success --verbose \
  --config eval_backends.yaml --backends $B --save $R           > $R/documents.log 2>&1
uv run python -m knaif.evalsuite safety --skill ffmpeg \
  --config eval_backends.yaml --backends $B --save $R/ffmpeg_safety.json    > $R/ffmpeg_safety.log 2>&1
uv run python -m knaif.evalsuite safety --skill documents \
  --config eval_backends.yaml --backends $B --save $R/documents_safety.json > $R/documents_safety.log 2>&1
echo "DONE $(date)" > $R/COMPLETE
