#!/usr/bin/env bash
# knaif 1.3.0, the Linux floor in both directions and the Linux clean room (RELEASE.md §4; the
# release plan's gate rules: "Linux floor and container checks as 1.2.1"), on the FROZEN Linux
# artifacts built from the freeze 8c94cbe. A port of 1.2.0's t16_floor_cleanroom.sh with 1.3.0's
# files; checks and pass rule unchanged. Runs from the Windows checkout's Git Bash with Docker
# Desktop running; every step happens inside a container, so nothing on the host is touched.
# Launched only after the owner approves it with its time budget (~20-30 min).
#
#   bash evals/runs/2026-10-13_r130-linux_success/cleanroom.sh
#
# RULES (1.2.0 T16's, unchanged). PASSES iff every check passes:
#   floor_tarball, floor_appimage   installers/linux/check-floor.sh exits 0 for each artifact: it
#       runs at the claimed floor (ubuntu:22.04) and fails below it (ubuntu:20.04) with a
#       symbol-version error (any other failure is inconclusive and fails the script);
#   cleanroom_*                      in a fresh ubuntu:24.04 with only what a user installs (ffmpeg,
#       CA certificates), no Python, no build tools, the GPU not passed through:
#       - the tarball's `knaif --version` says 1.3.0;
#       - `knaif models pull knaif-qwen3-4b-v2` downloads the model from its pinned Hugging Face
#         URL and verifies it against the manifest's sha256;
#       - with NO --model (auto-selected), one real request per skill through the tarball's binary
#         and one per skill through the AppImage (`--appimage-extract-and-run`, no FUSE in a
#         container): each exits 0 and writes its output file; the ffmpeg output is a valid
#         video to ffprobe, the documents output is a PDF (`%PDF` header).
# PREDICTION: every check passes; the pull takes a few minutes, each request 10-30 s on the CPU.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-10-13_r130-linux_success/cleanroom
TARBALL=dist/knaif-1.3.0-linux-x64.tar.gz
APPIMAGE=dist/knaif-1.3.0-linux-x86_64.AppImage
mkdir -p "$R"
: > "$R/results.txt"
check() { echo "$([ "$2" = 0 ] && echo PASS || echo FAIL) $1${3:+: $3}" | tee -a "$R/results.txt"; }

echo "d50189ec68dd9930470009d6712b39b597892e435ae8307dab92549337c6f093  $TARBALL" | sha256sum -c - >/dev/null \
  || { echo "HASH MISMATCH: $TARBALL"; exit 1; }
echo "680ab17f4142bcddcecbf37b6be21c8877e57dce0d0ac0ba348df9887ed9c099  $APPIMAGE" | sha256sum -c - >/dev/null \
  || { echo "HASH MISMATCH: $APPIMAGE"; exit 1; }
docker version >/dev/null 2>&1 || { echo "Docker is not running: start Docker Desktop first"; exit 1; }

# 1. Floor, both directions, each artifact.
bash installers/linux/check-floor.sh "$TARBALL" > "$R/floor_tarball.log" 2>&1
check floor_tarball $? "see cleanroom/floor_tarball.log"
bash installers/linux/check-floor.sh "$APPIMAGE" > "$R/floor_appimage.log" 2>&1
check floor_appimage $? "see cleanroom/floor_appimage.log"

# 2. Clean room. The container gets the artifacts and two fixtures read-only, and writes results.
# An earlier attempt's results never count: cleared first, and the container's exit is checked.
rm -rf "$R/cleanroom" && mkdir -p "$R/cleanroom"
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$(cygpath -aw dist):/in:ro" \
  -v "$(cygpath -aw sandbox/fixtures/ffmpeg/clip.mp4):/fixtures/clip.mp4:ro" \
  -v "$(cygpath -aw sandbox/fixtures/documents/sample.pdf):/fixtures/sample.pdf:ro" \
  -v "$(cygpath -aw "$R/cleanroom"):/out" \
  ubuntu:24.04 bash -c '
    set -u
    say() { local s=FAIL; [ "$2" = 0 ] && s=PASS; echo "$s $1${3:+: $3}" | tee -a /out/checks.txt; }
    : > /out/checks.txt
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends ffmpeg ca-certificates >/dev/null 2>&1
    command -v python3 >/dev/null && say cleanroom_no_python 1 "python3 present" || say cleanroom_no_python 0
    mkdir -p /opt && tar -xzf /in/knaif-1.3.0-linux-x64.tar.gz -C /opt
    K=/opt/knaif-1.3.0-linux-x64/bin/knaif
    v=$($K --version 2>&1); case "$v" in "knaif 1.3.0"*) say cleanroom_version 0 "$v";; *) say cleanroom_version 1 "$v";; esac
    $K models pull knaif-qwen3-4b-v2 > /out/pull.log 2>&1; say cleanroom_models_pull $? "see cleanroom/pull.log"
    $K models verify knaif-qwen3-4b-v2 >> /out/pull.log 2>&1; say cleanroom_models_verify $?
    run() {  # $1 check name, $2 binary (a command), $3 skill, $4 request, $5 kind (video|pdf)
      rm -rf /work && mkdir -p /work && cp /fixtures/* /work/ && cd /work
      $2 run "$3" --yes "$4" > "/out/$1.log" 2>&1; local rc=$?
      local out; out=$(ls -t | grep -vxE "clip.mp4|sample.pdf" | head -1)
      if [ $rc -ne 0 ] || [ -z "$out" ]; then say "$1" 1 "exit $rc, output ${out:-none}"; return; fi
      case "$5" in
        video) [ "$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_type -of csv=p=0 "$out" 2>/dev/null)" = video ] \
                 && say "$1" 0 "$out" || say "$1" 1 "$out has no video stream" ;;
        pdf)   [ "$(head -c 4 "$out")" = "%PDF" ] && say "$1" 0 "$out" || say "$1" 1 "$out is not a PDF" ;;
      esac
      cp "$out" "/out/$1.${out##*.}" 2>/dev/null
    }
    run cleanroom_tarball_ffmpeg "$K" ffmpeg "convert clip.mp4 to mkv" video
    run cleanroom_tarball_documents "$K" documents "rotate sample.pdf 90 degrees" pdf
    cp /in/knaif-1.3.0-linux-x86_64.AppImage /opt/knaif.AppImage && chmod +x /opt/knaif.AppImage
    A="/opt/knaif.AppImage --appimage-extract-and-run"
    run cleanroom_appimage_ffmpeg "$A" ffmpeg "convert clip.mp4 to webm" video
    run cleanroom_appimage_documents "$A" documents "turn all pages of sample.pdf 90 degrees" pdf
  ' > "$R/cleanroom.log" 2>&1
rc=$?
cat "$R/cleanroom/checks.txt" >> "$R/results.txt" 2>/dev/null
[ $rc -eq 0 ] || echo "FAIL cleanroom_container: docker exit $rc (see cleanroom/cleanroom.log)" | tee -a "$R/results.txt"
cat "$R/results.txt"
n=$(grep -c "^PASS" "$R/results.txt"); f=$(grep -c "^FAIL" "$R/results.txt")
# 2 floor checks + 8 clean-room checks; fewer lines means a step never reported, which fails too.
if [ "$f" -eq 0 ] && [ "$n" -eq 10 ]; then echo "CLEAN ROOM PASS ($n checks)"; exit 0; fi
echo "CLEAN ROOM FAIL ($n passed, $f failed; 10 expected)"; exit 1
