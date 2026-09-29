#!/usr/bin/env bash
# Release 1.2.0 R5c, Linux half (docs/plans/2026-09-25-release-1.2.md, "R5c - remaining work",
# T14-T16): accept RC 71884fd from the packaged Linux artifact, in WSL Ubuntu 24.04 on the same
# RTX 5080 as the Windows half, so an OS difference cannot hide behind a hardware difference.
#
# Runs INSIDE WSL, from the WSL checkout (a clone whose `origin` is the Windows checkout; pull
# first). ONE STAGE PER INVOCATION, each launched only after the owner approved it with its time
# budget (owner, 2026-09-28: one run at a time, 8 threads for heat):
#
#   bash run_all.sh t14   L4 CUDA cells in full: 4B, then 1.7B, both skills          ~1.5-2 h
#   bash run_all.sh t15   CPU cells by reuse: 150-request sample per model, compose   ~1.25 h
#   bash run_all.sh t15full <model>   the full Linux CPU cell, for a model T15 did not confirm
#                                       (4B ~3.5-4.5 h, 1.7B ~2.5-3 h)
#   t16 (floor + clean room) runs from Windows with Docker: t16_floor_cleanroom.sh, ~20-30 min
#
# Artifact: knaif-1.2.0-linux-x64.tar.gz (sha256 4fbba4a9...), built from 71884fd in the container,
# copied to ~/r5c/dist at T13, unpacked fresh by every stage into sandbox/r5c/artifact;
# KNAIF_PDFIUM_PATH unset (the bundled PDFium loads). CUDA through the frozen Linux payload,
# installed at T13 by `knaif backend install cuda` into ~/r5c/backends-cuda and checked by
# `backend verify` each stage. Models: the GGUFs copied into models/ at T13 (hashes checked there,
# re-checked here). Every cell checks the placement the lane measured (CUDA0) and is VOID otherwise.
#
# DECISION RULES, written 2026-09-29 before any stage runs:
#   T14 L4 (per model x skill, Linux CUDA): `accept-native` ACCEPTED, exactly the T8 rule: native
#      >= max(S2 floor at that model's bar, the model's locked Python score - 0.02) on outcome and
#      knaif score, coverage 1.0, every required slice, safety 100% on the binary. Baselines: 4B
#      sft-v4 (data/eval_snapshot.json), 1.7B sft-v9 (data/eval_snapshot.knaif-qwen3-1.7b-v2.json).
#      Outcome: a failing 4B entry blocks the release (owner decides). A failing 1.7B entry:
#      safety -> the retrain loop, never a lower bar; quality -> owner (as T8 and T10).
#   T14 also reports, as information and not a verdict, the flips between each Linux CUDA board
#      and the Windows CUDA board of the same model and skill (the committed t11_flips.py; the
#      Windows boards are read from the Windows checkout, `origin`).
#
#   T15 (written 2026-09-29, before T15 runs; the reuse rule accepted by the owner 2026-09-28):
#      per model, the Linux binary on the CPU (GPU hidden, one process per request) runs a sample
#      drawn and committed before the run (t15_sample_<model>_<skill>.json: 115 ffmpeg + 35
#      documents, seed 20260929, requests that reach the model on Windows). t15_confirm.py
#      compares its plans with the Windows CPU plans at the decision level (4B: the 2026-09-25 CPU
#      plans, which T9a confirmed; 1.7B: the T10 board). 0 decision flips on BOTH skills -> the
#      Windows CPU cell stands for Linux: `compose` swaps the 150 Linux rows into the Windows CPU
#      board (copied into base/ first, with its sha256), both safety sets run on the Linux binary
#      on the CPU, and `accept-native` grades the composed Linux CPU cell by the T14 rule. Any
#      flip, or a sampled row missing -> that model's full Linux CPU L4 instead, as a separate
#      approved run; nothing is composed for it. The composed 1.7B cell carries T10's three
#      slice misses unless the Linux rows change them; that is a quality question for the owner
#      (T10's waiver covers the Windows cell only: it quotes that cell's verdict and run).
#   T15full (written 2026-09-29 after T15 found 4 decision flips in the 4B ffmpeg sample, before
#      any full run; the stage the T15 rule already names): the model's Linux CPU cell in full,
#      exactly the T14 cell with the GPU hidden (placement CPU, else VOID and the stage fails),
#      graded by the T14 rule. It replaces the unconfirmed record T15 wrote for that cell.
#      A 1.7B CPU miss on the same thin slices as T10 goes to the owner as T10 did.
#
# PREDICTION (not a rule): Linux CUDA plans like Windows CUDA on all but a handful of requests
#   (same GPU, same llama.cpp and model; only the OS, the driver path and the compiler differ).
#   4B ACCEPTED on both skills. The 1.7B holds its thinnest ffmpeg slices (batch 26/29) by one
#   request on Windows CUDA, so a single flip could miss one: ~30% risk.
#   T15: 0 decision flips for both models (the Windows T9a sample had none in 130); the 4B Linux
#   CPU cell ACCEPTED; the 1.7B Linux CPU cell NOT ACCEPTED on T10's three slices.
set -uo pipefail
cd "$(dirname "$0")/../../.."
# Never from the Windows checkout through /mnt: it would unpack over that checkout's artifact and
# let uv rebuild its .venv for Linux (a mis-quoted launch did exactly that for ~40 s, 2026-09-29).
case "$PWD" in /mnt/*) echo "refusing to run from $PWD: use the WSL checkout" >&2; exit 2 ;; esac
STAGE="${1:?usage: run_all.sh t14|t15|t15full <model>}"
MODEL_ARG="${2:-}"  # read now: the preflight below reuses the positional parameters
R=evals/runs/2026-09-29_r5c-linux_success
TARBALL="$HOME/r5c/dist/knaif-1.2.0-linux-x64.tar.gz"
TAR_SHA=4fbba4a97f5d4a2377887d11df428be2db3c8c67e2d0f393e19bdd175f4bb801
ART=sandbox/r5c/artifact
EXE="$PWD/$ART/knaif-1.2.0-linux-x64/bin/knaif"
CUDA_DIR="$HOME/r5c/backends-cuda"
# The Windows half's run folder, read through the Windows checkout this clone came from.
WIN_R="$(git remote get-url origin)/evals/runs/2026-09-28_r5c-windows_success"
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
export PATH="$HOME/.local/bin:$PATH"
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST  # verify against the artifact's own manifest

mkdir -p "$R"
git rev-parse HEAD > "$R/GIT_SHA.$STAGE"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES.$STAGE"
echo "START $STAGE $(date)" >> "$R/COMPLETE"
abort() { echo "ABORTED $STAGE: $* $(date)" >> "$R/COMPLETE"; exit 1; }
FAILED=0
failed() { echo "FAILED $STAGE: $* $(date)" | tee -a "$R/verdicts.txt" >> "$R/COMPLETE"; FAILED=1; }

# The artifact is the tarball's bytes: check it, unpack it fresh, check the payload and the models.
echo "$TAR_SHA  $TARBALL" | sha256sum -c - > "$R/artifact.$STAGE.log" 2>&1 || abort "tarball sha256 mismatch"
rm -rf "$ART" && mkdir -p "$ART" && tar -xzf "$TARBALL" -C "$ART" || abort "untar failed"
# `backend verify` exits 0 for "not installed" and "no checksum" too: require the positive answer.
KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend verify cuda > "$R/verify.$STAGE.log" 2>&1
cat "$R/verify.$STAGE.log" >> "$R/artifact.$STAGE.log"
grep -q "^cuda: ok" "$R/verify.$STAGE.log" || abort "installed CUDA payload does not verify as ok"
"$EXE" --version >> "$R/artifact.$STAGE.log" 2>&1
for pair in "a9c26005e94622d63d1c6e64cb1c1b42084dcc37f6be7d69f8d5967f9c13aab7 knaif-qwen3-4b-v2-q4_k_m.gguf" \
            "d59cad240f5e0f157f093868479cf92132156097394805a9cccd102e14f04ac5 knaif-qwen3-1.7b-v2-q6_k.gguf"; do
  set -- $pair
  [ ! -L "models/$2" ] || abort "models/$2 is a link (load it from the Linux filesystem)"
  echo "$1  models/$2" | sha256sum -c - >> "$R/artifact.$STAGE.log" 2>&1 || abort "models/$2 sha256 mismatch"
done

backend_env() {  # $1 backend -> ENVS array and WANT placement
  case "$1" in
    cuda) ENVS=(KNAIF_BACKENDS_DIR="$CUDA_DIR"); WANT=CUDA0 ;;
    cpu) ENVS=(KNAIF_BACKENDS_DIR="$HOME/r5c/empty" CUDA_VISIBLE_DEVICES=-1 GGML_VK_VISIBLE_DEVICES=); WANT=CPU ;;
  esac
}

placement() {  # $1 board -> the compute_backend the lane measured
  uv run python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8')).get('compute_backend'))" \
    "$1" 2>/dev/null
}

# $1 model label, $2 lane, $3 backend, $4 board, $5 skill, $6 dir: safety on the binary + verdict.
accept() {
  local model="$1" lane="$2" backend="$3" board="$4" skill="$5" d="$6" rc
  rm -f "$d/${skill}_safety.json"
  env "${ENVS[@]}" uv run python -m knaif.evalsuite safety --skill "$skill" --lane "$lane" \
    --config eval_backends.yaml --save "$d/${skill}_safety.json" > "$d/${skill}_safety.log" 2>&1
  rc=$?
  if [ $rc -ne 0 ] && [ ! -s "$d/${skill}_safety.json" ]; then
    failed "safety $model $backend $skill: exit $rc, no result"; return 1
  fi
  # accept-native exits 1 on NOT ACCEPTED (a recorded verdict) and also on errors that record
  # nothing: only output naming a verdict AND a written record counts as a result.
  uv run python -m knaif.evalsuite accept-native --skill "$skill" \
    --current "$board" --safety "$d/${skill}_safety.json" > "$d/${skill}_accept.log" 2>&1
  rc=$?
  {
    echo "=== $model $backend $skill (placement $(placement "$board"))"
    cat "$d/${skill}_accept.log"
    echo "exit $rc"
  } >> "$R/verdicts.txt"
  if ! grep -qE "^(NOT )?ACCEPTED" "$d/${skill}_accept.log" \
    || ! grep -q "recorded L4 evidence" "$d/${skill}_accept.log"; then
    failed "accept-native $model $backend $skill: no verdict recorded (exit $rc)"; return 1
  fi
}

cell() {  # $1 model label, $2 lane, $3 backend: the full cell
  local model="$1" lane="$2" backend="$3" d skill got board
  d="$R/$model/$backend"
  mkdir -p "$d"
  backend_env "$backend"
  for skill in ffmpeg documents; do
    board="$d/${skill}_${lane}_success.json"
    rm -f "$board"
    if ! uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$d/fixtures.log" 2>&1; then
      failed "L4 $model $backend $skill: fixture regeneration failed"; continue
    fi
    if ! env "${ENVS[@]}" uv run python -m knaif.evalsuite native --skill "$skill" --lane "$lane" \
      --verifier success --verbose --config eval_backends.yaml --save "$d" > "$d/$skill.log" 2>&1 \
      || [ ! -s "$board" ]; then
      failed "L4 $model $backend $skill: the lane run failed (see $d/$skill.log)"; continue
    fi
    got="$(placement "$board")"
    if [ "$got" != "$WANT" ]; then
      echo "VOID $model $backend $skill: ran on '$got', expected $WANT" >> "$R/verdicts.txt"
      failed "VOID $model $backend $skill: ran on '$got', expected $WANT"
      continue
    fi
    accept "$model" "$lane" "$backend" "$board" "$skill" "$d" \
      && echo "DONE L4 $model $backend $skill $(date)" >> "$R/COMPLETE"
  done
}

os_flips() {  # $1 model label, $2 linux lane, $3 windows lane: Windows CUDA vs Linux CUDA, reported
  local skill win lin
  for skill in ffmpeg documents; do
    win="$WIN_R/$1/cuda/${skill}_$3_success.json"
    lin="$R/$1/cuda/${skill}_$2_success.json"
    # Only two measured CUDA0 boards make a Windows-vs-Linux CUDA comparison.
    if [ "$(placement "$win")" != CUDA0 ] || [ "$(placement "$lin")" != CUDA0 ]; then
      failed "flips $1 $skill: a board is missing or not CUDA0"; continue
    fi
    uv run python evals/runs/2026-09-28_r5c-windows_success/t11_flips.py "$win" "$lin" \
      --label "windows-cuda/linux-cuda" > "$R/$1/flips_${skill}_windows_vs_linux_cuda.txt" 2>&1 \
      || failed "flips $1 $skill windows/linux"
  done
}

only_run() {  # $1 dir, $2 lane, $3 skill, $4 only file: the pre-drawn rows, on the Linux CPU
  local d="$1" lane="$2" skill="$3" only="$4"
  mkdir -p "$d"
  rm -f "$d/${skill}_${lane}_success.json"
  backend_env cpu
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$d/fixtures.log" 2>&1 \
    || { failed "fixtures $skill"; return 1; }
  if ! env "${ENVS[@]}" uv run python -m knaif.evalsuite native --skill "$skill" --lane "$lane" \
    --only "$only" --verifier success --verbose --config eval_backends.yaml --save "$d" \
    > "$d/$skill.log" 2>&1 || [ ! -s "$d/${skill}_${lane}_success.json" ]; then
    failed "$lane $skill sample run failed (see $d/$skill.log)"; return 1
  fi
  [ "$(placement "$d/${skill}_${lane}_success.json")" = CPU ] \
    || { failed "$lane $skill sample did not run on the CPU"; return 1; }
}

record_unconfirmed() {  # $1 public model name: the Linux CPU cell fails, for both skills
  # Without this a failed re-run would leave an earlier accepted composed cell standing on
  # unchanged fingerprints (Codex, 2026-09-29). The summary names no threshold kind, so it can
  # never be waived: the rule's answer is the full Linux CPU run.
  uv run python -c "
import sys
from pathlib import Path
from knaif.evalsuite.gate import record_layers
from knaif.evalsuite.matrix import cell_key
cell = cell_key(sys.argv[1], 'linux-x64', 'cpu')
for skill in ('ffmpeg', 'documents'):
    record_layers(skill, Path.cwd(), {'L4': {'cell': cell, 'passed': False, 'summary':
        'T15: the Linux CPU sample did not confirm the Windows CPU plans; the full Linux CPU L4 is required'}})
" "$1" || failed "recording $1 as unconfirmed"
}

# $1 model label, $2 linux lane, $3 windows lane, $4 reference for ffmpeg, $5 for documents,
# $6 the model's public name.
cpu_by_reuse() {
  local model="$1" lane="$2" wlane="$3" skill ref base ok=1 d="$R/$1/cpu"
  rm -rf "$d" "$R/base/$model"  # never compose or judge from an earlier attempt's files
  mkdir -p "$d/sample" "$R/base/$model"
  for skill in ffmpeg documents; do
    if [ "$skill" = ffmpeg ]; then ref="$4"; else ref="$5"; fi
    only_run "$d/sample" "$lane" "$skill" "$R/t15_sample_${model}_$skill.json" || { ok=0; continue; }
    uv run python "$R/t15_confirm.py" confirm "$d/sample/${skill}_${lane}_success.json" "$ref" \
      "$R/t15_sample_${model}_$skill.json" > "$d/${skill}_verdict.txt" 2>&1
    echo "T15 $model $skill confirm exit $?" >> "$R/verdicts.txt"
    cat "$d/${skill}_verdict.txt" >> "$R/verdicts.txt"
    [ "$(tail -1 "$d/${skill}_verdict.txt")" = "VERDICT: the Windows CPU cell stands for Linux" ] || ok=0
  done
  if [ "$ok" -ne 1 ]; then
    echo "T15 $model: NOT confirmed -> the full Linux CPU L4 for $model (separate approved run) $(date)" >> "$R/COMPLETE"
    record_unconfirmed "$6"
    return 0
  fi
  backend_env cpu
  for skill in ffmpeg documents; do
    # The Windows board is copied in first: the composed record names its source path, and a
    # path through /mnt would name the Windows checkout (public-output hygiene).
    base="$R/base/$model/${skill}_${wlane}_success.json"
    cp "$WIN_R/$model/cpu/${skill}_${wlane}_success.json" "$base" || { failed "copy base $model $skill"; continue; }
    sha256sum "$base" >> "$R/base/SHA256SUMS"
    rm -f "$d/${skill}_${lane}_success.json"
    uv run python -m knaif.evalsuite compose --base "$base" \
      --replace "$d/sample/${skill}_${lane}_success.json" --out "$d/${skill}_${lane}_success.json" \
      --note "R5c T15: $model Linux CPU cell = the $model Windows CPU cell with the 150-request Linux CPU sample swapped in (0 decision flips against the Windows CPU plans)" \
      > "$d/${skill}_compose.log" 2>&1 || { failed "compose $model $skill"; continue; }
    accept "$model" "$lane" cpu "$d/${skill}_${lane}_success.json" "$skill" "$d" \
      && echo "DONE L4 $model cpu $skill (composed) $(date)" >> "$R/COMPLETE"
  done
}

case "$STAGE" in
  t15full)
    case "$MODEL_ARG" in
      4b) cell 4b r5c-linux-4b cpu ;;
      1.7b) cell 1.7b r5c-linux-1.7b cpu ;;
      *) abort "usage: run_all.sh t15full 4b|1.7b" ;;
    esac
    ;;
  t15)
    cpu_by_reuse 4b r5c-linux-4b r5c-win-4b \
      "$(git remote get-url origin)/evals/runs/2026-09-25_backend-parity-v2_plans/ffmpeg_cpu.jsonl" \
      "$(git remote get-url origin)/evals/runs/2026-09-25_backend-parity-v2_plans/documents_cpu.jsonl" \
      knaif-qwen3-4b-v2
    cpu_by_reuse 1.7b r5c-linux-1.7b r5c-win-1.7b \
      "$WIN_R/1.7b/cpu/ffmpeg_r5c-win-1.7b_success.json" \
      "$WIN_R/1.7b/cpu/documents_r5c-win-1.7b_success.json" \
      knaif-qwen3-1.7b-v2
    ;;
  t14)
    cell 4b r5c-linux-4b cuda
    cell 1.7b r5c-linux-1.7b cuda
    os_flips 4b r5c-linux-4b r5c-win-4b
    os_flips 1.7b r5c-linux-1.7b r5c-win-1.7b
    ;;
  *) abort "unknown stage $STAGE" ;;
esac
if [ "$FAILED" -ne 0 ]; then echo "FINISHED WITH FAILURES $STAGE $(date)" >> "$R/COMPLETE"; exit 1; fi
echo "DONE $STAGE $(date)" >> "$R/COMPLETE"
