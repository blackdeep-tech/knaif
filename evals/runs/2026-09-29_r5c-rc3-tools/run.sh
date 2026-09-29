#!/usr/bin/env bash
# Release 1.2.0 RC3 (supporting tools on Windows): Ghostscript, LibreOffice and Tesseract installers
# never add themselves to PATH, and setup offered winget installs on PCs without winget. Fixed in
# `c9218d8` (tools are also looked up in their declared install folders and launched from the path
# found) and rebuilt from `7c534e6` with the release scripts. Unlike RC2 this changes code on the
# execution path, so `evalsuite equivalence` (a text-fix check) does not apply; HOW the L3/L4
# evidence carries over is the owner's decision. This run gathers the evidence for either route.
#
#   bash run.sh win      from the Windows checkout (Git Bash)          ~10 min
#   bash run.sh linux    from the WSL checkout (pull first)             ~10 min
#
# Byte-level evidence gathered before this: against RC2 the Windows zip differs in exactly three
# files (knaif.exe and both skill.yaml); the VC++ runtime DLLs and every other file are identical.
#
# RULES, written 2026-09-29 before the runs: per OS, the rebuilt binary from the new artifact, 4B,
# CUDA (installed payload), one process per request, `success` verifier:
#  1. `tools_check.py`: every declared tool is OK in `skills deps`, and each resolved path is the
#     first PATH hit — the binary the accepted runs launched by bare name.
#  2. the sample runs (sample_<skill>.json): 20 ffmpeg drawn with seed 20260929:rc3; documents 10
#     drawn the same way plus EVERY corpus request whose accepted Windows plan uses compress_pdf
#     (Ghostscript), convert_document (LibreOffice for Office inputs) or ocr_document (Tesseract):
#     40 in all. Every request must give the SAME plan decision and the SAME grade (outcome, knaif
#     score) as the accepted run of the tested binary (Windows: T8; Linux: T14), per
#     sample_check.py; the placement must be CUDA0 and the payload must verify as ok.
# Any failure -> nothing is carried over and the owner decides.
set -uo pipefail
cd "$(dirname "$0")/../../.."
STAGE="${1:?usage: run.sh win|linux}"
R=evals/runs/2026-09-29_r5c-rc3-tools
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
    PKG="$HOME/r5c/dist-rc3/knaif-1.2.0-linux-x64.tar.gz"
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
uv run python "$R/tools_check.py" "$EXE" > "$R/${STAGE}_tools.txt" 2>&1 \
  || failed "a tool resolves somewhere other than the first PATH hit (see ${STAGE}_tools.txt)"

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
