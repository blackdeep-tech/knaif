#!/usr/bin/env bash
# T0 (a)-(c) on the maintainer's machine: does planning a cell in ONE batch (one model load) and
# executing each row from its plan give the same per-row grades as 1.3.0's cells, and what does
# it save? Plan docs/plans/2026-10-14-faster-release-evidence.md, Part B (B1) and Tasks (T0, T3).
#
# What is measured: the FROZEN 1.3.0 Windows artifact, dist/knaif-1.3.0-windows-x64.zip (sha256
# pinned below, the zip 1.3.0's Windows cells measured), model knaif-qwen3-4b-v2. Every stage
# compares its board row by row (`evalsuite row-diff`) with the same cell of 1.3.0's Windows run,
# evals/runs/2026-10-13_r130-windows_success/4b/<backend>/. Skills, corpora and verifiers are
# unchanged since the freeze commit 8c94cbe (checked below), so the grades are comparable.
#
# How a row runs (`evalsuite native --batch-plan`): `knaif plan --skill <s> --batch` plans every
# selected row with one model load, in a folder holding every fixture; then each row runs as in
# L4, `knaif run <s> --yes --overwrite` in its own folder, but with no model: the mock backend
# replays that row's batch plan (KNAIF_LLM_BACKEND=mock, KNAIF_LLM_MOCK_RESPONSE), so the
# unsafe-phrase screen, validation, expansion, the clarify gate and execution are the shipped
# path. A cold-start sample of 30 rows (evenly spaced, `evalsuite shards --rows 30`) runs live
# with the model, and its plans are compared with the batch's.
#
# Runs on the Windows box (RTX 5080), Git Bash, from the repo root. ONE STAGE PER INVOCATION,
# each only after the owner has approved it (nothing in parallel on this machine):
#
#   bash evals/runs/<this folder>/run_all.sh check    preflight only; measures nothing        ~1 min
#   bash evals/runs/<this folder>/run_all.sh exec     T0(b): execution alone, NO GPU, no model:
#                                                     1.3.0's own CUDA batch plans (the daemon
#                                                     check's in-process pass) replayed      ~20-30 min
#   bash evals/runs/<this folder>/run_all.sh setup    install + verify the CUDA payload       ~5 min
#   bash evals/runs/<this folder>/run_all.sh cuda     T0(a)+(c): batch-planned 4B cell, CUDA  ~25-35 min
#   bash evals/runs/<this folder>/run_all.sh vulkan   T0(c): the same cell on Vulkan          ~25-35 min
#   bash evals/runs/<this folder>/run_all.sh cpu      T0(c): 100 rows per skill on the CPU    ~30-45 min
#
# Budgets are estimates from 1.3.0's timings on this box, not promises. Each stage also prints
# where its time went (board `timing`: plan_batch_s, rows_s).
#
# What would answer B1, written before any result (not gate rules: T0 measures, it accepts
# nothing, and `accept-native` refuses a batch-planned board):
#   - every row's grade equals 1.3.0's on the same cell (same outcome, same correctness, same
#     knaif score); rows that differ are listed and explained one by one;
#   - on CUDA, the batch plans equal 1.3.0's `run` plans on every row the model planned (already
#     seen in 1.3.0's own evidence, recomputed here on a fresh batch);
#   - every cold-start row plans the same live as in the batch;
#   - the time: plan_batch_s + rows_s against 1.3.0's cell (CUDA ffmpeg ~36 min, documents ~10).
#
# Outputs: this folder: COMPLETE (the stage log), <stage>/<skill>/comparison.json (the row
# diff against 1.3.0, committed), boards and logs (ignored).

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh check|exec|setup|cuda|vulkan|cpu"
STAGE="${1:?$USAGE}"
case "$STAGE" in check | exec | setup | cuda | vulkan | cpu) ;; *) echo "$USAGE" >&2; exit 2 ;; esac

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
FREEZE=8c94cbe
ZIP=dist/knaif-1.3.0-windows-x64.zip
ZIP_SHA256=7c289f3e5637b3d3c71fa8843666f1810b44ebbcdd458e23de9f42dba305d0ad  # 1.3.0's Windows cells
ART=sandbox/r130/artifact                                                      # lane r130-win-4b
EXE_REL="$ART/knaif-1.3.0-windows-x64/bin/knaif.exe"
CUDA_DIR="$(cygpath -aw sandbox/r130/backends-cuda)"
EMPTY_DIR="$(cygpath -aw sandbox/r130/backends-empty)"
REF=evals/runs/2026-10-13_r130-windows_success/4b                  # 1.3.0's 4B Windows boards
BATCH130=evals/parity/2026-10-13_r130-win-daemon-plan-equality/4b  # 1.3.0's CUDA batch plans
LANE=r130-win-4b
GGUF=models/knaif-qwen3-4b-v2-q4_k_m.gguf
GGUF_SHA256=a9c26005e94622d63d1c6e64cb1c1b42084dcc37f6be7d69f8d5967f9c13aab7
THREADS="${THREADS:-8}"                     # as 1.3.0's cells
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST KNAIF_N_GPU_LAYERS KNAIF_LLM_BACKEND KNAIF_LLM_MOCK_RESPONSE
export KNAIF_NO_DAEMON=1                    # in process, as 1.3.0's L4 cells
UV="uv run --no-sync"                       # a plain `uv run` re-syncs llama-cpp-python to its CPU build

mkdir -p "$R"
LOCK="$R/.running"
mkdir "$LOCK" 2>/dev/null || { echo "another stage is running ($LOCK)" >&2; exit 2; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
log() { echo "$(date '+%F %T') $*" | tee -a "$R/COMPLETE"; }
FAILED=0
failed() { log "FAILED $STAGE: $*"; FAILED=1; }
abort() { log "ABORTED $STAGE: $*"; exit 1; }

# -- preflight -------------------------------------------------------------------------------------
log "START $STAGE"
git merge-base --is-ancestor "$FREEZE" HEAD || abort "HEAD does not contain the freeze commit $FREEZE"
[ -z "$(git diff --name-only "$FREEZE" HEAD -- skills)" ] \
  || abort "skills/ (corpora, verifiers, fixtures) differ from the freeze: grades would not be comparable"
echo "$ZIP_SHA256  $ZIP" | sha256sum -c - > /dev/null 2>&1 || abort "$ZIP sha256 is not the pinned one"
echo "$GGUF_SHA256  $GGUF" | sha256sum -c - > /dev/null 2>&1 || abort "$GGUF sha256 mismatch"
for skill in ffmpeg documents; do
  for b in cuda vulkan cpu; do
    [ -s "$REF/$b/${skill}_${LANE}_success.json" ] || abort "missing 1.3.0 board $REF/$b/${skill}_${LANE}_success.json"
  done
  [ -s "$BATCH130/$skill.inprocess.jsonl" ] || abort "missing 1.3.0 batch plans $BATCH130/$skill.inprocess.jsonl"
done
rm -rf "$ART" && mkdir -p "$ART" sandbox/r130/backends-empty && (cd "$ART" && unzip -q "$OLDPWD/$ZIP") \
  || abort "unzip failed"
EXE="$(cygpath -aw "$EXE_REL")"
[ "$("$EXE" --version)" = "knaif 1.3.0 (inference: llama.cpp)" ] || abort "the unpacked binary is not knaif 1.3.0"
if [ "$STAGE" = check ]; then log "END $STAGE: preflight passed"; exit 0; fi

if [ "$STAGE" = setup ]; then
  KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend install cuda > "$R/setup.log" 2>&1 || abort "backend install cuda failed"
fi
if [ "$STAGE" = setup ] || [ "$STAGE" = cuda ]; then
  # `backend verify` exits 0 for "not installed" too: require the positive answer.
  KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend verify cuda > "$R/verify.$STAGE.log" 2>&1
  grep -q "^cuda: ok" "$R/verify.$STAGE.log" || abort "the CUDA payload does not verify as ok (run the setup stage)"
fi
if [ "$STAGE" = setup ]; then log "END $STAGE: CUDA payload installed and verified"; exit 0; fi

case "$STAGE" in
  cuda) ENVS=(KNAIF_BACKENDS_DIR="$CUDA_DIR"); WANT=CUDA0 ;;
  vulkan) ENVS=(KNAIF_BACKENDS_DIR="$EMPTY_DIR"); WANT=Vulkan0 ;;
  # exec: no GPU at all. The lane's one placement probe then loads the model on the CPU; no row
  # does, the mock plans them all.
  cpu | exec) ENVS=(KNAIF_BACKENDS_DIR="$EMPTY_DIR" CUDA_VISIBLE_DEVICES=-1 GGML_VK_VISIBLE_DEVICES=); WANT=CPU ;;
esac

field() {  # $1 board, $2 python expression over `b` -> printed value
  $UV python -c "import json,sys; b=json.load(open(sys.argv[1],encoding='utf-8')); print($2)" "$1" 2>/dev/null
}

one() {  # $1 skill, $2 reference board: run the stage's cell for one skill, then diff it
  local skill="$1" ref="$2" d="$R/$STAGE/$skill" board args=() got
  rm -rf "$d" && mkdir -p "$d"
  $UV python -m knaif.evalsuite fixtures regen --skill "$skill" > "$d/fixtures.log" 2>&1 \
    || { failed "$skill: fixture regeneration failed"; return 1; }
  case "$STAGE" in
    exec) args=(--batch-plans "$BATCH130/$skill.inprocess.jsonl") ;;
    cpu)
      $UV python -m knaif.evalsuite shards --skill "$skill" --count 1 --rows 100 --out "$d/sample" > /dev/null \
        || { failed "$skill: drawing the 100-row sample"; return 1; }
      args=(--batch-plan --only "$d/sample/shard-00.json") ;;
    *)
      $UV python -m knaif.evalsuite shards --skill "$skill" --count 1 --rows 30 --out "$d/coldstart" > /dev/null \
        || { failed "$skill: drawing the cold-start sample"; return 1; }
      args=(--batch-plan --live-sample "$d/coldstart/shard-00.json") ;;
  esac
  board="$d/${skill}_${LANE}_success.json"
  env "${ENVS[@]}" $UV python -m knaif.evalsuite native --skill "$skill" --lane "$LANE" "${args[@]}" \
    --verifier success --verbose --config eval_backends.yaml --save "$d" > "$d/$skill.log" 2>&1
  [ -s "$board" ] || { failed "$skill: no board written (see $d/$skill.log)"; return 1; }
  got="$(field "$board" "b.get('compute_backend')")"
  [ "$got" = "$WANT" ] || { failed "VOID $skill: the probe ran on '$got', expected $WANT"; return 1; }
  $UV python -m knaif.evalsuite row-diff "$board" "$ref" --out "$d/comparison.json" > "$d/rowdiff.txt" 2>&1 \
    || { failed "$skill: row-diff failed"; return 1; }
  log "$STAGE $skill: placement $got; timing $(field "$board" "b.get('timing')"); cold start $(field "$board" "b.get('batch_live')")"
  sed 's/^/    /' "$d/rowdiff.txt" | head -40 | tee -a "$R/COMPLETE"
}

for skill in ffmpeg documents; do
  case "$STAGE" in
    exec) one "$skill" "$REF/cuda/${skill}_${LANE}_success.json" ;;
    *) one "$skill" "$REF/$STAGE/${skill}_${LANE}_success.json" ;;
  esac
done
[ "$FAILED" = 0 ] || { log "END $STAGE: FAILED"; exit 1; }
log "END $STAGE"
