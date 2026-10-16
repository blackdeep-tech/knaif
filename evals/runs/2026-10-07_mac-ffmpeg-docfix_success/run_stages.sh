#!/usr/bin/env bash
# The four L4 stages of run_all.sh, ffmpeg only (metal 4b, metal 1.7b, cpu 4b, cpu 1.7b), then L3 (run_l3.sh).
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-10-07_mac-ffmpeg-docfix_success
for s in "metal 4b" "metal 1.7b" "cpu 4b" "cpu 1.7b"; do bash $R/run_all.sh $s; done
bash $R/run_l3.sh
