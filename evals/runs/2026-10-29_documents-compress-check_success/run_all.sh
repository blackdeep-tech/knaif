#!/usr/bin/env bash
# Documents after the compress fix (2026-10-29): `compress_pdf` now reports its size line and the
# documents result view changed, on code newer than the documents snapshots (locked 2026-10-28 from
# evals/runs/2026-10-28_relock-1.4.0_success). Not a re-lock: the full documents corpus on both
# models, executing verifier, safety, then a row-diff against the boards the snapshots name and the
# acceptance verdict. Any moved row goes to the owner before the release evidence.
#
#   bash evals/runs/<this folder>/run_all.sh run       both models, 277 utt + safety   ~5 min
#
# Watch: bash scripts/watch_run.sh evals/runs/<this folder>

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh run"
STAGE="${1:?$USAGE}"
case "$STAGE" in run) ;; *) echo "$USAGE" >&2; exit 2 ;; esac

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
PREV=evals/runs/2026-10-28_relock-1.4.0_success
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
UV="uv run --no-sync"
key() { case "$1" in 4b) echo qwen3-4b-sft-v4-flat-q4 ;; 1.7b) echo qwen3-1.7b-sft-v9-flat-q6 ;; esac; }

LOCK="$R/.running"
mkdir "$LOCK" 2>/dev/null || { echo "another stage is running ($LOCK)" >&2; exit 2; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
log() { echo "$(date '+%F %T') $*" | tee -a "$R/COMPLETE"; }
FAILED=0
failed() { log "FAILED $STAGE: $*"; FAILED=1; }
abort() { log "ABORTED $STAGE: $*"; exit 1; }

log "START $STAGE"
git rev-parse HEAD > "$R/GIT_SHA.$STAGE"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES.$STAGE"
# knaif's own check: a bare `import llama_cpp` cannot find the CUDA DLLs, knaif adds their folders.
.venv/Scripts/python.exe -m knaif._gpu_check 2>&1 | grep -q 'GPU offload supported: True' \
  || abort "llama-cpp-python has no GPU offload (a plain uv run replaced the CUDA build)"

: > "$R/TOTALS"
for m in 4b 1.7b; do echo "$m/documents.log 277" >> "$R/TOTALS"; done
$UV python -m knaif.evalsuite fixtures regen --skill documents > "$R/fixtures.log" 2>&1 || abort "fixtures"
mkdir -p "$R/comparison"
for m in 4b 1.7b; do
  mkdir -p "$R/$m"
  board="$R/$m/documents_$(key "$m")_success.json"
  $UV python -m knaif.evalsuite run --skill documents --verifier success --verbose \
    --config eval_backends.yaml --backends "$(key "$m")" --save "$R/$m" > "$R/$m/documents.log" 2>&1 \
    || failed "run $m"
  [ -s "$board" ] || { failed "run $m: no board"; continue; }
  $UV python -m knaif.evalsuite safety --skill documents --config eval_backends.yaml \
    --backends "$(key "$m")" --save "$R/$m/documents_safety.json" > "$R/$m/documents_safety.log" 2>&1
  log "run $m: safety exit $? ($(grep -m1 -E 'as expected' "$R/$m/documents_safety.log" | tr -s ' '))"
  log "run $m: $(grep -m1 -E 'Outcome accuracy' "$R/$m/documents.log" | tr -s ' ') $(grep -m1 -E 'Avg knaif score' "$R/$m/documents.log" | tr -s ' ')"
  $UV python -m knaif.evalsuite row-diff "$board" "$PREV/$m/documents_$(key "$m")_success.json" \
    --out "$R/comparison/${m}_documents.json" > "$R/comparison/${m}_documents.txt" 2>&1 || failed "row-diff $m"
  log "compare $m vs the 1.4.0 re-lock: $(sed -n '2,4p' "$R/comparison/${m}_documents.txt" | tr -s ' \n' ' ')"
  $UV python -m knaif.evalsuite accept --skill documents --current "$board" \
    --safety "$R/$m/documents_safety.json" > "$R/$m/documents_accept.txt" 2>&1
  log "accept $m: $(grep -m1 -E '^(NOT )?ACCEPTED' "$R/$m/documents_accept.txt" || echo 'no verdict line')"
done

[ "$FAILED" = 0 ] && log "ALL DONE $STAGE" || log "ABORT $STAGE (see above)"
