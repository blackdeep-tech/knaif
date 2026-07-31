#!/usr/bin/env bash
# Build the published Linux artifacts inside the floor-pinned container.
#
# Produces dist/knaif-<ver>-linux-x64.tar.gz and dist/knaif-<ver>-linux-x86_64.AppImage with a
# glibc 2.35 floor, regardless of what the host runs. See installers/linux/Dockerfile for why
# that matters and what is pinned.
#
# Usage:
#   installers/linux/build-in-container.sh                 # release: build HEAD's commit
#   installers/linux/build-in-container.sh --rev=v1.1.0    # release: build that tag
#   installers/linux/build-in-container.sh --dev           # development: mount the worktree
#   installers/linux/build-in-container.sh --kind=cpu      # default kind is vulkan
#   installers/linux/build-in-container.sh --rebuild-image # force a docker build first
#   installers/linux/build-in-container.sh --clean         # wipe THIS kind's build cache
#
# RELEASE MODE CHECKS OUT A COMMIT; IT DOES NOT MOUNT. The working tree is mounted READ-ONLY at
# /repo and the container fetches from it into a clean /src. Three things fall out of that, all of
# which have already bitten this project:
#   - no uncommitted dirt can reach a published artifact (a stale dist/staging once produced an
#     installer naming the wrong publisher);
#   - a Windows checkout mounts over 9p with no `metadata` option, so every file reads 0777 and
#     those modes would be baked into the tarball. A checkout re-creates modes from git;
#   - .gitattributes declares `text eol=lf`, so the checkout gets LF even when the Windows working
#     copy has CRLF — and Linux refuses to run a script whose shebang ends in a carriage return.
# Fetching from the local /repo rather than from GitHub keeps this offline.
#
# --dev mounts the worktree read-write instead, for iterating on packaging without committing.
# Do not publish anything built that way.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

IMAGE=knaif-linux-build
DEV=0
KIND=vulkan
REV=""
REBUILD_IMAGE=0
CLEAN=0
for a in "$@"; do
  case "$a" in
    --dev)            DEV=1 ;;
    --rev=*|--tag=*)  REV="${a#*=}" ;;
    --kind=*)         KIND="${a#--kind=}" ;;
    --rebuild-image)  REBUILD_IMAGE=1 ;;
    --clean)          CLEAN=1 ;;
    *) echo "usage: build-in-container.sh [--dev] [--rev=<rev>] [--kind=cpu|vulkan|cuda] [--rebuild-image] [--clean]" >&2; exit 1 ;;
  esac
done
case "$KIND" in
  cpu|vulkan) ;;
  # The CUDA payload builds in its OWN image: the release image deliberately carries no toolkit
  # (3-5 GB for an artifact it does not produce), and the payload needs no Vulkan. The two images
  # pin the same Ubuntu release, apt snapshot and Rust version, because the payload is dlopen'ed by
  # an exe from the other image — a higher floor here would fail on exactly the older systems the
  # release image exists to serve, and nowhere else.
  cuda) IMAGE=knaif-linux-cuda-build; DOCKERFILE=Dockerfile.cuda ;;
  *) echo "ERROR: --kind must be cpu, vulkan or cuda." >&2; exit 1 ;;
esac
DOCKERFILE="${DOCKERFILE:-Dockerfile}"

command -v docker >/dev/null 2>&1 || {
  echo "ERROR: docker not found. This is the only step that needs it — every other build path" >&2
  echo "       (cargo build, just check, installers/package.sh) works natively without it." >&2
  exit 1
}
docker version >/dev/null 2>&1 || {
  echo "ERROR: the docker daemon is not reachable. Start Docker Desktop (or dockerd) and retry." >&2
  exit 1
}

if [ "$REBUILD_IMAGE" -eq 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building $IMAGE (first run takes a few minutes; afterwards it is cached)…"
  docker build -t "$IMAGE" -f "$ROOT/installers/linux/$DOCKERFILE" "$ROOT/installers/linux"
fi

# Report the CUDA base image's digest so pinning it is a copy-paste rather than a registry lookup.
# Dockerfile.cuda still names a TAG, which is precisely the drift everything else here pins against;
# it is left that way only because the digest has to be read from a registry, which a Dockerfile
# cannot do. Nagging once per build is deliberate — a silent unpinned base is how two "identical"
# payload builds come to differ.
if [ "$KIND" = cuda ]; then
  cuda_base="$(awk -F= '/^ARG CUDA_BASE=/{print $2}' "$ROOT/installers/linux/Dockerfile.cuda")"
  case "$cuda_base" in
    *@sha256:*) ;;
    *)
      digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$cuda_base" 2>/dev/null || true)"
      echo
      echo "NOTE: the CUDA base image is pinned by TAG, not by digest — two builds of this image can"
      echo "      silently differ. Before publishing a payload, pin it in Dockerfile.cuda:"
      if [ -n "$digest" ]; then
        echo "        ARG CUDA_BASE=$digest"
      else
        echo "        ARG CUDA_BASE=<repo>@sha256:<digest>   (docker image inspect $cuda_base)"
      fi
      echo
      ;;
  esac
fi

mkdir -p dist

# --- Git Bash / MSYS path mangling -------------------------------------------------------------
# MSYS rewrites arguments that look like absolute POSIX paths into Windows ones before exec. That
# is helpful for the HOST side of a -v mount and catastrophic for the CONTAINER side: `-w /src`
# becomes `-w C:/Program Files/Git/src` and docker rejects it. Disabling conversion wholesale is
# not the fix either, because then the host side stays as /c/Work/... which Docker Desktop cannot
# resolve.
#
# So: convert host paths EXPLICITLY with `cygpath -m` (mixed form, C:/... — docker accepts it and
# it contains no backslashes to re-quote), then switch conversion off so container paths survive
# verbatim. Both variables are inert on Linux and macOS.
hostpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}
HOST_ROOT="$(hostpath "$ROOT")"
HOST_DIST="$(hostpath "$ROOT/dist")"
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

# Named volumes, not bind mounts: llama.cpp takes many minutes to compile and none of that should
# depend on the host filesystem's semantics. The target volume is mounted AT /src/target so
# package.sh finds its usual relative path — it looks for target/release/knaif and
# target/release/build/llama-cpp-sys-2-*/out, so CARGO_TARGET_DIR would silently break it.
#
# ONE TARGET VOLUME PER KIND, and that is not tidiness. Each feature set gets its own
# llama-cpp-sys-2 out dir, but every one of them HARD-LINKS its libraries into the same
# target/release/. llama-cpp-sys-2's build.rs does `if !dst.exists() { hard_link(..).unwrap() }`,
# and `exists()` follows symlinks — so a stale SONAME symlink left by a different kind reads as
# absent while still occupying the name, and the hard_link panics with EEXIST. Sharing one volume
# therefore makes `--kind=cpu` after `--kind=vulkan` fail on a warm cache. Separate volumes make
# that impossible while keeping each kind's cache warm. This is the containerised form of the
# hazard package.sh already warns about: package each kind immediately after its own build.
TARGET_VOL="knaif-target-$KIND"
docker volume create knaif-cargo   >/dev/null
docker volume create "$TARGET_VOL" >/dev/null

if [ "$CLEAN" -eq 1 ]; then
  echo "Removing $TARGET_VOL (next build recompiles llama.cpp from scratch)…"
  docker volume rm "$TARGET_VOL" >/dev/null
  docker volume create "$TARGET_VOL" >/dev/null
fi

# Reproduce host ownership on the artifacts. Without this a Linux maintainer gets root-owned files
# in dist/; on Docker Desktop for Windows it is a harmless no-op.
HOST_UID="$(id -u 2>/dev/null || echo 0)"
HOST_GID="$(id -g 2>/dev/null || echo 0)"

# --- Build parallelism must be capped by MEMORY, not by CPU count ------------------------------
# llama.cpp's Vulkan backend compiles generated shader translation units (mul_mm.comp.cpp and
# friends) that need 1-2 GB EACH in cc1plus. Cargo defaults NUM_JOBS to the CPU count, the cmake
# crate passes that straight through as --parallel, and 16 jobs against a 7 GB Docker VM is a
# guaranteed OOM: the kernel kills cc1plus and ninja reports
#   c++: fatal error: Killed signal terminated program cc1plus
# which looks like a compiler bug and is not one.
#
# THIS ONLY BITES A CLEAN BUILD. With a warm cache few TUs compile at once, so a machine can build
# successfully for weeks and then fail the moment someone builds a release from scratch — which is
# exactly when it matters. Cap at roughly one job per 2 GB.
MEM_BYTES="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
NCPU="$(docker info --format '{{.NCPU}}' 2>/dev/null || echo 4)"
if [ -n "${JOBS:-}" ]; then
  :
elif [ "$MEM_BYTES" -gt 0 ]; then
  JOBS=$(( MEM_BYTES / 2147483648 ))
  [ "$JOBS" -lt 1 ] && JOBS=1
  [ "$JOBS" -gt "$NCPU" ] && JOBS="$NCPU"
else
  JOBS=2
fi
if [ "$JOBS" -lt "$NCPU" ]; then
  echo "Using $JOBS build job(s): the Docker VM has $(( MEM_BYTES / 1073741824 )) GB and the Vulkan"
  echo "  shader units need ~2 GB each. Raise the VM's memory (Docker Desktop → Resources, or"
  echo "  %USERPROFILE%\\.wslconfig) for a faster build, or set JOBS=<n> to override."
fi

if [ "$DEV" -eq 1 ]; then
  echo "DEV MODE: mounting the working tree. Do not publish artifacts built this way."
  SRC_MOUNT=(-v "$HOST_ROOT:/src")
  PREPARE='echo "dev mode: using the mounted worktree"'
else
  [ -n "$REV" ] || REV="$(git rev-parse HEAD)"
  git rev-parse --verify --quiet "${REV}^{commit}" >/dev/null || {
    echo "ERROR: '$REV' is not a commit in this repository." >&2
    exit 1
  }
  RESOLVED="$(git rev-parse "${REV}^{commit}")"
  echo "RELEASE MODE: checking out $REV ($RESOLVED) into a clean tree inside the container."
  SRC_MOUNT=(-v "$HOST_ROOT:/repo:ro")
  # `git clone` refuses a non-empty directory, and /src already contains the mounted target/
  # volume — so init + fetch + checkout rather than clone. target/ is gitignored, so nothing
  # conflicts. The result is byte-identical to a clone of that revision.
  PREPARE="git init -q /src \
    && git -C /src remote add origin /repo \
    && git -C /src fetch -q origin '+refs/heads/*:refs/remotes/origin/*' '+refs/tags/*:refs/tags/*' \
    && git -C /src checkout -q --force '$RESOLVED' \
    && git -C /src --no-pager log -1 --oneline"
fi

docker run --rm -i \
  "${SRC_MOUNT[@]}" \
  -v "$HOST_DIST:/out" \
  -v knaif-cargo:/usr/local/cargo/registry \
  -v "$TARGET_VOL:/src/target" \
  -e CMAKE_GENERATOR=Ninja \
  -e CARGO_BUILD_JOBS="$JOBS" \
  -e CUDAARCHS="${CUDAARCHS:-}" \
  -e KNAIF_CUDA_DEV_ARCHS="${KNAIF_CUDA_DEV_ARCHS:-}" \
  -e KIND="$KIND" -e DEV="$DEV" -e HOST_UID="$HOST_UID" -e HOST_GID="$HOST_GID" \
  -w /src \
  "$IMAGE" bash -euo pipefail -c "
    git config --global --add safe.directory '*'
    $PREPARE
    cd /src

    installers/package.sh --kind=\"\$KIND\"

    # The CUDA payload is not a runnable tree, so none of the artifact checks below apply to it:
    # there is no exe to smoke, no skills/ to resolve, and no AppImage. What it DOES need is the
    # floor audit — the whole reason this runs in a container — because a backend that fails to
    # dlopen on an older system presents to the user as \"CUDA didn't work\" rather than as a
    # missing symbol. check_elf_deps.py reads the ELF headers, so unlike running the payload it
    # gives the same answer here as on a user's machine.
    if [ \"\$KIND\" = cuda ]; then
      staged=\"\$(ls -d dist/staging/knaif-*-linux-x64-cuda-backend 2>/dev/null | head -1)\"
      [ -n \"\$staged\" ] || { echo \"ERROR: no staged CUDA payload\" >&2; exit 1; }
      echo \"staged payload: \$staged\"
      python3 scripts/check_elf_deps.py \"\$staged\"
      if [ \"\$DEV\" != 1 ]; then
        mkdir -p \"/out/\$(basename \"\$staged\")\"
        cp -f \"\$staged\"/* \"/out/\$(basename \"\$staged\")/\"
        cp -f dist/*.manifest-fragment.yaml /out/ 2>/dev/null || true
      fi
      chown -R \"\$HOST_UID:\$HOST_GID\" \"/out/\$(basename \"\$staged\")\" 2>/dev/null || true
      exit 0
    fi

    # Mirror package.sh's SUFFIX rule: vulkan is THE release artifact and gets the plain name;
    # every other kind carries its kind as a suffix. Globbing 'knaif-*-linux-x64' matches ONLY the
    # unsuffixed tree, so with --kind=cpu it would silently pick up a leftover vulkan staging dir
    # and build a vulkan AppImage labelled as this run's output. Derive the path from \$KIND.
    if [ \"\$KIND\" = vulkan ]; then sfx=; else sfx=\"-\$KIND\"; fi
    staged=\"\$(ls -d dist/staging/knaif-*-linux-x64\$sfx 2>/dev/null | head -1)\"
    [ -n \"\$staged\" ] || { echo \"ERROR: no staged tree for kind=\$KIND\" >&2; exit 1; }
    echo \"staged tree: \$staged\"

    installers/smoke.sh \"dist/knaif-\"*\"-linux-x64\$sfx.tar.gz\"

    # The AppImage is built for the RELEASE kind only. 'cpu' is a build kind, not a release
    # artifact (see docs/RELEASE.md §1), and build-appimage.sh writes an unsuffixed output name —
    # so a cpu run would overwrite the vulkan AppImage with something that must never be published.
    if [ \"\$KIND\" = vulkan ]; then
      installers/linux/build-appimage.sh \"\$staged\"
      # Smoke BOTH artifacts: the AppImage comes from a separate script and has drifted from the
      # tarball before — it shipped without NOTICE for weeks after package.sh was fixed.
      installers/smoke.sh dist/knaif-*-linux-x86_64.AppImage
    else
      echo \"skipping the AppImage: kind=\$KIND is a build kind, not a release artifact\"
    fi

    # In --dev mode /src IS the host checkout, so /src/dist and /out are literally the same
    # directory and cp would fail with 'are the same file'. Only the release path needs to move
    # artifacts out of the container's private tree.
    if [ \"\$DEV\" != 1 ]; then
      cp -f dist/knaif-*-linux-* /out/
    fi
    chown \"\$HOST_UID:\$HOST_GID\" /out/knaif-*-linux-* 2>/dev/null || true
  "

echo
echo "Artifacts in dist/:"
ls -lh dist/knaif-*-linux-* 2>/dev/null | awk '{printf "  %-52s %s\n", $9, $5}'
echo
echo "NOT YET VERIFIED: the floor. Prove it in BOTH directions (docs/RELEASE.md) — the artifact"
echo "must RUN on the floor image and FAIL below it. Tested one way only, a floor is decoration."
