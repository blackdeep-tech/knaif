#!/usr/bin/env bash
# Prove a Linux artifact's runtime floor — in BOTH directions.
#
# A floor tested one way is decoration. "It ran on Ubuntu 22.04" is equally consistent with a
# 2.35 floor and a 2.17 floor, so on its own it says nothing about where support ENDS. This runs
# the artifact at the claimed floor (must PASS) and below it (must FAIL), which is what turns the
# number in the docs into a measurement.
#
# Complements scripts/check_elf_deps.py: that reads what the binary REQUIRES, this checks what a
# real loader DOES with it. The static audit is the more informative of the two — it was what
# revealed that glibc is not knaif's binding constraint, libstdc++ is — but only an execution can
# catch a requirement neither of us thought to look for.
#
# EVERYTHING HAPPENS INSIDE THE CONTAINER, including unpacking. The tarball carries SONAME
# symlinks that Windows cannot create without privileges, and the AppImage needs a Linux loader;
# extracting on the host would make this script Linux-only and would test the host's filesystem
# semantics rather than the artifact's.
#
# Usage:
#   installers/linux/check-floor.sh dist/knaif-1.1.0-linux-x64.tar.gz
#   installers/linux/check-floor.sh dist/knaif-1.1.0-linux-x86_64.AppImage
#   FLOOR_IMAGE=ubuntu:22.04 BELOW_IMAGE=ubuntu:20.04 installers/linux/check-floor.sh <artifact>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

[ $# -eq 1 ] || { echo "usage: check-floor.sh <artifact.tar.gz|artifact.AppImage>" >&2; exit 1; }
ARTIFACT="$1"
[ -f "$ARTIFACT" ] || { echo "ERROR: no such artifact: $ARTIFACT" >&2; exit 1; }
ARTIFACT="$(cd "$(dirname "$ARTIFACT")" && pwd)/$(basename "$ARTIFACT")"

# The claimed floor, and a release below it. Keep these in step with the support table in
# site/docs/index.md — if this script and the docs disagree, one of them is lying to users.
FLOOR_IMAGE="${FLOOR_IMAGE:-ubuntu:22.04}"
BELOW_IMAGE="${BELOW_IMAGE:-ubuntu:20.04}"

command -v docker >/dev/null 2>&1 && docker version >/dev/null 2>&1 || {
  echo "ERROR: docker is required (it provides the clean floor images)." >&2
  exit 1
}

# Git Bash rewrites POSIX-looking arguments into Windows paths, which corrupts the CONTAINER side
# of a -v mount. Convert the HOST side explicitly, then switch conversion off. Inert elsewhere.
hostpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}
HOST_ARTIFACT="$(hostpath "$ARTIFACT")"
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

case "$ARTIFACT" in
  *.tar.gz|*.tgz)
    UNPACK='mkdir -p /x && tar xzf /artifact -C /x --strip-components=1'
    RUN='/x/bin/knaif'
    ;;
  *.AppImage)
    # Extracted, not executed: running an .AppImage as a single file needs FUSE, which these
    # images do not have. This tests the PAYLOAD's floor, not the AppRun wrapper — run the
    # .AppImage itself on a real desktop at least once per release as well.
    UNPACK='cp /artifact /tmp/a.AppImage && chmod +x /tmp/a.AppImage && cd /tmp && ./a.AppImage --appimage-extract >/dev/null && mkdir -p /x && mv /tmp/squashfs-root /x/r'
    RUN='/x/r/usr/bin/knaif'
    ;;
  *) echo "ERROR: expected a .tar.gz or .AppImage, got: $ARTIFACT" >&2; exit 1 ;;
esac

# Pull first, separately: otherwise docker's "Unable to find image locally" progress output is
# what gets captured, and a pull failure would be indistinguishable from the artifact refusing to
# run — a check that passes for the wrong reason.
for img in "$FLOOR_IMAGE" "$BELOW_IMAGE"; do
  docker image inspect "$img" >/dev/null 2>&1 || {
    echo "pulling $img…"
    docker pull -q "$img" >/dev/null || { echo "ERROR: cannot pull $img" >&2; exit 1; }
  }
done

probe() {  # $1 = image. Prints the image's glibc, then the CLI's own output; returns its status.
  docker run --rm -v "$HOST_ARTIFACT:/artifact:ro" "$1" sh -c \
    "ldd --version 2>/dev/null | head -1; $UNPACK >/dev/null 2>&1 || exit 90; $RUN skills list 2>&1 >/dev/null"
}

echo "artifact: $(basename "$ARTIFACT")"
echo

echo "1. MUST PASS at the claimed floor ($FLOOR_IMAGE)"
out="$(probe "$FLOOR_IMAGE" 2>&1)" && status=0 || status=$?
echo "   ${out%%$'\n'*}"
if [ "$status" -eq 0 ]; then
  echo "   ok  runs at the floor"
elif [ "$status" -eq 90 ]; then
  echo "   FAIL: could not unpack the artifact in $FLOOR_IMAGE (not a floor problem)." >&2
  exit 1
else
  echo "   FAIL: the artifact does not run at its own claimed floor (exit $status)." >&2
  echo "   Either the floor is wrong or the build environment drifted above it." >&2
  exit 1
fi
echo

echo "2. MUST FAIL below it ($BELOW_IMAGE)"
out="$(probe "$BELOW_IMAGE" 2>&1)" && status=0 || status=$?
echo "   ${out%%$'\n'*}"
if [ "$status" -eq 0 ]; then
  echo "   FAIL: it ALSO runs below the claimed floor." >&2
  echo "   This is a defect in the CLAIM, not in the artifact: the real floor is LOWER than" >&2
  echo "   documented, so users are being told a narrower range than they actually have." >&2
  echo "   Lower FLOOR_IMAGE until this step genuinely fails, then widen the support table." >&2
  exit 1
fi

# It failed — but a check that accepts ANY failure is a check that passes when docker is broken,
# when the mount is wrong, or when the artifact is corrupt. Require the failure to be the one we
# are actually asserting: the dynamic loader rejecting a symbol version.
if printf '%s' "$out" | grep -qE "version \`(GLIBC|GLIBCXX|CXXABI|GOMP)_[0-9.]+' not found"; then
  echo "   ok  refuses below the floor, for the right reason:"
  printf '%s\n' "$out" | grep -oE "version \`[A-Z_]+_[0-9.]+' not found" | sort -u | sed 's/^/         /'
else
  echo "   INCONCLUSIVE: it failed below the floor, but NOT with a symbol-version error." >&2
  echo "   That is not evidence about the floor — it may be a broken mount, a corrupt" >&2
  echo "   artifact, or a missing image. Output was:" >&2
  printf '%s\n' "$out" | sed 's/^/     /' >&2
  exit 1
fi
echo
echo "PASS: floor confirmed in both directions ($FLOOR_IMAGE runs, $BELOW_IMAGE does not)"
