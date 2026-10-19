#!/usr/bin/env bash
# Corpus v2 re-lock: the current models re-measured on the reviewed corpus, then the four
# baselines locked from those runs. Plan docs/plans/2026-10-14-faster-release-evidence.md, Part G
# (G2.3) and T7/T7b. PREPARED 2026-10-17; the owner starts each stage ("green light"), nothing
# runs before.
#
# What changes (owner, 2026-10-17: "finish all changes on the corpus before relocking"):
#   REMOVALS, 10: the 8 the audit proposed (every utterance of each removed row is in the row
#   kept in its place, with the same expected result) plus 2 labelling decisions, all listed in
#   evals/corpus-review/2026-10-17/<skill>/decisions.json:
#     ffmpeg    007 -> kept 080, 050 -> 179, 063 -> 080, 010 -> 168, 023 -> 097   (328 -> 323)
#     documents 067 -> 006, 071 -> 007, 075 -> 008, 082 -> 142, 115 -> 137        (143 -> 138)
#   NEW ROWS, tag `hard2` (G2.2): evals/corpus-review/2026-10-17/new_rows.<skill>.jsonl —
#     10 ffmpeg (chains and multi-file requests over the tools that had no chain row:
#     concat, prepare_for_platform, adjust_volume; 323 -> 333) and 8 documents (chains over
#     unlock, ocr, convert, reorder, merge, watermark; 138 -> 146), checked by
#     `evalsuite corpus-check` (schema, tag, no id or utterance the corpus or train.jsonl has).
#     Appended as-is; the owner validates the ffmpeg baselines (validated_by null) in the
#     authoring notebook, never the agent.
#   Kept rows stay byte-identical (eval_kept.sha256, checked by test_corpus_integrity); removed
#   rows stay readable in eval_removed.jsonl.
#
# What is measured: Python runtime, success verifier, CUDA (llama-cpp-python), the shipped
# models knaif-qwen3-4b-v2 (eval key qwen3-4b-sft-v4-flat-q4) and knaif-qwen3-1.7b-v2 (eval key
# qwen3-1.7b-sft-v9-flat-q6), the prompt as on this branch (unchanged since 1.3.0), both skills
# in full, plus both safety corpora. Fixtures regenerated first. One run at a time, 8 threads.
#
#   bash evals/runs/<this folder>/run_all.sh check     preflight only                      ~1 min
#   bash evals/runs/<this folder>/run_all.sh remove    apply the 10 removals + the tests    ~3 min
#   bash evals/runs/<this folder>/run_all.sh append    append the 18 new rows + the tests   ~3 min
#   bash evals/runs/<this folder>/run_all.sh run       4B + 1.7B, both skills, safety       ~40 min
#   bash evals/runs/<this folder>/run_all.sh compare   row-diff vs the previous source runs minutes
#   bash evals/runs/<this folder>/run_all.sh lock      write the 4 snapshots (source_run)   seconds
#
# Budgets: the 2026-09-26 reference run took 19 min for the 4B and 13 min for the 1.7B.
#
# RULES (pre-registered 2026-10-17, before any stage runs; a change afterwards is an amendment,
# recorded below with its reason, before the stage it affects):
#   R1  The `remove` stage lands only the 10 approved ids, through `evalsuite corpus-remove`
#       against the committed 2026-10-17 decisions.json files; nothing is reworded. The `append`
#       stage lands only the committed new_rows files, after `corpus-check` passes; the test
#       suite passes after each.
#   R2  `run` is one full run per model per skill on the reviewed corpus, success verifier,
#       CUDA; safety on the whole safety corpus per model per skill, 100% required (a miss goes
#       to the owner; nothing is locked while a safety miss stands).
#   R3  `compare` diffs every new board row by row against the previous source run of the same
#       model and skill (4B: evals/runs/2026-09-26_r3a-reference_success/sft-v4; 1.7B:
#       evals/runs/2026-09-27_r5a-1.7b-sft-v9_success/1.7b) on the kept rows. Same model, same
#       prompt, same backend, so the prediction is NO grade differs. Differences are listed and
#       go to the owner before `lock`; they do not block the lock by themselves (G2.3: the
#       reference run on the reviewed corpus is the re-lock), but each is explained in report.md.
#   R4  `lock` writes each snapshot with `evalsuite lock` from the saved board (source_run
#       recorded), one model x skill at a time; the owner commits each in its own commit
#       (G2.3). Acceptance at the model's own bar (`eval-accept`, with safety) is run and
#       printed for each board; NOT ACCEPTED goes to the owner before that lock.
#   R5  The `hard2` slice is reported by this run and its floor is written AFTER the run
#       measures it (G2.2), by the owner, in acceptance.yaml; it is not a gate on this re-lock
#       and existing floors are unchanged.
#
# PREDICTION (not a rule): every kept row grades as in the previous source run; the aggregates
# move by the removed rows' arithmetic and the new hard2 rows (expected below the corpus average:
# chains are where the shipped models are weakest; previous aggregates 4B ffmpeg 0.9431/0.9841,
# documents 0.9817/0.9803; 1.7B ffmpeg 0.9199/0.9785, documents 0.9634/0.9945); safety 11/11 and
# 9/9 for both models.
#
# AMENDMENTS:
#   A1 2026-10-18 11:56, after attempt 1 (owner decision; attempt 1's boards kept in
#       attempt1-tags-v1/). `lock` refused 4B documents, 1.7B ffmpeg and 1.7B documents: every unmet
#       floor was tripped by hard2 rows carrying the tool and chain tags of existing slices (G1's
#       warning; against G2.2's intent that hard rows do not gate the shipped models on existing
#       floors). The 18 new rows now carry `hard2` only (plus the floor-less `fan_out` /
#       `multi_file`), in eval.jsonl and the review record; kept rows untouched (eval_kept.sha256
#       holds). The documents aggregate outcome floor is re-set 0.95 -> 0.91 for corpus v2 (hard2
#       rows included; measured 0.931 / 0.926), as every floor was first set: measured, minus a
#       margin. The documents `compress` slice fell to 14 utterances (two duplicates removed),
#       under min_rate_rows, so its rate floor 0.90 is restated as `max_failures: 1` (the same bar
#       as a count). No other floor changes; the hard2 slice's own floor is the owner's, written
#       after this run (G2.2). `run`, `compare` and `lock` re-ran from the start for all four cells
#       (4B ffmpeg included, so the four snapshots share one tagging); model outputs were
#       identical to attempt 1 on every row, so the re-run changed the tags, not the grades.
#
# Outputs: this folder: COMPLETE, 4b/ and 1.7b/ (boards WITH rows — committed, they are the
# source runs the snapshots name), *_safety.json, *_accept.txt, comparison/<model>_<skill>.json,
# report.md (written by hand after `compare`).

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh check|remove|append|run|compare|lock"
STAGE="${1:?$USAGE}"
case "$STAGE" in check | remove | append | run | compare | lock) ;; *) echo "$USAGE" >&2; exit 2 ;; esac

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
AUDIT=evals/corpus-review/2026-10-17
REMOVE_FFMPEG=ffmpeg_007,ffmpeg_050,ffmpeg_063,ffmpeg_010,ffmpeg_023
REMOVE_DOCUMENTS=documents_067,documents_071,documents_075,documents_082,documents_115
PREV_4B=evals/runs/2026-09-26_r3a-reference_success/sft-v4
PREV_17B=evals/runs/2026-09-27_r5a-1.7b-sft-v9_success/1.7b
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
UV="uv run --no-sync"
key() { case "$1" in 4b) echo qwen3-4b-sft-v4-flat-q4 ;; 1.7b) echo qwen3-1.7b-sft-v9-flat-q6 ;; esac; }
pyname() { case "$1" in 4b) echo knaif-qwen3-4b-v2 ;; 1.7b) echo knaif-qwen3-1.7b-v2 ;; esac; }
prev() { case "$1" in 4b) echo "$PREV_4B" ;; 1.7b) echo "$PREV_17B" ;; esac; }

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
$UV python -m knaif._gpu_check 2>&1 | tail -1 | tee -a "$R/COMPLETE" | grep -qi "cuda\|gpu" \
  || abort "llama-cpp-python has no GPU offload (a plain uv run replaced the CUDA build)"
for s in ffmpeg documents; do
  [ -s "$AUDIT/$s/decisions.json" ] || abort "missing $AUDIT/$s/decisions.json"
  [ -s "$AUDIT/new_rows.$s.jsonl" ] || abort "missing $AUDIT/new_rows.$s.jsonl"
done
for m in 4b 1.7b; do for s in ffmpeg documents; do
  ls "$(prev "$m")/${s}_$(key "$m")_success.json" > /dev/null 2>&1 || abort "missing previous source run for $m $s"
done; done
if [ "$STAGE" = check ]; then log "END check: preflight passed"; exit 0; fi

if [ "$STAGE" = remove ]; then
  [ -f skills/ffmpeg/data/eval_removed.jsonl ] && abort "ffmpeg was already reviewed (eval_removed.jsonl exists)"
  $UV python -m knaif.evalsuite corpus-remove --skill ffmpeg --audit "$AUDIT/ffmpeg/decisions.json" \
    --ids "$REMOVE_FFMPEG" --date 2026-10-17 | tee -a "$R/COMPLETE" || abort "ffmpeg removal refused"
  $UV python -m knaif.evalsuite corpus-remove --skill documents --audit "$AUDIT/documents/decisions.json" \
    --ids "$REMOVE_DOCUMENTS" --date 2026-10-17 | tee -a "$R/COMPLETE" || abort "documents removal refused"
  $UV pytest python/core/tests/test_evalsuite_corpus_review.py python/core/tests/test_train_data_integrity.py \
    python/core/tests/test_evalsuite_corpus.py -q -p no:cacheprovider > "$R/remove-tests.log" 2>&1 \
    || abort "tests fail after the removal (see $R/remove-tests.log)"
  log "END remove: 10 rows removed, tests pass; next: append"
  exit 0
fi

if [ "$STAGE" = append ]; then
  for s in ffmpeg documents; do
    [ -f "skills/$s/data/eval_removed.jsonl" ] || abort "$s: run the remove stage first"
    $UV python -m knaif.evalsuite corpus-check --skill "$s" --rows "$AUDIT/new_rows.$s.jsonl" --tag hard2       | tee -a "$R/COMPLETE" || abort "$s: corpus-check refused the new rows"
    cat "$AUDIT/new_rows.$s.jsonl" >> "skills/$s/data/eval.jsonl"
  done
  $UV pytest python/core/tests/test_evalsuite_corpus_review.py python/core/tests/test_train_data_integrity.py     python/core/tests/test_evalsuite_corpus.py -q -p no:cacheprovider > "$R/append-tests.log" 2>&1     || abort "tests fail after the append (see $R/append-tests.log)"
  log "END append: 18 rows appended, tests pass; the owner commits skills/*/data (corpus v2)"
  exit 0
fi

if [ "$STAGE" = run ]; then
  for s in ffmpeg documents; do
    $UV python -m knaif.evalsuite fixtures regen --skill "$s" >> "$R/fixtures.log" 2>&1 || abort "fixtures $s"
  done
  for m in 4b 1.7b; do
    mkdir -p "$R/$m"
    for s in ffmpeg documents; do
      $UV python -m knaif.evalsuite run --skill "$s" --verifier success --verbose \
        --config eval_backends.yaml --backends "$(key "$m")" --save "$R/$m" > "$R/$m/$s.log" 2>&1 \
        || failed "run $m $s (see $R/$m/$s.log)"
      [ -s "$R/$m/${s}_$(key "$m")_success.json" ] || failed "run $m $s: no board"
      $UV python -m knaif.evalsuite safety --skill "$s" --config eval_backends.yaml \
        --backends "$(key "$m")" --save "$R/$m/${s}_safety.json" > "$R/$m/${s}_safety.log" 2>&1
      log "run $m $s: safety exit $? ($(grep -m1 -E 'passed|rejected|[0-9]+/[0-9]+' "$R/$m/${s}_safety.log" | head -c 120))"
      log "run $m $s: $(grep -m1 -E 'Outcome accuracy' "$R/$m/$s.log" | tr -s ' ') $(grep -m1 -E 'Avg knaif score' "$R/$m/$s.log" | tr -s ' ')"
    done
  done
fi

if [ "$STAGE" = compare ]; then
  mkdir -p "$R/comparison"
  for m in 4b 1.7b; do for s in ffmpeg documents; do
    $UV python -m knaif.evalsuite row-diff "$R/$m/${s}_$(key "$m")_success.json" \
      "$(prev "$m")/${s}_$(key "$m")_success.json" --out "$R/comparison/${m}_${s}.json" > "$R/comparison/${m}_${s}.txt" 2>&1 \
      || failed "row-diff $m $s"
    log "compare $m $s vs previous source run: $(sed -n '2,3p' "$R/comparison/${m}_${s}.txt" | tr -s ' \n' ' ')"
    grep "grade differs" "$R/comparison/${m}_${s}.txt" | sed 's/^/    /' | tee -a "$R/COMPLETE"
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
  log "lock: the owner commits each snapshot in its own commit (G2.3), then re-runs the gate"
fi

[ "$FAILED" = 0 ] || { log "END $STAGE: FAILED"; exit 1; }
log "END $STAGE"
