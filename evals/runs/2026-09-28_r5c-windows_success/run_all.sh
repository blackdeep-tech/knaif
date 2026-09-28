#!/usr/bin/env bash
# Release 1.2.0 R5c, Windows half, second attempt: accept the RE-FROZEN release candidate
# (1fa823d) from the packaged artifact (docs/plans/2026-09-25-release-1.2.md, "R5c - remaining
# work", T7-T11). Supersedes 2026-09-27_r5c-windows_success (RC 3077d2f, stopped).
#
# ONE STAGE PER INVOCATION, each launched only after the owner approved it with its time budget
# (owner, 2026-09-28: no ~14 h runs, one run at a time, 8 threads for heat):
#
#   bash run_all.sh t7    L3 parity, both models x both skills, CUDA                    ~1 h
#   bash run_all.sh t8    L4 GPU cells: 4B cuda, 4B vulkan, 1.7B cuda, 1.7B vulkan      ~3-3.5 h
#   bash run_all.sh t9a   4B CPU confirmation: 100 ffmpeg + 30 documents, pre-drawn     ~40-60 min
#   bash run_all.sh t9b   4B CPU cell, composed: rerun set + safety, compose, accept    ~30-40 min
#   bash run_all.sh t10   1.7B CPU cell in full                                         ~2.5-3 h
#   bash run_all.sh t11   cross-backend flips from the scoreboards                      minutes
#
# Nothing reused from the first attempt: the re-freeze changed bundle, contracts, grading and
# native, so `check-gate` reads its 4B L3 documents and 4B CUDA L4 records as stale (rerun rule).
#
# Artifact: dist/knaif-1.2.0-windows-x64.zip (sha256 ed674018...), built from 1fa823d and unpacked
# fresh by every stage; KNAIF_PDFIUM_PATH unset (the bundled PDFium loads). CUDA through the frozen
# Windows payload installed by `knaif backend install cuda` into sandbox/r5c/backends-cuda (checked
# by `backend verify` each stage); Vulkan from the same artifact with no payload; CPU from the same
# artifact with the GPU hidden (CUDA_VISIBLE_DEVICES=-1, GGML_VK_VISIBLE_DEVICES=""). Every cell
# checks the placement the lane measured (CUDA0 / Vulkan0 / CPU) and is VOID otherwise.
#
# DECISION RULES, written 2026-09-28 before any stage runs:
#   T7 L3 (per model x skill): 0 port bugs, 0 capabilities native lacks, plan disagreement within
#      the bound: ffmpeg <= 0.0411, documents <= 0.0183 (unchanged; the Python eval lane and native
#      differ by 1.97% / 1.83% on the full corpus, 2026-09-28).
#   T8/T10 L4 (per model x backend x skill): `accept-native` ACCEPTED: native >= max(S2 floor at
#      that model's bar, the model's locked Python score - 0.02) on outcome and knaif, coverage
#      1.0, every required slice, safety 100% on the binary. Baselines: 4B sft-v4
#      (data/eval_snapshot.json), 1.7B sft-v9 (data/eval_snapshot.knaif-qwen3-1.7b-v2.json).
#   T9a: the 2026-09-25 CPU plans stand for the release binary iff the sample (seed 20260928, drawn
#      and committed before the run: t9a_sample_<skill>.json, rows with a 2026-09-25 CPU plan that
#      reach the model) shows 0 DECISION flips against them, per t9a_confirm.py. Both sides are
#      the plans each CLI reports after its deterministic gates (clarify gate, stem resolution),
#      not raw model output; a difference those gates introduce counts as a flip too. A sampled row the
#      binary rejects before inference (unsafe-phrase gate; plan mode never applies it) has no
#      model decision and is set aside and reported. Any flip -> the full 4B CPU L4 (~4.3 h), as a
#      separate approved run; T9b does not run.
#   T9b: rerun set = `evalsuite rerun-set` against T8's NEW 4B CUDA board: every row whose full
#      plan differs from its 2026-09-25 CPU plan, plus every row with no plan on one side (corpus
#      rows added since; pre-inference rejects). Those rows run on the CPU (`native --only`), both
#      safety sets run on the CPU binary, `evalsuite compose` swaps them into the CUDA board, and
#      `accept-native` grades the composed cell by the T8 rule and records it as composed.
#   T11: reported, not a verdict. Decision and canonical flips CUDA vs Vulkan and CUDA vs CPU per
#      model, each flip set split into one-side-correct and both-wrong (the `success` grades).
#   Outcome: a failing 4B entry blocks the release (owner decides). A failing 1.7B entry: safety ->
#      the retrain loop, never a lower bar; quality -> owner.
#
# PREDICTION (not a rule):
#   T7: 0 port bugs on both models; disagreement ffmpeg ~2% (the full-corpus lane diff was 1.97%),
#      documents ~1-2%, the 1.7B a little higher than the 4B.
#   T8: 4B CUDA ACCEPTED at ~0.934 / 0.984 ffmpeg and ~0.98 / 0.99 documents (the stopped run);
#      Vulkan within ~1 pp of CUDA; 1.7B CUDA near its Python numbers (0.92 / 0.98), its weakest
#      point the reject slice (budget 4, all used in Python). Safety 11/11 + 9/9 everywhere.
#   T9a: 0 decision flips. Risk: batch vs per-process differed on 5/851 CUDA rows on 2026-09-25;
#      if that holds on CPU, P(>=1 in 130) is ~50%.
#   T10: 1.7B CPU ACCEPTED within ~1 pp of its CUDA cell.
set -uo pipefail
cd "$(dirname "$0")/../../.."
STAGE="${1:?usage: run_all.sh t7|t8|t9a|t9b|t10|t11}"
R=evals/runs/2026-09-28_r5c-windows_success
ZIP=dist/knaif-1.2.0-windows-x64.zip
ZIP_SHA=ed674018fd5815fb7b797dfa673d423a080459662d1d66e6618cde84c4d00c17
ART=sandbox/r5c/artifact
EXE="$(cygpath -aw "$ART/knaif-1.2.0-windows-x64/bin/knaif.exe")"
CUDA_DIR="$(cygpath -aw sandbox/r5c/backends-cuda)"
EMPTY_DIR="$(cygpath -aw sandbox/r5c/backends-empty)"
OLD_CPU=evals/runs/2026-09-25_backend-parity-v2_plans
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
unset KNAIF_PDFIUM_PATH

git rev-parse HEAD > "$R/GIT_SHA.$STAGE"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES.$STAGE"
echo "START $STAGE $(date)" >> "$R/COMPLETE"
abort() { echo "ABORTED $STAGE: $* $(date)" >> "$R/COMPLETE"; exit 1; }
# A command that fails is recorded and fails the stage; its output is never graded. Every output
# is deleted before its command runs, so a failed retry cannot pick up an earlier attempt's file
# (Codex audit of e6fea79).
FAILED=0
failed() { echo "FAILED $STAGE: $* $(date)" | tee -a "$R/verdicts.txt" >> "$R/COMPLETE"; FAILED=1; }

# The artifact is the zip's bytes: check the zip, unpack it fresh, check the payload install.
echo "$ZIP_SHA  $ZIP" | sha256sum -c - > "$R/artifact.$STAGE.log" 2>&1 || abort "zip sha256 mismatch"
rm -rf "$ART" && mkdir -p "$ART" sandbox/r5c/backends-empty && (cd "$ART" && unzip -q "../../../$ZIP") \
  || abort "unzip failed"
KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend verify cuda >> "$R/artifact.$STAGE.log" 2>&1 \
  || abort "installed CUDA payload does not verify"
"$EXE" --version >> "$R/artifact.$STAGE.log" 2>&1

# Where the binary actually puts the weights, measured the way the L4 lane measures it (one
# `--dry-run --verbose` probe, `native_lane.detect_backend`), not taken from an env variable.
probe() {  # $1 gguf, $2 skill -> prints the placement summary (CUDA0 / Vulkan0 / CPU)
  env "${ENVS[@]}" uv run python -c "
import sys
from pathlib import Path
from knaif.evalsuite.native_lane import LaneConfig, detect_backend
lane = LaneConfig(name='probe', binary=Path(sys.argv[1]), model_path=Path(sys.argv[2]))
print(detect_backend(lane, sys.argv[3], Path(sys.argv[4])).summary)
" "$EXE" "$1" "$2" "$(cygpath -aw "sandbox/fixtures/$2")" 2>>"$R/probe.log"
}

l3() {  # $1 model label, $2 gguf, $3 the models.yaml name backing it (the Python side)
  local model="$1" gguf bound skill out pyname="$3" got
  gguf="$(cygpath -aw "$2")"
  backend_env cuda
  for skill in ffmpeg documents; do
    bound=0.0411; [ "$skill" = documents ] && bound=0.0183
    out="evals/parity/2026-09-28_r5c-l3-$model-$skill"
    rm -rf "$out" && mkdir -p "$out"
    uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$R/fixtures.log" 2>&1
    got="$(probe "$gguf" "$skill")"
    echo "L3 $model $skill placement probe: $got" >> "$R/COMPLETE"
    if [ "$got" != CUDA0 ]; then failed "L3 $model $skill: probe placed the model on '$got', not CUDA0"; continue; fi
    # parity_check exits non-zero when the bound is missed; that is a verdict, read from the report.
    env "${ENVS[@]}" KNAIF_PARITY_BACKEND=cuda \
      uv run python scripts/parity_check.py --skill "$skill" --native-bin "$EXE" --model-path "$gguf" --python-model "$pyname" \
      --cwd "$(cygpath -aw "sandbox/fixtures/$skill")" --out "$out/report.json" \
      --label "r5c-l3-$model-$skill" --max-plan-disagreement "$bound" \
      --purpose "R5c L3, re-frozen RC 1fa823d, packaged Windows artifact, installed CUDA payload (probe: $got)" \
      > "$R/l3_${model}_$skill.log" 2>&1
    echo "L3 $model $skill parity_check exit $?" >> "$R/verdicts.txt"
    if [ ! -s "$out/report.json" ]; then failed "L3 $model $skill: no report written"; continue; fi
    uv run python -m knaif.evalsuite gate --skill "$skill" --record-parity "$out" \
      >> "$R/l3_record.log" 2>&1 || failed "L3 $model $skill: recording the parity run"
    echo "DONE L3 $model $skill $(date)" >> "$R/COMPLETE"
  done
}

backend_env() {  # $1 backend -> ENVS array and WANT placement
  case "$1" in
    cuda) ENVS=(KNAIF_BACKENDS_DIR="$CUDA_DIR"); WANT=CUDA0 ;;
    vulkan) ENVS=(KNAIF_BACKENDS_DIR="$EMPTY_DIR"); WANT=Vulkan0 ;;
    cpu) ENVS=(KNAIF_BACKENDS_DIR="$EMPTY_DIR" CUDA_VISIBLE_DEVICES=-1 GGML_VK_VISIBLE_DEVICES=); WANT=CPU ;;
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
  {
    echo "=== $model $backend $skill (placement $(placement "$board"))"
    uv run python -m knaif.evalsuite accept-native --skill "$skill" \
      --current "$board" --safety "$d/${skill}_safety.json"
    echo "exit $?"
  } >> "$R/verdicts.txt" 2>&1
}

cell() {  # $1 model label, $2 lane, $3 backend: the full cell
  local model="$1" lane="$2" backend="$3" d skill got board
  d="$R/$model/$backend"
  mkdir -p "$d"
  backend_env "$backend"
  for skill in ffmpeg documents; do
    board="$d/${skill}_${lane}_success.json"
    rm -f "$board"
    uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$d/fixtures.log" 2>&1
    if ! env "${ENVS[@]}" uv run python -m knaif.evalsuite native --skill "$skill" --lane "$lane" \
      --verifier success --verbose --config eval_backends.yaml --save "$d" > "$d/$skill.log" 2>&1 \
      || [ ! -s "$board" ]; then
      failed "L4 $model $backend $skill: the lane run failed (see $d/$skill.log)"; continue
    fi
    got="$(placement "$board")"
    if [ "$got" != "$WANT" ]; then
      echo "VOID $model $backend $skill: ran on '$got', expected $WANT" >> "$R/verdicts.txt"
      echo "VOID $model $backend $skill $(date)" >> "$R/COMPLETE"
      continue
    fi
    accept "$model" "$lane" "$backend" "$board" "$skill" "$d" \
      && echo "DONE L4 $model $backend $skill $(date)" >> "$R/COMPLETE"
  done
}

only_run() {  # $1 dir, $2 lane, $3 skill, $4 only file: CPU rows from a pre-drawn list
  local d="$1" lane="$2" skill="$3" only="$4"
  mkdir -p "$d"
  rm -f "$d/${skill}_${lane}_success.json"
  backend_env cpu
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$d/fixtures.log" 2>&1
  env "${ENVS[@]}" uv run python -m knaif.evalsuite native --skill "$skill" --lane "$lane" \
    --only "$only" --verifier success --verbose --config eval_backends.yaml --save "$d" \
    > "$d/$skill.log" 2>&1 || abort "$skill --only run failed (see $d/$skill.log)"
  [ "$(placement "$d/${skill}_${lane}_success.json")" = CPU ] \
    || abort "$skill sample did not run on the CPU"
}

case "$STAGE" in
  t7)
    l3 4b models/knaif-qwen3-4b-v2-q4_k_m.gguf knaif-qwen3-4b-v2
    l3 1.7b models/knaif-qwen3-1.7b-v2-q6_k.gguf knaif-qwen3-1.7b-v2
    ;;
  t8)
    for backend in cuda vulkan; do cell 4b r5c-win-4b "$backend"; done
    for backend in cuda vulkan; do cell 1.7b r5c-win-1.7b "$backend"; done
    ;;
  t9a)
    d="$R/4b/cpu-confirm"
    rm -f "$d"/*_verdict.txt
    for skill in ffmpeg documents; do
      only_run "$d" r5c-win-4b "$skill" "$R/t9a_sample_$skill.json"
      uv run python "$R/t9a_confirm.py" "$d/${skill}_r5c-win-4b_success.json" \
        "$OLD_CPU/${skill}_cpu.jsonl" "$R/t9a_sample_$skill.json" > "$d/${skill}_verdict.txt" 2>&1
      echo "T9a $skill exit $?" >> "$R/verdicts.txt"
      cat "$d/${skill}_verdict.txt" >> "$R/verdicts.txt"
      echo "DONE T9a $skill $(date)" >> "$R/COMPLETE"
    done
    ;;
  t9b)
    # Only the latest T9a counts: every T9a run deletes and rewrites these verdict files.
    for skill in ffmpeg documents; do
      [ "$(tail -1 "$R/4b/cpu-confirm/${skill}_verdict.txt" 2>/dev/null)" = "VERDICT: the 2026-09-25 CPU plans stand" ] \
        || abort "T9a ($skill) did not confirm the 2026-09-25 CPU plans; the full CPU L4 runs instead"
    done
    d="$R/4b/cpu"
    mkdir -p "$d"
    backend_env cpu
    for skill in ffmpeg documents; do
      base="$R/4b/cuda/${skill}_r5c-win-4b_success.json"
      [ "$(placement "$base")" = CUDA0 ] || abort "no measured 4B CUDA board for $skill"
      uv run python -m knaif.evalsuite rerun-set --base "$base" --plans "$OLD_CPU/${skill}_cpu.jsonl" \
        --out "$d/${skill}_rerun.json" > "$d/${skill}_rerun.log" 2>&1 || abort "rerun-set $skill"
      only_run "$d/rows" r5c-win-4b "$skill" "$d/${skill}_rerun.json"
      uv run python -m knaif.evalsuite compose --base "$base" \
        --replace "$d/rows/${skill}_r5c-win-4b_success.json" --out "$d/${skill}_r5c-win-4b_success.json" \
        --note "R5c T9b: 4B Windows CPU cell = the 4B CUDA cell + every row whose 2026-09-25 CPU plan differs in full or is missing, re-run on the CPU (T9a confirmed those plans)" \
        > "$d/${skill}_compose.log" 2>&1 || abort "compose $skill"
      accept 4b r5c-win-4b cpu "$d/${skill}_r5c-win-4b_success.json" "$skill" "$d"
      echo "DONE L4 4b cpu $skill (composed) $(date)" >> "$R/COMPLETE"
    done
    ;;
  t10)
    cell 1.7b r5c-win-1.7b cpu
    ;;
  t11)
    for spec in "4b r5c-win-4b" "1.7b r5c-win-1.7b"; do
      set -- $spec
      for skill in ffmpeg documents; do
        for other in vulkan cpu; do
          uv run python scripts/flip_rate.py compare \
            "$R/$1/cuda/${skill}_$2_success.json" "$R/$1/$other/${skill}_$2_success.json" \
            --label "cuda/$other" --list > "$R/$1/flips_${skill}_cuda_vs_$other.txt" 2>&1
        done
      done
    done
    ;;
  *) abort "unknown stage $STAGE" ;;
esac
if [ "$FAILED" -ne 0 ]; then echo "FINISHED WITH FAILURES $STAGE $(date)" >> "$R/COMPLETE"; exit 1; fi
echo "DONE $STAGE $(date)" >> "$R/COMPLETE"
