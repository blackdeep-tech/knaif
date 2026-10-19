#!/usr/bin/env bash
# Second real run of the result store (plan docs/plans/2026-10-14-faster-release-evidence.md, T5
# follow-ups and T4 Linux/macOS work, 2026-10-19 afternoon): after the per-process device check,
# the staged recording, the platform in the namespace, `--verbose` on every row that loads the model
# and KNAIF_NO_DAEMON in every lane process, does a store cell still plan, record, reuse and grade
# exactly as this morning's run did (evals/runs/2026-10-19_store-check_success)?
#
# What is measured: a developer CUDA build of this checkout (target/release-cuda/knaif.exe, rebuilt
# 13:37 with the audit's Rust fix; lane `native-cli`, unpackaged: a check, not L4 evidence), model
# knaif-qwen3-4b-v2, the committed release subsets (ffmpeg 194, documents 80 utterances), the
# morning run's ffmpeg cold-start sample (copied) and a new 30-row documents one, in process. The
# store is this folder's own `store/`. Owner gave the box for 45 min, 2026-10-19.
#
# Stages, one per invocation, in order:
#   record <skill>   empty store: the model plans everything (one --verbose batch, device-checked,
#                    staged), every row executes by replay, the cold-start rows run live  ~6 min
#   reuse <skill>    the same, from what `record` stored                                 ~5 min
#   safety           the ffmpeg safety corpus on the binary, every row device-checked     ~2 min
#   compare          row-diff: record vs reuse per skill; ffmpeg record vs the morning's record
#
# Written before any result, what counts as passing:
#   - record: exit 0, placement CUDA0, no VOID; store measured = all subset rows, reused 0; cold
#     start 30 compared (minus screen rejections), 0 mismatches, no unplanned live row except screen
#     rejections; the namespace's key.json names platform windows-x64; no `.record` staging folder
#     left; recorded.jsonl has one line per reply file.
#   - reuse: exit 0, measured 0, reused all, plan_batch_s null; the same cold-start result.
#   - safety: a result written, pass rate 1.0, no VOID.
#   - compare: every row the same grade, plan and command, for record vs reuse (both skills) and for
#     ffmpeg record vs the morning's record (194 rows): nothing that changed may change a grade.
# Anything else is a finding to explain row by row, not a pass.

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh record|reuse <skill> | safety | compare"
STAGE="${1:?$USAGE}"
case "$STAGE" in record | reuse) SKILL="${2:?$USAGE}" ;; safety | compare) ;; *) echo "$USAGE" >&2; exit 2 ;; esac

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
MORNING=evals/runs/2026-10-19_store-check_success
EVAL=(uv run --no-sync python -m knaif.evalsuite)
export KNAIF_NO_DAEMON=1
note() { echo "$(date -u +%FT%TZ) $*" | tee -a "$R/COMPLETE"; }

[ -f "$R/cold_start_ffmpeg.json" ] || cp "$MORNING/cold_start.json" "$R/cold_start_ffmpeg.json"
if [ ! -f "$R/cold_start_documents.json" ]; then
  "${EVAL[@]}" shards --skill documents --subset --count 1 --rows 30 --out "$R/.cold" >/dev/null &&
    mv "$R/.cold/shard-00.json" "$R/cold_start_documents.json" && rm -rf "$R/.cold"
fi

cell() {  # $1 stage, $2 skill
  local name="$1-$2"
  note "START $name"
  "${EVAL[@]}" native --skill "$2" --lane native-cli --allow-unpackaged --verifier success \
    --subset --store "$R/store" --live-sample "$R/cold_start_$2.json" \
    --save "$R/$name" --verbose >"$R/$name.log" 2>&1
  local rc=$?
  note "END $name exit=$rc"
  return $rc
}

case "$STAGE" in
  record | reuse) cell "$STAGE" "$SKILL" ;;
  safety)
    note "START safety"
    "${EVAL[@]}" safety --skill ffmpeg --lane native-cli --allow-unpackaged --config eval_backends.yaml \
      --save "$R/safety/ffmpeg_safety.json" >"$R/safety.log" 2>&1
    note "END safety exit=$?"
    ;;
  compare)
    for s in ffmpeg documents; do
      "${EVAL[@]}" row-diff "$R/record-$s/${s}_native-cli_success.json" \
        "$R/reuse-$s/${s}_native-cli_success.json" --out "$R/comparison_$s.json" | tee -a "$R/compare.log"
    done
    "${EVAL[@]}" row-diff "$MORNING/record/ffmpeg_native-cli_success.json" \
      "$R/record-ffmpeg/ffmpeg_native-cli_success.json" --out "$R/comparison_morning.json" | tee -a "$R/compare.log"
    note "compare done"
    note "ALL DONE"
    ;;
esac
