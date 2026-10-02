#!/usr/bin/env bash
# Release 1.2.1 — carry 1.2.0's L3/L4 results over on a pre-registered sample (owner, 2026-10-02:
# patch lane, "L4 sampled"; the gate's sampled equivalence was extended the same day so that a run
# which ALSO checks the Python runtime may carry `python_core`, a skill's Python modules, and a
# `contracts/backends/` change). Tested artifacts: the frozen 1.2.1 build (`7e39b45`), Windows zip
# signed, Linux tarball from the release container; CUDA payload = the 1.2.1 manifest's files
# (Windows: 1.2.0's ggml-cuda.dll, signed; Linux: unchanged 1.2.0 files), hand-placed without a
# receipt, so the loader uses them as an unmanaged payload.
#
#   bash run.sh win      from the Windows checkout (Git Bash)                 ~10 min
#   bash run.sh python   from the Windows checkout, after `win`               ~10 min
#   bash run.sh linux    from the WSL checkout, on this commit                ~10 min
#
# What 1.2.1 changed since the measured RC (71884fd): native (B1-B7, B10, B11, the terminal view,
# the backend-version fix), the Python core (B5: a grounded value keeps the user's spelling), the
# ffmpeg Python handler (B9: batch globs in preflight), skill.yaml `dependencies` (1.2.0 RC3), and
# contracts/backends/backend-manifest.yaml (version, signed ggml-cuda.dll). Prompts, tools,
# models, corpus, settings and grading are unchanged.
#
# RULES, written 2026-10-02 before any run. Model 4B, CUDA, one process per request, `success`.
#  1. The sample (sample_<skill>.json, `sample_check.py draw`, seed 20261002): from the accepted
#     Windows board's requests that planned something, 20 ffmpeg and 10 documents drawn at random,
#     plus every one of these that planned: each chain request's first phrasing, ffmpeg's
#     reverse_video phrasings, every documents phrasing that mentions a password. 47 ffmpeg, 31
#     documents.
#  2. win, linux: the payload's files match the 1.2.1 manifest (`sample_check.py payload`); each
#     sampled request gives the SAME plan decision and the SAME grade (outcome, knaif score) as
#     the accepted run of 1.2.0 on that OS (Windows: T8, 2026-09-28_r5c-windows_success; Linux:
#     T14, 2026-09-29_r5c-linux_success), and the run is placed on CUDA0.
#  3. python: `scripts/parity_check.py --only` over the sampled ids, 1.2.1 Windows binary vs the
#     Python runtime of this tree, CUDA; each id gives the SAME status and the SAME outcome kind
#     and commands on BOTH runtimes as the accepted L3 run (2026-09-28_r5c-l3-rc2-4b-<skill>). The
#     stage records the Python tree it ran on (python_tree.json), clean, for the gate to bind.
# Any difference -> nothing is carried over and the owner decides.
set -uo pipefail
cd "$(dirname "$0")/../../.."
STAGE="${1:?usage: run.sh win|python|linux}"
R=evals/runs/2026-10-02_r121-sampled
ART=sandbox/r121/artifact
export KNAIF_N_THREADS=8 KNAIF_N_THREADS_BATCH=8 PATH="$HOME/.local/bin:$PATH"
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST
FAILED=0
failed() { echo "FAILED $STAGE: $*" | tee -a "$R/verdicts.txt"; FAILED=1; }
echo "START $STAGE $(date)" >> "$R/COMPLETE"

case "$STAGE" in
  win)
    case "$PWD" in /mnt/*) echo "run the win stage from Windows" >&2; exit 2 ;; esac
    PKG=dist/knaif-1.2.1-windows-x64.zip
    sha256sum "$PKG" | tee "$R/win_artifact.sha256"
    rm -rf "$ART" && mkdir -p "$ART" && (cd "$ART" && unzip -q "../../../$PKG") || failed "unzip"
    CUDA=sandbox/r121/backends-cuda
    rm -rf "$CUDA" && mkdir -p "$CUDA" && cp dist/staging/knaif-1.2.1-windows-x64-cuda-backend/* "$CUDA/"
    PLATFORM=windows-x64
    LANE=r121-win-4b
    ACCEPTED=evals/runs/2026-09-28_r5c-windows_success/4b/cuda
    ACCEPTED_LANE=r5c-win-4b
    ;;
  linux)
    case "$PWD" in /mnt/*) echo "run the linux stage from the WSL checkout" >&2; exit 2 ;; esac
    PKG=/mnt/c/Work/Knaif/knaif/dist/knaif-1.2.1-linux-x64.tar.gz
    (cd "$(dirname "$PKG")" && sha256sum "$(basename "$PKG")") | tee "$R/linux_artifact.sha256"
    rm -rf "$ART" && mkdir -p "$ART" && tar -xzf "$PKG" -C "$ART" || failed "untar"
    CUDA="$HOME/r121/backends-cuda"
    rm -rf "$CUDA" && mkdir -p "$CUDA" && cp "$HOME"/r5c/backends-cuda/* "$CUDA/"
    PLATFORM=linux-x64
    LANE=r121-linux-4b
    ACCEPTED=evals/runs/2026-09-29_r5c-linux_success/4b/cuda
    ACCEPTED_LANE=r5c-linux-4b
    ;;
  python)
    case "$PWD" in /mnt/*) echo "run the python stage from Windows" >&2; exit 2 ;; esac
    EXE="$(cygpath -aw "$ART/knaif-1.2.1-windows-x64/bin/knaif.exe")"
    [ -f "$EXE" ] || { echo "run the win stage first" >&2; exit 2; }
    export KNAIF_PARITY_BACKEND=cuda KNAIF_BACKENDS_DIR="$(cygpath -aw sandbox/r121/backends-cuda)"
    d="$R/python"
    mkdir -p "$d"
    # The Python tree this stage tests, as the gate fingerprints it: an equivalence may carry results
    # only TO exactly this tree (python_core, contracts, each skill's bundle).
    uv run python -c "import json; from pathlib import Path; from knaif.evalsuite.gate import evidence_tuple as e; t = {s: e(s, Path('.')) for s in ('ffmpeg', 'documents')}; print(json.dumps({'python_core': t['ffmpeg']['python_core'], 'contracts': t['ffmpeg']['contracts'], 'bundle': {s: t[s]['bundle'] for s in t}}, indent=2))" > "$R/python_tree.json" \
      || failed "could not record the python tree"
    [ -z "$(git status --porcelain -- python/core skills contracts)" ] || failed "the Python tree has uncommitted changes"
    for skill in ffmpeg documents; do
      report="$d/${skill}_parity.json"
      rm -f "$report"
      uv run python scripts/parity_check.py --skill "$skill" --only "$R/sample_$skill.json" \
        --native-bin "$EXE" --model-path models/knaif-qwen3-4b-v2-q4_k_m.gguf \
        --cwd "sandbox/fixtures/$skill" --out "$report" \
        --purpose "1.2.1 sampled equivalence, python stage" > "$d/$skill.log" 2>&1
      [ -s "$report" ] || { failed "$skill parity wrote no report"; continue; }
      uv run python "$R/sample_check.py" parity \
        "evals/parity/2026-09-28_r5c-l3-rc2-4b-$skill/report.json" "$report" \
        "$R/sample_$skill.json" > "$d/${skill}_verdict.txt" 2>&1 || failed "$skill: not equivalent"
      { echo "== python $skill"; cat "$d/${skill}_verdict.txt"; } >> "$R/verdicts.txt"
    done
    if [ "$FAILED" -ne 0 ]; then echo "FINISHED WITH FAILURES $STAGE $(date)" >> "$R/COMPLETE"; exit 1; fi
    echo "DONE $STAGE $(date)" >> "$R/COMPLETE"
    exit 0
    ;;
  *) echo "usage: run.sh win|python|linux" >&2; exit 2 ;;
esac

uv run python "$R/sample_check.py" payload "$CUDA" "$PLATFORM" > "$R/${STAGE}_payload.txt" 2>&1 \
  || failed "the CUDA payload does not match the 1.2.1 manifest (see ${STAGE}_payload.txt)"
case "$STAGE" in win) BACKENDS="$(cygpath -aw "$CUDA")" ;; *) BACKENDS="$CUDA" ;; esac

d="$R/$STAGE"
mkdir -p "$d"
for skill in ffmpeg documents; do
  board="$d/${skill}_${LANE}_success.json"
  rm -f "$board"
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$d/fixtures.log" 2>&1 \
    || { failed "fixtures $skill"; continue; }
  KNAIF_BACKENDS_DIR="$BACKENDS" uv run python -m knaif.evalsuite native --skill "$skill" \
    --lane "$LANE" --only "$R/sample_$skill.json" --verifier success --verbose \
    --config eval_backends.yaml --save "$d" > "$d/$skill.log" 2>&1
  [ -s "$board" ] || { failed "$skill sample run wrote no board"; continue; }
  placed="$(uv run python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8')).get('compute_backend'))" "$board")"
  [ "$placed" = CUDA0 ] || { failed "$skill ran on '$placed', not CUDA0"; continue; }
  uv run python "$R/sample_check.py" compare "$ACCEPTED/${skill}_${ACCEPTED_LANE}_success.json" \
    "$board" "$R/sample_$skill.json" > "$d/${skill}_verdict.txt" 2>&1 || failed "$skill: not equivalent"
  { echo "== $STAGE $skill"; cat "$d/${skill}_verdict.txt"; } >> "$R/verdicts.txt"
done
if [ "$FAILED" -ne 0 ]; then echo "FINISHED WITH FAILURES $STAGE $(date)" >> "$R/COMPLETE"; exit 1; fi
echo "DONE $STAGE $(date)" >> "$R/COMPLETE"
