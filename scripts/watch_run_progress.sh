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

# One finished row, in either producer's format. `evalsuite native --verbose` carries an
# `N file(s)` column; `evalsuite run --verbose` does not — it prints
# `  [ffmpeg_001]  OK    plan   877ms  <utterance>` — so matching on `file(s)` alone counted
# zero rows forever for the more common of the two. Both start with a bracketed row id, and in
# both the outcome is field 3, which is what the tally below reads. The latency column is
# part of the pattern on purpose: ffmpeg's own stderr (`[in#0 @ 0x...] Error opening ...`)
# also starts with a bracketed token, and without it the count ran to 1458 on an 851-row
# log. Verified against a finished run: 851 rows, and the tally reproduces the
# scoreboard's by_outcome exactly (plan 580, clarify 202, reject 44, error 24,
# parse_error 1).
ROW_RE='(^[[:space:]]*\[[^][:space:]]+\][[:space:]]+[A-Za-z]+[[:space:]]+[a-z_]+[[:space:]]+[0-9.]+ms)|file\(s\)'

# `grep -c` PRINTS 0 and EXITS 1 when nothing matches, so `|| echo 0` appended a second line
# and every later `[ "$n" -gt 0 ]` died on a two-line "0" with "integer expression
# expected". Guard on the
# file existing instead, and let grep's own 0 stand.
progress_count() {
  if [ -f "$LOG" ]; then grep -cE "$ROW_RE" "$LOG" 2>/dev/null; else echo 0; fi
}

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

  tally=$(grep -E "$ROW_RE" "$LOG" 2>/dev/null | awk '{c[$3]++} END {for (k in c) printf "%s=%d ", k, c[k]}')
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
  # `wrote N native plans` / `native returned` end a `flip_rate.py native` batch.
  if grep -qE 'coverage +:|SCORE WITHHELD|Traceback|^ERROR|error: |^wrote [0-9]+ native plans|^native returned' "$LOG" 2>/dev/null; then
    printf '\n\n--- run finished ---\n'
    grep -E 'coverage +:|SCORE WITHHELD|Outcome accuracy|Avg knaif score|Traceback|^ERROR|^wrote [0-9]+ native plans|^native returned' "$LOG" | tail -20
    exit 0
  fi

  sleep "$INTERVAL"
done
