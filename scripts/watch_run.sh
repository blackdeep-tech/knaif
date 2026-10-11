#!/usr/bin/env bash
# Watch a staged eval run (a folder with COMPLETE, verdicts.txt and per-cell logs) until Ctrl+C.
#
#   bash scripts/watch_run.sh evals/runs/<run-folder>
#
# Every 10 s (WATCH_INTERVAL) it redraws one screen: a progress bar over every stage, time
# passed and left, one line per stage (done / running / waiting), the running stage's last two
# rows, the latest events from COMPLETE and any verdicts. While no stage with rows is running (a
# build, a probe) it shows the newest log's last line instead. Nothing it does touches the run.
# It stops on its own when COMPLETE says `ALL DONE` or `ABORT`. With WATCH_ONCE=1 it draws one
# screen and exits; WATCH_COLOR=0 turns colour off.
#
# Totals, per log, first match wins:
#   1. the log's own header: `rows to run: N` (`evalsuite run --verbose`) or
#      `exact-files: N row(s)` (`evalsuite exact-files`);
#   2. a `TOTALS` file in the run folder, one `<log name> <rows> [seconds per row]` line per
#      stage. It also fixes the stage order, so stages that have not started yet count toward
#      the total. The optional third column prices a stage that has not started (an encode row
#      costs ~10x a planning row); without it the run's average pace stands in.
# Without either, a log's total is unknown and the progress line says so instead of guessing.
R="${1:?usage: watch_run.sh <run folder>}"
R="${R%/}"
INTERVAL="${WATCH_INTERVAL:-10}"
# One finished row in either producer's format: `evalsuite native --verbose` carries an
# `N file(s)` column; `evalsuite run --verbose` prints `  [ffmpeg_001]  OK    plan   877ms  …`
# (same pattern as watch_run_progress.sh; matching `file(s)` alone counted 0 for the latter).
# `evalsuite exact-files` prints `  [ffmpeg_116#0]  clean` or `  [ffmpeg_116#0]  LEFT …`;
# `parity_check.py` prints `[  3/20] …`.
ROW_RE='(^[[:space:]]*\[[^][:space:]]+\][[:space:]]+[A-Za-z]+[[:space:]]+[a-z_]+[[:space:]]+[0-9.]+ms)|file\(s\)|^\[ *[0-9]+/|^[[:space:]]*\[[^][:space:]]+#[0-9]+\][[:space:]]+(clean|LEFT)'
ERR_RE=' error +[0-9]+ file|^[[:space:]]*\[[^][:space:]]+\][[:space:]]+[A-Za-z]+[[:space:]]+error[[:space:]]+[0-9.]+ms'
HEADER_RE='rows to run: [0-9]+|^exact-files: [0-9]+ row'

if [ "${WATCH_COLOR:-1}" = 1 ] && [ -t 1 ]; then
  B=$'\e[1m'; DIM=$'\e[2m'; GRN=$'\e[32m'; YEL=$'\e[33m'; RED=$'\e[31m'; N=$'\e[0m'
else
  B=; DIM=; GRN=; YEL=; RED=; N=
fi

fmt() { # seconds -> 1h02m / 12m05s / 9s
  local s=${1:-0}
  if [ "$s" -ge 3600 ]; then printf '%dh%02dm' $((s / 3600)) $((s % 3600 / 60))
  elif [ "$s" -ge 60 ]; then printf '%dm%02ds' $((s / 60)) $((s % 60))
  else printf '%ds' "$s"; fi
}
mtime() { stat -c %Y "$1" 2>/dev/null || echo 0; }
# When a stage's log was created. Creation time where the filesystem reports it (Git Bash on
# NTFS); WSL's bash on /mnt/c reports none, and falling back to the log's *modification* time
# made every running stage look 0 s old ("stage left 0s"). So the fallback is the moment the
# rest of the run last changed: stages run one after another, so the newest other file is the
# previous stage finishing.
started() {
  local f=$1 b m
  b=$(stat -c %W "$f" 2>/dev/null); m=$(mtime "$f")
  if [ -n "$b" ] && [ "$b" != 0 ] && [ "$b" != "-" ] && [ "$b" -le "$m" ]; then echo "$b"; return; fi
  find "$R" -maxdepth 1 -type f ! -path "$f" ! -name TOTALS ! -name 'run_all.*' \
    -printf '%T@\n' 2>/dev/null | cut -d. -f1 | awk -v m="$m" '$1 <= m' | sort -n | tail -1
}
rows_in() { if [ -f "$1" ]; then grep -cE "$ROW_RE" "$1" 2>/dev/null; else echo 0; fi; }
header_total() { [ -f "$1" ] && grep -m1 -oE "$HEADER_RE" "$1" 2>/dev/null | grep -oE '[0-9]+' | head -1; }
bar() { # done total width -> [█████░░░░░]
  local d=$1 t=$2 w=$3 fill i s=""
  fill=$(( t > 0 ? w * d / t : 0 )); [ "$fill" -gt "$w" ] && fill=$w
  for ((i = 0; i < w; i++)); do if [ "$i" -lt "$fill" ]; then s+="█"; else s+="░"; fi; done
  printf '%s' "$s"
}
strip() { local s=$1; s=${s#"${s%%[![:space:]]*}"}; printf '%s' "$s"; }
# `... exit=0 Sun Oct 11 11:51:19 RDT 2026` -> `11:51:19  ... exit=0`
event() {
  sed -E 's/^(.*[^ ]) +[A-Z][a-z]{2} [A-Z][a-z]{2} +[0-9]+ ([0-9]{2}:[0-9]{2}:[0-9]{2}) [A-Z]+ [0-9]{4}(.*)$/\2  \1\3/'
}

while true; do
  now=$(date +%s)
  cols=$(tput cols 2>/dev/null || echo 100); [ "$cols" -gt 120 ] && cols=120
  # The row-bearing logs: those TOTALS names (in its order), then any other log with rows.
  logs=()
  declare -A want=() pace=()
  if [ -f "$R/TOTALS" ]; then
    while read -r name n spr; do
      [ -z "$name" ] && continue
      logs+=("$R/$name"); want["$R/$name"]=$n
      [ -n "$spr" ] && pace["$R/$name"]=$spr
    done < "$R/TOTALS"
  fi
  while read -r _ p; do
    [ -n "${want[$p]+x}" ] && continue
    [ "$(rows_in "$p")" -gt 0 ] || [ -n "$(header_total "$p")" ] || continue
    logs+=("$p")
  done < <(find "$R" -name '*.log' ! -name 'fixtures*' ! -name '*_rerun.log' ! -name '*_compose.log' \
    -printf '%T@ %p\n' 2>/dev/null | sort -n)

  done_all=0; total_all=0; unknown=0
  for p in "${logs[@]}"; do
    d=$(rows_in "$p"); t=$(header_total "$p"); t=${t:-${want[$p]:-}}
    done_all=$((done_all + d))
    if [ -n "$t" ]; then total_all=$((total_all + t)); else unknown=1; fi
  done

  # The run started with its oldest file (the first stage's log, or the GPU check).
  start=$(find "$R" -maxdepth 1 -type f ! -name TOTALS ! -name 'run_all.*' -printf '%T@\n' \
    2>/dev/null | cut -d. -f1 | sort -n | head -1)
  for p in "$R"/*.log; do
    [ -f "$p" ] || continue
    b=$(stat -c %W "$p" 2>/dev/null)
    [ -n "$b" ] && [ "$b" != 0 ] && [ "$b" != "-" ] && [ "$b" -lt "${start:-$now}" ] && start=$b
  done
  [ -z "$start" ] && start=$now
  state=running
  if grep -q "ABORT" "$R/COMPLETE" 2>/dev/null; then state=aborted
  elif grep -q "ALL DONE" "$R/COMPLETE" 2>/dev/null; then state=finished; fi
  if [ "$state" = running ]; then end=$now; else end=$(mtime "$R/COMPLETE"); fi
  passed=$((end - start))

  # The active row log: the newest one with rows (or a header).
  f=""
  if [ "$state" = running ]; then
    f=$(for p in "${logs[@]}"; do [ -f "$p" ] && echo "$(mtime "$p") $p"; done | sort -rn | head -1 | cut -d' ' -f2-)
  fi
  # The newest log of any kind: a build or probe has no rows, but it is what is running.
  newest=$(find "$R" -maxdepth 1 -name '*.log' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)

  stage_left=""; run_left=""
  if [ -n "$f" ]; then
    # The current stage's own rate: stages differ by 10x (a planning row vs an encode).
    sd=$(rows_in "$f"); st=$(header_total "$f"); st=${st:-${want[$f]:-}}
    se=$((now - $(started "$f")))
    if [ "$sd" -gt 0 ] && [ -n "$st" ]; then
      stage_left=$(( (st - sd) * se / sd )); [ "$stage_left" -lt 0 ] && stage_left=0
    fi
    # The rest of the run: this stage's own estimate, plus every later stage priced by its
    # TOTALS pace, else by the run's average pace so far. A rough number, marked as such.
    if [ "$unknown" = 0 ] && [ -n "$stage_left" ] && [ "$done_all" -gt 0 ]; then
      run_left=$stage_left; later=0
      for p in "${logs[@]}"; do
        if [ "$later" = 1 ]; then
          t=$(header_total "$p"); t=${t:-${want[$p]:-0}}; left=$((t - $(rows_in "$p")))
          if [ -n "${pace[$p]:-}" ]; then
            run_left=$(awk -v a="$run_left" -v n="$left" -v s="${pace[$p]}" 'BEGIN{printf "%d", a + n * s}')
          else
            run_left=$((run_left + left * passed / done_all))
          fi
        fi
        [ "$p" = "$f" ] && later=1
      done
    fi
  fi

  rule=$(printf '%*s' "$((cols < 72 ? cols : 72))" '' | tr ' ' '-')
  out="${B}${R##*/}${N}"$'\n'"${DIM}${rule}${N}"$'\n'
  if [ "$unknown" = 0 ] && [ "$total_all" -gt 0 ]; then
    pct=$((100 * done_all / total_all))
    out+=$(printf '%-9s %s %3d%%   %s / %s rows' "Progress" "$(bar "$done_all" "$total_all" 30)" "$pct" "$done_all" "$total_all")$'\n'
  else
    out+=$(printf '%-9s %s rows done (a stage total is unknown)' "Progress" "$done_all")$'\n'
  fi
  case $state in
    finished) status="${GRN}${B}FINISHED${N}" ;;
    aborted) status="${RED}${B}ABORTED${N}" ;;
    *) status="${YEL}running${N}" ;;
  esac
  tline="$(fmt "$passed") passed"
  [ -n "$run_left" ] && tline+="   ~$(fmt "$run_left") left"
  out+=$(printf '%-9s %s' "Time" "$tline")$'\n'
  out+=$(printf '%-9s %s' "Status" "$status")$'\n\n'

  if [ "${#logs[@]}" -gt 0 ]; then
    out+="${B}Stages${N}"$'\n'
    w=0; for p in "${logs[@]}"; do n=${p#"$R"/}; n=${n%.log}; [ "${#n}" -gt "$w" ] && w=${#n}; done
    seen_active=0
    for p in "${logs[@]}"; do
      n=${p#"$R"/}; n=${n%.log}
      d=$(rows_in "$p"); t=$(header_total "$p"); t=${t:-${want[$p]:-?}}
      if [ "$p" = "$f" ]; then
        mark="${YEL}▶${N}"; extra=""; [ -n "$stage_left" ] && extra="   ${DIM}~$(fmt "$stage_left") left${N}"
        seen_active=1
      elif [ "$state" != running ] || { [ "$seen_active" = 0 ] && [ -n "$f" ] && [ -f "$p" ]; } \
        || { [ "$t" != "?" ] && [ "$d" -ge "$t" ]; }; then
        mark="${GRN}✓${N}"; extra=""
      else
        mark="${DIM}·${N}"; extra=""
      fi
      out+=$(printf '  %s  %-*s  %5s / %-5s%s' "$mark" "$w" "$n" "$d" "$t" "$extra")$'\n'
    done
    out+=$'\n'
  fi

  if [ -n "$f" ]; then
    errs=$(grep -cE "$ERR_RE" "$f")
    ecol=$DIM; [ "$errs" -gt 0 ] && ecol=$RED
    n=${f#"$R"/}
    out+="${B}Now${N}      ${n%.log}   $(rows_in "$f") rows   ${ecol}${errs} errors${N}"$'\n'
    last2=$(grep -E "$ROW_RE" "$f" | tail -n 2)
    if [ -n "$last2" ]; then
      prev=$(printf '%s\n' "$last2" | sed -n '1p'); cur=$(printf '%s\n' "$last2" | sed -n '2p')
      [ -z "$cur" ] && { cur=$prev; prev=""; }
      out+="  last   $(strip "$cur" | cut -c1-$((cols - 9)))"$'\n'
      [ -n "$prev" ] && out+="  ${DIM}prev   $(strip "$prev" | cut -c1-$((cols - 9)))${N}"$'\n'
    fi
    out+=$'\n'
  elif [ "$state" = running ] && [ -n "$newest" ]; then
    n=${newest#"$R"/}
    l=$(grep -v '^[[:space:]]*$' "$newest" 2>/dev/null | tail -n 1 | tr -d '\r')
    out+="${B}Now${N}      ${n%.log}   ${DIM}(no rows yet)${N}"$'\n'
    [ -n "$l" ] && out+="  last   $(strip "$l" | cut -c1-$((cols - 9)))"$'\n'
    out+=$'\n'
  fi

  if [ -f "$R/COMPLETE" ]; then
    out+="${B}Events${N}"$'\n'
    while IFS= read -r l; do
      [ -z "$l" ] && continue
      case $l in *ABORT*|*WARNING*) l="${RED}${l}${N}" ;; esac
      out+="  $(printf '%s' "$l" | cut -c1-$((cols - 2)))"$'\n'
    done < <(tail -n 4 "$R/COMPLETE" | event)
  fi
  v=$(grep -E "^===.*\(placement|ACCEPTED|REJECTED|parity_check exit|^T9a|VERDICT" "$R/verdicts.txt" 2>/dev/null | tail -n 8)
  [ -n "$v" ] && out+=$'\n'"${B}Verdicts${N}"$'\n'"$v"$'\n'

  clear
  printf '%s' "$out"
  unset want pace
  [ "${WATCH_ONCE:-0}" = 1 ] && break
  [ "$state" != running ] && break
  sleep "$INTERVAL"
done
