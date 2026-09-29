#!/usr/bin/env bash
# T6d: both arms on the current instrument AND the current code, taken back to back.
#
# Since T6c, two rounds of change landed and no run has seen either:
#   * success_criteria for the 12 create_thumbnail rows that previously auto-scored 1.0
#   * the review fixes — batch output disambiguation (a real data-loss bug: `clip.mp4` and
#     `clip.mov` into one destination both rendered `clip.mp4` under `-y`), the media filter
#     ported to native, resolved-path mkdir, a shared seconds format, refusing an unreadable
#     trim bound instead of discarding it, and a wider extension allowlist.
#
# An earlier attempt at this run was DISCARDED: the code was still being edited while it ran,
# and Python snapshots imports at process start, so the control and treatment arms executed
# three different code states. The one property this run exists to have is that both arms see
# exactly the same code and corpus.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-14_t6d-review-fixes_success

for arm in control:qwen3-4b-sft-v3-flat-q4 treatment:qwen3-4b-sft-v4-flat-q4; do
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
