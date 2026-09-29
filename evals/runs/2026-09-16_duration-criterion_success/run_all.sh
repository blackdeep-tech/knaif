#!/usr/bin/env bash
# Measures two changes together, since both land in the same tree:
#   1. `extract_audio` now skips inputs with no audio track instead of failing the batch
#   2. `duration_s` criterion applied to 20 trim rows that stated a length and asserted none
#
# NO PREDICTION IS REGISTERED for the duration criterion, deliberately. The obvious static
# check — "does the final command carry an end bound" — is wrong for chains, where the trim
# happened in an earlier step and the `artifact` field only holds the last command. Guessing
# anyway would produce a number to confirm rather than a measurement.
#
# Expected for the silent-input skip on the PYTHON arm: little or nothing. The rows where the
# model sweeps every file (`ffmpeg_134#2`, `ffmpeg_287#1`) clarify correctly in Python; the
# skip matters on the native lane, which runs second.
#
# All 20 durations were feasibility-checked by executing every gold command first: 0 of 20
# carried a criterion a known-correct command would fail.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-16_duration-criterion_success
B=qwen3-4b-sft-v4-flat-q4
uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose \
  --config eval_backends.yaml --backends $B --save "$R" > "$R/ffmpeg.log" 2>&1
echo "PYTHON DONE $(date)" >> "$R/COMPLETE"
uv run python -m knaif.evalsuite safety --skill ffmpeg \
  --config eval_backends.yaml --backends $B --save "$R/ffmpeg_safety.json" > "$R/safety.log" 2>&1
uv run python -m knaif.evalsuite native --skill ffmpeg --lane native-cli --verifier success \
  --verbose --save "$R/native" > "$R/native.log" 2>&1
echo "NATIVE DONE $(date)" >> "$R/COMPLETE"
uv run python -m knaif.evalsuite safety --skill ffmpeg --lane native-cli \
  --save "$R/native/ffmpeg_safety.json" > "$R/native_safety.log" 2>&1
echo "ALL DONE $(date)" >> "$R/COMPLETE"
