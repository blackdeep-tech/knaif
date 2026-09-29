#!/usr/bin/env bash
# T6c: the same sft-v4 model as T6b, on the four deterministic argument-vocabulary fixes
# (ISO-BMFF codec tags, media-filtered globs, signed/unit timestamps, symbolic at_time).
# Same model, verifier, corpus and policy as T6b — only the skill's argument handling moved,
# so the delta against T6b is those fixes and nothing else. The grading gap the T6b analysis
# found (rows with no success_criteria) is deliberately NOT touched here: changing the
# instrument in the same run would make this comparable to neither T6b nor the control.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-14_t6c-arg-vocabulary_success
B=qwen3-4b-sft-v4-flat-q4

uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose \
  --config eval_backends.yaml --backends $B --save $R           > $R/ffmpeg.log 2>&1
uv run python -m knaif.evalsuite run --skill documents --verifier success --verbose \
  --config eval_backends.yaml --backends $B --save $R           > $R/documents.log 2>&1
uv run python -m knaif.evalsuite safety --skill ffmpeg \
  --config eval_backends.yaml --backends $B --save $R/ffmpeg_safety.json    > $R/ffmpeg_safety.log 2>&1
uv run python -m knaif.evalsuite safety --skill documents \
  --config eval_backends.yaml --backends $B --save $R/documents_safety.json > $R/documents_safety.log 2>&1
echo "DONE $(date)" > $R/COMPLETE
