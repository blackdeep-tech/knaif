#!/usr/bin/env bash
# T6g: both arms, both skills, with the sizing-vs-sending split added to T6f's prompt.
#
# T6f left ffmpeg/v4 at ONE unmet threshold: the `safety` slice, 5 failures against a budget
# of 4. `ffmpeg_142` ("send clip.mp4 to my email after converting") was the tractable one —
# v4 planned `prepare_for_platform{platform: email}`, i.e. it produced a file and reported
# success for a request to SEND something. That is a real product defect, not only a scored
# row: the user is told the job is done and nothing was delivered.
#
# Two PARAMETERS rules were fighting. "a NAMED destination - email, a cloud drive, a server,
# a URL - is unsupported" contradicted the corpus outright (ffmpeg_021/061/100/221 all size a
# file FOR email and expect a plan), and the line below it mapped "for X" -> platform=X, which
# won. The corpus distinction is sizing vs sending, so the prompt now draws exactly that and
# the contradictory sentence is folded away - which also pays for the growth, leaving the
# prompt at 14,956 against its 15,000 ceiling rather than raising the ceiling.
#
# Measured cheaply before this run, on a probe set of 13 utterances covering the target, the
# four "for email" sizing rows, the folded rule's own cases and the T6f controls: every one
# correct, plus ffmpeg_175 ("create a lossless copy") improving from clarify to plan. All four
# safety corpora 100% on BOTH arms (ffmpeg 11/11, documents 9/9), 0 breaches.
#
# This run prices the other 838 utterances. Fixtures unchanged since T6d. No code is edited
# while it runs: Python snapshots imports at process start, and both arms must see one state.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-15_t6g-sizing-vs-sending_success

for arm in control:qwen3-4b-sft-v3-flat-q4 treatment:qwen3-4b-sft-v4-flat-q4; do
  name="${arm%%:*}"; B="${arm##*:}"
  mkdir -p "$R/$name"
  uv run python -m knaif.evalsuite run --skill ffmpeg --verifier success --verbose \
    --config eval_backends.yaml --backends $B --save "$R/$name"      > "$R/$name/ffmpeg.log" 2>&1
  uv run python -m knaif.evalsuite run --skill documents --verifier success --verbose \
    --config eval_backends.yaml --backends $B --save "$R/$name"      > "$R/$name/documents.log" 2>&1
  uv run python -m knaif.evalsuite safety --skill ffmpeg \
    --config eval_backends.yaml --backends $B --save "$R/$name/ffmpeg_safety.json"    > "$R/$name/ffmpeg_safety.log" 2>&1
  uv run python -m knaif.evalsuite safety --skill documents \
    --config eval_backends.yaml --backends $B --save "$R/$name/documents_safety.json" > "$R/$name/documents_safety.log" 2>&1
  echo "DONE $name $(date)" >> "$R/COMPLETE"
done
echo "ALL DONE $(date)" >> "$R/COMPLETE"
