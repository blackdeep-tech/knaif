#!/usr/bin/env bash
# First real run of the result store (plan docs/plans/2026-10-14-faster-release-evidence.md, T2c):
# does a cell planned through the store, then planned again from what it recorded, grade every row
# the same, and does the second pass ask the model nothing?
#
# What is measured: a developer CUDA build of this checkout (target/release-cuda/knaif.exe, lane
# `native-cli`, unpackaged, so `accept-native` would refuse it: this is a check, not L4 evidence),
# model knaif-qwen3-4b-v2, ffmpeg's committed release subset (194 utterances), in process
# (KNAIF_NO_DAEMON=1). The store is this folder's own `store/`, not evals/store: a developer
# build's namespace is of no use to a release.
#
# Stages, one per invocation, in order (owner approved the GPU for this on 2026-10-19):
#
#   bash evals/runs/<this folder>/run_all.sh record   empty store: replay misses everything, the
#                                                     model plans all 194 and records, then every
#                                                     row executes by replay; 30 cold-start rows
#                                                     run live                              ~8 min
#   bash evals/runs/<this folder>/run_all.sh reuse    the same, from what `record` stored  ~6 min
#   bash evals/runs/<this folder>/run_all.sh compare  row-diff of the two boards            seconds
#
# Written before any result, what counts as the store working:
#   - record: store measured 194, reused 0; cold start 30 compared (minus screen rejections),
#     0 mismatches, no live row without a plan except screen rejections;
#   - reuse: store measured 0, reused 194, `plan_batch_s` null (no model planning at all); the
#     same cold-start result;
#   - compare: the two boards agree on every row's grade, plan and rendered command.
# Anything else is a finding to explain row by row, not a pass.
#
# Outputs (this folder): COMPLETE, cold_start.json, record/ and reuse/ (boards), comparison.json,
# the logs, and store/ (the recorded replies).

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh record|reuse|compare"
STAGE="${1:?$USAGE}"
case "$STAGE" in record | reuse | compare) ;; *) echo "$USAGE" >&2; exit 2 ;; esac

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
EVAL=(uv run --no-sync python -m knaif.evalsuite)
export KNAIF_NO_DAEMON=1
note() { echo "$(date -u +%FT%TZ) $*" | tee -a "$R/COMPLETE"; }

if [ ! -f "$R/cold_start.json" ]; then
  "${EVAL[@]}" shards --skill ffmpeg --subset --count 1 --rows 30 --out "$R/.cold" >/dev/null &&
    mv "$R/.cold/shard-00.json" "$R/cold_start.json" && rm -rf "$R/.cold"
fi

cell() {
  local name=$1
  note "START $name"
  "${EVAL[@]}" native --skill ffmpeg --lane native-cli --allow-unpackaged --verifier success \
    --subset --store "$R/store" --live-sample "$R/cold_start.json" \
    --save "$R/$name" --verbose >"$R/$name.log" 2>&1
  local rc=$?
  note "END $name exit=$rc"
  return $rc
}

case "$STAGE" in
  record | reuse) cell "$STAGE" ;;
  compare)
    "${EVAL[@]}" row-diff "$R/record/ffmpeg_native-cli_success.json" \
      "$R/reuse/ffmpeg_native-cli_success.json" --out "$R/comparison.json" | tee "$R/compare.log"
    note "compare done"
    note "ALL DONE"
    ;;
esac
