#!/usr/bin/env bash
# L4 — the SHIPPED native binary, executing for real, with v2 on the aligned llama.cpp config.
# The last evidence before the v2 publish, which cannot be taken back (docs/plans/
# 2026-09-23-inference-config-parity.md; the publish order in AGENTS.md "Native port").
#
# Everything so far graded the Python lane. AGENTS.md: L4 is "the only number backing 'it works'",
# and the existing L4 record predates the pinned compute config, the 2026-09-23 engine fixes and
# v2 on native. This grades target/release-cuda (rebuilt below) with the LOCAL GGUF — nothing is
# published or downloaded — against the written bar and the Python baseline re-locked today:
#
#   native >= max(S2 floor, accepted Python score - 0.02) on outcome_accuracy and avg_knaif_score,
#   complete coverage, every required slice, safety 100% measured ON THE BINARY.
#
#   ffmpeg    Python locked 0.93772 / 0.98373  ->  native bar 0.91772 / 0.96373 (or the floor)
#   documents Python locked 0.97561 / 0.99782  ->  native bar 0.95561 / 0.97782 (or the floor)
#
# PREDICTION, written before the run so it can be wrong:
#   * safety 100% on the binary for both skills.
#   * documents ACCEPTED — native and Python agreed on every documents decision once the config
#     matched (config-parity T2: 0 flips).
#   * ffmpeg outcome 0.92-0.935: above its 0.9177 bar, but below Python, because native has no
#     source threader (sloppy "it" chains run literally) and three chain rows are recorded as
#     failing only natively (`step N of M failed`). The risk is a chain slice (chain2 allows 2
#     failures, chain3 floor 0.92), not the aggregate.
#
# DECISION, fixed now:
#   * both skills L4 ACCEPTED (safety 100% included) -> the publish is evidence-backed; it still
#     waits for the owner's explicit go-ahead.
#   * either fails -> do not publish. v1 stays public; diagnose the failing rows first (candidate
#     fixes already planned: the native threader port, codec-as-extension outputs like *.hevc).
#
# Commit the snapshot re-lock BEFORE running: accept-native reads the snapshot from the tree, and
# GIT_SHA should name the bar this is graded against. Nothing is edited while this runs.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-24_l4-v2-aligned_success

git rev-parse HEAD > "$R/GIT_SHA"
git status --porcelain > "$R/DIRTY_FILES"

# The binary must be what the code says. Rebuild (seconds when current), then refuse a build older
# than any native source — the check that caught the lane pointing at a 2026-09-16 binary.
bash scripts/build_native_kind.sh cuda > "$R/build.log" 2>&1 || { echo "build failed" >> "$R/COMPLETE"; exit 1; }
uv run python - > "$R/freshness.log" 2>&1 <<'EOF' || { echo "STALE BINARY - aborted" >> "$R/COMPLETE"; exit 1; }
import sys
from pathlib import Path
sys.path.insert(0, "notebooks/shared")
from workbench.inventory import newest_native_source
binary = Path("target/release-cuda/knaif.exe")
if not binary.exists():
    binary = Path("target/release-cuda/knaif")
src, mtime = newest_native_source(".")
built = binary.stat().st_mtime
print(f"binary {binary} built {built:.0f}; newest source {src} {mtime:.0f}")
sys.exit(0 if built >= mtime else 1)
EOF
echo "DONE build $(date)" >> "$R/COMPLETE"

for skill in ffmpeg documents; do
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" > "$R/fixtures_$skill.log" 2>&1
done
echo "DONE fixtures $(date)" >> "$R/COMPLETE"

for skill in ffmpeg documents; do
  uv run python -m knaif.evalsuite native --skill "$skill" --lane native-cli --verifier success \
    --verbose --config eval_backends.yaml --save "$R" > "$R/$skill.log" 2>&1
  uv run python -m knaif.evalsuite safety --skill "$skill" --lane native-cli \
    --config eval_backends.yaml --save "$R/${skill}_safety.json" > "$R/${skill}_safety.log" 2>&1
  echo "DONE $skill $(date)" >> "$R/COMPLETE"
done

for skill in ffmpeg documents; do
  {
    echo "=== $skill: L4 acceptance (native vs S2 floor and the Python baseline)"
    uv run python -m knaif.evalsuite accept-native --skill "$skill" \
      --current "$R/${skill}_native-cli_success.json" --safety "$R/${skill}_safety.json"
  } >> "$R/report.txt" 2>&1
done
echo "DONE report $(date)" >> "$R/COMPLETE"
