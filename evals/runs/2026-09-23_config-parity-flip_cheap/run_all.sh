#!/usr/bin/env bash
# Inference-config parity T2 — how many eval answers change when ONLY the llama.cpp config does.
# Plan: docs/plans/2026-09-23-inference-config-parity.md
#
# Same model (knaif-qwen3-4b-v2, Q4_K_M), same prompt, same corpus, greedy decoding throughout.
# What varies is how llama.cpp is configured:
#
#   A   qwen3-4b-sft-v4-flat-q4            the config every existing snapshot was measured on:
#                                           llama-cpp-python defaults, KV prefix reused row to row
#   A2  the same arm again                  the within-config floor — same order, same cache path
#   B   qwen3-4b-sft-v4-flat-q4-cold        A with a fresh cache per call — corpus-order dependence
#   C   qwen3-4b-sft-v4-flat-q4-nativecfg   native's config: flash attention on, batch = n_ctx, cold
#   D   native `plan --batch`               the shipped binary (target/release-cuda), as reference
#
# Verifier: cheap (plan-level, dry run). This measures DECISIONS, which is what a config can flip;
# execution adds nothing a config changes.
#
# PREDICTION, written before the run so it can be wrong:
#   * A vs A2: 0 decision flips. Same order means the same cache state before every row, so the
#     same batch layout and the same arithmetic. Anything else means nondeterminism beyond config.
#   * A vs B: a small nonzero number — the corpus-order effect. The one known case is
#     documents_042#0 (TODO: "not bitwise reproducible").
#   * C vs D: far fewer flips than A vs D. If aligning the config is the whole story, C and D
#     differ only by llama.cpp version; if C/D is no closer than A/D, config is not the cause and
#     the plan's T3-T5 would not buy parity.
#   * "cut its audio" (the utterance that started this) is not in the corpus, so it cannot move.
#
# DECISION RULE (pre-registered in the plan): the per-utterance OUTCOME flip rate between A and
# C, against the smallest margin any existing decision rests on. Below it: the existing evals and
# the v2 verdict stand. At or above it: align, re-measure, re-lock, re-derive the verdict.
#
# Nothing is edited while this runs: Python snapshots its imports at process start and every arm
# must see one state.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-23_config-parity-flip_cheap
V2=qwen3-4b-sft-v4-flat-q4
MODEL=models/knaif-qwen3-4b-v2-q4_k_m.gguf
BIN=target/release-cuda/knaif.exe
[ -x "$BIN" ] || BIN=target/release-cuda/knaif

git rev-parse HEAD > "$R/GIT_SHA"
git status --porcelain > "$R/DIRTY_FILES"

run_arm() {  # <dir> <backend>
  local dir="$1" backend="$2"
  mkdir -p "$R/$dir"
  for skill in ffmpeg documents; do
    uv run python -m knaif.evalsuite run --skill "$skill" --verifier cheap --verbose \
      --config eval_backends.yaml --backends "$backend" --save "$R/$dir" \
      > "$R/$dir/$skill.log" 2>&1
  done
  echo "DONE $dir $(date)" >> "$R/COMPLETE"
}

run_arm A  "$V2"
run_arm A2 "$V2"
run_arm B  "$V2-cold"
run_arm C  "$V2-nativecfg"

mkdir -p "$R/D"
for skill in ffmpeg documents; do
  uv run python scripts/flip_rate.py native --skill "$skill" --bin "$BIN" --model "$MODEL" \
    --out "$R/D/${skill}_native.jsonl" > "$R/D/$skill.log" 2>&1
done
echo "DONE D $(date)" >> "$R/COMPLETE"

# Every pair the prediction names, both skills. Reports land beside the runs.
for skill in ffmpeg documents; do
  a="$R/A/${skill}_${V2}_cheap.json"
  for pair in "A2:$R/A2/${skill}_${V2}_cheap.json" \
              "B:$R/B/${skill}_${V2}-cold_cheap.json" \
              "C:$R/C/${skill}_${V2}-nativecfg_cheap.json" \
              "D:$R/D/${skill}_native.jsonl"; do
    name="${pair%%:*}"; other="${pair#*:}"
    uv run python scripts/flip_rate.py compare "$a" "$other" --list \
      --label "$skill A vs $name" --json "$R/flips_${skill}_A_vs_${name}.json" \
      >> "$R/report.txt" 2>&1
  done
  uv run python scripts/flip_rate.py compare "$R/C/${skill}_${V2}-nativecfg_cheap.json" \
    "$R/D/${skill}_native.jsonl" --list --label "$skill C vs D" \
    --json "$R/flips_${skill}_C_vs_D.json" >> "$R/report.txt" 2>&1
done
echo "DONE compare $(date)" >> "$R/COMPLETE"
