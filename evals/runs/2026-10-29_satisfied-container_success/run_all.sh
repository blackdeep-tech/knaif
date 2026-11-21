#!/usr/bin/env bash
# satisfied_args, measured on rows first (owner, 2026-10-29): compress_video accepts `container: mp4`
# (not in the prompt), so "convert clip_tiny.mov to mp4 and make it smaller" plans the compress
# instead of asking (ffmpeg_size_003 #0 on the 4B), and a compress that is not smaller delivers the
# conversion. superseded_drop_rule/ is the first attempt (WHY.txt). corpus.jsonl here: every row whose
# expected tools or recorded plans (either model, 2026-10-29 re-lock) use compress_video, plus
# ffmpeg_045 ("smaller and convert it to mkv"), 56 rows / 163 utterances. Both shipped models,
# executing verifier (`success`), Python runtime, CUDA. The full re-lock follows from this.
#
#   bash evals/runs/<this folder>/run_all.sh run       4B + 1.7B on corpus.jsonl
#   bash evals/runs/<this folder>/run_all.sh compare   row-diff against the 2026-10-29 re-lock
#
# Watch: bash scripts/watch_run.sh evals/runs/<this folder>

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh run|compare"
STAGE="${1:?$USAGE}"
case "$STAGE" in run | compare) ;; *) echo "$USAGE" >&2; exit 2 ;; esac

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
PREV=evals/runs/2026-10-29_relock-compress-result_success
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

if [ "$STAGE" = run ]; then
  # knaif's own check: a bare `import llama_cpp` cannot find the CUDA DLLs, knaif adds their folders.
  .venv/Scripts/python.exe -m knaif._gpu_check 2>&1 | grep -q 'GPU offload supported: True' \
    || abort "llama-cpp-python has no GPU offload (a plain uv run replaced the CUDA build)"
  $UV python -m knaif.evalsuite fixtures regen --skill ffmpeg > "$R/fixtures.log" 2>&1 || abort "fixtures"
  for m in 4b 1.7b; do
    mkdir -p "$R/$m"
    log "run $m ffmpeg"
    $UV python -m knaif.evalsuite run --skill ffmpeg --corpus "$R/corpus.jsonl" --verifier success \
      --verbose --config eval_backends.yaml --backends "$(key "$m")" --save "$R/$m" \
      > "$R/$m/ffmpeg.log" 2>&1 || failed "run $m"
  done
fi

if [ "$STAGE" = compare ]; then
  mkdir -p "$R/comparison"
  for m in 4b 1.7b; do
    board="$R/$m/ffmpeg_$(key "$m")_success.json"
    $UV python -m knaif.evalsuite row-diff "$board" "$PREV/$m/ffmpeg_$(key "$m")_success.json" \
      --out "$R/comparison/${m}_ffmpeg.json" > "$R/comparison/${m}_ffmpeg.txt" 2>&1 \
      || failed "row-diff $m"
    log "compare $m vs the 2026-10-29 re-lock: $(sed -n '2,3p' "$R/comparison/${m}_ffmpeg.txt" | tr -s ' \n' ' ')"
  done
fi

[ "$FAILED" = 0 ] && log "ALL DONE $STAGE" || log "ABORT $STAGE (see above)"
