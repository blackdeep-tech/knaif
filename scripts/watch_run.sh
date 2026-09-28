#!/usr/bin/env bash
# Watch a staged eval run (a folder with COMPLETE, verdicts.txt and per-cell logs) until Ctrl+C.
#
#   bash scripts/watch_run.sh evals/runs/<run-folder>
#
# Every 10 s: the last stage lines, the latest verdicts, and the active log's row and error
# counts with its last rows. It follows whichever cell is running; nothing it does touches the run.
R="${1:?usage: watch_run.sh <run folder>}"
while true; do
  clear
  f=$(ls -t "$R"/*.log "$R"/*/*/*.log 2>/dev/null | grep -v fixtures | head -1)
  echo "== stage"
  tail -n 3 "$R/COMPLETE" 2>/dev/null
  echo "== verdicts"
  grep -E "^===.*\(placement|ACCEPTED|REJECTED|parity_check exit|^T9a|VERDICT" "$R/verdicts.txt" 2>/dev/null | tail -n 8
  if [ -n "$f" ]; then
    echo "== now: $f  rows $(grep -cE 'file\(s\)|^\[ *[0-9]+/' "$f")  errors $(grep -cE ' error +[0-9]+ file' "$f")"
    tail -n 2 "$f"
  fi
  sleep 10
done
