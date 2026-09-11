#!/usr/bin/env bash
# Live progress for a long-running eval / lane / parity run.
#
# These runs are 30-60 minutes and print one line per utterance, which is unreadable as a
# firehose and invisible when redirected to a file. This redraws a single status line:
# how far in, the outcome tally so far, throughput and an ETA.
#
#   bash scripts/watch_run_progress.sh <log-file> [total-rows]
#
# Counts lines emitted by `evalsuite native --verbose` and `evalsuite run --verbose`, which
# carry a `N file(s)` column. Without <total-rows> it reports position and rate but no ETA —
# a percentage against an unknown denominator would be a guess.
set -uo pipefail

LOG="${1:?usage: watch_run_progress.sh <log-file> [total-rows]}"
TOTAL="${2:-0}"
INTERVAL="${WATCH_INTERVAL:-2}"

progress_count() { grep -c 'file(s)' "$LOG" 2>/dev/null || echo 0; }

start_epoch=$(date +%s)
start_n=$(progress_count)
baseline_set=0

printf 'watching %s\n' "$LOG"
[ "$TOTAL" -gt 0 ] && printf 'total rows: %s\n' "$TOTAL"
printf 'Ctrl-C to stop watching (the run itself keeps going)\n\n'

while true; do
  if [ ! -f "$LOG" ]; then
    printf '\r  waiting for the log to appear ...'
    sleep "$INTERVAL"
    continue
  fi

  n=$(progress_count)
  # Rows already finished before this watcher started must not count toward its rate,
  # or the ETA is computed from work it never observed.
  if [ "$baseline_set" -eq 0 ] && [ "$n" -gt 0 ]; then
    start_n=$n
    start_epoch=$(date +%s)
    baseline_set=1
  fi

  tally=$(grep 'file(s)' "$LOG" 2>/dev/null | awk '{c[$3]++} END {for (k in c) printf "%s=%d ", k, c[k]}')
  elapsed=$(( $(date +%s) - start_epoch ))
  observed=$(( n - start_n ))

  rate_txt="--"
  eta_txt=""
  if [ "$observed" -gt 0 ] && [ "$elapsed" -gt 0 ]; then
    rate_txt=$(awk -v o="$observed" -v e="$elapsed" 'BEGIN{printf "%.2f/s", o/e}')
    if [ "$TOTAL" -gt 0 ] && [ "$n" -lt "$TOTAL" ]; then
      eta_txt=$(awk -v r="$TOTAL" -v n="$n" -v o="$observed" -v e="$elapsed" \
        'BEGIN{s=(r-n)*e/o; printf "  eta %dm%02ds", s/60, s%60}')
    fi
  fi

  if [ "$TOTAL" -gt 0 ]; then
    pct=$(awk -v n="$n" -v t="$TOTAL" 'BEGIN{printf "%5.1f", 100*n/t}')
    printf '\r  %d/%d (%s%%)  %s %s%s          ' "$n" "$TOTAL" "$pct" "$tally" "$rate_txt" "$eta_txt"
  else
    printf '\r  %d rows  %s %s          ' "$n" "$tally" "$rate_txt"
  fi

  # Every terminal state, not just the happy one: a watcher that only knows how success
  # looks stays silent through a crash, and silence reads identical to "still running".
  if grep -qE 'coverage +:|SCORE WITHHELD|Traceback|^ERROR|error: ' "$LOG" 2>/dev/null; then
    printf '\n\n--- run finished ---\n'
    grep -E 'coverage +:|SCORE WITHHELD|Outcome accuracy|Avg knaif score|Traceback|^ERROR' "$LOG" | tail -20
    exit 0
  fi

  sleep "$INTERVAL"
done
