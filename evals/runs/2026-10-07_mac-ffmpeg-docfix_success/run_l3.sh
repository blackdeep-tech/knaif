#!/usr/bin/env bash
# macOS 1.3.0, the Mac's list step 7 (docs/plans/2026-08-02-macos-support.md §0; C5): L3 behavioral
# parity, native vs Python, per model and skill, on this Mac.
#
#   bash evals/runs/2026-10-03_mac-l4_success/run_l3.sh            all four cells, one at a time
#
# Native: the PACKAGED metal binary (sandbox/macos/knaif, unpacked by run_all.sh from the zip whose
# L4 this run folder records), passed with --native-bin. `just parity` hard-codes target/debug/knaif,
# which on macOS needs C5's static `--features llama` build; the packaged exe needs neither that nor
# rpath surgery, and it is the binary L4 measured. Python: llama-cpp-python 0.3.36 (built here, Metal
# on). Both runtimes on Metal: KNAIF_PARITY_BACKEND=metal records it. Command mode, full corpora
# (ffmpeg 328 rows, documents 143), the 1.2.0 R5c instrument.
#
# DECISION RULE, written 2026-10-03 before any cell runs: PASS = 0 port bugs (same plan, different
# command or outcome) and plan disagreement within 1.2.0's bounds, ffmpeg <= 4.11%, documents <=
# 1.83% (--max-plan-disagreement). A port bug or an exceeded bound is recorded as a FAIL and goes to
# the owner with its rows; nothing is re-run to a different result.

set -uo pipefail
cd "$(dirname "$0")/../../.."
case "$PWD" in "$HOME"/*) echo "refusing to run from $PWD: use a checkout outside ~ (E5)" >&2; exit 2 ;; esac
R=evals/runs/2026-10-07_mac-ffmpeg-docfix_success
BIN="$PWD/sandbox/macos/knaif/bin/knaif"
[ -x "$BIN" ] || { echo "no $BIN: run run_all.sh first" >&2; exit 2; }
export KNAIF_PARITY_BACKEND=metal
unset KNAIF_N_GPU_LAYERS KNAIF_PDFIUM_PATH
echo "START l3 $(date)" >> "$R/COMPLETE"

for pair in "4b knaif-qwen3-4b-v2 knaif-qwen3-4b-v2-q4_k_m.gguf" "1.7b knaif-qwen3-1.7b-v2 knaif-qwen3-1.7b-v2-q6_k.gguf"; do
  set -- $pair
  model="$1" name="$2" gguf="$3"
  for skill in ffmpeg; do
    case "$skill" in ffmpeg) bound=0.0411 ;; documents) bound=0.0183 ;; esac
    label="mac-l3-release-$model-$skill"
    uv run python -m knaif.evalsuite fixtures regen --skill "$skill" > /dev/null 2>&1 \
      || { echo "FAILED l3 $model $skill: fixtures $(date)" >> "$R/COMPLETE"; continue; }
    uv run python scripts/parity_check.py --skill "$skill" --mode command \
      --native-bin "$BIN" --model-path "$PWD/models/$gguf" --python-model "$name" \
      --cwd "$PWD/sandbox/fixtures/$skill" --max-plan-disagreement "$bound" --label "$label" \
      --purpose "macOS L3, packaged metal zip, both runtimes on Metal (M1 Pro, macOS 27.2 Beta 2, release build 8cbab23)" \
      > "$R/l3_${model}_${skill}.log" 2>&1
    echo "DONE l3 $model $skill exit $? $(date)" >> "$R/COMPLETE"
  done
done
echo "END l3 $(date)" >> "$R/COMPLETE"
