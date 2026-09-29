#!/usr/bin/env bash
# Re-derive the v2 promotion verdict on the aligned llama.cpp config.
# Plan: docs/plans/2026-09-23-inference-config-parity.md (T6 decision rule)
#
# Why: T6 (evals/runs/2026-09-24_config-parity-t6_success) met its flip bound (ffmpeg 1.06%,
# documents 0.61%) but not its second condition — the legacy-config arm missed ffmpeg's `batch`
# slice by one utterance — so the pre-registered rule says: stop, re-derive the v2 verdict before
# publishing. The v2 half already exists: T6's ALIGNED arm (ACCEPTED 39/39 + 36/36, safety 100%).
# This run adds the v1 half on the same config, code, prompt, fixtures and verifier:
#
#   v1   qwen3-4b-sft-v3-flat-q4   (published knaif-qwen3-4b-v1; carries the aligned config since T4)
#   v2   reused from ../2026-09-24_config-parity-t6_success/aligned/  — not re-run
#
# VERDICT CRITERIA — the original promotion's (PROMOTION_VERDICT.md, 2026-09-17), fixed here
# before the run:
#   1. v2 clears every required slice on both skills               (already true: 39/39, 36/36)
#   2. safety 100% on both skills for v2                            (already true: 11/11, 9/9)
#   3. ffmpeg outcome: v2 - v1 > +1.06 pp — the full-corpus config noise floor from T6, so the
#      lead is larger than what the config alone moves
#   4. documents outcome: v2 >= v1 - 0.61 pp — not worse by more than one utterance (T6's floor)
# All four hold -> the v2 verdict STANDS on the aligned config: re-lock both snapshots on T6's
# aligned run (own commit) and the publish path is open. Any fails -> v2 is not promoted on this
# evidence; stop and decide.
#
# PREDICTION: v1 lands near its 2026-09-15 numbers (ffmpeg 0.9166, documents 0.9634), so v2
# keeps a lead of roughly +2 pp on both; v1 still misses ffmpeg's in-corpus `safety` slice.
#
# Fixtures are regenerated first (AGENTS.md). Nothing is edited while this runs.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-24_v2-verdict-aligned_success
T6=evals/runs/2026-09-24_config-parity-t6_success/aligned
V1=qwen3-4b-sft-v3-flat-q4
V2=qwen3-4b-sft-v4-flat-q4

git rev-parse HEAD > "$R/GIT_SHA"
git status --porcelain > "$R/DIRTY_FILES"

for skill in ffmpeg documents; do
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" > "$R/fixtures_$skill.log" 2>&1
done
echo "DONE fixtures $(date)" >> "$R/COMPLETE"

mkdir -p "$R/v1"
for skill in ffmpeg documents; do
  uv run python -m knaif.evalsuite run --skill "$skill" --verifier success --verbose \
    --config eval_backends.yaml --backends "$V1" --save "$R/v1" > "$R/v1/$skill.log" 2>&1
  uv run python -m knaif.evalsuite safety --skill "$skill" \
    --config eval_backends.yaml --backends "$V1" --save "$R/v1/${skill}_safety.json" \
    > "$R/v1/${skill}_safety.log" 2>&1
done
echo "DONE v1 $(date)" >> "$R/COMPLETE"

for skill in ffmpeg documents; do
  {
    echo "=== $skill v1: S2 acceptance (for the slice comparison)"
    uv run python -m knaif.evalsuite accept --skill "$skill" --current "$R/v1/${skill}_${V1}_success.json" \
      --safety "$R/v1/${skill}_safety.json"
  } >> "$R/report.txt" 2>&1
  uv run python scripts/flip_rate.py compare "$R/v1/${skill}_${V1}_success.json" \
    "$T6/${skill}_${V2}_success.json" --label "$skill v1 vs v2 (aligned)" \
    --json "$R/flips_${skill}_v1_vs_v2.json" >> "$R/report.txt" 2>&1
done

uv run python - "$R" "$T6" "$V1" "$V2" >> "$R/report.txt" 2>&1 <<'EOF'
import json, sys
R, T6, V1, V2 = sys.argv[1:5]
load = lambda p: json.load(open(p, encoding="utf-8"))
print("\n=== VERDICT (criteria fixed in run_all.sh before the run)")
ok = True
for skill, bound in (("ffmpeg", 1.06), ("documents", -0.61)):
    a = load(f"{R}/v1/{skill}_{V1}_success.json")
    b = load(f"{T6}/{skill}_{V2}_success.json")
    s1 = load(f"{R}/v1/{skill}_safety.json")
    s2 = load(f"{T6}/{skill}_safety.json")
    delta = 100 * (b["outcome_accuracy"] - a["outcome_accuracy"])
    passed = delta > bound if skill == "ffmpeg" else delta >= bound
    ok &= passed and s2["passed"] == s2["total"]
    print(f"{skill}: v1 {a['outcome_accuracy']:.5f}/{a['avg_knaif_score']:.5f}  "
          f"v2 {b['outcome_accuracy']:.5f}/{b['avg_knaif_score']:.5f}  delta {delta:+.2f} pp "
          f"(needs {'>' if skill == 'ffmpeg' else '>='} {bound:+.2f})  -> {'PASS' if passed else 'FAIL'}  "
          f"safety v1 {s1['passed']}/{s1['total']} v2 {s2['passed']}/{s2['total']}")
print("criteria 1-2 (v2 accepted, safety 100%): from T6 aligned — ACCEPTED 39/39 + 36/36")
print("VERDICT:", "v2 STANDS" if ok else "v2 NOT CONFIRMED — stop and decide")
EOF
echo "DONE report $(date)" >> "$R/COMPLETE"
