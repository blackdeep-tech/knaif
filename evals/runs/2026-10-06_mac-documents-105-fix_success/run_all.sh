#!/usr/bin/env bash
# macOS 1.3.0, the Mac's list step 5 (docs/plans/2026-08-02-macos-support.md §0; C4, D14): L4 of the
# packaged metal .zip on Apple Silicon, per model, safety on the binary. DOCUMENTS ONLY, on the
# documents_105 fix (4d71261, mac/documents-105-dryrun), 2026-10-06, to show the fix changes nothing
# else; earlier header follows. RE-RUN on
# the merged 1.2.1 tree bdd01b5 (2026-10-05), because the merge of main made the macOS L3/L4 stale;
# macOS 27.2 Beta 2. Same procedure and decision rules as evals/runs/2026-10-03_mac-l4_success.
#
# Runs on the Mac, from a checkout OUTSIDE the home directory (the E5 path guard), one stage per
# invocation, one at a time:
#
#   bash run_all.sh metal 4b      L4 Metal cell in full, ffmpeg + documents            ~2.5-3 h
#   bash run_all.sh metal 1.7b    the same for the 1.7B model                          ~1.5-2 h
#   bash run_all.sh cpu 4b|1.7b   the CPU sample (D14): the committed 1.2.0 Linux sample rows on
#                                 the Metal-less tree, reported against the Linux CPU sample
#
# Artifact: dist/knaif-<ver>-macos-arm64.zip from `just package-native metal` on this tree,
# unpacked fresh by every stage into sandbox/macos/knaif (the lanes in eval_backends.yaml) and
# copied to sandbox/macos/knaif-cpu with libggml-metal.* removed (D2: no Metal backend to find,
# never KNAIF_N_GPU_LAYERS=0). Its sha256 is recorded per stage. Models: models/*.gguf, checked
# against the published hashes.
#
# DECISION RULES, written 2026-10-03 before any stage runs:
#   metal <model> (per skill): `accept-native` ACCEPTED, the 1.2.0 T14 rule unchanged: native >=
#      max(S2 floor at that model's bar, the model's locked Python score - 0.02) on outcome and
#      knaif score, coverage 1.0, every required slice, safety 100% on the binary. Baselines are
#      the committed snapshots (4B data/eval_snapshot.json, 1.7B
#      data/eval_snapshot.knaif-qwen3-1.7b-v2.json); nothing is re-locked (§11). The board must
#      have run on MTL0, else the cell is VOID. A NOT ACCEPTED verdict is recorded as evidence and
#      goes to the owner; no bar moves here.
#   cpu <model>: the sample rows (t15_sample_<model>_<skill>.json, drawn for 1.2.0) on the CPU
#      (placement CPU, else VOID). Reported, not a verdict on its own: step 6 compares its rows with
#      the Linux CPU sample (scripts/l4_rows.py), and how the macOS CPU cell is composed or
#      accepted is the owner's call, as it was for Linux (T15s).
#   Every stage also records the flips against Windows and Linux in step 6, as information.
#   Note for release integration: these boards record os=macos and compute_backend=MTL0, so
#   accept-native keys the cells `<model>|macos|mtl`; acceptance_matrix.yaml has no macOS entry yet.

set -uo pipefail
cd "$(dirname "$0")/../../.."
case "$PWD" in "$HOME"/*) echo "refusing to run from $PWD: use a checkout outside ~ (E5)" >&2; exit 2 ;; esac
STAGE="${1:?usage: run_all.sh metal|cpu 4b|1.7b}"
MODEL="${2:?usage: run_all.sh metal|cpu 4b|1.7b}"
case "$STAGE:$MODEL" in metal:4b | metal:1.7b | cpu:4b | cpu:1.7b) ;; *) echo "unknown stage $STAGE $MODEL" >&2; exit 2 ;; esac
R=evals/runs/2026-10-06_mac-documents-105-fix_success
TAG="$STAGE-$MODEL"
VER="$(grep -A3 '\[workspace.package\]' Cargo.toml | grep -m1 '^version' | sed -E 's/.*"([^"]+)".*/\1/')"
ZIP="dist/knaif-$VER-macos-arm64.zip"
LINUX_SAMPLES=evals/runs/2026-09-29_r5c-linux_success
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST KNAIF_N_GPU_LAYERS  # the artifact's own layout

mkdir -p "$R"
git rev-parse HEAD > "$R/GIT_SHA.$TAG"
git status --porcelain --untracked-files=no > "$R/DIRTY_FILES.$TAG"
echo "START $TAG $(date)" >> "$R/COMPLETE"
abort() { echo "ABORTED $TAG: $* $(date)" >> "$R/COMPLETE"; exit 1; }
FAILED=0
failed() { echo "FAILED $TAG: $* $(date)" | tee -a "$R/verdicts.txt" >> "$R/COMPLETE"; FAILED=1; }

[ -s "$ZIP" ] || abort "no $ZIP: run just package-native metal first"
shasum -a 256 "$ZIP" > "$R/artifact.$TAG.log"
rm -rf sandbox/macos && mkdir -p sandbox/macos || abort "cannot reset sandbox/macos"
ditto -x -k "$ZIP" sandbox/macos && mv sandbox/macos/knaif-* sandbox/macos/knaif || abort "unzip failed"
cp -R sandbox/macos/knaif sandbox/macos/knaif-cpu && rm sandbox/macos/knaif-cpu/bin/libggml-metal.* \
  || abort "could not build the Metal-less tree"
ls sandbox/macos/knaif-cpu/bin | grep -q 'libggml-metal' && abort "the CPU tree still has a Metal backend"
sandbox/macos/knaif/bin/knaif --version >> "$R/artifact.$TAG.log" 2>&1
for pair in "a9c26005e94622d63d1c6e64cb1c1b42084dcc37f6be7d69f8d5967f9c13aab7 knaif-qwen3-4b-v2-q4_k_m.gguf" \
            "d59cad240f5e0f157f093868479cf92132156097394805a9cccd102e14f04ac5 knaif-qwen3-1.7b-v2-q6_k.gguf"; do
  set -- $pair
  echo "$1  models/$2" | shasum -a 256 -c - >> "$R/artifact.$TAG.log" 2>&1 || abort "models/$2 sha256 mismatch"
done

case "$STAGE" in
  metal) LANE="mac-$MODEL"; WANT=MTL0 ;;
  cpu) LANE="mac-cpu-$MODEL"; WANT=CPU ;;
esac

placement() {  # $1 board -> the compute_backend the lane measured
  uv run python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8')).get('compute_backend'))" \
    "$1" 2>/dev/null
}

run_skill() {  # $1 skill, $2 dir, [$3 --only file]: one lane run, VOID unless it ran on $WANT
  local skill="$1" d="$2" only="${3:-}" board got
  board="$d/${skill}_${LANE}_success.json"
  rm -f "$board"
  if ! uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$d/fixtures.log" 2>&1; then
    failed "$skill: fixture regeneration failed"; return 1
  fi
  if ! uv run python -m knaif.evalsuite native --skill "$skill" --lane "$LANE" \
    ${only:+--only "$only"} --verifier success --verbose --config eval_backends.yaml --save "$d" \
    > "$d/$skill.log" 2>&1 || [ ! -s "$board" ]; then
    failed "$skill: the lane run failed (see $d/$skill.log)"; return 1
  fi
  got="$(placement "$board")"
  if [ "$got" != "$WANT" ]; then
    echo "VOID $TAG $skill: ran on '$got', expected $WANT" >> "$R/verdicts.txt"
    failed "VOID $skill: ran on '$got', expected $WANT"; return 1
  fi
}

accept() {  # $1 skill, $2 dir: safety on the binary, then the recorded verdict
  local skill="$1" d="$2" board="$2/${1}_${LANE}_success.json" rc
  rm -f "$d/${skill}_safety.json"
  uv run python -m knaif.evalsuite safety --skill "$skill" --lane "$LANE" \
    --config eval_backends.yaml --save "$d/${skill}_safety.json" > "$d/${skill}_safety.log" 2>&1
  rc=$?
  if [ $rc -ne 0 ] && [ ! -s "$d/${skill}_safety.json" ]; then
    failed "safety $skill: exit $rc, no result"; return 1
  fi
  # accept-native exits 1 on NOT ACCEPTED (a recorded verdict) and also on errors that record
  # nothing: only output naming a verdict AND a written record counts as a result.
  uv run python -m knaif.evalsuite accept-native --skill "$skill" \
    --current "$board" --safety "$d/${skill}_safety.json" > "$d/${skill}_accept.log" 2>&1
  rc=$?
  {
    echo "=== $TAG $skill (placement $(placement "$board"))"
    cat "$d/${skill}_accept.log"
    echo "exit $rc"
  } >> "$R/verdicts.txt"
  if ! grep -qE "^(NOT )?ACCEPTED" "$d/${skill}_accept.log" \
    || ! grep -q "recorded L4 evidence" "$d/${skill}_accept.log"; then
    failed "accept-native $skill: no verdict recorded (exit $rc)"; return 1
  fi
}

D="$R/$MODEL/$STAGE"
mkdir -p "$D"
for skill in documents; do
  case "$STAGE" in
    metal)
      run_skill "$skill" "$D" && accept "$skill" "$D" && echo "DONE $TAG $skill $(date)" >> "$R/COMPLETE" ;;
    cpu)
      run_skill "$skill" "$D" "$LINUX_SAMPLES/t15_sample_${MODEL}_${skill}.json" \
        && echo "DONE $TAG $skill $(date)" >> "$R/COMPLETE" ;;
  esac
done

if [ $FAILED -ne 0 ]; then
  echo "END $TAG with failures $(date)" >> "$R/COMPLETE"; exit 1
fi
echo "END $TAG $(date)" >> "$R/COMPLETE"
