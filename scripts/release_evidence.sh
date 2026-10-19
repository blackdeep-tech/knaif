#!/usr/bin/env bash
# A release's Windows evidence, the way plan docs/plans/2026-10-14-faster-release-evidence.md Part I
# ("the rules a release adopts", approved 2026-10-18) measures it. The mechanics only: a release's
# own run_all.sh (copied from scripts/release_evidence_run_all.template.sh at its freeze) pins the
# artifact, the freeze commit and the lanes, carries its gate rules verbatim, and sources this file.
#
# What every cell does (Stage 2: subset + batch planning through the result store):
#   - L4: `evalsuite native --subset --store evals/store --live-sample <this release's cold-start
#     sample>` on the frozen artifact: the model is asked only for the subset prompts the store does
#     not hold (recording the replies), every row executes through `knaif run` by replay, and the
#     cold-start rows run live. Then safety over the whole safety corpus on the same binary and
#     backend, and `accept-native --population subset`: per-row regression against the snapshot's
#     source run, aggregates within 0.02 on the same rows, coverage 1.0, safety 100%, the cold-start
#     sample holding. A platform where the skill never had a full-corpus cell is refused by
#     accept-native: that cell is the owner's (the first release there measures the full corpus).
#   - L3: `parity_check.py --subset`, CUDA, the bounds the release's rules name.
#   - Daemon: `plan --batch` over the subset's utterances, in process vs through the daemon,
#     byte-identical.
# A cell that did not run on its expected device is VOID.
#
# Variables the release's run_all.sh sets before sourcing (all required):
#   R          the run folder (evals/runs/<date>_r<ver>-windows_success)
#   VERSION    e.g. 1.4.0;  FREEZE  the freeze commit;  ZIP / ZIP_SHA256  the frozen artifact
#   LANE       lane name prefix in eval_backends.yaml, completed with the model (e.g. r140-win-)
#   MODELS     e.g. "4b 1.7b";  SKILLS  e.g. "ffmpeg documents"
#   GGUF_4B / GGUF_1_7B, SHA_4B / SHA_1_7B, NAME_4B / NAME_1_7B   the models
#   L3_BOUND_<skill>   L3's plan-disagreement bound per skill
# and calls `release_stage <stage>`. Stages: check | setup | draw | l3 | gpu | cpu | live | daemon |
# store; `live` re-runs one cell live after its cold start did not hold (MODEL, BACKEND, SKILL).
#
# Runs on the Windows box, Git Bash, from the repo root, one stage per invocation, each only after
# the owner approved it. Exit 0 passed, 1 the stage broke, 3 it ran with gate misses for the owner.

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }

UV="uv run --no-sync"   # a plain `uv run` re-syncs llama-cpp-python to its CPU build
EVAL="$UV python -m knaif.evalsuite"
STORE=evals/store
ART="sandbox/r${VERSION//./}/artifact"
EXE_REL="$ART/knaif-$VERSION-windows-x64/bin/knaif.exe"
CUDA_DIR="$(cygpath -aw "sandbox/r${VERSION//./}/backends-cuda")"
EMPTY_DIR="$(cygpath -aw "sandbox/r${VERSION//./}/backends-empty")"
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST KNAIF_N_GPU_LAYERS \
  KNAIF_LLM_BACKEND KNAIF_LLM_MOCK_RESPONSE KNAIF_LLM_REPLAY_DIR KNAIF_LLM_RECORD_DIR
export KNAIF_NO_DAEMON=1   # L4 in process (Part I)

gguf() { case "$1" in 4b) echo "$GGUF_4B" ;; 1.7b) echo "$GGUF_1_7B" ;; esac; }
gguf_sha() { case "$1" in 4b) echo "$SHA_4B" ;; 1.7b) echo "$SHA_1_7B" ;; esac; }
pyname() { case "$1" in 4b) echo "$NAME_4B" ;; 1.7b) echo "$NAME_1_7B" ;; esac; }
bound() { local v="L3_BOUND_$1"; echo "${!v:?no L3 bound for $1}"; }
cold_start() { echo "$R/cold_start_$1.json"; }

FAILED=0
GATE_MISS=0
log() { echo "$(date '+%F %T') $*" | tee -a "$R/COMPLETE"; }
failed() { log "FAILED $STAGE: $*"; echo "FAILED $STAGE: $*" >> "$R/verdicts.txt"; FAILED=1; }
missed() { log "GATE MISS $STAGE: $*"; echo "GATE MISS $STAGE: $*" >> "$R/verdicts.txt"; GATE_MISS=1; }
abort() { log "ABORTED $STAGE: $*"; exit 1; }

# Committed files carry repo-relative paths: every spelling of the checkout is dropped and the
# home directory becomes ~ (AGENTS.md, Public Output Hygiene).
REL_POSIX="$PWD/"; REL_WIN="$(cygpath -aw "$PWD")\\"; REL_MIXED="$(cygpath -am "$PWD")/"
REL_HOME_POSIX="$HOME/"; REL_HOME_WIN="$(cygpath -aw "$HOME")\\"; REL_HOME_MIXED="$(cygpath -am "$HOME")/"
export REL_POSIX REL_WIN REL_MIXED REL_HOME_POSIX REL_HOME_WIN REL_HOME_MIXED
rel() {
  perl -pe 'BEGIN { for my $k (qw(REL_WIN REL_HOME_WIN)) { ($ENV{"${k}_JSON"} = $ENV{$k}) =~ s/\\/\\\\/g } }
    s/\Q$ENV{REL_WIN_JSON}\E//gi; s/\Q$ENV{REL_POSIX}\E//g; s/\Q$ENV{REL_WIN}\E//gi; s/\Q$ENV{REL_MIXED}\E//gi;
    s/\Q$ENV{REL_HOME_WIN_JSON}\E/~\\\\/gi; s/\Q$ENV{REL_HOME_POSIX}\E/~\//g; s/\Q$ENV{REL_HOME_WIN}\E/~\\/gi;
    s/\Q$ENV{REL_HOME_MIXED}\E/~\//gi'
}

# -- preflight: the freeze, the artifact, the models, the subsets -----------------------------------
preflight() {
  : > "$R/artifact.$STAGE.log"
  git rev-parse HEAD > "$R/GIT_SHA.$STAGE"
  git status --porcelain --untracked-files=no > "$R/DIRTY_FILES.$STAGE"
  git merge-base --is-ancestor "$FREEZE" HEAD || abort "HEAD does not contain the freeze commit $FREEZE"
  # The working tree, not only HEAD: the store's keys are computed from it (Fable audit, 2026-10-19).
  local frozen=(native apps skills contracts Cargo.toml Cargo.lock)
  [ -z "$(git diff --name-only "$FREEZE" -- "${frozen[@]}")" ] \
    || abort "native, apps, skills, contracts or the Cargo files differ from the freeze commit $FREEZE"
  [ -z "$(git ls-files --others --exclude-standard -- "${frozen[@]}")" ] \
    || abort "untracked files under native, apps, skills or contracts: the tree is not the freeze"
  local skill
  for skill in $SKILLS; do
    $EVAL release-subset --skill "$skill" --check >> "$R/artifact.$STAGE.log" 2>&1 \
      || abort "$skill: the committed release subset does not match its corpus"
  done
  # What the store keys this release's replies on, for the record (E1).
  $EVAL fingerprints $(printf -- '--skill %s ' $SKILLS) > "$R/fingerprints.json" 2>>"$R/artifact.$STAGE.log" \
    || abort "computing the fingerprints"
  $UV python -m knaif._gpu_check 2>&1 | tail -1 >> "$R/artifact.$STAGE.log"
  [ "${PIPESTATUS[0]}" = 0 ] || abort "llama-cpp-python has no GPU offload (a uv sync replaced the CUDA build)"
  echo "$ZIP_SHA256  $ZIP" | sha256sum -c - >> "$R/artifact.$STAGE.log" 2>&1 || abort "$ZIP sha256 is not the pinned one"
  rm -rf "$ART" && mkdir -p "$ART" "sandbox/r${VERSION//./}/backends-empty" \
    && (cd "$ART" && unzip -q "$OLDPWD/$ZIP") || abort "unzip failed"
  EXE="$(cygpath -aw "$EXE_REL")"
  EXE_SHA="$(sha256sum "$EXE_REL" | cut -d' ' -f1)"
  [ "$("$EXE" --version)" = "knaif $VERSION (inference: llama.cpp)" ] || abort "the unpacked binary is not knaif $VERSION"
  {
    "$EXE" --version
    echo "knaif.exe sha256: $(sha256sum "$EXE_REL" | cut -d' ' -f1)"
    powershell -NoProfile -Command "(Get-AuthenticodeSignature '$EXE').Status"
  } | rel >> "$R/artifact.$STAGE.log" 2>&1
  local model
  for model in $MODELS; do
    echo "$(gguf_sha "$model")  $(gguf "$model")" | sha256sum -c - >> "$R/artifact.$STAGE.log" 2>&1 \
      || abort "$(gguf "$model") sha256 mismatch"
  done
}

verify_cuda() {
  # `backend verify` exits 0 for "not installed" and "no checksum" too: require the positive answer.
  KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend verify cuda 2>&1 | rel > "$R/verify.$STAGE.log"
  cat "$R/verify.$STAGE.log" >> "$R/artifact.$STAGE.log"
  grep -q "^cuda: ok" "$R/verify.$STAGE.log" || abort "the installed CUDA payload does not verify as ok (run setup)"
}

backend_env() {  # $1 backend -> ENVS array and WANT placement
  case "$1" in
    cuda) ENVS=(KNAIF_BACKENDS_DIR="$CUDA_DIR"); WANT=CUDA0 ;;
    vulkan) ENVS=(KNAIF_BACKENDS_DIR="$EMPTY_DIR"); WANT=Vulkan0 ;;
    cpu) ENVS=(KNAIF_BACKENDS_DIR="$EMPTY_DIR" CUDA_VISIBLE_DEVICES=-1 GGML_VK_VISIBLE_DEVICES=); WANT=CPU ;;
  esac
}
placement() {
  $UV python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8')).get('compute_backend'))" \
    "$1" 2>/dev/null
}
regen() {
  $EVAL fixtures regen --skill "$1" >> "$2" 2>&1 || { failed "$1: fixture regeneration failed"; return 1; }
}

# -- draw: the cold-start sample, once per release, before any result (I5) ---------------------------
stage_draw() {
  local skill out
  for skill in $SKILLS; do
    out="$(cold_start "$skill")"
    [ -e "$out" ] && { log "draw $skill: kept the sample drawn earlier ($out)"; continue; }
    rm -rf "$R/.draw" && $EVAL shards --skill "$skill" --subset --count 1 --rows 30 --out "$R/.draw" \
      >> "$R/draw.log" 2>&1 && mv "$R/.draw/shard-00.json" "$out" && rm -rf "$R/.draw" \
      || { failed "draw $skill: drawing the cold-start sample"; continue; }
    log "draw $skill: 30 cold-start utterances -> $out (commit it before the first cell)"
  done
}

# -- L4 cells ----------------------------------------------------------------------------------------
record_failed_cell() {  # $1 model, $2 backend, $3 skill, $4 reason
  $UV python -c "
import sys
from pathlib import Path
from knaif.evalsuite.gate import record_layers
from knaif.evalsuite.matrix import cell_key
record_layers(sys.argv[3], Path.cwd(), {'L4': {'cell': cell_key(sys.argv[1], 'windows-x64', sys.argv[2]),
    'passed': False, 'summary': sys.argv[5] + ' run: ' + sys.argv[4]}})
" "$(pyname "$1")" "$2" "$3" "$4" "$VERSION" || failed "recording $1 $2 $3 as failed"
}

board_sha() {
  $UV python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8')).get('binary_sha256'))" \
    "$1" 2>/dev/null
}

run_cell() {  # $1 skill, $2 lane, $3 dir, [$4 live]: VOID unless it ran the verified binary on $WANT
  local skill="$1" lane="$2" d="$3" how="${4:-store}" board got planning
  board="$d/${skill}_${lane}_success.json"
  rm -f "$board"
  if [ "$how" = live ]; then
    planning=()   # Part I, I5: after a cold start that did not hold, the cell runs live, in full
  else
    # Drawn once and committed before any cell (I5), so no result can have chosen it.
    git ls-files --error-unmatch "$(cold_start "$skill")" > /dev/null 2>&1 \
      || { failed "$skill: the cold-start sample is not committed (run draw, commit it)"; return 1; }
    planning=(--store "$STORE" --live-sample "$(cold_start "$skill")")
  fi
  regen "$skill" "$d/fixtures.log" || return 1
  if ! env "${ENVS[@]}" $EVAL native --skill "$skill" --lane "$lane" --subset "${planning[@]}" \
    --verifier success --verbose --config eval_backends.yaml \
    --save "$d" > "$d/$skill.log" 2>&1 || [ ! -s "$board" ]; then
    failed "$skill $lane: the lane run failed (see $d/$skill.log)"; return 1
  fi
  got="$(placement "$board")"
  log "$STAGE $skill $lane: placement $got; $(grep -m1 'store       :' "$d/$skill.log" | sed 's/^ *//')"
  if [ "$got" != "$WANT" ]; then
    echo "VOID $STAGE $skill $lane: ran on '$got', expected $WANT" >> "$R/verdicts.txt"
    failed "VOID $skill $lane: ran on '$got', expected $WANT"; return 1
  fi
  # The lane's binary is whatever eval_backends.yaml names; it must be the artifact verified above.
  if [ "$(board_sha "$board")" != "$EXE_SHA" ]; then
    echo "VOID $STAGE $skill $lane: ran binary $(board_sha "$board"), not the verified $EXE_SHA" >> "$R/verdicts.txt"
    failed "VOID $skill $lane: the lane's binary is not the verified artifact"; return 1
  fi
}

safety() {  # $1 skill, $2 lane, $3 dir: the whole safety corpus on that lane's binary (never reused)
  local skill="$1" lane="$2" d="$3" rc
  rm -f "$d/${skill}_safety.json"
  env "${ENVS[@]}" $EVAL safety --skill "$skill" --lane "$lane" --config eval_backends.yaml \
    --save "$d/${skill}_safety.json" > "$d/${skill}_safety.log" 2>&1
  rc=$?
  if [ $rc -ne 0 ] && [ ! -s "$d/${skill}_safety.json" ]; then failed "safety $skill $lane: exit $rc, no result"; return 1; fi
  log "$STAGE $skill $lane: safety exit $rc"
}

accept() {  # $1 model, $2 lane, $3 backend, $4 board, $5 skill, $6 dir
  local model="$1" lane="$2" backend="$3" board="$4" skill="$5" d="$6" rc
  safety "$skill" "$lane" "$d" || { record_failed_cell "$model" "$backend" "$skill" "the safety run failed"; return 1; }
  $EVAL accept-native --skill "$skill" --current "$board" --safety "$d/${skill}_safety.json" \
    --population subset > "$d/${skill}_accept.log" 2>&1
  rc=$?
  rel < "$d/${skill}_accept.log" > "$d/${skill}_verdict.txt"
  { echo "=== $STAGE $model $backend $skill (placement $(placement "$board"))"; cat "$d/${skill}_verdict.txt"; echo "exit $rc"; } \
    >> "$R/verdicts.txt"
  if grep -q "measures the full corpus" "$d/${skill}_verdict.txt"; then
    missed "$model $backend $skill: no earlier full-corpus cell on this platform; its first release measures the full corpus (owner)"
    return 0
  fi
  if grep -q "cold-start sample does not hold" "$d/${skill}_verdict.txt"; then
    missed "$model $backend $skill: the cold-start sample did not hold; the cell re-runs live on the subset (Part I, I5)"
    return 0
  fi
  if ! grep -qE "^(NOT )?ACCEPTED" "$d/${skill}_verdict.txt" || ! grep -q "recorded L4 evidence" "$d/${skill}_verdict.txt"; then
    failed "accept-native $model $backend $skill: no verdict recorded (exit $rc)"
    record_failed_cell "$model" "$backend" "$skill" "accept-native recorded no verdict"; return 1
  fi
  log "$STAGE $model $backend $skill: $(grep -m1 -E '^(NOT )?ACCEPTED' "$d/${skill}_verdict.txt")"
  # No slice floors on the subset, so nothing to waive: a regressed row goes to the owner (I3).
  grep -q "^NOT ACCEPTED" "$d/${skill}_verdict.txt" && missed "$model $backend $skill NOT ACCEPTED: goes to the owner"
  return 0
}

cell() {  # $1 model, $2 backend, [$3 live, $4 skills]: the subset cell
  local model="$1" backend="$2" how="${3:-store}" lane="$LANE$1" d skill
  d="$R/$model/$backend"; [ "$how" = live ] && d="$d/live"
  mkdir -p "$d"
  backend_env "$backend"
  for skill in ${4:-$SKILLS}; do
    run_cell "$skill" "$lane" "$d" "$how" || { record_failed_cell "$model" "$backend" "$skill" "the lane run failed or was VOID"; continue; }
    accept "$model" "$lane" "$backend" "$d/${skill}_${lane}_success.json" "$skill" "$d"
  done
}

# -- L3 on the subset (I4) ---------------------------------------------------------------------------
stage_l3() {
  local date model skill out rc m
  date="${R##*/}"; date="${date%%_*}"
  backend_env cuda
  for model in $MODELS; do
    m="$(cygpath -aw "$(gguf "$model")")"
    for skill in $SKILLS; do
      out="evals/parity/${date}_r${VERSION//./}-win-l3-$model-$skill"
      rm -rf "$out" && mkdir -p "$out"
      regen "$skill" "$out/fixtures.log" || continue
      env "${ENVS[@]}" KNAIF_PARITY_BACKEND=cuda $UV python scripts/parity_check.py --skill "$skill" --subset \
        --native-bin "$EXE" --model-path "$m" --python-model "$(pyname "$model")" \
        --cwd "$(cygpath -aw "sandbox/fixtures/$skill")" --out "$out/report.json" \
        --label "r${VERSION//./}-win-l3-$model-$skill" --max-plan-disagreement "$(bound "$skill")" \
        --purpose "knaif $VERSION L3 on the release subset, frozen Windows artifact ($FREEZE)" \
        > "$out/l3.log" 2>&1
      rc=$?
      log "l3 $model $skill: parity_check exit $rc (bound $(bound "$skill"))"
      [ -s "$out/report.json" ] || { failed "L3 $model $skill: no report written"; continue; }
      $EVAL gate --skill "$skill" --record-parity "$out" 2>&1 | rel >> "$R/l3_record.log" \
        || failed "L3 $model $skill: recording the parity run"
      [ $rc -eq 0 ] || missed "L3 $model $skill: parity_check exit $rc (the bar is missed): goes to the owner"
    done
  done
}

# -- daemon plan equality on the subset (I7) ----------------------------------------------------------
subset_utterances() {  # $1 skill -> the subset's utterances, one per line, corpus order
  $UV python -c "
import json, sys
from pathlib import Path
from knaif.evalsuite.corpus import load_corpus
from knaif.evalsuite.release_subset import load_subset_beside, subset_rows
corpus = Path('skills') / sys.argv[1] / 'data' / 'eval.jsonl'
keep = set(subset_rows(load_subset_beside(sys.argv[1], corpus)))
for row in load_corpus(corpus):
    for j, u in enumerate(row.utterances):
        if (row.id, j) in keep:
            print(u.replace('\n', ' '))" "$1"
}
daemon_requests() {
  env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon status 2>/dev/null | sed -n 's/^ *requests: *\([0-9]*\).*/\1/p'
}
stage_daemon() {
  local date d model m skill n rc before after a b errs
  date="${R##*/}"; date="${date%%_*}"
  d="evals/parity/${date}_r${VERSION//./}-win-daemon-plan-equality"
  # A client that sets generation overrides never uses a daemon (daemon.rs, disabled_by_env).
  unset KNAIF_N_THREADS KNAIF_N_THREADS_BATCH
  backend_env cuda
  for model in $MODELS; do
    rm -rf "${d:?}/$model" && mkdir -p "$d/$model"
    m="$(cygpath -aw "$(gguf "$model")")"
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon stop > /dev/null 2>&1
    for skill in $SKILLS; do
      regen "$skill" "$R/fixtures.log" || return 1
      subset_utterances "$skill" > "$d/$model/$skill.utterances.txt" || { failed "daemon $model $skill: listing"; return 1; }
      n="$(wc -l < "$d/$model/$skill.utterances.txt" | tr -d ' ')"
      (cd "sandbox/fixtures/$skill" && env "${ENVS[@]}" KNAIF_NO_DAEMON=1 "$EXE" plan --skill "$skill" --model "$m" \
        --batch "$(cygpath -aw "$OLDPWD/$d/$model/$skill.utterances.txt")" --json) \
        > "$d/$model/$skill.inprocess.jsonl" 2> "$R/daemon_${model}_$skill.inprocess.err"
      rc=$?
      [ $rc -eq 0 ] && [ "$(wc -l < "$d/$model/$skill.inprocess.jsonl" | tr -d ' ')" = "$n" ] \
        || { failed "daemon $model $skill: the in-process pass failed or is incomplete"; return 1; }
    done
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon start --model "$m" 2>&1 | rel > "$d/$model/daemon.txt" \
      || { failed "daemon $model: daemon start failed"; return 1; }
    for skill in $SKILLS; do
      n="$(wc -l < "$d/$model/$skill.utterances.txt" | tr -d ' ')"
      before="$(daemon_requests)"
      [ -n "$before" ] || { failed "daemon $model $skill: no daemon running"; return 1; }
      (cd "sandbox/fixtures/$skill" && env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" plan --skill "$skill" --model "$m" \
        --batch "$(cygpath -aw "$OLDPWD/$d/$model/$skill.utterances.txt")" --json) \
        > "$d/$model/$skill.daemon.jsonl" 2> "$R/daemon_${model}_$skill.daemon.err"
      rc=$?
      after="$(daemon_requests)"
      [ $rc -eq 0 ] && [ "$(wc -l < "$d/$model/$skill.daemon.jsonl" | tr -d ' ')" = "$n" ] \
        || { failed "daemon $model $skill: the daemon pass failed or is incomplete"; continue; }
      [ -n "$after" ] && [ $((after - before)) -ge "$n" ] \
        || { failed "daemon $model $skill: the daemon served $((${after:-0} - before)) of $n requests"; continue; }
      errs="$(grep -c '"error"' "$d/$model/$skill.inprocess.jsonl")"
      [ "$errs" -lt "$n" ] || { failed "daemon $model $skill: every line is an error envelope"; continue; }
      a="$(sha256sum < "$d/$model/$skill.inprocess.jsonl" | cut -d' ' -f1)"
      b="$(sha256sum < "$d/$model/$skill.daemon.jsonl" | cut -d' ' -f1)"
      if [ "$a" = "$b" ]; then
        echo "$model $skill: IDENTICAL sha256 $a ($n lines)" >> "$d/$model/result.txt"; log "daemon $model $skill: IDENTICAL"
      else
        echo "$model $skill: DIFFERENT in process $a, daemon $b" >> "$d/$model/result.txt"
        missed "daemon $model $skill: DIFFERENT: goes to the owner"
      fi
    done
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon stop 2>&1 | rel >> "$d/$model/daemon.txt"
  done
}

# -- store: what this release added to the committed store ---------------------------------------
stage_store() {
  $UV python scripts/check_no_local_paths.py "$STORE" >> "$R/store.log" 2>&1 \
    || failed "the store holds a local path: fix before committing it"
  git status --porcelain --untracked-files=all -- "$STORE" | wc -l | xargs -I{} log "store: {} new or changed file(s) under $STORE to commit"
}

release_stage() {
  STAGE="${1:?usage: release_stage check|setup|draw|l3|gpu|cpu|live|daemon|store}"
  case "$STAGE" in check | setup | draw | l3 | gpu | cpu | live | daemon | store) ;; *) echo "unknown stage $STAGE" >&2; exit 2 ;; esac
  if [ "$STAGE" = live ]; then
    # One cell whose cold-start sample did not hold, re-run live (I5): MODEL, BACKEND, SKILL.
    : "${MODEL:?live needs MODEL=4b|1.7b}" "${BACKEND:?live needs BACKEND=cuda|vulkan|cpu}" \
      "${SKILL:?live needs SKILL=<skill>}"
  fi
  mkdir -p "$R"
  LOCK="$R/.running"
  mkdir "$LOCK" 2>/dev/null || { echo "another stage is running ($LOCK)" >&2; exit 2; }
  trap 'rmdir "$LOCK" 2>/dev/null' EXIT
  log "START $STAGE"
  case "$STAGE" in
    draw) stage_draw ;;
    store) stage_store ;;
    *)
      preflight
      if [ "$STAGE" = check ]; then log "END $STAGE: preflight passed"; exit 0; fi
      if [ "$STAGE" = setup ]; then
        KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend install cuda 2>&1 | rel >> "$R/artifact.$STAGE.log" \
          || abort "backend install cuda failed"
      fi
      verify_cuda
      case "$STAGE" in
        setup) log "END $STAGE: CUDA payload installed and verified"; exit 0 ;;
        l3) stage_l3 ;;
        gpu) for model in $MODELS; do for backend in cuda vulkan; do cell "$model" "$backend"; done; done ;;
        cpu) for model in $MODELS; do cell "$model" cpu; done ;;
        live) cell "$MODEL" "$BACKEND" live "$SKILL" ;;
        daemon) stage_daemon ;;
      esac
      ;;
  esac
  if [ $FAILED -ne 0 ]; then log "END $STAGE with failures"; exit 1; fi
  if [ $GATE_MISS -ne 0 ]; then log "END $STAGE: ran, with gate misses for the owner"; exit 3; fi
  log "END $STAGE"
}
