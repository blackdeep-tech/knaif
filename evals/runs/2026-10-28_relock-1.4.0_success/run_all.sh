#!/usr/bin/env bash
# Re-lock for release 1.4.0 (owner, 2026-10-28): both shipped models, both skills, on the
# generation-1 prompts and the 1.4.0 corpus (the feature rows folded into eval.jsonl, kept rows
# ffmpeg_204/208 fixed, the second reject example). Executing verifier (`success`), CUDA, plus the
# whole safety corpus per model per skill. Modelled on evals/runs/2026-10-18_relock-corpus-v2_success.
#
#   bash evals/runs/<this folder>/run_all.sh check     preflight: GPU, clean tree, previous runs
#   bash evals/runs/<this folder>/run_all.sh run       4B + 1.7B, both skills, safety   ~60-75 min
#   bash evals/runs/<this folder>/run_all.sh documents the documents arm again, both models   ~5 min
#   bash evals/runs/<this folder>/run_all.sh compare   row-diff against the 2026-10-18 re-lock
#   bash evals/runs/<this folder>/run_all.sh lock      accept at each model's bar, then lock
#   bash evals/runs/<this folder>/run_all.sh subset    re-derive each skill's release subset
#
# Rules this run keeps (docs/EVAL_FRAMEWORK.md, *Changing a corpus that has a locked baseline*):
# - re-lock once, after every corpus change is in (5fff335), and each snapshot in its own commit;
# - the new review tags (url_input, alerts_and_folders, many_files) get floors written from THIS
#   run, the one that first measures them, before `lock`; nothing is locked while a safety miss
#   stands (a miss goes to the owner);
# - the release subset is re-derived after the corpus change (`subset`).
#
# `documents`: the first `run` measured the documents arm on fixtures whose recorded hashes were
# stale (regenerating the 1.4.0 folder fixtures rewrote the cached samples, see
# test_a_sibling_rewritten_by_a_regenerating_command_gets_its_new_hash), so `accept` refused
# both documents boards for fixture integrity. After the regen fix and a forced documents regen,
# this stage measures that arm again; the refused boards are kept in superseded_fixture_integrity/.
# Watch: bash scripts/watch_run.sh evals/runs/<this folder>

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh check|run|documents|compare|lock|subset"
STAGE="${1:?$USAGE}"
case "$STAGE" in check | run | documents | compare | lock | subset) ;; *) echo "$USAGE" >&2; exit 2 ;; esac
SKILLS="ffmpeg documents"
[ "$STAGE" = documents ] && SKILLS=documents

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
PREV=evals/runs/2026-10-18_relock-corpus-v2_success
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
UV="uv run --no-sync"
key() { case "$1" in 4b) echo qwen3-4b-sft-v4-flat-q4 ;; 1.7b) echo qwen3-1.7b-sft-v9-flat-q6 ;; esac; }

mkdir -p "$R"
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
for m in 4b 1.7b; do for s in ffmpeg documents; do
  [ -s "$PREV/$m/${s}_$(key "$m")_success.json" ] || abort "missing previous run for $m $s"
done; done
if [ "$STAGE" = check ]; then
  [ -s "$R/DIRTY_FILES.check" ] && abort "tracked files are modified (see $R/DIRTY_FILES.check)"
  log "END check: preflight passed"
  exit 0
fi

if [ "$STAGE" = run ] || [ "$STAGE" = documents ]; then
  # One line per stage for scripts/watch_run.sh: <log> <rows>.
  : > "$R/TOTALS"
  for m in 4b 1.7b; do
    [ "$STAGE" = run ] && echo "$m/ffmpeg.log 1057" >> "$R/TOTALS"
    echo "$m/documents.log 277" >> "$R/TOTALS"
  done
  for s in $SKILLS; do
    $UV python -m knaif.evalsuite fixtures regen --skill "$s" >> "$R/fixtures.log" 2>&1 || abort "fixtures $s"
  done
  for m in 4b 1.7b; do
    mkdir -p "$R/$m"
    for s in $SKILLS; do
      if [ "$STAGE" = documents ]; then
        mkdir -p "$R/superseded_fixture_integrity/$m"
        for f in "$R/$m/${s}"_*; do [ -e "$f" ] && mv "$f" "$R/superseded_fixture_integrity/$m/"; done
        mv "$R/$m/$s.log" "$R/superseded_fixture_integrity/$m/" 2>/dev/null
      fi
      $UV python -m knaif.evalsuite run --skill "$s" --verifier success --verbose \
        --config eval_backends.yaml --backends "$(key "$m")" --save "$R/$m" > "$R/$m/$s.log" 2>&1 \
        || failed "run $m $s (see $R/$m/$s.log)"
      [ -s "$R/$m/${s}_$(key "$m")_success.json" ] || failed "run $m $s: no board"
      $UV python -m knaif.evalsuite safety --skill "$s" --config eval_backends.yaml \
        --backends "$(key "$m")" --save "$R/$m/${s}_safety.json" > "$R/$m/${s}_safety.log" 2>&1
      log "run $m $s: safety exit $? ($(grep -m1 -E 'as expected' "$R/$m/${s}_safety.log" | tr -s ' '))"
      log "run $m $s: $(grep -m1 -E 'Outcome accuracy' "$R/$m/$s.log" | tr -s ' ') $(grep -m1 -E 'Avg knaif score' "$R/$m/$s.log" | tr -s ' ')"
    done
  done
fi

if [ "$STAGE" = compare ]; then
  mkdir -p "$R/comparison"
  for m in 4b 1.7b; do for s in ffmpeg documents; do
    $UV python -m knaif.evalsuite row-diff "$R/$m/${s}_$(key "$m")_success.json" \
      "$PREV/$m/${s}_$(key "$m")_success.json" --out "$R/comparison/${m}_${s}.json" > "$R/comparison/${m}_${s}.txt" 2>&1 \
      || failed "row-diff $m $s"
    log "compare $m $s vs 2026-10-18: $(sed -n '2,3p' "$R/comparison/${m}_${s}.txt" | tr -s ' \n' ' ')"
  done; done
fi

if [ "$STAGE" = lock ]; then
  for m in 4b 1.7b; do for s in ffmpeg documents; do
    board="$R/$m/${s}_$(key "$m")_success.json"
    $UV python -m knaif.evalsuite accept --skill "$s" --current "$board" --safety "$R/$m/${s}_safety.json" \
      > "$R/$m/${s}_accept.txt" 2>&1
    log "lock $m $s: $(grep -m1 -E '^(NOT )?ACCEPTED' "$R/$m/${s}_accept.txt" || echo 'no verdict line')"
    if grep -q "^NOT ACCEPTED" "$R/$m/${s}_accept.txt"; then
      failed "$m $s NOT ACCEPTED at its bar: goes to the owner before this lock"; continue
    fi
    $UV python -m knaif.evalsuite lock --skill "$s" --current "$board" | tee -a "$R/COMPLETE" || failed "lock $m $s"
  done; done
fi

if [ "$STAGE" = subset ]; then
  for s in ffmpeg documents; do
    $UV python -m knaif.evalsuite release-subset --skill "$s" | tee -a "$R/COMPLETE" || failed "subset $s"
  done
fi

[ "$FAILED" = 0 ] || { log "END $STAGE: FAILED"; exit 1; }
log "END $STAGE"
