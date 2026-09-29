#!/usr/bin/env bash
# Post-fix measurement. Three changes are in this tree since the last locked snapshot:
#   1. `extract_audio` skips inputs with no audio track instead of failing the batch
#   2. `duration_s` criterion on 20 trim rows that stated a length and asserted none
#   3. THE RANGE-TRIM FIX: `-to` is now an INPUT option. `-ss 2 -i in.mp4 -to 5` rendered
#      five seconds starting at two, not the range 2->5 (measured: 5.000s vs 3.000s).
#
# REBUILDS FIRST: the fix touches skills/ffmpeg/native/src/engine.rs, so the lane must not
# run against a binary that predates it.
#
# Prediction on record BEFORE the run, so it can be wrong. The 29 score changes the previous
# (pre-fix) run showed should LARGELY REVERSE — `ffmpeg_004`, `_091`, `_109`, `_119`, `_140`,
# `_267` were failing `duration_s` because the renderer was wrong, and should now pass on the
# merits. Two should NOT recover, because they are different defects:
#   * `ffmpeg_092` "remove the last 3 seconds" -> model EXTRACTS them (3s, want 7s). Comprehension.
#   * `ffmpeg_268` "first 3 seconds"          -> model emits no end bound at all (10s, want 3s).
# `ffmpeg_003` should stay at 0.667: its duration was already right; the `flags` criterion
# catches the reversed reading of "trim the first 5 seconds OFF".
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-16_trim-range-fix_success
B=qwen3-4b-sft-v4-flat-q4
export CMAKE_CUDA_ARCHITECTURES=120
cargo build --release -p knaif-cli --features llama,cuda,pdfium > "$R/build.log" 2>&1
echo "BUILD rc=$? $(date)" >> "$R/COMPLETE"
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
