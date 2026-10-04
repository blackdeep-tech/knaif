#!/usr/bin/env bash
# Runs the four L4 stages of run_all.sh one after another (metal 4b, metal 1.7b, cpu 4b, cpu 1.7b).
cd "$(dirname "$0")/../../.."
for s in "metal 4b" "metal 1.7b" "cpu 4b" "cpu 1.7b"; do bash evals/runs/2026-10-03_mac-l4-rerun_success/run_all.sh $s; done
