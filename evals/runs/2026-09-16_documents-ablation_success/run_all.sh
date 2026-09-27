#!/usr/bin/env bash
# documents prompt fix — "a destination given as the REASON is context, not the request" —
# measured on BOTH models, because the prompt is shared.
#
# The 1.7B is the target: its v4 lost documents' S2 acceptance on 5 rows, three of them the
# sizing-vs-sending failure ffmpeg needed T6g's prompt fix for. The 4B is here because its
# acceptance is FROZEN against the old prompt (2026-09-15_t6g), and a shared-prompt edit
# invalidates that evidence whether or not it helps. Measuring only the model you hoped to
# improve is how a fix for one tier quietly re-baselines the other.
#
# ffmpeg is NOT re-run: only skills/documents/prompt.yaml moved.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-16_documents-ablation_success

for arm in small:qwen3-1.7b-sft-v4-flat-q6 large:qwen3-4b-sft-v4-flat-q4; do
  name="${arm%%:*}"; B="${arm##*:}"
  mkdir -p "$R/$name"
  uv run python -m knaif.evalsuite run --skill documents --verifier success --verbose \
    --config eval_backends.yaml --backends $B --save "$R/$name"      > "$R/$name/documents.log" 2>&1
  uv run python -m knaif.evalsuite safety --skill documents \
    --config eval_backends.yaml --backends $B --save "$R/$name/documents_safety.json" > "$R/$name/documents_safety.log" 2>&1
  echo "DONE $name $(date)" >> "$R/COMPLETE"
done
echo "ALL DONE $(date)" >> "$R/COMPLETE"
