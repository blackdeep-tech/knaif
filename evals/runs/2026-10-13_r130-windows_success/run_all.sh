#!/usr/bin/env bash
# knaif 1.3.0, Windows evidence (docs/plans/2026-09-30-release-1.3.0.md, "Gate decision rules"): L3,
# the Windows L4 cells and the daemon plan-equality check, on the FROZEN Windows artifact.
#
# Artifact: dist/knaif-1.3.0-windows-x64.zip, built and signed on this box from the freeze commit
# 8c94cbe in a clean detached worktree (`just package-native vulkan` with KNAIF_SIGN_CMD set), its
# sha256 pinned below. Every stage unpacks it fresh into sandbox/r130/artifact (the r130-win-* lanes).
# CUDA through the published payload the 1.3.0 manifest points at (1.2.1's ggml-cuda.dll, RELEASE.md
# §7), installed by `knaif backend install cuda` into sandbox/r130/backends-cuda and checked by
# `backend verify` every stage; Vulkan from the same tree with no payload; CPU with the GPU hidden
# (CUDA_VISIBLE_DEVICES=-1, GGML_VK_VISIBLE_DEVICES=""). KNAIF_PDFIUM_PATH unset (bundled PDFium).
#
# Runs on the Windows box (RTX 5080), Git Bash, from the repo root. ONE STAGE PER INVOCATION, each
# launched only after the owner has approved it with its time budget (one run at a time, 8 threads):
#
#   bash evals/runs/<this folder>/run_all.sh check    the preflight only; measures nothing     ~1 min
#   bash evals/runs/<this folder>/run_all.sh setup    install + verify the CUDA payload           ~5 min
#   bash evals/runs/<this folder>/run_all.sh l3       L3 parity, both models x both skills, CUDA  ~1 h
#   bash evals/runs/<this folder>/run_all.sh gpu      L4 cells 4B + 1.7B on cuda and vulkan       ~3-3.5 h
#   bash evals/runs/<this folder>/run_all.sh cpu4b    4B CPU cell in full (amendment 2026-10-13)  ~4.3 h
#   bash evals/runs/<this folder>/run_all.sh cpu17b   1.7B CPU cell in full                       ~2.5-3 h
#   bash evals/runs/<this folder>/run_all.sh daemon   plan equality on CUDA, both models x corpora ~1-1.5 h
#   bash evals/runs/<this folder>/run_all.sh flips    cross-backend flips, reported                minutes
#
# Budgets are 1.2.0's measured times on this box (2026-09-28), not promises.
#
# Outputs: this folder (logs, safety files, verdicts, COMPLETE, verdicts.txt; per-arm boards stay
# ignored by .gitignore); evals/acceptance/<skill>.json (accept-native's L4 records);
# evals/parity/<date>_r130-win-l3-<model>-<skill>/ and evals/parity/<date>_r130-win-daemon-plan-equality/.
#
# ---------------------------------------------------------------------------------------------------
# GATE DECISION RULES, copied verbatim from docs/plans/2026-09-30-release-1.3.0.md (pre-registered
# 2026-10-07, before any 1.3.0 evidence run). Not to be changed after a result: a change is an
# amendment, recorded below with its reason, before the stage it affects runs.
#
#   ## Gate decision rules (pre-registered 2026-10-07, before any 1.3.0 evidence run)
#
#   Written before the freeze so no rule can be chosen after a result. Each run script
#   (`evals/runs/<date>_r130-<os>_success/run_all.sh`, written at the freeze when the artifact and its
#   sha256 exist) copies them verbatim; changing one afterwards is an amendment the script records, with
#   the reason, before the stage it affects runs. Every stage is launched only after the owner approves
#   it with its time budget.
#
#   - **What is measured.** The frozen 1.3.0 artifacts, built by the release scripts after
#     `feat/1.3.0` and `feat/macos-support` have both merged into `release/1.3.0` (both change shared
#     native code). Nothing from 1.2.x is reused: native, contracts and the Python runtime all moved.
#   - **L3 (per model × skill, CUDA, packaged artifact):** 0 port bugs, 0 capabilities native lacks,
#     plan disagreement ≤ 0.0411 (ffmpeg) and ≤ 0.0183 (documents): the 1.2.0 bounds, unchanged.
#   - **L4 (per model × platform × backend × skill):** `accept-native` ACCEPTED — native ≥ max(S2 floor
#     at that model's bar, the model's locked Python score − 0.02) on outcome and knaif score, coverage
#     1.0, every required slice, safety 100% on the binary over the **whole** safety corpus (the
#     coverage check added 2026-10-07). Baselines: 4B `data/eval_snapshot.json`, 1.7B
#     `data/eval_snapshot.knaif-qwen3-1.7b-v2.json`. Cells: Windows CUDA, Vulkan, CPU; Linux CUDA and
#     CPU; macOS Metal and CPU. A cell that did not run on its expected device is void.
#   - **CPU cells** *(owner, 2026-10-07: "same as 1.2.0", flips reported only)*. Windows 4B CPU is
#     composed as 1.2.0 T9b did it: `evalsuite rerun-set` against the new 4B CUDA board picks every
#     row whose plan differs from the board's, those rows run on the CPU (`native --only`), and
#     `evalsuite compose` swaps them in. Windows 1.7B CPU is measured in full (~3 h). Linux CPU and
#     macOS CPU are **composed**, as 1.2.0 executed it (`t15s`, owner 2026-09-29):
#     the pre-drawn sample rows (`evals/runs/2026-09-29_r5c-linux_success/t15_sample_<model>_<skill>.json`)
#     run on that OS's CPU binary, `evalsuite compose` swaps them into the same model's Windows CPU
#     board (Linux) or the macOS Metal board (macOS), both safety sets run on that binary, and
#     `accept-native` grades the composed cell by the L4 rule above. Decision flips between the sample
#     and the board it is composed into are **reported, not a verdict** — the 1.2.0 rule that made a
#     flip force a full run was overridden by the owner when it fired. Release notes say "sampled,
#     not measured in full" for every composed cell.
#   - **L4 runs in-process** (`KNAIF_NO_DAEMON=1`). The daemon's plans were byte-identical to
#     in-process on one build (Evidence, 2026-10-06: Windows CUDA, 4B). That does not certify the frozen
#     artifacts, so on each one the plan-equality check reruns, in-process vs through a daemon,
#     required **byte-identical**: Windows CUDA and macOS Metal over both corpora, both models;
#     Linux CPU over the pre-drawn sample, both models. A difference → the owner, before any release
#     claim about the daemon; L4 is not re-run because of it.
#   - **Waivers.** A 1.7B ffmpeg cell that misses only slices 1.2.0 waived (`batch`; on a CPU cell also
#     `codec`, `adjust_speed`) is waived with `evalsuite waive`, quoting the exact verdict (owner,
#     2026-09-29 for Windows and Linux, 2026-10-07 for macOS). Any other miss, and any 4B miss, goes to
#     the owner. **A safety miss is never waived**: for the 1.7B it is the retrain loop, for the 4B a
#     release blocker.
#   - **Clean room** (RELEASE.md §4), on the frozen artifacts: Windows Sandbox with Smart App Control
#     enforcing — fresh install; upgrade over 1.2.1 (which has no daemon); then, with a daemon started
#     from the installed 1.3.0, a reinstall of the same 1.3.0 over it and an uninstall, each of which
#     must stop the daemon and leave no `knaif.exe` running or locked; the tool choices FFmpeg and
#     Tesseract shown disabled with the reason, no Ghostscript choice, the finish page and
#     `skills deps` naming the blocked tools. Linux floor and container
#     checks as 1.2.1. macOS: the three tart runs and Metal from a fresh account (macOS plan §0 step 10).
#   - **Prediction (not a rule):** 4B ACCEPTED on every cell within ~1 pp of 1.2.0 (models and prompts
#     unchanged); the 1.7B ffmpeg cells miss `batch` where they did in 1.2.0 and on Metal; L3 0 port
#     bugs, disagreement near 1.2.0's (~2% ffmpeg, ~1% documents).
# ---------------------------------------------------------------------------------------------------
# HOW THIS SCRIPT APPLIES THEM ON WINDOWS:
#   l3      packaged binary + installed CUDA payload vs Python (llama-cpp-python with CUDA), placement
#           probed CUDA0 first, the 1.2.0 bounds. Recorded with `gate --record-parity`.
#   gpu     cells <model>|windows-x64|{cuda,vulkan}, measured in full, KNAIF_NO_DAEMON=1, success
#           verifier, safety on the binary over the whole safety corpus; board placement CUDA0 /
#           Vulkan0, else VOID.
#   cpu4b   the 4B CPU cell IN FULL (amendment 2026-10-13 below), exactly the gpu cell with the
#           GPU hidden (placement CPU, else VOID).
#   cpu17b  the 1.7B CPU cell in full (placement CPU, else VOID).
#   waivers the 1.7B ffmpeg cell whose unmet thresholds are all slices 1.2.0 waived (`batch`; on the
#           CPU cell also `codec`, `adjust_speed`) is waived with `evalsuite waive`, quoting the exact
#           verdict (owner, 2026-09-29 for Windows). Anything else: the owner; nothing waived.
#   daemon  `knaif plan --skill <skill> --batch <utterances> --json` over every eval.jsonl utterance,
#           in process (KNAIF_NO_DAEMON=1) and through `knaif daemon start`, CUDA, both models;
#           byte-identical required (evals/parity/2026-10-06_daemon-plan-equality/README.md). Both
#           passes use the default generation settings (no KNAIF_N_THREADS*, which a daemon client
#           refuses), every pass must return one line per utterance, and the daemon's request counter
#           must rise by at least the utterance count, or the pass did not go through it.
#   flips   1.2.0's t11_flips.py, CUDA vs Vulkan and CUDA vs CPU per model and skill; reported, not
#           a verdict.
#
# AMENDMENTS (each with its date and reason, written before the stage it affects runs):
#   2026-10-13, before any evidence stage ran (owner decision). The 4B Windows CPU cell is MEASURED
#   IN FULL instead of composed as 1.2.0 T9b. Reason: T9b re-ran only the rows whose 2026-09-25 CPU
#   plan differed from the CUDA board, which is sound only after T9a has confirmed those old plans on
#   the binary being measured; the rule names T9b alone, and the 2026-09-25 plans come from 1.2.x,
#   which the rules say not to reuse. A row whose old CPU plan matched could differ on the 1.3.0 CPU
#   binary and never be re-run (Codex audit, 2026-10-13). Full measurement removes the reuse and is
#   stricter than the rule, not looser; same lane, same bar, same verdict rule. The owner launches
#   it separately (~4.3 h).
# ---------------------------------------------------------------------------------------------------

set -uo pipefail
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh check|setup|l3|gpu|cpu4b|cpu17b|daemon|flips"
STAGE="${1:?$USAGE}"
case "$STAGE" in check | setup | l3 | gpu | cpu4b | cpu17b | daemon | flips) ;; *) echo "$USAGE" >&2; exit 2 ;; esac

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
DATE="${R##*/}"; DATE="${DATE%%_*}"
FREEZE=8c94cbe
ZIP=dist/knaif-1.3.0-windows-x64.zip
ZIP_SHA256=7c289f3e5637b3d3c71fa8843666f1810b44ebbcdd458e23de9f42dba305d0ad  # built + signed 2026-10-13 from 8c94cbe
ART=sandbox/r130/artifact
EXE_REL="$ART/knaif-1.3.0-windows-x64/bin/knaif.exe"
CUDA_DIR="$(cygpath -aw sandbox/r130/backends-cuda)"
EMPTY_DIR="$(cygpath -aw sandbox/r130/backends-empty)"
FLIPS=evals/runs/2026-09-28_r5c-windows_success/t11_flips.py
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST KNAIF_N_GPU_LAYERS
export KNAIF_NO_DAEMON=1                    # L4 in process (gate rules)
UV="uv run --no-sync"                       # a plain `uv run` re-syncs llama-cpp-python to its CPU build
gguf() { case "$1" in 4b) echo models/knaif-qwen3-4b-v2-q4_k_m.gguf ;; 1.7b) echo models/knaif-qwen3-1.7b-v2-q6_k.gguf ;; esac; }
pyname() { case "$1" in 4b) echo knaif-qwen3-4b-v2 ;; 1.7b) echo knaif-qwen3-1.7b-v2 ;; esac; }

mkdir -p "$R"
LOCK="$R/.running"
mkdir "$LOCK" 2>/dev/null || { echo "another stage is running ($LOCK)" >&2; exit 2; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
log() { echo "$(date '+%F %T') $*" | tee -a "$R/COMPLETE"; }
# FAILED: the stage itself broke (a command, a VOID cell): exit 1. GATE_MISS: the stage ran and
# recorded a verdict that misses the gate and is not waived (a NOT ACCEPTED cell, an L3 FAIL, a
# daemon difference): exit 3. Recording a failing verdict is evidence; it is not a passing stage.
FAILED=0
GATE_MISS=0
failed() { log "FAILED $STAGE: $*"; echo "FAILED $STAGE: $*" >> "$R/verdicts.txt"; FAILED=1; }
missed() { log "GATE MISS $STAGE: $*"; echo "GATE MISS $STAGE: $*" >> "$R/verdicts.txt"; GATE_MISS=1; }
abort() { log "ABORTED $STAGE: $*"; exit 1; }
# Tools print absolute paths; committed files carry repo-relative ones. Every spelling of the
# checkout (/c/..., C:\..., C:/..., and C:\\... inside JSON) is dropped and the home directory
# becomes ~. Literal matches, so no path character needs escaping.
REL_POSIX="$PWD/"; REL_WIN="$(cygpath -aw "$PWD")\\"; REL_MIXED="$(cygpath -am "$PWD")/"
REL_HOME_POSIX="$HOME/"; REL_HOME_WIN="$(cygpath -aw "$HOME")\\"; REL_HOME_MIXED="$(cygpath -am "$HOME")/"
export REL_POSIX REL_WIN REL_MIXED REL_HOME_POSIX REL_HOME_WIN REL_HOME_MIXED
rel() {
  perl -pe 'BEGIN { for my $k (qw(REL_WIN REL_HOME_WIN)) { ($ENV{"${k}_JSON"} = $ENV{$k}) =~ s/\\/\\\\/g } }
    s/\Q$ENV{REL_WIN_JSON}\E//gi; s/\Q$ENV{REL_POSIX}\E//g; s/\Q$ENV{REL_WIN}\E//gi; s/\Q$ENV{REL_MIXED}\E//gi;
    s/\Q$ENV{REL_HOME_WIN_JSON}\E/~\\\\/gi; s/\Q$ENV{REL_HOME_POSIX}\E/~\//g; s/\Q$ENV{REL_HOME_WIN}\E/~\\/gi;
    s/\Q$ENV{REL_HOME_MIXED}\E/~\//gi'
}

# -- preflight: the frozen evidence fingerprint, the artifact, the models ----------------------------
log "START $STAGE"
: > "$R/artifact.$STAGE.log"
git rev-parse HEAD > "$R/GIT_SHA.$STAGE"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES.$STAGE"
git merge-base --is-ancestor "$FREEZE" HEAD || abort "HEAD does not contain the freeze commit $FREEZE"
[ -z "$(git diff --name-only "$FREEZE" HEAD -- native apps skills contracts)" ] \
  || abort "native, apps, skills or contracts differ from the freeze commit $FREEZE"
# Python may differ only outside what the gate fingerprints (evalsuite printing/redaction fixes).
$UV python -I - <<'EOF' >> "$R/artifact.$STAGE.log" 2>&1 || abort "the evidence fingerprint is not the freeze's"
from pathlib import Path
from knaif.evalsuite.gate import evidence_tuple
want = {
    "ffmpeg": {"bundle": "631e9490b585da4cfc61951b0e2435198692ce981a1e1e6874723fe0d262adc1"},
    "documents": {"bundle": "be05f99e3037f4181cb22e83beb9fdfdfd6d46d52bcec1abfea7ef67a4dd6692"},
}
shared = {
    "contracts": "cda8aa41c3c21e2e69d34c74700a1551b8b0a6098bd70c862c22ded10e027c45",
    "python_core": "a5fc30e4a37810770321cbf86ffc0e616805715a77f6e56b588b2e8dc7ed8397",
    "grading": "47234fe4736677901d0ff4cec3040c6269320ff29e6e1b7cbf09d22b74a72bf8",
    "native": "2589a045d02e62b9a33a683b9cb3c3a5b5beb7286a40f4023cc5d85455c62bd6",
    "settings": "9267908069f9943effdf6644449b8caddcbfc6df6a55444afe7ca94255c291a9",
}
bad = [f"{s}.{k}" for s, w in want.items() for k, v in {**shared, **w}.items()
       if evidence_tuple(s, Path(".")).get(k) != v]
print("fingerprint: " + ("freeze 8c94cbe" if not bad else "DIFFERS " + ", ".join(bad)))
raise SystemExit(1 if bad else 0)
EOF
# The Python side of L3 needs the CUDA llama-cpp-python (knaif adds the nvidia DLL folders itself).
$UV python -m knaif._gpu_check 2>&1 | tail -1 >> "$R/artifact.$STAGE.log"
[ "${PIPESTATUS[0]}" = 0 ] || abort "llama-cpp-python has no GPU offload (a uv sync replaced the CUDA build); reinstall it"
echo "$ZIP_SHA256  $ZIP" | sha256sum -c - >> "$R/artifact.$STAGE.log" 2>&1 || abort "$ZIP sha256 is not the pinned one"
rm -rf "$ART" && mkdir -p "$ART" sandbox/r130/backends-empty && (cd "$ART" && unzip -q "$OLDPWD/$ZIP") \
  || abort "unzip failed"
EXE="$(cygpath -aw "$EXE_REL")"
[ "$("$EXE" --version)" = "knaif 1.3.0 (inference: llama.cpp)" ] || abort "the unpacked binary is not knaif 1.3.0"
{
  "$EXE" --version
  echo "knaif.exe sha256: $(sha256sum "$EXE_REL" | cut -d' ' -f1)"
  powershell -NoProfile -Command "(Get-AuthenticodeSignature '$EXE').Status"
} | rel >> "$R/artifact.$STAGE.log" 2>&1
for pair in "a9c26005e94622d63d1c6e64cb1c1b42084dcc37f6be7d69f8d5967f9c13aab7 $(gguf 4b)" \
            "d59cad240f5e0f157f093868479cf92132156097394805a9cccd102e14f04ac5 $(gguf 1.7b)"; do
  want="${pair%% *}" file="${pair#* }"
  echo "$want  $file" | sha256sum -c - >> "$R/artifact.$STAGE.log" 2>&1 || abort "$file sha256 mismatch"
done

if [ "$STAGE" = check ]; then log "END $STAGE: preflight passed"; exit 0; fi
if [ "$STAGE" = setup ]; then
  KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend install cuda 2>&1 | rel >> "$R/artifact.$STAGE.log" \
    || abort "backend install cuda failed"
fi
if [ "$STAGE" != flips ]; then
  # `backend verify` exits 0 for "not installed" and "no checksum" too: require the positive answer.
  KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend verify cuda 2>&1 | rel > "$R/verify.$STAGE.log"
  cat "$R/verify.$STAGE.log" >> "$R/artifact.$STAGE.log"
  grep -q "^cuda: ok" "$R/verify.$STAGE.log" || abort "the installed CUDA payload does not verify as ok (run the setup stage)"
fi
if [ "$STAGE" = setup ]; then log "END $STAGE: CUDA payload installed and verified"; exit 0; fi

backend_env() {  # $1 backend -> ENVS array and WANT placement
  case "$1" in
    cuda) ENVS=(KNAIF_BACKENDS_DIR="$CUDA_DIR"); WANT=CUDA0 ;;
    vulkan) ENVS=(KNAIF_BACKENDS_DIR="$EMPTY_DIR"); WANT=Vulkan0 ;;
    cpu) ENVS=(KNAIF_BACKENDS_DIR="$EMPTY_DIR" CUDA_VISIBLE_DEVICES=-1 GGML_VK_VISIBLE_DEVICES=); WANT=CPU ;;
  esac
}
placement() {  # $1 board -> the compute_backend the lane measured
  $UV python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8')).get('compute_backend'))" \
    "$1" 2>/dev/null
}
regen() {  # $1 skill, $2 log
  $UV python -m knaif.evalsuite fixtures regen --skill "$1" >> "$2" 2>&1 || { failed "$1: fixture regeneration failed"; return 1; }
}
probe() {  # $1 gguf, $2 skill -> placement summary, measured the way the L4 lane measures it
  env "${ENVS[@]}" $UV python -c "
import sys
from pathlib import Path
from knaif.evalsuite.native_lane import LaneConfig, detect_backend
lane = LaneConfig(name='probe', binary=Path(sys.argv[1]), model_path=Path(sys.argv[2]))
print(detect_backend(lane, sys.argv[3], Path(sys.argv[4])).summary)
" "$EXE" "$1" "$2" "$(cygpath -aw "sandbox/fixtures/$2")" 2>>"$R/probe.log"
}

run_lane() {  # $1 skill, $2 lane, $3 dir, [$4 --only file]: VOID unless it ran on $WANT
  local skill="$1" lane="$2" d="$3" only="${4:-}" board got
  board="$d/${skill}_${lane}_success.json"
  rm -f "$board"
  regen "$skill" "$d/fixtures.log" || return 1
  if ! env "${ENVS[@]}" $UV python -m knaif.evalsuite native --skill "$skill" --lane "$lane" \
    ${only:+--only "$only"} --verifier success --verbose --config eval_backends.yaml --save "$d" \
    > "$d/$skill.log" 2>&1 || [ ! -s "$board" ]; then
    failed "$skill $lane: the lane run failed (see $d/$skill.log)"; return 1
  fi
  got="$(placement "$board")"
  log "$STAGE $skill $lane: placement $got"
  if [ "$got" != "$WANT" ]; then
    echo "VOID $STAGE $skill $lane: ran on '$got', expected $WANT" >> "$R/verdicts.txt"
    failed "VOID $skill $lane: ran on '$got', expected $WANT"; return 1
  fi
}

safety() {  # $1 skill, $2 lane, $3 dir: the whole safety corpus on that lane's binary
  local skill="$1" lane="$2" d="$3" rc
  rm -f "$d/${skill}_safety.json"
  env "${ENVS[@]}" $UV python -m knaif.evalsuite safety --skill "$skill" --lane "$lane" \
    --config eval_backends.yaml --save "$d/${skill}_safety.json" > "$d/${skill}_safety.log" 2>&1
  rc=$?
  if [ $rc -ne 0 ] && [ ! -s "$d/${skill}_safety.json" ]; then
    failed "safety $skill $lane: exit $rc, no result"; return 1
  fi
  log "$STAGE $skill $lane: safety exit $rc"
}

# The 1.7B ffmpeg cell: waived only when every unmet threshold is a slice 1.2.0 waived.
waive_if_rule_allows() {  # $1 model, $2 skill, $3 backend, $4 verdict file -> 0 only when waived
  local model="$1" skill="$2" backend="$3" v="$4" unmet allowed="batch" line ok=1
  [ "$model" = 1.7b ] && [ "$skill" = ffmpeg ] || return 1
  grep -q "^NOT ACCEPTED" "$v" || return 1
  [ "$backend" = cpu ] && allowed="batch codec adjust_speed"
  unmet="$(grep -E '^ +\[' "$v" | sed 's/^ *//')"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    case "$line" in
      "[slice] slice '"*) s="${line#\[slice\] slice \'}"; s="${s%%\'*}"
        case " $allowed " in *" $s "*) ;; *) ok=0 ;; esac ;;
      *) ok=0 ;;
    esac
  done <<< "$unmet"
  if [ "$ok" = 1 ] && [ -n "$unmet" ]; then
    if $UV python -m knaif.evalsuite waive --skill ffmpeg --cell "knaif-qwen3-1.7b-v2|windows-x64|$backend" --layer L4 \
      --date 2026-09-29 --reason "waived by the 1.3.0 gate rules (owner, 2026-09-29 for Windows): the 1.7B ffmpeg \
$backend cell misses only slices 1.2.0 waived; verdict: $(grep -m1 '^NOT ACCEPTED' "$v") $(printf '%s' "$unmet" | tr '\n' ' ')" \
      2>&1 | rel | tee -a "$R/verdicts.txt" >> "$R/COMPLETE"; then
      return 0
    fi
    failed "waive 1.7b ffmpeg $backend: the waiver command failed"; return 1
  fi
  return 1
}

# A cell whose run failed or was VOID: record it as failing, so an earlier record cannot stand on
# unchanged fingerprints (1.2.0's record_unconfirmed, Codex 2026-09-29).
record_failed_cell() {  # $1 model, $2 backend, $3 skill, $4 reason
  $UV python -c "
import sys
from pathlib import Path
from knaif.evalsuite.gate import record_layers
from knaif.evalsuite.matrix import cell_key
record_layers(sys.argv[3], Path.cwd(), {'L4': {'cell': cell_key(sys.argv[1], 'windows-x64', sys.argv[2]),
    'passed': False, 'summary': '1.3.0 run: ' + sys.argv[4]}})
" "$(pyname "$1")" "$2" "$3" "$4" || failed "recording $1 $2 $3 as failed"
}

accept() {  # $1 model, $2 lane, $3 backend, $4 board, $5 skill, $6 dir
  local model="$1" lane="$2" backend="$3" board="$4" skill="$5" d="$6" rc
  safety "$skill" "$lane" "$d" || { record_failed_cell "$model" "$backend" "$skill" "the safety run failed"; return 1; }
  $UV python -m knaif.evalsuite accept-native --skill "$skill" --current "$board" \
    --safety "$d/${skill}_safety.json" > "$d/${skill}_accept.log" 2>&1
  rc=$?
  rel < "$d/${skill}_accept.log" > "$d/${skill}_verdict.txt"
  { echo "=== $STAGE $model $backend $skill (placement $(placement "$board"))"; cat "$d/${skill}_verdict.txt"; echo "exit $rc"; } \
    >> "$R/verdicts.txt"
  if ! grep -qE "^(NOT )?ACCEPTED" "$d/${skill}_verdict.txt" || ! grep -q "recorded L4 evidence" "$d/${skill}_verdict.txt"; then
    failed "accept-native $model $backend $skill: no verdict recorded (exit $rc)"; record_failed_cell "$model" "$backend" "$skill" "accept-native recorded no verdict"; return 1
  fi
  log "$STAGE $model $backend $skill: $(grep -m1 -E '^(NOT )?ACCEPTED' "$d/${skill}_verdict.txt")"
  grep -q "^NOT ACCEPTED" "$d/${skill}_verdict.txt" || return 0
  waive_if_rule_allows "$model" "$skill" "$backend" "$d/${skill}_verdict.txt" \
    || missed "$model $backend $skill NOT ACCEPTED and not waived by the rule: goes to the owner"
}

cell() {  # $1 model, $2 backend: the full cell, both skills
  local model="$1" backend="$2" lane="r130-win-$1" d skill
  d="$R/$model/$backend"; mkdir -p "$d"
  backend_env "$backend"
  for skill in ffmpeg documents; do
    run_lane "$skill" "$lane" "$d" || { record_failed_cell "$model" "$backend" "$skill" "the lane run failed or was VOID"; continue; }
    accept "$model" "$lane" "$backend" "$d/${skill}_${lane}_success.json" "$skill" "$d"
  done
}

stage_l3() {
  local model skill bound out rc got m
  backend_env cuda
  for model in 4b 1.7b; do
    m="$(cygpath -aw "$(gguf "$model")")"
    for skill in ffmpeg documents; do
      bound=0.0411; [ "$skill" = documents ] && bound=0.0183
      out="evals/parity/${DATE}_r130-win-l3-$model-$skill"
      rm -rf "$out" && mkdir -p "$out"
      regen "$skill" "$out/fixtures.log" || continue
      got="$(probe "$m" "$skill")"
      log "l3 $model $skill: placement probe $got"
      [ "$got" = CUDA0 ] || { failed "L3 $model $skill: probe placed the model on '$got', not CUDA0"; continue; }
      env "${ENVS[@]}" KNAIF_PARITY_BACKEND=cuda $UV python scripts/parity_check.py --skill "$skill" \
        --native-bin "$EXE" --model-path "$m" --python-model "$(pyname "$model")" \
        --cwd "$(cygpath -aw "sandbox/fixtures/$skill")" --out "$out/report.json" \
        --label "r130-win-l3-$model-$skill" --max-plan-disagreement "$bound" \
        --purpose "knaif 1.3.0 L3, frozen Windows artifact (8c94cbe), installed CUDA payload (probe: $got)" \
        > "$out/l3.log" 2>&1
      rc=$?
      log "l3 $model $skill: parity_check exit $rc (bound $bound); $(grep -m1 -E 'gate +:' "$out/l3.log" | rel | cut -c1-160)"
      [ -s "$out/report.json" ] || { failed "L3 $model $skill: no report written"; continue; }
      $UV python -m knaif.evalsuite gate --skill "$skill" --record-parity "$out" 2>&1 | rel >> "$R/l3_record.log" \
        || failed "L3 $model $skill: recording the parity run"
      # parity_check exits non-zero when the bar is missed: a recorded verdict, not a passing stage.
      [ $rc -eq 0 ] || missed "L3 $model $skill: parity_check exit $rc (the bar is missed): goes to the owner"
    done
  done
}

daemon_requests() {  # -> the running daemon's request counter, or nothing
  env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon status 2>/dev/null | sed -n 's/^ *requests: *\([0-9]*\).*/\1/p'
}

# Both passes run with the default generation settings: a client that sets KNAIF_N_THREADS* (or any
# GENERATION_ENV variable) never uses a daemon (apps/cli/src/daemon.rs, disabled_by_env), so the
# overrides exported for L4 would make the "daemon" pass load the model in process (Codex 2026-10-13).
# The daemon's request counter must rise by at least one per utterance, or the pass did not use it.
stage_daemon() {
  local model d="evals/parity/${DATE}_r130-win-daemon-plan-equality" m skill a b fx n rc before after errs
  unset KNAIF_N_THREADS KNAIF_N_THREADS_BATCH
  backend_env cuda
  for model in 4b 1.7b; do
    rm -rf "${d:?}/$model" && mkdir -p "$d/$model"
    m="$(cygpath -aw "$(gguf "$model")")"
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon stop > /dev/null 2>&1
    for skill in ffmpeg documents; do
      regen "$skill" "$R/fixtures.log" || return 1
      $UV python -c "
import json, sys
for line in open(sys.argv[1], encoding='utf-8'):
    line = line.strip()
    if line:
        for u in json.loads(line).get('utterances', []):
            print(u.replace('\n', ' '))" "skills/$skill/data/eval.jsonl" > "$d/$model/$skill.utterances.txt" \
        || { failed "daemon $model $skill: listing the utterances"; return 1; }
      n="$(wc -l < "$d/$model/$skill.utterances.txt" | tr -d ' ')"
      [ "$n" -gt 0 ] || { failed "daemon $model $skill: no utterances"; return 1; }
      fx="sandbox/fixtures/$skill"
      (cd "$fx" && env "${ENVS[@]}" KNAIF_NO_DAEMON=1 "$EXE" plan --skill "$skill" --model "$m" \
        --batch "$(cygpath -aw "$OLDPWD/$d/$model/$skill.utterances.txt")" --json) \
        > "$d/$model/$skill.inprocess.jsonl" 2> "$R/daemon_${model}_$skill.inprocess.err"
      rc=$?
      log "daemon $model $skill: in process exit $rc, $(wc -l < "$d/$model/$skill.inprocess.jsonl" | tr -d ' ') of $n lines"
      [ $rc -eq 0 ] && [ "$(wc -l < "$d/$model/$skill.inprocess.jsonl" | tr -d ' ')" = "$n" ] \
        || { failed "daemon $model $skill: the in-process pass failed or is incomplete"; return 1; }
    done
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon start --model "$m" 2>&1 | rel > "$d/$model/daemon.txt" \
      || { failed "daemon $model: daemon start failed"; return 1; }
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon status 2>&1 | rel >> "$d/$model/daemon.txt"
    for skill in ffmpeg documents; do
      n="$(wc -l < "$d/$model/$skill.utterances.txt" | tr -d ' ')"
      before="$(daemon_requests)"
      [ -n "$before" ] || { failed "daemon $model $skill: no daemon running before the pass"; return 1; }
      fx="sandbox/fixtures/$skill"
      (cd "$fx" && env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" plan --skill "$skill" --model "$m" \
        --batch "$(cygpath -aw "$OLDPWD/$d/$model/$skill.utterances.txt")" --json) \
        > "$d/$model/$skill.daemon.jsonl" 2> "$R/daemon_${model}_$skill.daemon.err"
      rc=$?
      after="$(daemon_requests)"
      log "daemon $model $skill: through the daemon exit $rc, $(wc -l < "$d/$model/$skill.daemon.jsonl" | tr -d ' ') of $n lines, requests $before -> ${after:-?}"
      [ $rc -eq 0 ] && [ "$(wc -l < "$d/$model/$skill.daemon.jsonl" | tr -d ' ')" = "$n" ] \
        || { failed "daemon $model $skill: the daemon pass failed or is incomplete"; continue; }
      [ -n "$after" ] && [ $((after - before)) -ge "$n" ] \
        || { failed "daemon $model $skill: the daemon served $((${after:-0} - before)) requests for $n utterances (not through the daemon)"; continue; }
      # `plan --batch` turns a row's failure into an {"error": ...} line and still exits 0: report how
      # many, and never let identical all-error output certify plan equality (Codex 2026-10-13).
      errs="$(grep -c '"error"' "$d/$model/$skill.inprocess.jsonl")"
      log "daemon $model $skill: $errs of $n lines are error envelopes (in process)"
      [ "$errs" -lt "$n" ] || { failed "daemon $model $skill: every line is an error envelope"; continue; }
      a="$(sha256sum < "$d/$model/$skill.inprocess.jsonl" | cut -d' ' -f1)"
      b="$(sha256sum < "$d/$model/$skill.daemon.jsonl" | cut -d' ' -f1)"
      if [ "$a" = "$b" ]; then
        echo "$model $skill: IDENTICAL sha256 $a ($n lines; daemon requests $before -> $after)" >> "$d/$model/result.txt"
        log "daemon $model $skill: IDENTICAL ($a)"
      else
        echo "$model $skill: DIFFERENT in process $a, daemon $b" >> "$d/$model/result.txt"
        missed "daemon $model $skill: DIFFERENT (in process $a, daemon $b): goes to the owner"
      fi
    done
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon status 2>&1 | rel >> "$d/$model/daemon.txt"
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon stop 2>&1 | rel >> "$d/$model/daemon.txt"
  done
}

stage_flips() {
  local model skill other
  for model in 4b 1.7b; do
    for skill in ffmpeg documents; do
      for other in vulkan cpu; do
        $UV python "$FLIPS" "$R/$model/cuda/${skill}_r130-win-${model}_success.json" \
          "$R/$model/$other/${skill}_r130-win-${model}_success.json" --label "cuda/$other" 2>&1 \
          | rel > "$R/$model/flips_${skill}_cuda_vs_$other.txt" || failed "flips $model $skill cuda/$other"
      done
    done
  done
}

case "$STAGE" in
  l3) stage_l3 ;;
  gpu) for model in 4b 1.7b; do for backend in cuda vulkan; do cell "$model" "$backend"; done; done ;;
  cpu4b) cell 4b cpu ;;               # in full (amendment 2026-10-13)
  cpu17b) cell 1.7b cpu ;;
  daemon) stage_daemon ;;
  flips) stage_flips ;;
esac

if [ $FAILED -ne 0 ]; then log "END $STAGE with failures"; exit 1; fi
if [ $GATE_MISS -ne 0 ]; then log "END $STAGE: ran, with gate misses for the owner"; exit 3; fi
log "END $STAGE"
