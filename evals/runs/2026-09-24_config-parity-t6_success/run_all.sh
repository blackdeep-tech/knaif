#!/usr/bin/env bash
# Inference-config parity T6 — the full-corpus re-measure on the aligned config.
# Plan: docs/plans/2026-09-23-inference-config-parity.md
#
# T2 measured only first phrasings with `cheap`. This is the measurement the snapshots are made
# of: EVERY utterance (ffmpeg 851, documents 164), `success` verifier (executes ffmpeg on
# fixtures), plus both safety corpora — on two arms of the same v2 weights:
#
#   legacy   qwen3-4b-sft-v4-flat-q4-legacycfg   the config every snapshot to date was measured on
#   aligned  qwen3-4b-sft-v4-flat-q4             the contract's config (T3/T4), as shipped now
#
# Two arms, not one, because code has moved since the snapshots were locked (engine fixes of
# 2026-09-23: container keeping, webm encoder, video-only guards). legacy-vs-snapshot isolates the
# code changes; aligned-vs-legacy isolates the config change on the full corpus.
#
# PREDICTION, written before the run so it can be wrong:
#   * aligned vs legacy: outcome flips about 1-2% of utterances, net within +/-1 pp (T2: 1.23%,
#     net 0.00 pp on first phrasings). Multilingual phrasings may flip more — T2 never saw them.
#   * safety 100% on both arms (T2: 11/11 and 9/9 in every config).
#   * both arms clear S2 acceptance for both skills; eval-regression may flag rows the engine
#     fixes changed, on BOTH arms alike — those are code, not config.
#
# DECISION (plan's rule, restated for the full corpus): if aligned-vs-legacy outcome flips stay
# below 1.83 pp and both arms are ACCEPTED, re-lock the snapshots on the ALIGNED run in their
# own commit (the shipped config is what the bar should describe). Otherwise stop and re-derive
# the v2 verdict before any publish.
#
# Fixtures are regenerated first — missing fixtures score correct plans ~0 (AGENTS.md).
# Nothing is edited while this runs.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-24_config-parity-t6_success
V2=qwen3-4b-sft-v4-flat-q4

git rev-parse HEAD > "$R/GIT_SHA"
git status --porcelain > "$R/DIRTY_FILES"

for skill in ffmpeg documents; do
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" > "$R/fixtures_$skill.log" 2>&1
done
echo "DONE fixtures $(date)" >> "$R/COMPLETE"

run_arm() {  # <dir> <backend>
  local dir="$1" backend="$2"
  mkdir -p "$R/$dir"
  for skill in ffmpeg documents; do
    uv run python -m knaif.evalsuite run --skill "$skill" --verifier success --verbose \
      --config eval_backends.yaml --backends "$backend" --save "$R/$dir" \
      > "$R/$dir/$skill.log" 2>&1
    uv run python -m knaif.evalsuite safety --skill "$skill" \
      --config eval_backends.yaml --backends "$backend" --save "$R/$dir/${skill}_safety.json" \
      > "$R/$dir/${skill}_safety.log" 2>&1
  done
  echo "DONE $dir $(date)" >> "$R/COMPLETE"
}

run_arm legacy  "$V2-legacycfg"
run_arm aligned "$V2"

for skill in ffmpeg documents; do
  for arm in "legacy:$V2-legacycfg" "aligned:$V2"; do
    dir="${arm%%:*}"; b="${arm#*:}"; cur="$R/$dir/${skill}_${b}_success.json"
    {
      echo "=== $skill $dir: regression vs committed snapshot"
      uv run python -m knaif.evalsuite regression --skill "$skill" --current "$cur"
      echo "=== $skill $dir: S2 acceptance"
      uv run python -m knaif.evalsuite accept --skill "$skill" --current "$cur" \
        --safety "$R/$dir/${skill}_safety.json"
    } >> "$R/report.txt" 2>&1
  done
  uv run python scripts/flip_rate.py compare \
    "$R/legacy/${skill}_${V2}-legacycfg_success.json" "$R/aligned/${skill}_${V2}_success.json" \
    --list --label "$skill legacy vs aligned (full corpus)" \
    --json "$R/flips_${skill}_legacy_vs_aligned.json" >> "$R/report.txt" 2>&1
done
echo "DONE report $(date)" >> "$R/COMPLETE"
