#!/usr/bin/env bash
# E2a (docs/plans/2026-09-26-policy-gate-and-skill-adapters.md): does the shared base + the sft-v4
# adapter applied at load reproduce the merged sft-v4? Criteria are in the plan, written before
# this run: plans byte-identical on >= 99% of ffmpeg utterances, or within the 1.2% CUDA noise
# floor with no outcome flip on a safety-tagged row; adapter GGUF < 150 MB (66.1 MB, measured at
# conversion). Swap time is a native-lane measurement (lora_adapter_set) and is reported
# separately, not from this Python run.
#
#   merged   qwen3-4b-sft-v4-flat-q4         merged sft-v4, quantized after merging (shipped form)
#   lora     qwen3-4b-base-plus-sft-v4-lora  base Q4_K_M + sft-v4 LoRA f16 at load
#   base     qwen3-4b-base-unsloth-q4        control, first 60 rows: shows the adapter is applied
#
# Expected difference: "merged" quantizes the merged weights; "lora" applies an f16 delta to a
# quantized base. Not the same arithmetic, so a small plan difference is expected, not a bug.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-26_e2a-lora-spike_success
git rev-parse HEAD > "$R/GIT_SHA"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES"
echo "START $(date)" > "$R/COMPLETE"
uv run python -m knaif.evalsuite fixtures regen --skill ffmpeg >> "$R/fixtures.log" 2>&1
for arm in merged:qwen3-4b-sft-v4-flat-q4 lora:qwen3-4b-base-plus-sft-v4-lora; do
  name="${arm%%:*}"; B="${arm##*:}"; mkdir -p "$R/$name"
  uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose \
    --config eval_backends.yaml --backends "$B" --save "$R/$name" > "$R/$name/ffmpeg.log" 2>&1
  uv run python -m knaif.evalsuite safety --skill ffmpeg \
    --config eval_backends.yaml --backends "$B" --save "$R/$name/ffmpeg_safety.json" > "$R/$name/ffmpeg_safety.log" 2>&1
  echo "DONE $name $(date)" >> "$R/COMPLETE"
done
mkdir -p "$R/base"
uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose --limit 60 \
  --config eval_backends.yaml --backends qwen3-4b-base-unsloth-q4 --save "$R/base" > "$R/base/ffmpeg.log" 2>&1
echo "DONE base $(date)" >> "$R/COMPLETE"
echo "ALL DONE $(date)" >> "$R/COMPLETE"
