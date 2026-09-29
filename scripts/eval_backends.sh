#!/usr/bin/env bash
# The cross-backend check: do the CUDA, Vulkan and CPU native builds plan alike and refuse alike?
#
#   just eval-native-backends <skill> --max-flips N [--kinds "cuda vulkan cpu"] [--model PATH]
#                              [--out DIR] [--no-build]
#
# Made reusable from evals/runs/2026-09-25_backend-parity-v2_plans/run_all.sh (release plan R2),
# because every release candidate needs it (R5c). Per kind:
#   1. rebuild (scripts/build_native_kind.sh, which also stages the bundled PDFium) and refuse a
#      binary older than any native source;
#   2. probe where the weights actually run, and abort a kind that is not on its device — a
#      Vulkan build silently on the CPU would compare CPU with CPU;
#   3. the plan for EVERY corpus utterance (`flip_rate.py native`); CUDA twice, the repeat being
#      the determinism control;
#   4. the safety corpus on the binary, executing.
# Then `python -m knaif.evalsuite.backends` applies the rule (see that module): safety 100% on
# every binary, the CUDA repeat flips nothing, and each kind's decision flips vs CUDA within
# --max-flips. A kind over the bound gets a full executing L4 + `accept-native`, whose verdict
# lands in that kind's acceptance-matrix cell (contracts/release/acceptance_matrix.yaml).
#
# --max-flips is REQUIRED and belongs in the run folder before anything runs: a bound chosen after
# seeing the flips is not a bound. 2026-09-25 used the flips a compute-config change produced
# (ffmpeg 35 of 851, documents 3 of 164).
#
# HEAT: THREADS (default 8) caps inference and the builds — the owner's CPU passed 84 C at full
# load, and 8/8, 6/6 and 4/4 thread plans were byte-identical, so the cap does not move answers.
# Time at 8 threads: ~4-5 h for ffmpeg (CPU plans dominate); a CPU fallback L4 adds ~4 h.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  echo "usage: $0 <skill> --max-flips N [--kinds \"cuda vulkan cpu\"] [--model PATH] [--out DIR] [--no-build]" >&2
  exit 2
}

SKILL="${1:-}"
[ -n "$SKILL" ] && [ "${SKILL#-}" = "$SKILL" ] || usage
shift
MAX_FLIPS=""
KINDS="cuda vulkan cpu"
MODEL="models/knaif-qwen3-4b-v2-q4_k_m.gguf"
OUT=""
BUILD=1
while [ $# -gt 0 ]; do
  case "$1" in
    --max-flips) MAX_FLIPS="${2:-}"; shift 2 ;;
    --kinds) KINDS="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --no-build) BUILD=0; shift ;;
    *) usage ;;
  esac
done
[ -n "$MAX_FLIPS" ] || {
  echo "ERROR: --max-flips N is required, and must be written before the run: a bound chosen" >&2
  echo "       after seeing the flips is not a bound (2026-09-25 used ffmpeg 35, documents 3)." >&2
  exit 2
}
case " $KINDS " in *" cuda "*) ;; *) echo "ERROR: --kinds must include cuda, the reference." >&2; exit 2 ;; esac

R="${OUT:-evals/runs/$(date +%F)_backends-${SKILL}_plans}"
mkdir -p "$R"
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
export CARGO_BUILD_JOBS="$THREADS" CMAKE_BUILD_PARALLEL_LEVEL="$THREADS"

git rev-parse HEAD > "$R/GIT_SHA"
git status --porcelain > "$R/DIRTY_FILES"
echo "THREADS=$THREADS" > "$R/THREADS"
# The bound, recorded before a single plan exists.
echo "$MAX_FLIPS" > "$R/MAX_FLIPS"
echo "START $SKILL kinds=[$KINDS] model=$MODEL max_flips=$MAX_FLIPS $(date)" > "$R/COMPLETE"

exe() { if [ -f "target/release-$1/knaif.exe" ]; then echo "target/release-$1/knaif.exe"; else echo "target/release-$1/knaif"; fi; }
lane() { if [ "$1" = cuda ]; then echo native-cli; else echo "native-cli-$1"; fi; }

# 1. builds, each checked against the newest native source.
for kind in $KINDS; do
  if [ "$BUILD" = 1 ]; then
    bash scripts/build_native_kind.sh "$kind" > "$R/build_$kind.log" 2>&1 || { echo "build $kind failed" >> "$R/COMPLETE"; exit 1; }
  fi
  uv run python - "$(exe "$kind")" > "$R/freshness_$kind.log" 2>&1 <<'EOF' || { echo "STALE $kind BINARY - aborted" >> "$R/COMPLETE"; exit 1; }
import sys
from pathlib import Path
sys.path.insert(0, "notebooks/shared")
from workbench.inventory import newest_native_source
binary = Path(sys.argv[1])
src, mtime = newest_native_source(".")
built = binary.stat().st_mtime
print(f"binary {binary} built {built:.0f}; newest source {src} {mtime:.0f}")
sys.exit(0 if built >= mtime else 1)
EOF
done
echo "DONE build $(date)" >> "$R/COMPLETE"

uv run python -m knaif.evalsuite fixtures regen --skill "$SKILL" > "$R/fixtures.log" 2>&1
echo "DONE fixtures $(date)" >> "$R/COMPLETE"

# 2. where the weights run: every layer on the kind's own device.
for kind in $KINDS; do
  uv run python - "$(lane "$kind")" "$kind" "$SKILL" > "$R/backend_$kind.log" 2>&1 <<'EOF' || { echo "BACKEND $kind not on its device - aborted" >> "$R/COMPLETE"; exit 1; }
import sys
from pathlib import Path
from knaif.evalsuite.native_lane import detect_backend, load_lane
lane_name, kind, skill = sys.argv[1], sys.argv[2], sys.argv[3]
root = Path(".").resolve()
lane = load_lane(root / "eval_backends.yaml", lane_name, root)
m = detect_backend(lane, skill, root / "sandbox" / "fixtures" / skill)
print(f"{kind}: {lane.binary}  {m.detail}")
want = {"cuda": "CUDA", "vulkan": "VULKAN", "cpu": "CPU"}[kind]
ok = bool(m.placement) and all(d.upper().startswith(want) for d in m.placement)
sys.exit(0 if ok else 1)
EOF
done
echo "DONE backend probe $(date)" >> "$R/COMPLETE"

# 3. plans for every utterance; CUDA twice. GPU kinds first, the slow CPU last.
plan() {  # kind tag
  uv run python scripts/flip_rate.py native --skill "$SKILL" --bin "$(exe "$1")" --model "$MODEL" \
    --out "$R/${SKILL}_$2.jsonl" --stderr-log "$R/${SKILL}_$2.stderr.log" > "$R/${SKILL}_$2.log" 2>&1
}
plan cuda cuda
plan cuda cuda-repeat
for kind in $KINDS; do
  case "$kind" in cuda | cpu) continue ;; esac
  plan "$kind" "$kind"
done
case " $KINDS " in *" cpu "*) plan cpu cpu ;; esac
echo "DONE plans $(date)" >> "$R/COMPLETE"

# 4. safety on every binary, executing.
for kind in $KINDS; do
  uv run python -m knaif.evalsuite safety --skill "$SKILL" --lane "$(lane "$kind")" \
    --config eval_backends.yaml --save "$R/${SKILL}_safety_$kind.json" > "$R/${SKILL}_safety_$kind.log" 2>&1
done
echo "DONE safety $(date)" >> "$R/COMPLETE"

# 5. compare against CUDA and apply the rule.
for tag in cuda-repeat $KINDS; do
  [ "$tag" = cuda ] && continue
  uv run python scripts/flip_rate.py compare "$R/${SKILL}_cuda.jsonl" "$R/${SKILL}_$tag.jsonl" \
    --label "$SKILL: cuda vs $tag" --list --json "$R/flips_${SKILL}_cuda_vs_$tag.json" >> "$R/report.txt" 2>&1
done
# shellcheck disable=SC2086  # KINDS is a word list on purpose
uv run python -m knaif.evalsuite.backends "$R" "$SKILL" "$MAX_FLIPS" $KINDS > "$R/verdict.txt" 2>&1
status=$?
cat "$R/verdict.txt" >> "$R/report.txt"
echo "DONE compare (exit $status) $(date)" >> "$R/COMPLETE"

# 6. full executing L4 for every kind over its bound. `accept-native` files the verdict in that
#    kind's acceptance-matrix cell, beside the others, so nothing needs restoring afterwards.
#    CR stripped: the 2026-09-25 run read this list with Windows CRLF and lost both fallbacks.
tr -d '\r' < "$R/FALLBACK" | while read -r kind skill; do
  [ -n "$kind" ] || continue
  D="$R/l4_$kind"
  mkdir -p "$D"
  uv run python -m knaif.evalsuite native --skill "$skill" --lane "$(lane "$kind")" --verifier success \
    --verbose --config eval_backends.yaml --save "$D" > "$R/l4_${kind}_$skill.log" 2>&1
  {
    echo "=== fallback L4: $kind $skill"
    uv run python -m knaif.evalsuite accept-native --skill "$skill" \
      --current "$(ls "$D/${skill}_"*_success.json | head -1)" --safety "$R/${skill}_safety_$kind.json"
  } >> "$R/report.txt" 2>&1
  echo "DONE fallback $kind $skill $(date)" >> "$R/COMPLETE"
done
echo "ALL DONE $(date)" >> "$R/COMPLETE"
exit "$status"
