#!/usr/bin/env bash
# Release 1.2.0 RC2 (text fix): the owner found the RC `71884fd` artifacts naming the v1 model in
# the installer, the README, NOTICE and two CLI help strings (2026-09-29). Fixed in the source
# (`36f9651`) and rebuilt with the release scripts; the owner chose to keep the acceptance results
# and verify the rebuilt binaries with a short sample instead of re-running the evaluation.
#
#   bash run.sh win      from the Windows checkout (Git Bash)          ~5 min
#   bash run.sh linux    from the WSL checkout (pull first)             ~5 min
#
# Byte-level evidence gathered before this (see report.md): the rebuilt Windows knaif.exe differs
# from the tested one in the two text bytes plus link timestamps and debug ids; the Linux knaif in
# the two text bytes (.rodata) and the build-id note, every loaded section otherwise identical.
# Every other file in the zip, tarball and AppImage is byte-identical to the tested ones except
# README.txt and NOTICE.
#
# RULES, written 2026-09-29 before the runs: per OS, the rebuilt binary from the new artifact, 4B,
# CUDA (installed payload), one process per request, `success` verifier, runs the pre-drawn sample
# (sample_<skill>.json: 20 ffmpeg + 10 documents, seed 20260929) and must give, for every request,
# the SAME plan decision and the SAME grade (outcome, knaif score) as the accepted run of the tested
# binary (Windows: T8; Linux: T14), per sample_check.py; the placement must be CUDA0, the payload
# must verify as ok, and `knaif run --help` must name knaif-qwen3-4b-v2. Any difference -> the
# equivalence is not recorded and the owner decides.
set -uo pipefail
cd "$(dirname "$0")/../../.."
STAGE="${1:?usage: run.sh win|linux}"
R=evals/runs/2026-09-29_r5c-rc2-textfix
ART=sandbox/r5c/artifact
export KNAIF_N_THREADS=8 KNAIF_N_THREADS_BATCH=8 PATH="$HOME/.local/bin:$PATH"
unset KNAIF_PDFIUM_PATH KNAIF_BACKEND_MANIFEST
FAILED=0
failed() { echo "FAILED $STAGE: $*" | tee -a "$R/verdicts.txt"; FAILED=1; }
echo "START $STAGE $(date)" >> "$R/COMPLETE"

case "$STAGE" in
  win)
    case "$PWD" in /mnt/*) echo "run the win stage from Windows" >&2; exit 2 ;; esac
    PKG=dist/knaif-1.2.0-windows-x64.zip
    sha256sum "$PKG" | tee "$R/win_artifact.sha256"
    rm -rf "$ART" && mkdir -p "$ART" && (cd "$ART" && unzip -q "../../../$PKG") || failed "unzip"
    EXE="$(cygpath -aw "$ART/knaif-1.2.0-windows-x64/bin/knaif.exe")"
    CUDA="$(cygpath -aw sandbox/r5c/backends-cuda)"
    LANE=r5c-win-4b
    ACCEPTED=evals/runs/2026-09-28_r5c-windows_success/4b/cuda
    ;;
  linux)
    case "$PWD" in /mnt/*) echo "run the linux stage from the WSL checkout" >&2; exit 2 ;; esac
    PKG="$HOME/r5c/dist-rc2/knaif-1.2.0-linux-x64.tar.gz"
    (cd "$(dirname "$PKG")" && sha256sum "$(basename "$PKG")") | tee "$R/linux_artifact.sha256"
    rm -rf "$ART" && mkdir -p "$ART" && tar -xzf "$PKG" -C "$ART" || failed "untar"
    EXE="$PWD/$ART/knaif-1.2.0-linux-x64/bin/knaif"
    CUDA="$HOME/r5c/backends-cuda"
    LANE=r5c-linux-4b
    ACCEPTED=evals/runs/2026-09-29_r5c-linux_success/4b/cuda
    ;;
  *) echo "usage: run.sh win|linux" >&2; exit 2 ;;
esac

KNAIF_BACKENDS_DIR="$CUDA" "$EXE" backend verify cuda > "$R/${STAGE}_verify.log" 2>&1
grep -q "^cuda: ok" "$R/${STAGE}_verify.log" || failed "CUDA payload does not verify as ok"
"$EXE" run --help 2>&1 | grep -q "knaif-qwen3-4b-v2" || failed "run --help does not name knaif-qwen3-4b-v2"
"$EXE" run --help 2>&1 | grep -q "knaif-qwen3-4b-v1" && failed "run --help still names v1"

d="$R/$STAGE"
mkdir -p "$d"
for skill in ffmpeg documents; do
  board="$d/${skill}_${LANE}_success.json"
  rm -f "$board"
  uv run python -m knaif.evalsuite fixtures regen --skill "$skill" >> "$d/fixtures.log" 2>&1 \
    || { failed "fixtures $skill"; continue; }
  KNAIF_BACKENDS_DIR="$CUDA" uv run python -m knaif.evalsuite native --skill "$skill" --lane "$LANE" \
    --only "$R/sample_$skill.json" --verifier success --verbose --config eval_backends.yaml \
    --save "$d" > "$d/$skill.log" 2>&1
  [ -s "$board" ] || { failed "$skill sample run wrote no board"; continue; }
  placed="$(uv run python -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8')).get('compute_backend'))" "$board")"
  [ "$placed" = CUDA0 ] || { failed "$skill ran on '$placed', not CUDA0"; continue; }
  uv run python "$R/sample_check.py" compare "$ACCEPTED/${skill}_${LANE}_success.json" "$board" \
    "$R/sample_$skill.json" > "$d/${skill}_verdict.txt" 2>&1 || failed "$skill: not equivalent"
  { echo "== $STAGE $skill"; cat "$d/${skill}_verdict.txt"; } >> "$R/verdicts.txt"
done
if [ "$FAILED" -ne 0 ]; then echo "FINISHED WITH FAILURES $STAGE $(date)" >> "$R/COMPLETE"; exit 1; fi
echo "DONE $STAGE $(date)" >> "$R/COMPLETE"
