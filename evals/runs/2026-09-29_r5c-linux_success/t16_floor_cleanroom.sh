#!/usr/bin/env bash
# Release 1.2.0 R5c T16 (docs/plans/2026-09-25-release-1.2.md; RELEASE.md §4): the Linux floor in
# both directions, and the Linux clean room, on RC 71884fd's FROZEN Linux artifacts. Runs from the
# Windows checkout's Git Bash with Docker Desktop running (the WSL distro has no docker); every
# step happens inside a container, so nothing on the host is touched.
#
#   bash evals/runs/2026-09-29_r5c-linux_success/t16_floor_cleanroom.sh
#
# RULES, written 2026-09-29 before the run. T16 PASSES iff every check passes:
#   floor_tarball, floor_appimage   installers/linux/check-floor.sh exits 0 for each artifact: it
#       runs at the claimed floor (ubuntu:22.04) and fails below it (ubuntu:20.04) with a
#       symbol-version error (any other failure is inconclusive and fails the script);
#   cleanroom_*                      in a fresh ubuntu:24.04 with only what a user installs (ffmpeg,
#       CA certificates), no Python, no build tools, the GPU not passed through:
#       - the tarball's `knaif --version` says 1.2.0;
#       - `knaif models pull knaif-qwen3-4b-v2` downloads the model from its pinned Hugging Face
#         URL and verifies it against the manifest's sha256 (the staged file, as R7 publishes it);
#       - with NO --model (auto-selected), one real request per skill through the tarball's binary
#         and one per skill through the AppImage (`--appimage-extract-and-run`, no FUSE in a
#         container): each exits 0 and writes its output file; the ffmpeg output is a valid
#         video to ffprobe, the documents output is a PDF (`%PDF` header).
#       Requests: ffmpeg_ "convert clip.mp4 to mkv" / "convert clip.mp4 to webm", documents_036
#       "rotate sample.pdf 90 degrees" / documents_010 "turn all pages of sample.pdf 90 degrees".
# PREDICTION: every check passes; the pull takes a few minutes, each request 10-30 s on the CPU.
set -uo pipefail
cd "$(dirname "$0")/../../.."
R=evals/runs/2026-09-29_r5c-linux_success/t16
TARBALL=dist/knaif-1.2.0-linux-x64.tar.gz
APPIMAGE=dist/knaif-1.2.0-linux-x86_64.AppImage
mkdir -p "$R"
: > "$R/results.txt"
check() { echo "$([ "$2" = 0 ] && echo PASS || echo FAIL) $1${3:+: $3}" | tee -a "$R/results.txt"; }

echo "4fbba4a97f5d4a2377887d11df428be2db3c8c67e2d0f393e19bdd175f4bb801  $TARBALL" | sha256sum -c - >/dev/null \
  || { echo "HASH MISMATCH: $TARBALL"; exit 1; }
echo "643f52f129175abddf9d4187723f6a77427cc1726f34f82633529a04fe2b4781  $APPIMAGE" | sha256sum -c - >/dev/null \
  || { echo "HASH MISMATCH: $APPIMAGE"; exit 1; }
docker version >/dev/null 2>&1 || { echo "Docker is not running: start Docker Desktop first"; exit 1; }

# 1. Floor, both directions, each artifact.
bash installers/linux/check-floor.sh "$TARBALL" > "$R/floor_tarball.log" 2>&1
check floor_tarball $? "see t16/floor_tarball.log"
bash installers/linux/check-floor.sh "$APPIMAGE" > "$R/floor_appimage.log" 2>&1
check floor_appimage $? "see t16/floor_appimage.log"

# 2. Clean room. The container gets the artifacts and two fixtures read-only, and writes results.
mkdir -p "$R/cleanroom"
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
    mkdir -p /opt && tar -xzf /in/knaif-1.2.0-linux-x64.tar.gz -C /opt
    K=/opt/knaif-1.2.0-linux-x64/bin/knaif
    v=$($K --version 2>&1); case "$v" in "knaif 1.2.0"*) say cleanroom_version 0 "$v";; *) say cleanroom_version 1 "$v";; esac
    $K models pull knaif-qwen3-4b-v2 > /out/pull.log 2>&1; say cleanroom_models_pull $? "see cleanroom/pull.log"
    $K models verify knaif-qwen3-4b-v2 >> /out/pull.log 2>&1; say cleanroom_models_verify $?
    run() {  # $1 check name, $2 binary (a command), $3 skill, $4 request, $5 kind (video|pdf)
      rm -rf /work && mkdir -p /work && cp /fixtures/* /work/ && cd /work
      $2 run "$3" --yes "$4" > "/out/$1.log" 2>&1; local rc=$?
      local out; out=$(ls -t | grep -vxE "clip.mp4|sample.pdf" | head -1)
      if [ $rc -ne 0 ] || [ -z "$out" ]; then say "$1" 1 "exit $rc, output ${out:-none}"; return; fi
      case "$5" in
        video) ffprobe -v error -show_entries format=duration -of csv=p=0 "$out" > /dev/null 2>&1 \
                 && say "$1" 0 "$out" || say "$1" 1 "$out is not a valid video" ;;
        pdf)   [ "$(head -c 4 "$out")" = "%PDF" ] && say "$1" 0 "$out" || say "$1" 1 "$out is not a PDF" ;;
      esac
      cp "$out" "/out/$1.${out##*.}" 2>/dev/null
    }
    run cleanroom_tarball_ffmpeg "$K" ffmpeg "convert clip.mp4 to mkv" video
    run cleanroom_tarball_documents "$K" documents "rotate sample.pdf 90 degrees" pdf
    cp /in/knaif-1.2.0-linux-x86_64.AppImage /opt/knaif.AppImage && chmod +x /opt/knaif.AppImage
    A="/opt/knaif.AppImage --appimage-extract-and-run"
    run cleanroom_appimage_ffmpeg "$A" ffmpeg "convert clip.mp4 to webm" video
    run cleanroom_appimage_documents "$A" documents "turn all pages of sample.pdf 90 degrees" pdf
  ' > "$R/cleanroom.log" 2>&1
cat "$R/cleanroom/checks.txt" >> "$R/results.txt" 2>/dev/null
cat "$R/results.txt"
n=$(grep -c "^PASS" "$R/results.txt"); f=$(grep -c "^FAIL" "$R/results.txt")
# 2 floor checks + 8 clean-room checks; fewer lines means a step never reported, which fails too.
if [ "$f" -eq 0 ] && [ "$n" -eq 10 ]; then echo "T16 PASS ($n checks)"; exit 0; fi
echo "T16 FAIL ($n passed, $f failed; 10 expected)"; exit 1
