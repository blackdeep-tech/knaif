#!/usr/bin/env bash
# knaif 1.3.0, Linux evidence (docs/plans/2026-09-30-release-1.3.0.md, "Gate decision rules"): the
# Linux L4 cells and the daemon plan-equality check, on the FROZEN Linux artifact, in WSL Ubuntu
# 24.04 on the same RTX 5080 as the Windows half (as 1.2.0 did it).
#
# Artifact: knaif-1.3.0-linux-x64.tar.gz, built in the floor-pinned container from the freeze commit
# 8c94cbe (`just package-linux --rev=8c94cbe`), its sha256 pinned below; the setup stage copies it
# from the Windows checkout (`origin`) into ~/r130/dist. Every stage unpacks it fresh into
# sandbox/r130/artifact (the r130-linux-* lanes). CUDA through the published payload the 1.3.0
# manifest points at, installed by `knaif backend install cuda` into ~/r130/backends-cuda and checked
# by `backend verify` every stage; CPU with the GPU hidden. KNAIF_PDFIUM_PATH unset.
#
# Runs INSIDE WSL, from the WSL checkout (~/knaif-r130, a clone whose `origin` is the Windows checkout;
# pull the branch first). ONE STAGE PER INVOCATION, each launched only after the owner has approved
# it with its time budget (one run at a time, 8 threads):
#
#   bash evals/runs/<this folder>/run_all.sh check    the preflight only; measures nothing     ~1 min
#   bash evals/runs/<this folder>/run_all.sh setup    copy the tarball, documents deps, CUDA payload ~5 min
#   bash evals/runs/<this folder>/run_all.sh cuda     L4 CUDA cells in full, 4B then 1.7B         ~1.5-2 h
#   bash evals/runs/<this folder>/run_all.sh cpu      CPU cells composed on the t15 sample        ~1.25 h
#   bash evals/runs/<this folder>/run_all.sh daemon   plan equality on the CPU, the t15 sample    ~30-45 min
#
# `cpu` composes into the Windows CPU boards, so it runs after the Windows cpu4b and cpu17b stages.
# Budgets are 1.2.0's measured times (2026-09-29), not promises. The floor and container clean room
# run from Windows with Docker, as 1.2.0's t16 did (a separate script, written with that stage).
#
# Outputs: this folder (logs, safety files, verdicts, COMPLETE, verdicts.txt; boards stay ignored);
# evals/acceptance/<skill>.json; evals/parity/<date>_r130-linux-daemon-plan-equality/.
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
# HOW THIS SCRIPT APPLIES THEM ON LINUX:
#   cuda    cells <model>|linux-x64|cuda in full, KNAIF_NO_DAEMON=1, success verifier, safety on the
#           binary over the whole safety corpus; placement CUDA0, else VOID. Flips against the
#           Windows CUDA board of the same model and skill are reported, not a verdict.
#   cpu     cells <model>|linux-x64|cpu, composed as 1.2.0 T15s: the pre-drawn sample
#           (evals/runs/2026-09-29_r5c-linux_success/t15_sample_<model>_<skill>.json) on the Linux
#           binary on the CPU (placement CPU, else VOID), swapped by `compose` into the same model's
#           1.3.0 Windows CPU board (copied into base/ with its sha256), both safety sets on the Linux
#           CPU binary, `accept-native`. Decision flips between the sample and the board it is
#           composed into are reported, not a verdict. "Sampled, not measured in full."
#   waivers the 1.7B ffmpeg cell whose unmet thresholds are all slices 1.2.0 waived (`batch`; on the
#           CPU cell also `codec`, `adjust_speed`) is waived with `evalsuite waive`, quoting the exact
#           verdict (owner, 2026-09-29 for Linux). Anything else: the owner; nothing waived.
#   daemon  `knaif plan --skill <skill> --batch <utterances> --json` over the pre-drawn sample's
#           utterances, in process (KNAIF_NO_DAEMON=1) and through `knaif daemon start`, on the CPU,
#           both models; byte-identical required. Both passes use the default generation settings
#           (no KNAIF_N_THREADS*, which a daemon client refuses), every pass must return one line per
#           utterance, and the daemon's request counter must rise by at least the utterance count.
#
# AMENDMENTS (each with its date and reason, written before the stage it affects runs): none.
# ---------------------------------------------------------------------------------------------------

set -uo pipefail
case "$PWD" in /mnt/*) echo "refusing to run from $PWD: use the WSL checkout" >&2; exit 2 ;; esac
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh check|setup|cuda|cpu|daemon"
STAGE="${1:?$USAGE}"
case "$STAGE" in check | setup | cuda | cpu | daemon) ;; *) echo "$USAGE" >&2; exit 2 ;; esac

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
DATE="${R##*/}"; DATE="${DATE%%_*}"
FREEZE=8c94cbe
ORIGIN="$(git remote get-url origin)"
TARBALL="$HOME/r130/dist/knaif-1.3.0-linux-x64.tar.gz"
TAR_SHA256=d50189ec68dd9930470009d6712b39b597892e435ae8307dab92549337c6f093  # built 2026-10-13 in the container from 8c94cbe
ART=sandbox/r130/artifact
EXE="$PWD/$ART/knaif-1.3.0-linux-x64/bin/knaif"
CUDA_DIR="$HOME/r130/backends-cuda"
EMPTY_DIR="$HOME/r130/empty"
WIN_R="$ORIGIN/evals/runs/2026-10-13_r130-windows_success"
SAMPLES=evals/runs/2026-09-29_r5c-linux_success
FLIPS=evals/runs/2026-09-28_r5c-windows_success/t11_flips.py
THREADS="${THREADS:-8}"
export KNAIF_N_THREADS="$THREADS" KNAIF_N_THREADS_BATCH="$THREADS"
export PATH="$HOME/.local/bin:$PATH"
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST KNAIF_N_GPU_LAYERS
export KNAIF_NO_DAEMON=1                    # L4 in process (gate rules)
UV="uv run --no-sync"                       # never let a sync drop the documents groups (macOS round 2)
gguf() { case "$1" in 4b) echo models/knaif-qwen3-4b-v2-q4_k_m.gguf ;; 1.7b) echo models/knaif-qwen3-1.7b-v2-q6_k.gguf ;; esac; }
public() { case "$1" in 4b) echo knaif-qwen3-4b-v2 ;; 1.7b) echo knaif-qwen3-1.7b-v2 ;; esac; }

mkdir -p "$R"
LOCK="$R/.running"
mkdir "$LOCK" 2>/dev/null || { echo "another stage is running ($LOCK)" >&2; exit 2; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
log() { echo "$(date '+%F %T') $*" | tee -a "$R/COMPLETE"; }
# FAILED: the stage itself broke: exit 1. GATE_MISS: a recorded verdict misses the gate and is not
# waived: exit 3. Recording a failing verdict is evidence; it is not a passing stage.
FAILED=0
GATE_MISS=0
failed() { log "FAILED $STAGE: $*"; echo "FAILED $STAGE: $*" >> "$R/verdicts.txt"; FAILED=1; }
missed() { log "GATE MISS $STAGE: $*"; echo "GATE MISS $STAGE: $*" >> "$R/verdicts.txt"; GATE_MISS=1; }
abort() { log "ABORTED $STAGE: $*"; exit 1; }
# The frozen Windows knaif.exe: a Windows board this stage composes into must have been measured on it.
WIN_EXE_SHA256=c2e758e7d20ca197c05ff7e76f6b741d0e92b0c9f1d813bee1fb759924c39e79
export REL_A="$PWD/" REL_B="$HOME/" REL_C="$ORIGIN/"
rel() { perl -pe 's/\Q$ENV{REL_A}\E//g; s/\Q$ENV{REL_C}\E/<windows-checkout>\//g; s/\Q$ENV{REL_B}\E/~\//g'; }

log "START $STAGE"
: > "$R/artifact.$STAGE.log"
git rev-parse HEAD > "$R/GIT_SHA.$STAGE"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES.$STAGE"

if [ "$STAGE" = setup ]; then
  mkdir -p "$HOME/r130/dist" "$EMPTY_DIR"
  cp "$ORIGIN/dist/knaif-1.3.0-linux-x64.tar.gz" "$TARBALL" || abort "copying the tarball from the Windows checkout"
  uv sync >> "$R/artifact.$STAGE.log" 2>&1 || abort "uv sync"
  uv pip install --group documents --group documents-ocr >> "$R/artifact.$STAGE.log" 2>&1 || abort "installing the documents groups"
fi

# -- preflight: the frozen evidence fingerprint, the artifact, the models, the grading deps --------
git merge-base --is-ancestor "$FREEZE" HEAD || abort "HEAD does not contain the freeze commit $FREEZE"
[ -z "$(git diff --name-only "$FREEZE" HEAD -- native apps skills contracts)" ] \
  || abort "native, apps, skills or contracts differ from the freeze commit $FREEZE"
$UV python - <<'EOF' >> "$R/artifact.$STAGE.log" 2>&1 || abort "the evidence fingerprint is not the freeze's"
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
$UV python -c "import pypdf, pikepdf" 2>> "$R/artifact.$STAGE.log" \
  || abort "the documents grading deps are missing (run the setup stage): pypdf/pikepdf"
( cd "$(dirname "$TARBALL")" && echo "$TAR_SHA256  $(basename "$TARBALL")" | sha256sum -c - ) \
  >> "$R/artifact.$STAGE.log" 2>&1 || abort "the tarball sha256 is not the pinned one"
rm -rf "$ART" && mkdir -p "$ART" && tar -xzf "$TARBALL" -C "$ART" || abort "untar failed"
[ "$("$EXE" --version)" = "knaif 1.3.0 (inference: llama.cpp)" ] || abort "the unpacked binary is not knaif 1.3.0"
{ "$EXE" --version; echo "knaif sha256: $(sha256sum "$EXE" | cut -d' ' -f1)"; } | rel >> "$R/artifact.$STAGE.log"
for m in 4b 1.7b; do
  [ ! -L "$(gguf $m)" ] || abort "$(gguf $m) is a link (load it from the Linux filesystem)"
done
for pair in "a9c26005e94622d63d1c6e64cb1c1b42084dcc37f6be7d69f8d5967f9c13aab7 $(gguf 4b)" \
            "d59cad240f5e0f157f093868479cf92132156097394805a9cccd102e14f04ac5 $(gguf 1.7b)"; do
  want="${pair%% *}" file="${pair#* }"
  echo "$want  $file" | sha256sum -c - >> "$R/artifact.$STAGE.log" 2>&1 || abort "$file sha256 mismatch"
done
if [ "$STAGE" = setup ]; then
  KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend install cuda 2>&1 | rel >> "$R/artifact.$STAGE.log" \
    || abort "backend install cuda failed"
fi
# `backend verify` exits 0 for "not installed" too: require the positive answer.
KNAIF_BACKENDS_DIR="$CUDA_DIR" "$EXE" backend verify cuda 2>&1 | rel > "$R/verify.$STAGE.log"
cat "$R/verify.$STAGE.log" >> "$R/artifact.$STAGE.log"
grep -q "^cuda: ok" "$R/verify.$STAGE.log" || abort "the installed CUDA payload does not verify as ok"

if [ "$STAGE" = check ] || [ "$STAGE" = setup ]; then log "END $STAGE: preflight passed"; exit 0; fi

backend_env() {  # $1 backend -> ENVS array and WANT placement
  case "$1" in
    cuda) ENVS=(KNAIF_BACKENDS_DIR="$CUDA_DIR"); WANT=CUDA0 ;;
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

run_lane() {  # $1 skill, $2 lane, $3 dir, [$4 --only file]: VOID unless it ran on $WANT
  local skill="$1" lane="$2" d="$3" only="${4:-}" board got
  board="$d/${skill}_${lane}_success.json"
  mkdir -p "$d"; rm -f "$board"
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

waive_if_rule_allows() {  # $1 model, $2 skill, $3 backend, $4 verdict file -> 0 only when waived
  local model="$1" skill="$2" backend="$3" v="$4" unmet allowed="batch" line ok=1 s
  [ "$model" = 1.7b ] && [ "$skill" = ffmpeg ] || return 1
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
    if $UV python -m knaif.evalsuite waive --skill ffmpeg --cell "knaif-qwen3-1.7b-v2|linux-x64|$backend" --layer L4 \
      --date 2026-09-29 --reason "waived by the 1.3.0 gate rules (owner, 2026-09-29 for Linux): the 1.7B ffmpeg \
$backend cell misses only slices 1.2.0 waived; verdict: $(grep -m1 '^NOT ACCEPTED' "$v") $(printf '%s' "$unmet" | tr '\n' ' ')" \
      2>&1 | rel | tee -a "$R/verdicts.txt" >> "$R/COMPLETE"; then
      return 0
    fi
    failed "waive 1.7b ffmpeg $backend: the waiver command failed"
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
record_layers(sys.argv[3], Path.cwd(), {'L4': {'cell': cell_key(sys.argv[1], 'linux-x64', sys.argv[2]),
    'passed': False, 'summary': '1.3.0 run: ' + sys.argv[4]}})
" "$(public "$1")" "$2" "$3" "$4" || failed "recording $1 $2 $3 as failed"
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

stage_cuda() {
  local model skill d lane win
  backend_env cuda
  for model in 4b 1.7b; do
    lane="r130-linux-$model" d="$R/$model/cuda"
    for skill in ffmpeg documents; do
      run_lane "$skill" "$lane" "$d" || { record_failed_cell "$model" cuda "$skill" "the lane run failed or was VOID"; continue; }
      accept "$model" "$lane" cuda "$d/${skill}_${lane}_success.json" "$skill" "$d"
      # Reported, not a verdict: the same model and skill on Windows CUDA (1.2.0 T14).
      win="$WIN_R/$model/cuda/${skill}_r130-win-${model}_success.json"
      if [ "$(placement "$win")" = CUDA0 ]; then
        $UV python "$FLIPS" "$win" "$d/${skill}_${lane}_success.json" --label "windows-cuda/linux-cuda" 2>&1 \
          | rel > "$R/$model/flips_${skill}_windows_vs_linux_cuda.txt" || failed "flips $model $skill windows/linux"
      else
        failed "flips $model $skill: no measured Windows CUDA board to compare with (run the Windows gpu stage first)"
      fi
    done
  done
}

stage_cpu() {
  local model skill d lane wlane wbase base sample
  backend_env cpu
  rm -rf "$R/base" && mkdir -p "$R/base"
  for model in 4b 1.7b; do
    lane="r130-linux-$model" wlane="r130-win-$model" d="$R/$model/cpu"
    rm -rf "$d" && mkdir -p "$d/sample" "$R/base/$model"   # never compose from an earlier attempt's files
    for skill in ffmpeg documents; do
      sample="$SAMPLES/t15_sample_${model}_${skill}.json"
      wbase="$WIN_R/$model/cpu/${skill}_${wlane}_success.json"
      # The base must be the 1.3.0 Windows CPU board, measured on the frozen Windows binary.
      $UV python - "$wbase" "$WIN_EXE_SHA256" <<'EOF' > "$d/${skill}_base_check.txt" 2>&1 \
        || { failed "the Windows CPU board for $model $skill is missing or not the frozen binary's (run the Windows cpu stages first)"; record_failed_cell "$model" cpu "$skill" "no valid Windows CPU board to compose into"; continue; }
import json, sys
b = json.load(open(sys.argv[1], encoding="utf-8"))
# Both Windows CPU cells are measured in full (Windows amendment 2026-10-13): a composed board is refused.
ok = b.get("compute_backend") == "CPU" and b.get("binary_sha256") == sys.argv[2] and b.get("coverage") == 1.0 and "composed_from" not in b
print(f"windows cpu board: compute_backend={b.get('compute_backend')} binary={str(b.get('binary_sha256'))[:12]} coverage={b.get('coverage')} composed={'composed_from' in b}")
raise SystemExit(0 if ok else 1)
EOF
      run_lane "$skill" "$lane" "$d/sample" "$sample" \
        || { record_failed_cell "$model" cpu "$skill" "the Linux CPU sample run failed or was VOID"; continue; }
      # Every sampled row must have come back: --only skips an unknown key silently, and compose would
      # then keep the Windows row in its place.
      $UV python - "$sample" "$d/sample/${skill}_${lane}_success.json" <<'EOF' >> "$d/${skill}_base_check.txt" 2>&1 \
        || { failed "the Linux CPU sample for $model $skill is incomplete"; record_failed_cell "$model" cpu "$skill" "the Linux CPU sample is incomplete"; continue; }
import json, sys
want = {(i, int(n)) for i, n in json.load(open(sys.argv[1], encoding="utf-8"))}
got = {(r["id"], int(r["utterance_idx"])) for r in json.load(open(sys.argv[2], encoding="utf-8"))["rows"] if not r.get("is_warmup")}
missing = sorted(want - got)
print(f"sample: {len(want)} requested, {len(got & want)} returned, missing {missing[:10]}")
raise SystemExit(1 if missing else 0)
EOF
      # Copied in first: the composed record names its source path, and a /mnt path would name the
      # Windows checkout (public-output hygiene).
      base="$R/base/$model/${skill}_${wlane}_success.json"
      cp "$wbase" "$base" || { failed "copy base $model $skill"; record_failed_cell "$model" cpu "$skill" "copying the Windows CPU board failed"; continue; }
      sha256sum "$base" >> "$R/base/SHA256SUMS"
      # Decision flips (scripts/flip_rate.py levels, split by the success grade), reported, not a verdict.
      $UV python "$FLIPS" "$base" "$d/sample/${skill}_${lane}_success.json" --label "windows-cpu/linux-cpu" 2>&1 \
        | rel > "$d/${skill}_flips.txt" || { failed "flips $model $skill windows-cpu/linux-cpu"; record_failed_cell "$model" cpu "$skill" "the flip report failed"; continue; }
      log "cpu $model $skill vs the Windows CPU board: $(sed -n 2p "$d/${skill}_flips.txt" | sed 's/^ *//')"
      $UV python -m knaif.evalsuite compose --base "$base" --replace "$d/sample/${skill}_${lane}_success.json" \
        --out "$d/${skill}_${lane}_success.json" \
        --note "1.3.0 gate rule (CPU cells, owner 2026-10-07, as 1.2.0 T15s): $model Linux CPU cell = the $model 1.3.0 Windows CPU cell with the pre-drawn Linux CPU sample swapped in. Against the Windows CPU plans: $(sed -n 2p "$d/${skill}_flips.txt" | sed 's/^ *//'). Flips are reported, not a verdict. Sampled, not measured in full." \
        2>&1 | rel > "$d/${skill}_compose.log" \
        || { failed "compose $model $skill"; record_failed_cell "$model" cpu "$skill" "compose failed"; continue; }
      accept "$model" "$lane" cpu "$d/${skill}_${lane}_success.json" "$skill" "$d"
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
  local model d="evals/parity/${DATE}_r130-linux-daemon-plan-equality" m skill a b n rc before after errs
  unset KNAIF_N_THREADS KNAIF_N_THREADS_BATCH
  backend_env cpu
  for model in 4b 1.7b; do
    rm -rf "${d:?}/$model" && mkdir -p "$d/$model"
    m="$PWD/$(gguf "$model")"
    env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" daemon stop > /dev/null 2>&1
    for skill in ffmpeg documents; do
      regen "$skill" "$R/fixtures.log" || return 1
      $UV python - "skills/$skill/data/eval.jsonl" "$SAMPLES/t15_sample_${model}_${skill}.json" \
        > "$d/$model/$skill.utterances.txt" <<'EOF' || { failed "daemon $model $skill: listing the sample's utterances"; return 1; }
import json, sys
rows = {}
for line in open(sys.argv[1], encoding="utf-8"):
    if line.strip():
        r = json.loads(line)
        rows[r["id"]] = r.get("utterances", [])
for rid, idx in json.load(open(sys.argv[2], encoding="utf-8")):
    print(rows[rid][idx].replace("\n", " "))
EOF
      n="$(wc -l < "$d/$model/$skill.utterances.txt" | tr -d ' ')"
      [ "$n" -gt 0 ] || { failed "daemon $model $skill: no utterances"; return 1; }
      (cd "sandbox/fixtures/$skill" && env "${ENVS[@]}" KNAIF_NO_DAEMON=1 "$EXE" plan --skill "$skill" --model "$m" \
        --batch "$OLDPWD/$d/$model/$skill.utterances.txt" --json) \
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
      (cd "sandbox/fixtures/$skill" && env -u KNAIF_NO_DAEMON "${ENVS[@]}" "$EXE" plan --skill "$skill" --model "$m" \
        --batch "$OLDPWD/$d/$model/$skill.utterances.txt" --json) \
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

"stage_$STAGE"

if [ $FAILED -ne 0 ]; then log "END $STAGE with failures"; exit 1; fi
if [ $GATE_MISS -ne 0 ]; then log "END $STAGE: ran, with gate misses for the owner"; exit 3; fi
log "END $STAGE"
