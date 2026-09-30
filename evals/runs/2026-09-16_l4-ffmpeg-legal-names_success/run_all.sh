#!/usr/bin/env bash
# L4 after the illegal-filename binding fix and the batch-naming fix.
#
# What changed since 2026-09-15_l4-ffmpeg-argatespost (35/39 = 0.897 on extract_audio,
# against a 0.900 floor needing 36):
#   * a model-supplied output name the filesystem would reject is normalised, and every
#     later step that read it is rebound (`ffmpeg_268#4` - the row that WAS the whole gap)
#   * batch output names carry the source part that actually differs (`audio_mp4_5.mp3`)
#   * the CUDA offer no longer fires on a CUDA-compiled binary, so it stops polluting
#     captured native stdout/stderr
#
# Binary rebuilt 2026-09-16 10:13 with llama,cuda,pdfium. Same model as the run it is
# compared against (sft-v4), same corpus, same verifier.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-16_l4-ffmpeg-legal-names_success
uv run python -m knaif.evalsuite native --skill ffmpeg --lane native-cli --verifier success \
  --verbose --save "$R" > "$R/native.log" 2>&1
echo "SUITE DONE $(date)" >> "$R/COMPLETE"
uv run python -m knaif.evalsuite safety --skill ffmpeg --lane native-cli \
  --save "$R/ffmpeg_safety.json" > "$R/safety.log" 2>&1
echo "ALL DONE $(date)" >> "$R/COMPLETE"
