#!/usr/bin/env bash
# A release's remaining work on a machine the owner does not sit at (the Mac), as one queue that
# reports to a GitHub issue and waits there only for the owner. The relay itself, and the format
# of its comments, is python/core/knaif/evalsuite/relay.py; RELEASE.md, *The remote relay*, says
# when a release uses it.
#
# A release's own queue file (e.g. evals/runs/<run>/queue.sh) sets these, sources this file, and
# lists its steps with `step <name> <budget minutes> <function> [args...]`:
#   R               the run folder; the queue keeps its state in $R/queue
#   RELAY_REPO      owner/repo of the issue
#   RELAY_TITLE     the issue's exact title (one open issue)
#   RELAY_AUTHORS   comma-separated GitHub logins whose decisions count (the owner's accounts)
#
# What a step does:
#   - posts "started", runs its function, then posts a report: the exit code, the step's summary
#     (`$STEP_DIR/summary.md`) and each cell it checked (`cell_check`), its misses split into those
#     the owner already accepted ($R/accepted.yaml plus every decision's `accept`) and new ones;
#   - is done when the function exited 0 or 3 (an evidence stage's "ran, with gate misses") and no
#     miss is new and nothing asked for the owner (`needs_owner`);
#   - otherwise waits on the issue for the owner's `knaif-decision`: continue, rerun (the step
#     again; `$RERUN` is set) or stop;
#   - posts once more if it runs past its budget, and keeps running.
# A done step is never run again ($R/queue/done/<name>), so the queue can be started again after any
# stop; a step that was waiting goes straight back to waiting on the same report.
#
# Exit 0 every step done, 1 the issue could not be reached, 4 the owner said stop.
# Written for bash 3.2 (macOS's own): no empty arrays under `set -u`, no bash-4 builtins.

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
: "${R:?the queue file sets R}" "${RELAY_REPO:?}" "${RELAY_TITLE:?}" "${RELAY_AUTHORS:?}"
[ -d "$HOME/.local/bin" ] && export PATH="$HOME/.local/bin:$PATH"

UV="${UV:-uv run --no-sync}"   # a plain `uv run` re-syncs llama-cpp-python to its CPU build
RELAY="$UV python -m knaif.evalsuite.relay"
Q="$R/queue"
RELAY_POLL="${RELAY_POLL:-120}"
mkdir -p "$Q/done" "$Q/waiting"
touch "$Q/decisions.jsonl"

qlog() { echo "$(date '+%F %T') $*" | tee -a "$Q/LOG"; }
# stdin -> stdout, the checkout as <repo>, the home as ~: for every file the queue writes that is
# committed. A step's summary is not committed and is scrubbed when it is posted.
scrubbed() { $RELAY scrub; }
note() { echo "$*" >> "$STEP_DIR/summary.md"; }   # a line of the step's summary
note_file() {  # $1 title, $2 file, [$3 lines]: the file's last lines, as a block
  [ -f "$2" ] || { note "$1: (no file)"; return 0; }
  { echo; echo "$1:"; echo '```text'; tail -n "${3:-25}" "$2"; echo '```'; } >> "$STEP_DIR/summary.md"
}
needs_owner() { note "**For the owner:** $*"; touch "$STEP_DIR/NEEDS_OWNER"; }

relay_issue() {
  [ -n "${RELAY_ISSUE:-}" ] && return 0
  RELAY_ISSUE="$($RELAY issue --repo "$RELAY_REPO" --title "$RELAY_TITLE")" \
    || { qlog "no open issue titled '$RELAY_TITLE' in $RELAY_REPO"; exit 1; }
}

relay_post() {  # $1 file -> the comment id on stdout
  local id
  id="$($RELAY post --repo "$RELAY_REPO" --issue "$RELAY_ISSUE" --file "$1" --root "$PWD")" \
    || { qlog "could not post $1; it is kept in $Q"; return 1; }
  echo "$id"
}

# A cell's verdict, sorted against what the owner accepted: $STEP_DIR/cell-<model>-<backend>-<skill>.json
cell_check() {  # $1 model, $2 backend, $3 skill, $4 the cell's folder
  local name="$1/$2/$3" rc
  [ "${4##*/}" = live ] && name="$name (live)"
  $RELAY misses --cell-dir "$4" --skill "$3" --model "$1" --backend "$2" --cell "$name" \
    --rules "$R/accepted.yaml" "$Q/decisions.jsonl" \
    --out "$STEP_DIR/cell-$1-$2-$3$([ "${4##*/}" = live ] && echo -live).json" >> "$STEP_DIR/checks.log" 2>&1
  rc=$?
  case $rc in
    0) ;;
    3) touch "$STEP_DIR/NEEDS_OWNER" ;;
    *) needs_owner "reading the verdict of $name failed (exit $rc)" ;;
  esac
}

render() {  # $1 status, $2 rc, $3 elapsed s, $4 budget, $5 out, [$6 summary]: the step's comment
  local cells=""
  [ "$1" = started ] || [ "$1" = manual ] || cells="$(ls "$STEP_DIR"/cell-*.json 2> /dev/null | tr '\n' ' ')"
  # shellcheck disable=SC2086  # the cell files are word-split on purpose; none has a space
  $RELAY report --step "$STEP" --status "$1" --rc "$2" --elapsed "$3" --budget "$4" \
    --summary "${6:-$STEP_DIR/summary.md}" --out "$5" --cells $cells
}

watchdog() {  # $1 budget minutes: one comment if the step runs past it
  (
    # Killed when the step ends: take the sleep along, or it outlives the queue.
    trap 'kill "$s" 2> /dev/null; exit 0' TERM
    sleep $(($1 * 60)) &
    s=$!
    wait "$s"
    [ -f "$STEP_DIR/.running" ] || exit 0
    echo "Still running after its budget of $1 min; it carries on." > "$STEP_DIR/budget.md"
    render started 0 $(($1 * 60)) "$1" "$STEP_DIR/budget-comment.md" "$STEP_DIR/budget.md" > /dev/null 2>&1 \
      && relay_post "$STEP_DIR/budget-comment.md" > /dev/null
  ) &
  WATCHDOG=$!
}

decide() {  # $1 the report's comment id: wait for the owner's decision on $STEP
  local action
  echo "$1" > "$Q/waiting/$STEP"
  qlog "$STEP: waiting for the owner (comment $1)"
  action="$($RELAY wait --repo "$RELAY_REPO" --issue "$RELAY_ISSUE" --step "$STEP" \
    --authors "$RELAY_AUTHORS" --after-id "$1" --decisions "$Q/decisions.jsonl" --poll "$RELAY_POLL")" \
    || { qlog "$STEP: waiting on the issue failed"; exit 1; }
  rm -f "$Q/waiting/$STEP"
  qlog "$STEP: the owner says $action"
  DECISION="$action"
}

step() {  # $1 name, $2 budget minutes, then the function and its arguments
  STEP="$1"; local budget="$2"; shift 2
  local start rc elapsed status id
  relay_issue
  [ -f "$Q/done/$STEP" ] && { qlog "$STEP: done in an earlier run, skipped"; return 0; }
  STEP_DIR="$Q/$STEP"
  RERUN=""
  if [ -s "$Q/waiting/$STEP" ]; then   # stopped while it waited: wait on the same report
    decide "$(cat "$Q/waiting/$STEP")"
    case "$DECISION" in
      continue) touch "$Q/done/$STEP"; return 0 ;;
      stop) qlog "STOPPED at $STEP by the owner"; exit 4 ;;
      rerun) RERUN=1 ;;
    esac
  fi
  while :; do
    rm -rf "$STEP_DIR" && mkdir -p "$STEP_DIR"
    : > "$STEP_DIR/summary.md"
    start=$(date +%s)
    qlog "START $STEP (budget $budget min${RERUN:+, again})"
    echo "Started$([ -n "$RERUN" ] && echo ' again, as the owner asked'); budget $budget min." > "$STEP_DIR/start-summary.md"
    render started 0 0 "$budget" "$STEP_DIR/start.md" "$STEP_DIR/start-summary.md" > /dev/null 2>&1 \
      && relay_post "$STEP_DIR/start.md" > /dev/null
    touch "$STEP_DIR/.running"
    watchdog "$budget"
    "$@"
    rc=$?
    rm -f "$STEP_DIR/.running"
    kill "$WATCHDOG" 2> /dev/null
    elapsed=$(($(date +%s) - start))
    status="done"
    case $rc in
      0) ;;
      3) ls "$STEP_DIR"/cell-*.json > /dev/null 2>&1 || needs_owner "exit 3 with no cell checked" ;;
      *) needs_owner "the step exited $rc" ;;
    esac
    [ -f "$STEP_DIR/NEEDS_OWNER" ] && status=waiting
    render "$status" "$rc" "$elapsed" "$budget" "$STEP_DIR/report.md" \
      || { qlog "$STEP: rendering the report failed"; exit 1; }
    id="$(relay_post "$STEP_DIR/report.md")" || exit 1
    qlog "$STEP: $status (exit $rc, $((elapsed / 60)) min), comment $id"
    if [ "$status" = "done" ]; then touch "$Q/done/$STEP"; return 0; fi
    decide "$id"
    case "$DECISION" in
      continue) touch "$Q/done/$STEP"; return 0 ;;
      stop) qlog "STOPPED at $STEP by the owner"; exit 4 ;;
      rerun) RERUN=1 ;;
    esac
  done
}

manual() {  # $1 the file a person writes when done, $2 the checklist: wait for the file
  local waited=0
  { echo "**Manual step, for the person at the Mac.** The queue waits for \`$1\`."; echo; cat "$2"; } \
    | scrubbed > "$STEP_DIR/manual-summary.md"
  render manual 0 0 0 "$STEP_DIR/manual.md" "$STEP_DIR/manual-summary.md" > /dev/null 2>&1 \
    && relay_post "$STEP_DIR/manual.md" > /dev/null
  qlog "$STEP: waiting for $1 (manual)"
  while [ ! -s "$1" ]; do sleep 60; waited=$((waited + 1)); done
  note "Done by hand after about $waited min; \`$1\` written."
}

queue_end() {
  STEP=end STEP_DIR="$Q/end"
  mkdir -p "$STEP_DIR" && : > "$STEP_DIR/summary.md"
  note "Every step is done. $*"
  render "done" 0 0 0 "$STEP_DIR/report.md" > /dev/null && relay_post "$STEP_DIR/report.md" > /dev/null
  qlog "END: every step done"
}
