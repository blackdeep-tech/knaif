#!/usr/bin/env bash
# A release's evidence on one platform, the way plan docs/plans/2026-10-14-faster-release-evidence.md
# Part I ("the rules a release adopts", approved 2026-10-18) measures it. The mechanics only: a
# release's own run_all.sh per platform (copied from scripts/release_evidence_run_all.template.sh at
# its freeze) pins the platform, the artifact, the freeze commit and the lanes, carries its gate rules
# verbatim, and sources this file.
#
# What every cell does (Stage 2: subset + batch planning through the result store):
#   - L4: `evalsuite native --subset --store evals/store --live-sample <this release's cold-start
#     sample>` on the frozen artifact: the model is asked only for the subset prompts the store does
#     not hold for this platform and backend (recording the replies), every row executes through
#     `knaif run` by replay, and the cold-start rows run live. Every process that loads the model must
#     put its layers on the cell's device, or the cell is VOID. Then safety over the whole safety
#     corpus on the same binary and backend, and `accept-native --population subset`: per-row
#     regression against the snapshot's source run, aggregates within 0.02 on the same rows,
#     coverage 1.0, safety 100%, the cold-start sample holding. A platform where the skill never had
#     a full-corpus cell is refused by accept-native: that cell is the owner's (the first release
#     there measures the full corpus).
#   - L3: `parity_check.py --subset`, on the platform's GPU (Windows CUDA, macOS Metal), the bounds
#     the release's rules name.
#   - Daemon: `plan --batch` over the subset's utterances, in process vs through the daemon,
#     byte-identical, where 1.3.0 checked it (Windows CUDA, macOS Metal, Linux CPU; I7).
# A cell that did not run on its expected device is VOID.
#
#   platform  cells                    L3      daemon  where
#   windows   cuda, vulkan, cpu        CUDA    CUDA    the Windows box, Git Bash
#   linux     cuda, cpu                -       CPU     WSL on the same box, a clone on the Linux disk
#   macos     metal, cpu               Metal   Metal   the Mac, a checkout outside the home folder
#
# The store keys replies per platform (owner, 2026-10-19), so no platform reuses another's. The
# cold-start sample is drawn once per release, by the first platform's run (COLD_START_DIR names
# that run folder; the other platforms' runs use the same committed files).
#
# Variables the release's run_all.sh sets before sourcing:
#   PLATFORM   windows | linux | macos
#   R          the run folder (evals/runs/<date>_r<ver>-<platform>_success)
#   VERSION    e.g. 1.4.0;  FREEZE  the freeze commit
#   ARTIFACT / ARTIFACT_SHA256   the frozen zip (Windows, macOS) or tarball (Linux) and its sha256
#   LANE       lane name prefix in eval_backends.yaml, completed with the model (e.g. r140-win-)
#   CPU_LANE   macOS only: the lane prefix of the Metal-less copy of the artifact (e.g. r140-mac-cpu-)
#   COLD_START_DIR   the run folder holding this release's cold-start samples (default: R)
#   MODELS     e.g. "4b 1.7b";  SKILLS  e.g. "ffmpeg documents"
#   GGUF_4B / GGUF_1_7B, SHA_4B / SHA_1_7B, NAME_4B / NAME_1_7B   the models
#   L3_BOUND_<skill>   L3's plan-disagreement bound per skill
# and calls `release_stage <stage>`. Stages: check | setup | draw | l3 | gpu | cpu | live | daemon |
# store; `live` re-runs one cell live after its cold start did not hold (MODEL, BACKEND, SKILL).
#
# From the repo root, one stage per invocation, each only after the owner approved it. Exit 0 passed,
# 1 the stage broke, 2 it was refused before running, 3 it ran with gate misses for the owner.
# Written for bash 3.2 too (macOS's own): no empty arrays under `set -u`, no bash-4 builtins.

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }

PLATFORM="${PLATFORM:-windows}"
case "$PLATFORM" in
  windows) OS_ID=windows-x64 TAG=win GPU_BACKENDS="cuda vulkan" L3_BACKEND=cuda DAEMON_BACKEND=cuda
    PKG="knaif-$VERSION-windows-x64" EXE_NAME=knaif.exe HAS_CUDA=1 ;;
  linux) OS_ID=linux-x64 TAG=linux GPU_BACKENDS="cuda" L3_BACKEND="" DAEMON_BACKEND=cpu
    PKG="knaif-$VERSION-linux-x64" EXE_NAME=knaif HAS_CUDA=1 ;;
  macos) OS_ID=macos TAG=mac GPU_BACKENDS="metal" L3_BACKEND=metal DAEMON_BACKEND=metal
    PKG="knaif-$VERSION-macos-arm64" EXE_NAME=knaif HAS_CUDA="" ;;
  *) echo "PLATFORM must be windows, linux or macos (got '$PLATFORM')" >&2; exit 2 ;;
esac
case "$PLATFORM:$PWD" in
  linux:/mnt/*) echo "refusing to run from $PWD: use the WSL clone on the Linux disk" >&2; exit 2 ;;
esac
if [ "$PLATFORM" = macos ]; then
  case "$PWD/" in "$HOME"/*) echo "refusing to run from $PWD: use a checkout outside the home folder" >&2; exit 2 ;; esac
fi
[ -d "$HOME/.local/bin" ] && export PATH="$HOME/.local/bin:$PATH"   # uv on Linux and macOS

# An existing or new path, absolute, in the spelling the binary takes (Windows: C:\...).
abspath() {
  if [ "$PLATFORM" = windows ]; then cygpath -aw "$1"; return; fi
  case "$1" in /*) echo "$1" ;; *) echo "$PWD/$1" ;; esac
}
sha256_of() {
  if command -v sha256sum > /dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

UV="uv run --no-sync"   # a plain `uv run` re-syncs llama-cpp-python to its CPU build
EVAL="$UV python -m knaif.evalsuite"
STORE=evals/store
VER="r${VERSION//./}"
ART="sandbox/$VER/artifact"
ART_CPU="sandbox/$VER/artifact-cpu"   # macOS: the same files without the Metal backend
EXE_REL="$ART/$PKG/bin/$EXE_NAME"
CUDA_DIR="$(abspath "sandbox/$VER/backends-cuda")"
EMPTY_DIR="$(abspath "sandbox/$VER/backends-empty")"
COLD_START_DIR="${COLD_START_DIR:-$R}"
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST KNAIF_N_GPU_LAYERS \
  KNAIF_LLM_BACKEND KNAIF_LLM_MOCK_RESPONSE KNAIF_LLM_REPLAY_DIR KNAIF_LLM_RECORD_DIR
export KNAIF_NO_DAEMON=1   # L4 in process (Part I)

gguf() { case "$1" in 4b) echo "$GGUF_4B" ;; 1.7b) echo "$GGUF_1_7B" ;; esac; }
gguf_sha() { case "$1" in 4b) echo "$SHA_4B" ;; 1.7b) echo "$SHA_1_7B" ;; esac; }
pyname() { case "$1" in 4b) echo "$NAME_4B" ;; 1.7b) echo "$NAME_1_7B" ;; esac; }
bound() { local v="L3_BOUND_$1"; echo "${!v:?no L3 bound for $1}"; }
cold_start() { echo "$COLD_START_DIR/cold_start_$1.json"; }
lane_for() {  # $1 model, $2 backend -> the eval_backends.yaml lane
  if [ "$PLATFORM" = macos ] && [ "$2" = cpu ]; then echo "${CPU_LANE:?macOS needs CPU_LANE}$1"; else echo "$LANE$1"; fi
}
cell_backend() {  # $1 backend -> its name in a cell key (matrix.backend_family of the placement)
  case "$1" in metal) echo mtl ;; *) echo "$1" ;; esac
}

FAILED=0
GATE_MISS=0
log() { echo "$(date '+%F %T') $*" | tee -a "$R/COMPLETE"; }
failed() { log "FAILED $STAGE: $*"; echo "FAILED $STAGE: $*" >> "$R/verdicts.txt"; FAILED=1; }
missed() { log "GATE MISS $STAGE: $*"; echo "GATE MISS $STAGE: $*" >> "$R/verdicts.txt"; GATE_MISS=1; }
abort() { log "ABORTED $STAGE: $*"; exit 1; }

# Committed files carry repo-relative paths: every spelling of the checkout is dropped, the home
# directory becomes ~, and on Linux the Windows checkout the clone came from becomes
# <windows-checkout> (AGENTS.md, Public Output Hygiene).
REL_POSIX="$PWD/"; REL_HOME_POSIX="$HOME/"; REL_ORIGIN=""; REL_WINDOWS=""
REL_WIN=""; REL_MIXED=""; REL_HOME_WIN=""; REL_HOME_MIXED=""
if [ "$PLATFORM" = windows ]; then
  REL_WINDOWS=1
  REL_WIN="$(cygpath -aw "$PWD")\\"; REL_MIXED="$(cygpath -am "$PWD")/"
  REL_HOME_WIN="$(cygpath -aw "$HOME")\\"; REL_HOME_MIXED="$(cygpath -am "$HOME")/"
elif [ "$PLATFORM" = linux ]; then
  REL_ORIGIN="$(git remote get-url origin 2>/dev/null | sed 's:/*$::')/"
fi
export REL_POSIX REL_WIN REL_MIXED REL_HOME_POSIX REL_HOME_WIN REL_HOME_MIXED REL_ORIGIN REL_WINDOWS
rel() {
  perl -pe 'BEGIN { for my $k (qw(REL_WIN REL_HOME_WIN)) { ($ENV{"${k}_JSON"} = $ENV{$k}) =~ s/\\/\\\\/g } }
    s/\Q$ENV{REL_ORIGIN}\E/<windows-checkout>\//g if length($ENV{REL_ORIGIN}) > 1;
    if ($ENV{REL_WINDOWS}) {
      s/\Q$ENV{REL_WIN_JSON}\E//gi; s/\Q$ENV{REL_WIN}\E//gi; s/\Q$ENV{REL_MIXED}\E//gi;
      s/\Q$ENV{REL_HOME_WIN_JSON}\E/~\\\\/gi; s/\Q$ENV{REL_HOME_WIN}\E/~\\/gi; s/\Q$ENV{REL_HOME_MIXED}\E/~\//gi;
    }
    s/\Q$ENV{REL_POSIX}\E//g; s/\Q$ENV{REL_HOME_POSIX}\E/~\//g'
}

# -- the platform's artifact ---------------------------------------------------------------------
unpack() {  # the frozen artifact, afresh; on macOS also its Metal-less copy for the CPU cells
  local src
  src="$(case "$ARTIFACT" in /*) echo "$ARTIFACT" ;; *) echo "$PWD/$ARTIFACT" ;; esac)"
  rm -rf "$ART" "$ART_CPU" && mkdir -p "$ART" "sandbox/$VER/backends-empty" || return 1
  case "$PLATFORM" in
    windows) (cd "$ART" && unzip -q "$src") ;;
    linux) tar -xzf "$src" -C "$ART" ;;
    macos)
      ditto -x -k "$src" "$ART" || return 1
      ls "$ART/$PKG/bin" | grep -q 'libggml-metal' || { echo "the artifact has no Metal backend" >&2; return 1; }
      cp -R "$ART" "$ART_CPU" && rm -f "$ART_CPU/$PKG/bin/"libggml-metal.* || return 1
      if ls "$ART_CPU/$PKG/bin" | grep -q 'libggml-metal'; then echo "the CPU copy still has Metal" >&2; return 1; fi
      ;;
  esac
}
signature() {  # what the platform says about the binary's signature
  case "$PLATFORM" in
    windows) powershell -NoProfile -Command "(Get-AuthenticodeSignature '$EXE').Status" ;;
    macos) codesign -dv "$EXE" 2>&1 | grep -E '^(Identifier|TeamIdentifier|Timestamp|Runtime Version)=' ;;
    linux) echo "signature: none (the Linux artifact is checksummed, not signed)" ;;
  esac
}
machine() {  # the OS and CPU, never the host or user name
  case "$PLATFORM" in
    windows) powershell -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).Caption + ' ' + [Environment]::OSVersion.Version" ;;
    linux) (. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-linux}, kernel $(uname -r)") ;;
    macos) echo "macOS $(sw_vers -productVersion) ($(sw_vers -buildVersion)), $(sysctl -n machdep.cpu.brand_string)" ;;
  esac
}

# -- preflight: the freeze, the artifact, the models, the subsets -----------------------------------
preflight() {
  : > "$R/artifact.$STAGE.log"
  git rev-parse HEAD > "$R/GIT_SHA.$STAGE"
  git status --porcelain --untracked-files=no > "$R/DIRTY_FILES.$STAGE"
  git merge-base --is-ancestor "$FREEZE" HEAD || abort "HEAD does not contain the freeze commit $FREEZE"
  # The working tree, not only HEAD: the store's keys are computed from it (Fable audit, 2026-10-19).
  local frozen="native apps skills contracts Cargo.toml Cargo.lock"
  # shellcheck disable=SC2086
  [ -z "$(git diff --name-only "$FREEZE" -- $frozen)" ] \
    || abort "native, apps, skills, contracts or the Cargo files differ from the freeze commit $FREEZE"
  # shellcheck disable=SC2086
  [ -z "$(git ls-files --others --exclude-standard -- $frozen)" ] \
    || abort "untracked files under native, apps, skills or contracts: the tree is not the freeze"
  local skill
  for skill in $SKILLS; do
    $EVAL release-subset --skill "$skill" --check >> "$R/artifact.$STAGE.log" 2>&1 \
      || abort "$skill: the committed release subset does not match its corpus"
  done
  case " $SKILLS " in *" documents "*)
    $UV python -c "import pypdf, pikepdf" >> "$R/artifact.$STAGE.log" 2>&1 \
      || abort "the documents grading dependencies are missing: pypdf, pikepdf" ;;
  esac
  # What the store keys this release's replies on, for the record (E1).
  # shellcheck disable=SC2046
  $EVAL fingerprints $(printf -- '--skill %s ' $SKILLS) > "$R/fingerprints.json" 2>>"$R/artifact.$STAGE.log" \
    || abort "computing the fingerprints"
  if [ -n "$L3_BACKEND" ]; then
    $UV python -m knaif._gpu_check 2>&1 | tail -1 >> "$R/artifact.$STAGE.log"
    [ "${PIPESTATUS[0]}" = 0 ] || abort "llama-cpp-python has no GPU offload (a uv sync replaced the GPU build); L3 needs it"
  fi
  [ "$(sha256_of "$ARTIFACT")" = "$ARTIFACT_SHA256" ] || abort "$ARTIFACT sha256 is not the pinned one"
  echo "$ARTIFACT: sha256 $ARTIFACT_SHA256 OK" | rel >> "$R/artifact.$STAGE.log"
  unpack >> "$R/artifact.$STAGE.log" 2>&1 || abort "unpacking $ARTIFACT failed"
  EXE="$(abspath "$EXE_REL")"
  EXE_SHA="$(sha256_of "$EXE_REL")"
  [ "$("$EXE" --version)" = "knaif $VERSION (inference: llama.cpp)" ] || abort "the unpacked binary is not knaif $VERSION"
  {
    "$EXE" --version
    echo "$EXE_NAME sha256: $EXE_SHA"
    signature
    machine
  } 2>&1 | rel >> "$R/artifact.$STAGE.log"
  local model
  for model in $MODELS; do
    [ "$(sha256_of "$(gguf "$model")")" = "$(gguf_sha "$model")" ] || abort "$(gguf "$model") sha256 mismatch"
    [ ! -L "$(gguf "$model")" ] || abort "$(gguf "$model") is a link: load the model from this machine's disk"
  done
}

verify_cuda() {
  [ -n "$HAS_CUDA" ] || return 0
  # `backend verify` exits 0 for "not installed" and "no checksum" too: require the positive answer.
  KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend verify cuda 2>&1 | rel > "$R/verify.$STAGE.log"
  cat "$R/verify.$STAGE.log" >> "$R/artifact.$STAGE.log"
  grep -q "^cuda: ok" "$R/verify.$STAGE.log" || abort "the installed CUDA payload does not verify as ok (run setup)"
}

# $1 backend -> ENVS (never empty: bash 3.2 refuses an empty array under set -u) and WANT placement
backend_env() {
  ENVS=(KNAIF_EVIDENCE_PLATFORM="$PLATFORM")
  case "$1" in
    cuda) ENVS+=(KNAIF_BACKENDS_DIR="$CUDA_DIR"); WANT=CUDA0 ;;
    vulkan) ENVS+=(KNAIF_BACKENDS_DIR="$EMPTY_DIR"); WANT=Vulkan0 ;;
    metal) WANT=MTL0 ;;
    cpu)
      # macOS: the CPU lane is the artifact without its Metal backend; elsewhere the GPUs are hidden.
      [ "$PLATFORM" = macos ] || ENVS+=(KNAIF_BACKENDS_DIR="$EMPTY_DIR" CUDA_VISIBLE_DEVICES=-1 GGML_VK_VISIBLE_DEVICES=)
      WANT=CPU ;;
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
  if [ "$COLD_START_DIR" != "$R" ]; then
    failed "this release draws its cold-start samples once, in $COLD_START_DIR; this run uses them"
    return
  fi
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
record_layers(sys.argv[3], Path.cwd(), {'L4': {'cell': cell_key(sys.argv[1], sys.argv[6], sys.argv[2]),
    'passed': False, 'summary': sys.argv[5] + ' run: ' + sys.argv[4]}})
" "$(pyname "$1")" "$(cell_backend "$2")" "$3" "$4" "$VERSION" "$OS_ID" || failed "recording $1 $2 $3 as failed"
}

board_sha() {
  $UV python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8')).get('binary_sha256'))" \
    "$1" 2>/dev/null
}

void_from_log() {  # $1 what, $2 log: a process ran the model on another device than the probe (I1)
  local why
  why="$(grep -m1 '^VOID: ' "$2" 2>/dev/null)" || return 1
  echo "VOID $STAGE $1: ${why#VOID: }" >> "$R/verdicts.txt"
  failed "VOID $1: ${why#VOID: }"
}

run_cell() {  # $1 skill, $2 lane, $3 dir, [$4 live]: VOID unless it ran the verified binary on $WANT
  local skill="$1" lane="$2" d="$3" how="${4:-store}" board got
  board="$d/${skill}_${lane}_success.json"
  rm -f "$board"
  # Part I, I5: after a cold start that did not hold, the cell runs live, in full (`live`).
  set -- --subset
  if [ "$how" != live ]; then
    # Drawn once and committed before any cell (I5), so no result can have chosen it.
    git ls-files --error-unmatch "$(cold_start "$skill")" > /dev/null 2>&1 \
      || { failed "$skill: the cold-start sample $(cold_start "$skill") is not committed (run draw, commit it)"; return 1; }
    set -- --subset --store "$STORE" --live-sample "$(cold_start "$skill")"
  fi
  regen "$skill" "$d/fixtures.log" || return 1
  if ! env "${ENVS[@]}" $EVAL native --skill "$skill" --lane "$lane" "$@" \
    --verifier success --verbose --config eval_backends.yaml \
    --save "$d" > "$d/$skill.log" 2>&1 || [ ! -s "$board" ]; then
    void_from_log "$skill $lane" "$d/$skill.log" && return 1
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
  if [ $rc -ne 0 ] && [ ! -s "$d/${skill}_safety.json" ]; then
    void_from_log "safety $skill $lane" "$d/${skill}_safety.log" && return 1
    failed "safety $skill $lane: exit $rc, no result"; return 1
  fi
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
  local model="$1" backend="$2" how="${3:-store}" lane d skill
  lane="$(lane_for "$model" "$backend")"
  d="$R/$model/$backend"; [ "$how" = live ] && d="$d/live"
  mkdir -p "$d"
  backend_env "$backend"
  for skill in ${4:-$SKILLS}; do
    run_cell "$skill" "$lane" "$d" "$how" || { record_failed_cell "$model" "$backend" "$skill" "the lane run failed or was VOID"; continue; }
    accept "$model" "$lane" "$backend" "$d/${skill}_${lane}_success.json" "$skill" "$d"
  done
}

# -- L3 on the subset (I4), on the platform's GPU -----------------------------------------------------
stage_l3() {
  local date model skill out rc m label
  date="${R##*/}"; date="${date%%_*}"
  backend_env "$L3_BACKEND"
  for model in $MODELS; do
    m="$(abspath "$(gguf "$model")")"
    for skill in $SKILLS; do
      label="$VER-$TAG-l3-$model-$skill"
      out="evals/parity/${date}_$label"
      rm -rf "$out" && mkdir -p "$out"
      regen "$skill" "$out/fixtures.log" || continue
      env "${ENVS[@]}" KNAIF_PARITY_BACKEND="$L3_BACKEND" $UV python scripts/parity_check.py --skill "$skill" --subset \
        --native-bin "$EXE" --model-path "$m" --python-model "$(pyname "$model")" \
        --cwd "$(abspath "sandbox/fixtures/$skill")" --out "$out/report.json" \
        --label "$label" --max-plan-disagreement "$(bound "$skill")" \
        --purpose "knaif $VERSION L3 on the release subset, frozen $PLATFORM artifact ($FREEZE), $L3_BACKEND" \
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
  local date d model m skill n rc before after a b errs list
  date="${R##*/}"; date="${date%%_*}"
  d="evals/parity/${date}_$VER-$TAG-daemon-plan-equality"
  # A client that sets generation overrides never uses a daemon (daemon.rs, disabled_by_env).
  unset KNAIF_N_THREADS KNAIF_N_THREADS_BATCH
  backend_env "$DAEMON_BACKEND"
  for model in $MODELS; do
    rm -rf "${d:?}/$model" && mkdir -p "$d/$model"
    m="$(abspath "$(gguf "$model")")"
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon stop > /dev/null 2>&1
    for skill in $SKILLS; do
      regen "$skill" "$R/fixtures.log" || return 1
      subset_utterances "$skill" > "$d/$model/$skill.utterances.txt" || { failed "daemon $model $skill: listing"; return 1; }
      n="$(wc -l < "$d/$model/$skill.utterances.txt" | tr -d ' ')"
      list="$(abspath "$d/$model/$skill.utterances.txt")"
      (cd "sandbox/fixtures/$skill" && env "${ENVS[@]}" KNAIF_NO_DAEMON=1 "$EXE" plan --skill "$skill" --model "$m" \
        --batch "$list" --json) \
        > "$d/$model/$skill.inprocess.jsonl" 2> "$R/daemon_${model}_$skill.inprocess.err"
      rc=$?
      [ $rc -eq 0 ] && [ "$(wc -l < "$d/$model/$skill.inprocess.jsonl" | tr -d ' ')" = "$n" ] \
        || { failed "daemon $model $skill: the in-process pass failed or is incomplete"; return 1; }
    done
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon start --model "$m" 2>&1 | rel > "$d/$model/daemon.txt" \
      || { failed "daemon $model: daemon start failed"; return 1; }
    for skill in $SKILLS; do
      n="$(wc -l < "$d/$model/$skill.utterances.txt" | tr -d ' ')"
      list="$(abspath "$d/$model/$skill.utterances.txt")"
      before="$(daemon_requests)"
      [ -n "$before" ] || { failed "daemon $model $skill: no daemon running"; return 1; }
      (cd "sandbox/fixtures/$skill" && env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" plan --skill "$skill" --model "$m" \
        --batch "$list" --json) \
        > "$d/$model/$skill.daemon.jsonl" 2> "$R/daemon_${model}_$skill.daemon.err"
      rc=$?
      after="$(daemon_requests)"
      [ $rc -eq 0 ] && [ "$(wc -l < "$d/$model/$skill.daemon.jsonl" | tr -d ' ')" = "$n" ] \
        || { failed "daemon $model $skill: the daemon pass failed or is incomplete"; continue; }
      [ -n "$after" ] && [ $((after - before)) -ge "$n" ] \
        || { failed "daemon $model $skill: the daemon served $((${after:-0} - before)) of $n requests"; continue; }
      errs="$(grep -c '"error"' "$d/$model/$skill.inprocess.jsonl")"
      [ "$errs" -lt "$n" ] || { failed "daemon $model $skill: every line is an error envelope"; continue; }
      a="$(sha256_of "$d/$model/$skill.inprocess.jsonl")"
      b="$(sha256_of "$d/$model/$skill.daemon.jsonl")"
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
  log "store: $(git status --porcelain --untracked-files=all -- "$STORE" | wc -l | tr -d ' ') new or changed file(s) under $STORE to commit"
}

# One stage at a time. The lock names the shell that holds it; a hard kill skips the EXIT trap and
# leaves the lock behind, so a lock whose process is gone is taken over, with a line in the log. A
# lock with no pid yet belongs to a stage that has only just taken it, unless it is a minute old.
take_lock() {
  LOCK="$R/.running"
  if ! mkdir "$LOCK" 2>/dev/null; then
    local pid
    pid="$(cat "$LOCK/pid" 2>/dev/null)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "another stage is running ($LOCK, pid $pid)" >&2
      exit 2
    fi
    if [ -z "$pid" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin -1 2>/dev/null)" ]; then
      echo "another stage is starting ($LOCK)" >&2
      exit 2
    fi
    log "taking over a stale lock (pid ${pid:-none} is gone)"
    rm -rf "$LOCK"
    mkdir "$LOCK" 2>/dev/null || { echo "another stage took $LOCK" >&2; exit 2; }
  fi
  echo $$ > "$LOCK/pid"
  trap 'rm -rf "$LOCK"' EXIT
}

# Whether this platform runs $1 at all, refused before anything else happens.
stage_refusal() {
  case "$1" in
    check | setup | draw | gpu | cpu | daemon | store) ;;
    l3) [ -n "$L3_BACKEND" ] || echo "L3 runs on Windows (CUDA) and macOS (Metal), not on $PLATFORM" ;;
    live)
      if [ -z "${MODEL:-}" ] || [ -z "${BACKEND:-}" ] || [ -z "${SKILL:-}" ]; then
        echo "live needs MODEL=4b|1.7b BACKEND=<$GPU_BACKENDS cpu> SKILL=<skill>"
      else
        case " $GPU_BACKENDS cpu " in *" $BACKEND "*) ;; *) echo "live: $PLATFORM has no $BACKEND cells" ;; esac
      fi
      ;;
    *) echo "unknown stage $1" ;;
  esac
}

release_stage() {
  STAGE="${1:?usage: release_stage check|setup|draw|l3|gpu|cpu|live|daemon|store}"
  local refused
  refused="$(stage_refusal "$STAGE")"
  [ -z "$refused" ] || { echo "$refused" >&2; exit 2; }
  mkdir -p "$R"
  take_lock
  log "START $STAGE ($PLATFORM)"
  case "$STAGE" in
    draw) stage_draw ;;
    store) stage_store ;;
    *)
      preflight
      if [ "$STAGE" = check ]; then log "END $STAGE: preflight passed"; exit 0; fi
      if [ "$STAGE" = setup ]; then
        if [ -n "$HAS_CUDA" ]; then
          KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend install cuda 2>&1 | rel >> "$R/artifact.$STAGE.log" \
            || abort "backend install cuda failed"
        fi
      fi
      verify_cuda
      case "$STAGE" in
        setup)
          if [ -n "$HAS_CUDA" ]; then log "END $STAGE: CUDA payload installed and verified"
          else log "END $STAGE: nothing to install (Metal ships in the artifact)"; fi
          exit 0 ;;
        l3) stage_l3 ;;
        gpu) for model in $MODELS; do for backend in $GPU_BACKENDS; do cell "$model" "$backend"; done; done ;;
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
