#!/usr/bin/env bash
# T6b treatment arm: the sft-v4 union LoRA (reject/clarify taxonomy + 10 new clarify rows)
# against the frozen control evals/runs/2026-09-13_t6a-control-v4_success. Same verifier,
# same corpus, same policy, same eval config — only the model changes, which is the point.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-14_t6b-sft-v4_success
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
