#!/usr/bin/env bash
# L4 re-run to clear the staleness this branch introduced in its own evidence.
#
# 3019c6a added `max_size_kb` (verifier hash) and touched eval.jsonl (corpus hash), and
# 1af36a9 re-locked the snapshot — all AFTER the 2026-09-16 L4 lane had already run. So the
# ACCEPTED 36/36 verdict in b33edb6 no longer attests to the current tree, and `check-gate`
# correctly reported L4 STALE.
#
# REBUILDS FIRST, deliberately. target/release/knaif.exe was built 10:13; the Rust
# capped-CRF code landed in engine.rs at 13:42. Running the lane against the old binary would
# produce evidence that still fails the `native` fingerprint — the exact staleness being fixed.
#
# Expectation on record BEFORE the run: the verdict holds. The native lane emits
# `-crf 28 -preset slow` for both size rows, ~217 KB against 1024 KB and 500 KB ceilings, so
# `max_size_kb` passes with 2-5x margin; `target_size_mb` is inert because the model never
# emits it. If anything else moves, that is a finding.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-16_l4-ffmpeg-size-criterion_success
export CMAKE_CUDA_ARCHITECTURES=120
cargo build --release -p knaif-cli --features llama,cuda,pdfium > "$R/build.log" 2>&1
echo "BUILD rc=$? $(date)" >> "$R/COMPLETE"
uv run python -m knaif.evalsuite native --skill ffmpeg --lane native-cli --verifier success \
  --verbose --save "$R" > "$R/native.log" 2>&1
echo "SUITE DONE $(date)" >> "$R/COMPLETE"
uv run python -m knaif.evalsuite safety --skill ffmpeg --lane native-cli \
  --save "$R/ffmpeg_safety.json" > "$R/safety.log" 2>&1
echo "ALL DONE $(date)" >> "$R/COMPLETE"
