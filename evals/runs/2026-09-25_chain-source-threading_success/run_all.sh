#!/usr/bin/env bash
# T6 of docs/plans/2026-09-23-chain-source-threading.md: does the named-once + kind rule for
# chain source threading cost anything on the existing rows?
#
# Two arms, same model (qwen3-4b-sft-v4-flat-q4, public knaif-qwen3-4b-v2), same interpreter,
# run one after the other (never concurrently: shared GPU, and timings skew):
#   control   = release/1.2.0 @ 9b66e47, from a detached worktree at sandbox/t6-control
#               (PYTHONPATH points at the worktree so its own knaif is imported, not the
#               editable install of the main checkout; models/ is a junction)
#   treatment = fix/chain-source-threading @ 5df2047, from the main checkout
# Each arm regenerates its own fixtures first, then ffmpeg + documents `success`, then both
# safety corpora. ffmpeg's corpus differs by design: treatment has ffmpeg_301/302 (10 utt,
# 861 vs 851); compare the shared rows, and read the two new rows on treatment alone.
#
# Pass bar (plan T6): no regression against either snapshot on the existing rows, and the new
# rows correct. A drop on a chain row means the model repeats a filename while meaning the
# transformed file -- read those rows before touching the rule.
#
# Nothing in either tree is edited while this runs.
set -uo pipefail
MAIN="$(cd "$(dirname "$0")/../../.." && pwd)"
R="$MAIN/evals/runs/2026-09-25_chain-source-threading_success"
PY="$MAIN/.venv/Scripts/python.exe"
B=qwen3-4b-sft-v4-flat-q4

run_arm() {
  local name="$1" tree="$2"
  local out="$R/$name"
  mkdir -p "$out"
  cd "$tree"
  git rev-parse HEAD > "$out/GIT_SHA"
  git status --porcelain --untracked-files=no > "$out/DIRTY_FILES"
  export PYTHONPATH="$tree/python/core"
  "$PY" -c "import knaif; print(knaif.__file__)" > "$out/KNAIF_IMPORTED_FROM"
  "$PY" -m knaif.evalsuite fixtures regen --skill ffmpeg     > "$out/fixtures.log" 2>&1
  "$PY" -m knaif.evalsuite fixtures regen --skill documents >> "$out/fixtures.log" 2>&1
  "$PY" -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose \
    --config eval_backends.yaml --backends $B --save "$out"   > "$out/ffmpeg.log" 2>&1
  "$PY" -m knaif.evalsuite run --skill documents --verifier success --verbose \
    --config eval_backends.yaml --backends $B --save "$out"   > "$out/documents.log" 2>&1
  "$PY" -m knaif.evalsuite safety --skill ffmpeg \
    --config eval_backends.yaml --backends $B --save "$out/ffmpeg_safety.json"    > "$out/ffmpeg_safety.log" 2>&1
  "$PY" -m knaif.evalsuite safety --skill documents \
    --config eval_backends.yaml --backends $B --save "$out/documents_safety.json" > "$out/documents_safety.log" 2>&1
  echo "DONE $name $(date)" >> "$R/COMPLETE"
}

echo "START $(date)" > "$R/COMPLETE"
run_arm control   "$MAIN/sandbox/t6-control"
run_arm treatment "$MAIN"
echo "ALL DONE $(date)" >> "$R/COMPLETE"
