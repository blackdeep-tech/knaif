#!/usr/bin/env bash
# Assert that a per-kind cargo profile really gave this build its own directory.
#
# The whole point of the `release-<kind>` profiles is that llama-cpp-sys-2's build script stages
# its shared libraries into `OUT_DIR.ancestors().nth(3)` — i.e. `target/<profile>/` — so changing
# the profile relocates the staged libs with no patch to the crate and no CARGO_TARGET_DIR. This
# script is what checks that claim, rather than trusting it.
#
# See docs/plans/2026-09-21-per-backend-build-profiles.md (T1 verifies, T7 rehearses).
#
#   scripts/verify_build_profile.sh cpu
#   scripts/verify_build_profile.sh cuda
#
# Exits non-zero with a named reason on the first failed assertion.

set -u

KIND="${1:-}"
case "$KIND" in
  base|cpu|vulkan|cuda) ;;
  *)
    echo "usage: $0 <base|cpu|vulkan|cuda>" >&2
    exit 2
    ;;
esac

PROFILE="release-$KIND"
DIR="target/$PROFILE"

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) EXE=knaif.exe; LIB_EXT=dll ;;
  Darwin)                   EXE=knaif;     LIB_EXT=dylib ;;
  *)                        EXE=knaif;     LIB_EXT=so ;;
esac

fails=0
pass() { echo "  ok    $1"; }
fail() { echo "  FAIL  $1" >&2; fails=$((fails + 1)); }

echo "verifying $PROFILE ($KIND)"

# 1. The profile got its own directory at all.
if [ -d "$DIR" ]; then
  pass "$DIR exists"
else
  fail "$DIR does not exist — was this kind built with --profile $PROFILE?"
  echo "aborting: nothing else can be checked" >&2
  exit 1
fi

# 2. The binary landed in it, not in target/release/.
if [ -f "$DIR/$EXE" ]; then
  pass "$DIR/$EXE"
else
  fail "$DIR/$EXE missing"
fi

# 3. The build script's OUT_DIR moved with the profile. This is the `nth(3)` claim: if the out dir
#    is under the profile directory, so is everything the build script stages from it.
out_dir=""
for d in $(ls -dt "$DIR"/build/llama-cpp-sys-2-*/out 2>/dev/null); do
  [ -d "$d" ] || continue
  out_dir="$d"
  break
done

if [ "$KIND" = "base" ]; then
  # A base build has no llama.cpp in it, so there is nothing to stage and nothing to collide.
  if [ -z "$out_dir" ]; then
    pass "no llama-cpp-sys-2 out dir (expected for a base build)"
  else
    fail "a base build should not have built llama-cpp-sys-2, but found $out_dir"
  fi
else
  if [ -n "$out_dir" ]; then
    pass "llama-cpp-sys-2 out dir is under the profile: $out_dir"
  else
    fail "no llama-cpp-sys-2 out dir under $DIR/build — OUT_DIR did not move with the profile"
  fi

  # 4. The core libs are staged beside the binary. The exe is NOT self-contained for any
  #    dynamic-backends kind (installers/package.sh:20-21), so this is what makes the directory
  #    runnable rather than merely populated.
  for lib in llama ggml ggml-base llama-common; do
    if ls "$DIR"/*"$lib"."$LIB_EXT" >/dev/null 2>&1; then
      pass "staged $lib.$LIB_EXT"
    else
      fail "no $lib.$LIB_EXT staged in $DIR"
    fi
  done

  # 5. Identify the build by what it emitted, never by its name — the same rule package.sh's
  #    out_dir() follows after newest-wins silently staged ggml-vulkan into a CPU artifact
  #    (installers/package.sh:191-195).
  backends="$out_dir/backends"
  if [ -n "$out_dir" ] && [ -d "$backends" ]; then
    has_vulkan=no; has_cuda=no
    ls "$backends"/*ggml-vulkan.* >/dev/null 2>&1 && has_vulkan=yes
    ls "$backends"/*ggml-cuda.*   >/dev/null 2>&1 && has_cuda=yes
    case "$KIND" in
      cpu)
        if [ "$has_vulkan" = no ] && [ "$has_cuda" = no ]; then
          pass "backends/ carries no GPU backend, as a cpu build should"
        else
          fail "a cpu build emitted a GPU backend (vulkan=$has_vulkan cuda=$has_cuda)"
        fi
        ;;
      vulkan)
        [ "$has_vulkan" = yes ] && pass "backends/ carries ggml-vulkan" \
                                || fail "a vulkan build emitted no ggml-vulkan"
        ;;
      cuda)
        [ "$has_cuda" = yes ] && pass "backends/ carries ggml-cuda" \
                              || fail "a cuda build emitted no ggml-cuda"
        ;;
    esac
  elif [ -n "$out_dir" ]; then
    # A static (non-dynamic-backends) build emits no loadable backends. Report it rather than
    # guessing which case this is.
    echo "  note  no backends/ in $out_dir — a static build, so the kind cannot be read from it"
  fi
fi

# 6. The shared directory was not the destination. The defect this whole change exists to remove
#    is two kinds writing the same path, so say plainly whether that path was involved.
if [ -e "target/release/$EXE" ]; then
  echo "  note  target/release/$EXE also exists — it belongs to whatever was built without a"
  echo "        --profile flag, and this build did not touch it"
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "PASS  $PROFILE has its own directory, binary and staged libraries"
  exit 0
fi
echo "FAIL  $fails assertion(s) failed for $PROFILE" >&2
exit 1
