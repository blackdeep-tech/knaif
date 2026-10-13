#!/usr/bin/env bash
# knaif 1.3.0, macOS round 2 (docs/plans/2026-08-02-macos-support.md §0, "Round 2, at the freeze",
# steps 2-5): the evidence runs on the FROZEN release files. Freeze commit 8c94cbe on release/1.3.0;
# the files are the artifact knaif-macos-8c94cbee216103777d79e14630de38f496d9b590 of CI run
# blackdeep-tech/knaif/actions/runs/38075857537 (Package, workflow_dispatch; signed and notarized in
# CI, decision 8). Nothing is built or signed on this Mac.
#
# Runs on the Mac (M1 Pro, macOS 27.2 beta), from the repo root of a checkout OUTSIDE the home
# directory (the E5 path guard), one stage per invocation, each launched only after the owner has
# approved it with its time budget:
#
#   bash evals/runs/<this folder>/run_all.sh check            the preflight only; measures nothing
#   bash evals/runs/<this folder>/run_all.sh l4     [4b|1.7b]  L4 Metal, both skills, safety, verdicts
#   bash evals/runs/<this folder>/run_all.sh cpu    [4b|1.7b]  the t15 sample rows + both safety sets, CPU
#   bash evals/runs/<this folder>/run_all.sh daemon [4b|1.7b]  plan equality, in process vs daemon
#   bash evals/runs/<this folder>/run_all.sh l3     [4b|1.7b]  L3 on Metal, recorded, not gated
#   SKILLS=documents bash ...  one skill only (amendment 2026-10-11)
#
# Without a model, a stage runs both (4B first). Every stage unpacks the CI .zip afresh into the lanes'
# folders (eval_backends.yaml: sandbox/macos/knaif, and sandbox/macos/knaif-cpu without
# libggml-metal.*), after checking its sha256 against the run's summary.
#
# Outputs: this folder (boards, safety files, verdicts, COMPLETE, verdicts.txt); evals/acceptance/
# <skill>.json (accept-native's L4 records, kept: they are the evidence); evals/parity/<date>_r130-
# macos-daemon-plan-equality/<model>/ and evals/parity/<date>_r130-macos-l3-<model>-<skill>/.
#
# ---------------------------------------------------------------------------------------------------
# GATE DECISION RULES, copied verbatim from docs/plans/2026-09-30-release-1.3.0.md (pre-registered
# 2026-10-07, before any 1.3.0 evidence run). Not to be changed after a result: a change is an
# amendment, recorded below with its reason, before the stage it affects runs.
# ---------------------------------------------------------------------------------------------------
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
# HOW THIS SCRIPT APPLIES THEM ON macOS (plan §0 round 2, decisions 1, 5, 8 in "Needs the owner"):
#   l4      cells <model>|macos|mtl: `accept-native` per model x skill, KNAIF_NO_DAEMON=1, the success
#           verifier, safety on the binary over the whole safety corpus. The board must report
#           compute_backend MTL0, else the cell is VOID. The 1.7B ffmpeg cell missing ONLY the `batch`
#           slice is waived with `evalsuite waive`, the reason quoting the exact verdict (owner,
#           2026-10-07 for macOS). Any other miss, any 4B miss, any safety miss: reported to the
#           owner, nothing waived.
#   cpu     cells <model>|macos|cpu are COMPOSED by the Windows box: this stage only runs the
#           pre-drawn sample rows (evals/runs/2026-09-29_r5c-linux_success/t15_sample_<model>_
#           <skill>.json) on the Metal-less tree (placement CPU, else VOID) and both safety sets on
#           that binary. No verdict here; flips are reported, not a verdict.
#   daemon  `knaif plan --skill <skill> --batch <utterances> --json` over every utterance of
#           skills/<skill>/data/eval.jsonl, once with KNAIF_NO_DAEMON=1 and once through
#           `knaif daemon start`; required byte-identical (method:
#           evals/parity/2026-10-06_daemon-plan-equality/README.md). A difference goes to the owner;
#           L4 is not re-run because of it.
#   l3      packaged Metal binary vs Python (llama-cpp-python on Metal), the 1.2.0 bounds: plan
#           disagreement <= 0.0411 ffmpeg, <= 0.0183 documents, 0 port bugs, 0 missing capabilities.
#           Recorded, not gated: the gate's L3 is the CUDA run on Windows.
#
# AMENDMENTS (each with its date and reason, written before the stage it affects runs):
#   2026-10-11, before any re-run. Not a rule change: the grading environment was incomplete. Step 0's
#   `uv sync` removed the repo's `documents` dependency groups (pypdf, pikepdf, ...), and only
#   llama-cpp-python was put back before launch. The documents verifier needs pypdf to read the PDFs
#   the binary writes, so those checks failed with "No module named 'pypdf'":
#     l4 4b documents   NOT ACCEPTED, avg_knaif_score 0.333 (111 of 164 rows lost checks)    INVALID
#     l4 1.7b documents NOT ACCEPTED, avg_knaif_score 0.326                                  INVALID
#     cpu 4b documents  20 of 35 sample rows lost checks                                     INVALID
#     cpu 1.7b documents 22 of 35 sample rows lost checks                                    INVALID
#   Plans and outcomes were unaffected (all 164 L4 documents plans equal the dry run's). ffmpeg cells,
#   the daemon stage and L3 are valid: the group was restored (`just install-skills`, additions only)
#   at 11:40 on 2026-10-11, during L3 4b ffmpeg and before both L3 documents cells.
#   Change: SKILLS=documents re-runs only those four cells (`SKILLS=documents bash run_all.sh l4`, then
#   `... cpu`), launched after the owner approves; same rules, same lanes, same files. The invalid
#   boards are kept under <model>/<stage>/invalid-2026-10-11/; accept-native's re-run replaces the
#   invalid L4 records in evals/acceptance/documents.json.
# ---------------------------------------------------------------------------------------------------

set -uo pipefail
case "$PWD" in "$HOME"/*) echo "refusing to run from $PWD: use a checkout outside ~ (E5)" >&2; exit 2 ;; esac
[ -f Cargo.toml ] && [ -f eval_backends.yaml ] || { echo "run from the repo root" >&2; exit 2; }
USAGE="usage: run_all.sh check|l4|cpu|daemon|l3 [4b|1.7b]"
STAGE="${1:?$USAGE}"
MODELS="${2:-4b 1.7b}"
case "$STAGE" in check | l4 | cpu | daemon | l3) ;; *) echo "$USAGE" >&2; exit 2 ;; esac
for m in $MODELS; do case "$m" in 4b | 1.7b) ;; *) echo "$USAGE" >&2; exit 2 ;; esac; done

R="evals/runs/$(basename "$(cd "$(dirname "$0")" && pwd)")"
DATE="${R##*/}"; DATE="${DATE%%_*}"
SKILLS="${SKILLS:-ffmpeg documents}"  # amendment 2026-10-11: SKILLS=documents for the re-run
for s in $SKILLS; do case "$s" in ffmpeg | documents) ;; *) echo "SKILLS: ffmpeg and/or documents" >&2; exit 2 ;; esac; done
TAG="$STAGE-${2:-both}"; [ "$SKILLS" = "ffmpeg documents" ] || TAG="$TAG-$(echo $SKILLS | tr ' ' -)"
FREEZE=8c94cbe
CI=sandbox/r130/ci
ZIP="$CI/knaif-1.3.0-macos-arm64.zip"
PKG="$CI/knaif-1.3.0-macos-arm64.pkg"
ZIP_SHA256=bb042cc45c094503d7ef2b89df0dce9efb3bdc87e67b74b94a201fe882f8590e  # = the run summary (checked 2026-10-10)
PKG_SHA256=e9a317684a69822aec6f39830cd698abcb727317a9bd5760198196ec82aa6470  # = the run summary (checked 2026-10-10)
BIN=sandbox/macos/knaif/bin/knaif
CPUBIN=sandbox/macos/knaif-cpu/bin/knaif
LINUX_SAMPLES=evals/runs/2026-09-29_r5c-linux_success
gguf() { case "$1" in 4b) echo models/knaif-qwen3-4b-v2-q4_k_m.gguf ;; 1.7b) echo models/knaif-qwen3-1.7b-v2-q6_k.gguf ;; esac; }
pyname() { case "$1" in 4b) echo knaif-qwen3-4b-v2 ;; 1.7b) echo knaif-qwen3-1.7b-v2 ;; esac; }
export PATH="$HOME/.local/share/mise/shims:$PATH"
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST KNAIF_N_GPU_LAYERS  # the artifact's own layout
export KNAIF_NO_DAEMON=1                                          # L4 in process (gate rules)

mkdir -p "$R"
LOCK="$R/.running"
mkdir "$LOCK" 2>/dev/null || { echo "another stage is running ($LOCK)" >&2; exit 2; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
log() { echo "$(date '+%F %T') $*" | tee -a "$R/COMPLETE"; }
FAILED=0
failed() { log "FAILED $TAG: $*"; echo "FAILED $TAG: $*" >> "$R/verdicts.txt"; FAILED=1; }
abort() { log "ABORTED $TAG: $*"; exit 1; }
rel() { sed "s|$PWD/||g"; }  # tools print absolute paths; committed files carry repo-relative ones

# -- preflight: the frozen tree, the CI files, the models -------------------------------------------
log "START $TAG"
git rev-parse HEAD > "$R/GIT_SHA.$TAG"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES.$TAG"
git merge-base --is-ancestor "$FREEZE" HEAD || abort "HEAD does not contain the freeze commit $FREEZE"
[ -z "$(git diff --name-only "$FREEZE" HEAD -- python native apps skills contracts eval_backends.yaml)" ] \
  || abort "code, contracts or skills differ from the freeze commit $FREEZE"
{
  echo "zip: $(shasum -a 256 "$ZIP" 2>&1)"
  echo "pkg: $(shasum -a 256 "$PKG" 2>&1)"
} > "$R/artifact.$TAG.log"
echo "$ZIP_SHA256  $ZIP" | shasum -a 256 -c - >> "$R/artifact.$TAG.log" 2>&1 || abort "$ZIP sha256 is not the CI run's"
echo "$PKG_SHA256  $PKG" | shasum -a 256 -c - >> "$R/artifact.$TAG.log" 2>&1 || abort "$PKG sha256 is not the CI run's"
rm -rf sandbox/macos && mkdir -p sandbox/macos || abort "cannot reset sandbox/macos"
ditto -x -k "$ZIP" sandbox/macos && mv sandbox/macos/knaif-* sandbox/macos/knaif || abort "unzip failed"
cp -R sandbox/macos/knaif sandbox/macos/knaif-cpu && rm sandbox/macos/knaif-cpu/bin/libggml-metal.* \
  || abort "could not build the Metal-less tree"
ls sandbox/macos/knaif-cpu/bin | grep -q 'libggml-metal' && abort "the CPU tree still has a Metal backend"
[ "$("$BIN" --version)" = "knaif 1.3.0 (inference: llama.cpp)" ] || abort "the unpacked binary is not knaif 1.3.0"
{
  "$BIN" --version
  echo "knaif sha256: $(shasum -a 256 "$BIN" | cut -d' ' -f1)"
  codesign -dv "$BIN" 2>&1 | grep -E '^(Identifier|TeamIdentifier|Timestamp|Runtime Version)='
  echo "macOS $(sw_vers -productVersion) ($(sw_vers -buildVersion)), $(sysctl -n machdep.cpu.brand_string)"
} | rel >> "$R/artifact.$TAG.log" 2>&1
for pair in "a9c26005e94622d63d1c6e64cb1c1b42084dcc37f6be7d69f8d5967f9c13aab7 $(gguf 4b)" \
            "d59cad240f5e0f157f093868479cf92132156097394805a9cccd102e14f04ac5 $(gguf 1.7b)"; do
  want="${pair%% *}" file="${pair#* }"
  echo "$want  $file" | shasum -a 256 -c - >> "$R/artifact.$TAG.log" 2>&1 || abort "$file sha256 mismatch"
done

if [ "$STAGE" = check ]; then log "END $TAG: preflight passed"; exit 0; fi

placement() {  # $1 board -> the compute_backend the lane measured
  uv run python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8')).get('compute_backend'))" \
    "$1" 2>/dev/null
}
regen() {  # $1 skill, $2 log
  uv run python -m knaif.evalsuite fixtures regen --skill "$1" >> "$2" 2>&1 || { failed "$1: fixture regeneration failed"; return 1; }
}

run_lane() {  # $1 skill, $2 lane, $3 dir, $4 wanted placement, [$5 --only file]: VOID unless it ran on $4
  local skill="$1" lane="$2" d="$3" want="$4" only="${5:-}" board got
  board="$d/${skill}_${lane}_success.json"
  rm -f "$board"
  regen "$skill" "$d/fixtures.log" || return 1
  if ! uv run python -m knaif.evalsuite native --skill "$skill" --lane "$lane" \
    ${only:+--only "$only"} --verifier success --verbose --config eval_backends.yaml --save "$d" \
    > "$d/$skill.log" 2>&1 || [ ! -s "$board" ]; then
    failed "$skill $lane: the lane run failed (see $d/$skill.log)"; return 1
  fi
  got="$(placement "$board")"
  log "$TAG $skill $lane: placement $got"
  if [ "$got" != "$want" ]; then
    echo "VOID $TAG $skill $lane: ran on '$got', expected $want" >> "$R/verdicts.txt"
    failed "VOID $skill $lane: ran on '$got', expected $want"; return 1
  fi
}

safety() {  # $1 skill, $2 lane, $3 dir: the whole safety corpus on that lane's binary
  local skill="$1" lane="$2" d="$3" rc
  rm -f "$d/${skill}_safety.json"
  uv run python -m knaif.evalsuite safety --skill "$skill" --lane "$lane" \
    --config eval_backends.yaml --save "$d/${skill}_safety.json" > "$d/${skill}_safety.log" 2>&1
  rc=$?
  if [ $rc -ne 0 ] && [ ! -s "$d/${skill}_safety.json" ]; then
    failed "safety $skill $lane: exit $rc, no result"; return 1
  fi
  log "$TAG $skill $lane: safety exit $rc"
}

# 1.7B ffmpeg on Metal: waived only when `batch` is the one unmet threshold (gate rules, Waivers).
waive_if_rule_allows() {  # $1 model, $2 skill, $3 verdict file
  local model="$1" skill="$2" v="$3" unmet
  [ "$model" = 1.7b ] && [ "$skill" = ffmpeg ] || return 0
  grep -q "^NOT ACCEPTED" "$v" || return 0
  unmet="$(grep -E '^ +\[' "$v")"
  if [ "$(printf '%s\n' "$unmet" | grep -c .)" = 1 ] && printf '%s\n' "$unmet" | grep -q "^ *\[slice\] slice 'batch' "; then
    uv run python -m knaif.evalsuite waive --skill ffmpeg --cell "knaif-qwen3-1.7b-v2|macos|mtl" --layer L4 \
      --date 2026-10-07 --reason "waived by the 1.3.0 gate rules (owner, 2026-10-07 for macOS): the 1.7B ffmpeg \
Metal cell misses only the batch slice, which 1.2.0 waived; verdict: $(grep -m1 '^NOT ACCEPTED' "$v") \
$(printf '%s' "$unmet" | sed 's/^ *//')" 2>&1 | rel | tee -a "$R/verdicts.txt" >> "$R/COMPLETE"
  else
    log "$TAG ffmpeg 1.7b: NOT waived, a miss outside the rule: goes to the owner"
  fi
}

# -- L4 on Metal ------------------------------------------------------------------------------------
stage_l4() {
  local model="$1" lane="mac-$1" d="$R/$1/metal" skill board rc
  mkdir -p "$d"
  for skill in $SKILLS; do
    run_lane "$skill" "$lane" "$d" MTL0 || continue
    safety "$skill" "$lane" "$d" || continue
    board="$d/${skill}_${lane}_success.json"
    # accept-native exits 1 on NOT ACCEPTED (a recorded verdict) and also on errors that record
    # nothing: only output naming a verdict AND a written record counts as a result.
    uv run python -m knaif.evalsuite accept-native --skill "$skill" --current "$board" \
      --safety "$d/${skill}_safety.json" > "$d/${skill}_accept.log" 2>&1
    rc=$?
    rel < "$d/${skill}_accept.log" > "$d/${skill}_verdict.txt"
    { echo "=== $TAG $model $skill (placement $(placement "$board"))"; cat "$d/${skill}_verdict.txt"; echo "exit $rc"; } \
      >> "$R/verdicts.txt"
    if ! grep -qE "^(NOT )?ACCEPTED" "$d/${skill}_verdict.txt" || ! grep -q "recorded L4 evidence" "$d/${skill}_verdict.txt"; then
      failed "accept-native $model $skill: no verdict recorded (exit $rc)"; continue
    fi
    log "$TAG $model $skill: $(grep -m1 -E '^(NOT )?ACCEPTED' "$d/${skill}_verdict.txt")"
    waive_if_rule_allows "$model" "$skill" "$d/${skill}_verdict.txt"
  done
}

# -- CPU sample (composed by the Windows box) -------------------------------------------------------
stage_cpu() {
  local model="$1" lane="mac-cpu-$1" d="$R/$1/cpu" skill only
  mkdir -p "$d"
  for skill in $SKILLS; do
    only="$LINUX_SAMPLES/t15_sample_${model}_${skill}.json"
    [ -s "$only" ] || { failed "no sample file $only"; continue; }
    run_lane "$skill" "$lane" "$d" CPU "$only" || continue
    safety "$skill" "$lane" "$d"
  done
}

# -- daemon plan equality ---------------------------------------------------------------------------
stage_daemon() {
  local model="$1" d="evals/parity/${DATE}_r130-macos-daemon-plan-equality/$1" m skill a b
  m="$PWD/$(gguf "$model")"
  mkdir -p "$d"
  env -u KNAIF_NO_DAEMON "$BIN" daemon stop > /dev/null 2>&1
  for skill in $SKILLS; do
    regen "$skill" "$R/fixtures.log" || return 1
    uv run python -c "
import json, sys
for line in open(sys.argv[1], encoding='utf-8'):
    line = line.strip()
    if line:
        for u in json.loads(line).get('utterances', []):
            print(u.replace('\n', ' '))" "skills/$skill/data/eval.jsonl" > "$d/$skill.utterances.txt"
    (cd "sandbox/fixtures/$skill" && KNAIF_NO_DAEMON=1 "$OLDPWD/$BIN" plan --skill "$skill" --model "$m" \
      --batch "$OLDPWD/$d/$skill.utterances.txt" --json) > "$d/$skill.inprocess.jsonl" 2> "$d/$skill.inprocess.err"
    log "$TAG $model $skill: in process exit $?, $(wc -l < "$d/$skill.inprocess.jsonl" | tr -d ' ') lines"
  done
  env -u KNAIF_NO_DAEMON "$BIN" daemon start --model "$m" 2>&1 | rel > "$d/daemon.txt"
  env -u KNAIF_NO_DAEMON "$BIN" daemon status 2>&1 | rel >> "$d/daemon.txt"
  for skill in $SKILLS; do
    (cd "sandbox/fixtures/$skill" && env -u KNAIF_NO_DAEMON "$OLDPWD/$BIN" plan --skill "$skill" --model "$m" \
      --batch "$OLDPWD/$d/$skill.utterances.txt" --json) > "$d/$skill.daemon.jsonl" 2> "$d/$skill.daemon.err"
    log "$TAG $model $skill: through the daemon exit $?"
    a="$(shasum -a 256 < "$d/$skill.inprocess.jsonl" | cut -d' ' -f1)"
    b="$(shasum -a 256 < "$d/$skill.daemon.jsonl" | cut -d' ' -f1)"
    if [ "$a" = "$b" ]; then
      echo "$model $skill: IDENTICAL sha256 $a ($(wc -l < "$d/$skill.inprocess.jsonl" | tr -d ' ') lines)" >> "$d/result.txt"
      log "$TAG $model $skill: IDENTICAL ($a)"
    else
      echo "$model $skill: DIFFERENT in process $a, daemon $b" >> "$d/result.txt"
      failed "daemon $model $skill: DIFFERENT (in process $a, daemon $b): goes to the owner"
    fi
  done
  env -u KNAIF_NO_DAEMON "$BIN" daemon status 2>&1 | rel >> "$d/daemon.txt"
  env -u KNAIF_NO_DAEMON "$BIN" daemon stop 2>&1 | rel >> "$d/daemon.txt"
}

# -- L3 on Metal (recorded, not gated) --------------------------------------------------------------
stage_l3() {
  local model="$1" skill bound out rc
  for skill in $SKILLS; do
    bound=0.0411; [ "$skill" = documents ] && bound=0.0183
    out="evals/parity/${DATE}_r130-macos-l3-$model-$skill"
    mkdir -p "$out"
    regen "$skill" "$out/fixtures.log" || continue
    KNAIF_PARITY_BACKEND=metal uv run python scripts/parity_check.py --skill "$skill" --native-bin "$PWD/$BIN" \
      --model-path "$PWD/$(gguf "$model")" --python-model "$(pyname "$model")" --cwd "$PWD/sandbox/fixtures/$skill" \
      --out "$out/report.json" --label "r130-macos-l3-$model-$skill" --max-plan-disagreement "$bound" \
      --purpose "knaif 1.3.0 macOS round 2: L3 of the frozen CI build on Metal (recorded, not gated)" \
      > "$out/l3.log" 2>&1
    rc=$?
    log "$TAG $model $skill: parity_check exit $rc (bound $bound); $(grep -m1 -i -E 'verdict|PASS|FAIL' "$out/l3.log" | rel | cut -c1-140)"
    [ $rc -eq 0 ] || failed "L3 $model $skill: parity_check exit $rc"
  done
}

for model in $MODELS; do
  log "$TAG: $model"
  "stage_$STAGE" "$model"
done

if [ $FAILED -ne 0 ]; then log "END $TAG with failures"; exit 1; fi
log "END $TAG"
