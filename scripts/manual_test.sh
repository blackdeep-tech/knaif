#!/usr/bin/env bash
# Shell helpers for testing knaif by hand: a throwaway folder of fixture copies, and one command
# per runtime, in bash (Git Bash on Windows):
#
#   just manual-test                fresh folder, then a bash in sandbox/manual/ffmpeg with the helpers
#                                   loaded, from any shell (`exit` returns to the shell you came from)
#   source scripts/manual_test.sh   the helpers alone, in the current bash
#
#   k <skill> "…"        run the native build in target/release-<kind>/:  k ffmpeg "make clip.mov smaller"
#   knative <skill> "…"  run the installed knaif (the release on PATH), to compare with k
#   kpy <skill> "…"      run the Python CLI, for real
#   kbuild [kind]        build target/release-<kind>/ (cuda, vulkan, cpu; default $KNAIF_KIND), point k at it
#   kreset               wipe sandbox/manual/ and copy the fixtures back, staying in ffmpeg/ or documents/
#
# The runners take the CLI's own flags after the skill, e.g. `k ffmpeg --model knaif-qwen3-1.7b-v2 "…"`.
# Run as a script (what manual-test does), it resets the folder and opens that bash.
#
# KNAIF_KIND picks the binary (default cuda). KNAIF_BUILD_JOBS caps the build's parallel jobs
# (default 4: a full-width CUDA build runs the CPU hot).

KNAIF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KNAIF_KIND="${KNAIF_KIND:-cuda}"
KNAIF_MANUAL="$KNAIF_ROOT/sandbox/manual"

_k_exe() { case "$(uname -s)" in MINGW* | MSYS* | CYGWIN*) echo "$1.exe" ;; *) echo "$1" ;; esac; }
_k_bin() { _k_exe "$KNAIF_ROOT/target/release-$KNAIF_KIND/knaif"; }
_k_py() {
  if [ -d "$KNAIF_ROOT/.venv/Scripts" ]; then _k_exe "$KNAIF_ROOT/.venv/Scripts/knaif-cli"
  else echo "$KNAIF_ROOT/.venv/bin/knaif-cli"; fi
}

kbuild() {
  [ -n "${1:-}" ] && KNAIF_KIND="$1"
  CARGO_BUILD_JOBS="${KNAIF_BUILD_JOBS:-4}" bash "$KNAIF_ROOT/scripts/build_native_kind.sh" "$KNAIF_KIND" \
    && echo "k runs $(_k_bin)"
}

k() {
  local bin; bin="$(_k_bin)"
  [ -x "$bin" ] || { echo "no binary at $bin: run kbuild $KNAIF_KIND first" >&2; return 1; }
  "$bin" run "$@"
}

knative() {
  local bin; bin="$(command -v knaif)"
  [ -n "$bin" ] || { echo "no knaif on PATH: install a release first" >&2; return 1; }
  case "$bin" in "$KNAIF_ROOT"/*) echo "knaif on PATH is this checkout's build ($bin), not a release" >&2; return 1 ;; esac
  "$bin" run "$@"
}

# -D: the Python CLI previews by default; a manual test wants the files.
kpy() {
  local py; py="$(_k_py)"
  [ -x "$py" ] || { echo "no Python CLI at $py: run just install first" >&2; return 1; }
  "$py" run -D "$@"
}

kreset() {
  local f="$KNAIF_ROOT/sandbox/fixtures" here
  here="$(basename "$PWD")"
  for s in ffmpeg documents; do
    if [ ! -d "$f/$s" ]; then
      echo "no fixtures for $s: generating them" >&2
      (cd "$KNAIF_ROOT" && uv run --no-sync python -m knaif.evalsuite fixtures regen --skill "$s" >/dev/null) \
        || { echo "fixtures regen failed for $s" >&2; return 1; }
    fi
  done
  cd "$KNAIF_ROOT" || return
  rm -rf "$KNAIF_MANUAL" && mkdir -p "$KNAIF_MANUAL/ffmpeg" "$KNAIF_MANUAL/documents"
  cp -r "$f"/ffmpeg/. "$KNAIF_MANUAL/ffmpeg/"
  # The clips already squeezed too small to shrink, beside the others (where the fixtures have them).
  for t in tiny_clip/clip_tiny.mp4 tiny_mov/clip_tiny.mov; do
    [ -f "$f/ffmpeg/$t" ] && cp "$f/ffmpeg/$t" "$KNAIF_MANUAL/ffmpeg/"
  done
  cp -r "$f"/documents/. "$KNAIF_MANUAL/documents/"
  case "$here" in ffmpeg | documents) cd "$KNAIF_MANUAL/$here" ;; *) cd "$KNAIF_MANUAL/ffmpeg" ;; esac
  echo "reset: sandbox/manual/$(basename "$PWD")"
}

# Loaded as the --rcfile of the shell the script opens: the user's own setup first, then the folder.
if [ "${KNAIF_MANUAL_SHELL:-}" = 1 ] && [ "${BASH_SOURCE[0]}" != "$0" ]; then
  unset KNAIF_MANUAL_SHELL
  [ -f ~/.bashrc ] && . ~/.bashrc
  cd "$KNAIF_MANUAL/ffmpeg" || true
  PS1='\[\e[32m\](knaif manual)\[\e[0m\] \w\$ '
fi

# Run, not sourced: the helpers are bash functions, which no other shell can load, so open a bash.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  kreset >/dev/null || exit 1
  echo "ready: sandbox/manual/ (ffmpeg/, documents/)"
  [ -x "$(_k_bin)" ] || echo "no $KNAIF_KIND binary yet: kbuild $KNAIF_KIND builds it"
  echo "k, knative, kpy, kbuild, kreset are loaded; exit to leave"
  KNAIF_MANUAL_SHELL=1 exec "$BASH" --rcfile "$KNAIF_ROOT/scripts/manual_test.sh" -i
fi
