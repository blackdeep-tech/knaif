#!/usr/bin/env bash
# Rehearse `knaif backend install` end to end WITHOUT publishing anything.
#
# WHY THIS EXISTS. Backend publishing is a chicken-and-egg: `backend install` wants live URLs, and
# nobody wants to upload ~668 MB to find out the install path is wrong. This breaks it with a local
# HTTP server, so the whole fetch -> verify -> stage -> swap -> receipt path runs against the REAL
# staged payload and the REAL per-file checksums from package.sh's manifest fragment, with zero
# uploads. Run it every release, before RELEASE.md §7.
#
# The unit tests in knaif-models cover the same branches; what they cannot cover is the actual
# artifact — a fragment whose checksums drifted from the staged bytes, a manifest key that does not
# match the platform the binary computes, or a payload file package.sh stages but never declares.
# Those only show up here.
#
#   installers/rehearse-backend-install.sh                        # auto-detect the newest staging dir
#   installers/rehearse-backend-install.sh --platform linux-x64
#   installers/rehearse-backend-install.sh --keep                 # leave the install for a manual run
#
# Requires: python3 (the server + the splice), curl, and a knaif binary. Point at a packaged one with
# --exe; the default prefers the staged artifact over target/release, because what the artifact does
# is the question. NOTHING here touches ~/.knaif — every path is a scratch dir under $TMPDIR.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
PLATFORM=""
STAGE=""
FRAGMENT=""
EXE=""
KEEP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --platform=*) PLATFORM="${1#*=}" ;;
    --platform)   PLATFORM="$2"; shift ;;
    --stage=*)    STAGE="${1#*=}" ;;
    --fragment=*) FRAGMENT="${1#*=}" ;;
    --exe=*)      EXE="${1#*=}" ;;
    --port=*)     PORT="${1#*=}" ;;
    --keep)       KEEP=1 ;;
    -h|--help)    sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "usage: rehearse-backend-install.sh [--platform K] [--stage DIR] [--fragment F] [--exe P] [--port N] [--keep]" >&2; exit 1 ;;
  esac
  shift
done

case "$(uname -s)" in
  Linux*)  HOST_PLATFORM=linux-x64 ;;
  MINGW*|MSYS*|CYGWIN*) HOST_PLATFORM=windows-x64 ;;
  Darwin*) HOST_PLATFORM=macos-arm64 ;;
  *) HOST_PLATFORM=unknown ;;
esac
: "${PLATFORM:=$HOST_PLATFORM}"

# The payload tree and the fragment are both produced by `package.sh --kind=cuda`. Pick the newest
# pair rather than guessing a version, and fail loudly rather than rehearsing against a stale build.
[ -n "$STAGE" ]    || STAGE="$(ls -dt "$REPO"/dist/staging/*-"$PLATFORM"-*-backend 2>/dev/null | head -1 || true)"
[ -n "$FRAGMENT" ] || FRAGMENT="$(ls -t "$REPO"/dist/*-"$PLATFORM"-*.manifest-fragment.yaml 2>/dev/null | head -1 || true)"
[ -n "$STAGE" ] && [ -d "$STAGE" ] || {
  echo "ERROR: no staged payload for $PLATFORM under dist/staging/." >&2
  echo "       Build one first:  installers/package.sh --no-build --kind=cuda" >&2
  exit 1
}
[ -n "$FRAGMENT" ] && [ -f "$FRAGMENT" ] || {
  echo "ERROR: no manifest fragment for $PLATFORM in dist/. package.sh writes it beside the payload." >&2
  exit 1
}
# A DEVARCH payload is a packaging aid, not a release payload. Rehearsing with one is fine and is
# the cheap loop this exists for — say so, so a green run is not mistaken for release sign-off.
case "$STAGE" in *-DEVARCH*) echo "NOTE: this is a -DEVARCH payload — good enough to rehearse the install path, NOT a release payload." ;; esac

# The binary must come from the SAME release as the payload, not merely the same platform. A plain
# glob sorts `knaif-1.0.1-windows-x64` ahead of `knaif-1.1.0-windows-x64`, so with both staged it
# silently rehearsed the new payload against the previous release's exe — which is exactly the
# mismatch BackendStore's receipt exists to REFUSE, so the run fails for a reason that has nothing to
# do with the release being rehearsed. STAGE is `<name>-<platform>-cuda-backend[-DEVARCH]`; the app
# tree beside it is the same name without that suffix, which pins the version exactly.
if [ -z "$EXE" ]; then
  for c in "${STAGE%-cuda-backend*}/bin/knaif" "${STAGE%-cuda-backend*}/bin/knaif.exe" \
           "$REPO"/target/release/knaif "$REPO"/target/release/knaif.exe; do
    [ -x "$c" ] && EXE="$c" && break
  done
fi
[ -n "$EXE" ] && [ -x "$EXE" ] || { echo "ERROR: no knaif binary found; pass --exe PATH." >&2; exit 1; }

# A python with PyYAML, for the splice and the server — and never one from the OTHER operating
# system. The probe is "can it import yaml", never "does the name exist": on Windows `python3` is
# usually the Microsoft Store stub, which resolves through `command -v` and then refuses to run.
#
# The repo venv is offered first because it is the one guaranteed to have PyYAML, but which venv
# layout exists depends on where the checkout was made, not on where this script is running. A
# Windows checkout mounted into WSL (which is exactly how this repo builds Linux artifacts) has
# `.venv/Scripts/python.exe`, and WSL's interop will happily EXECUTE it. `import yaml` then
# succeeds, so it gets picked — and the splice dies with `KeyError: 'KNAIF_REHEARSE_ARGS'`, because
# environment variables do not cross into a Windows process unless they are listed in $WSLENV. The
# traceback names a `C:\...` path from a bash prompt and explains nothing.
#
# So gate the venv candidate on the host we are actually running as, and let PY_BIN override
# everything for the case where neither default is right.
pick_python() {
  local c candidates=()
  if [ -n "${PY_BIN:-}" ]; then
    "$PY_BIN" -c "import yaml" >/dev/null 2>&1 && { echo "$PY_BIN"; return 0; }
    echo "ERROR: PY_BIN='$PY_BIN' cannot import yaml." >&2
    return 1
  fi
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) candidates+=("$REPO/.venv/Scripts/python.exe") ;;
    *)                    candidates+=("$REPO/.venv/bin/python") ;;
  esac
  candidates+=(python3 python py)
  for c in "${candidates[@]}"; do
    "$c" -c "import yaml" >/dev/null 2>&1 && { echo "$c"; return 0; }
  done
  return 1
}
PY_BIN="$(pick_python)" || {
  echo "ERROR: no python with PyYAML found (tried the repo venv, python3, python, py)." >&2
  echo "       'uv sync' in the repo, or pip install pyyaml." >&2
  exit 1
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/knaif-rehearse-XXXXXX")"
SERVER_PID=""
cleanup() {
  [ -z "$SERVER_PID" ] || kill "$SERVER_PID" 2>/dev/null || true
  if [ "$KEEP" -eq 1 ]; then
    echo; echo "Kept: $WORK"
    echo "  manifest: $WORK/manifest.yaml"
    echo "  backends: $WORK/backends   (export KNAIF_BACKENDS_DIR to use it)"
  else
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

echo "repo:     $REPO"
echo "platform: $PLATFORM"
echo "payload:  $STAGE"
echo "fragment: $(basename "$FRAGMENT")"
echo "binary:   $EXE"
echo "scratch:  $WORK"
echo

# Splice the fragment's real checksums into a copy of the real manifest, flip the entry to
# published, and repoint every URL at the local server. Text splice rather than a YAML round-trip:
# the manifest is mostly comments explaining the schema, and a rehearsal that silently discards them
# is a rehearsal of a file nobody ships.
splice() {  # $1 = out path, $2 = knaif_version override or "", $3 = file to corrupt or ""
  KNAIF_REHEARSE_ARGS="$1|$2|$3" "$PY_BIN" - "$FRAGMENT" "$PLATFORM" "$REPO/contracts/backends/backend-manifest.yaml" "$PORT" <<'PY'
import os, re, sys, textwrap, yaml
out, version, corrupt = os.environ["KNAIF_REHEARSE_ARGS"].split("|")
fragment, platform, manifest, port = sys.argv[1:5]

files = yaml.safe_load(textwrap.dedent(open(fragment, encoding="utf-8").read()))[platform]["files"]
if corrupt and corrupt not in [f["name"] for f in files]:
    # A corrupt-file name that matches nothing produces a perfectly valid manifest, and the test
    # built on it then "passes" having verified nothing. Fail instead.
    sys.exit(f"corrupt target {corrupt!r} is not in the fragment: {[f['name'] for f in files]}")
lines = []
for f in files:
    sha = f["sha256"]
    if corrupt and f["name"] == corrupt:          # flip one nibble: a wrong hash, still well-formed
        sha = ("0" if sha[0] != "0" else "1") + sha[1:]
    lines += [f"          - name: {f['name']}",
              f"            tag: {f['tag']}",
              f"            url: http://127.0.0.1:{port}/{f['name']}",
              f'            sha256: "{sha}"',
              f"            size_bytes: {f['size_bytes']}"]
block = f"      {platform}:\n        files:\n" + "\n".join(lines) + "\n"

text = open(manifest, encoding="utf-8").read()
# The platform's own block, from its key to the next 6-space key (or EOF) — the other platform's
# entry is left exactly as it is, TODO urls and all, so a wrong platform key fails loudly.
pat = re.compile(rf"^      {re.escape(platform)}:\n(?:(?:      [^ \n].*|.{{7,}}|)\n)*?(?=^      [^ \n]|\Z)", re.M)
text, n = pat.subn(block, text)
if n != 1:
    sys.exit(f"platform block {platform!r} matched {n} times in the manifest, expected 1")
text = text.replace("    status: unpublished", "    status: published")
if version:
    text = re.sub(r'^knaif_version: ".*"$', f'knaif_version: "{version}"', text, flags=re.M)
open(out, "w", encoding="utf-8").write(text)
PY
}

# Every name comes back through `tr -d '\r'`: a Windows python prints CRLF, and one stray `\r` turns
# the corrupt-file match and the landed-file check into no-ops that still report PASS. (Observed —
# the first version of this script did exactly that.) The splice itself hard-fails on a name that
# matches nothing, so a rehearsal can never quietly test an uncorrupted manifest.
declared() {
  "$PY_BIN" -c "import sys,textwrap,yaml;print('\n'.join(f['name'] for f in yaml.safe_load(textwrap.dedent(open(sys.argv[1],encoding='utf-8').read()))[sys.argv[2]]['files']))" \
    "$FRAGMENT" "$PLATFORM" | tr -d '\r'
}

splice "$WORK/manifest.yaml"       ""       ""
splice "$WORK/manifest-bad.yaml"   ""       "$(declared | tail -1)"
splice "$WORK/manifest-stale.yaml" "0.0.9"  ""

# Test 1 asserts that an unpublished entry is refused, so it needs an unpublished entry to assert
# against. It used to read the SHIPPED manifest, which only worked while cuda was still unpublished:
# the moment 1.1.0 published the payload the assertion inverted and the whole rehearsal exited 1 —
# reporting a normal release-state change as a product failure, on the one script RELEASE.md §7 tells
# you to run before publishing. Synthesize the unpublished case instead, so the refusal is covered
# permanently and the test says nothing about what happens to be published today. That the shipped
# manifest is in the right state for its release is a different question, and
# test_backend_manifest_release_ready.py is what answers it.
sed 's/^\( *\)status: published$/\1status: unpublished/' \
  "$REPO/contracts/backends/backend-manifest.yaml" > "$WORK/manifest-unpublished.yaml"
grep -q "^ *status: unpublished$" "$WORK/manifest-unpublished.yaml" \
  || { echo "ERROR: no 'status:' line in the shipped manifest — cannot rehearse the refusal." >&2; exit 1; }

( cd "$STAGE" && exec "$PY_BIN" -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 ) &
SERVER_PID=$!
for _ in $(seq 1 50); do
  curl -fsS -o /dev/null --max-time 1 "http://127.0.0.1:$PORT/" && break
  sleep 0.2
done
curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/" || { echo "ERROR: local server did not come up on $PORT." >&2; exit 1; }

FAIL=0
ok()   { echo "  PASS  $1"; }
bad()  { echo "  FAIL  $1"; FAIL=1; }
run()  { KNAIF_BACKEND_MANIFEST="$1" KNAIF_BACKENDS_DIR="$2" "$EXE" "${@:3}"; }

DIR="$WORK/backends"

echo "1. unpublished entry is refused before any download"
run "$WORK/manifest-unpublished.yaml" "$DIR" backend list 2>/dev/null | grep -q "not published yet" \
  && ok "an unpublished entry reports 'not published yet'" || bad "an unpublished entry should refuse"

echo "2. install"
run "$WORK/manifest.yaml" "$DIR" backend list | sed 's/^/     /'
run "$WORK/manifest.yaml" "$DIR" backend install cuda | sed 's/^/     /'
run "$WORK/manifest.yaml" "$DIR" backend verify cuda  | grep -q "ok" && ok "verify reports ok" || bad "verify did not report ok"

echo "3. the swap left nothing behind"
# `.part` and a `.stage-*` dir are the two torn-install signatures; the receipt is what the loader
# reads. Checked by listing rather than by trusting the exit code above.
ls -a "$DIR" | grep -qE '\.part$|^\.stage-' && bad "staging leftovers in the backends dir" || ok "no .part / .stage-* left"
[ -f "$DIR/.knaif-backends.yaml" ] && ok "receipt written" || bad "no receipt"
MISSING=0
while IFS= read -r n; do [ -f "$DIR/$n" ] || { echo "        missing: $n"; MISSING=1; }; done < <(declared)
[ "$MISSING" -eq 0 ] && ok "every declared file landed" || bad "declared files are missing from the install"

echo "4. a corrupted checksum installs NOTHING"
# Into a fresh dir: the point is that files which verified before the bad one still must not land.
run "$WORK/manifest-bad.yaml" "$WORK/backends-fresh" backend install cuda >/dev/null 2>&1 \
  && bad "install succeeded against a corrupted manifest" \
  || { [ -z "$(ls -A "$WORK/backends-fresh" 2>/dev/null || true)" ] && ok "nothing landed in a fresh dir" || bad "a failed install left files behind"; }
# Over the good install: a failed re-install must not damage what is already there.
run "$WORK/manifest-bad.yaml" "$DIR" backend install cuda >/dev/null 2>&1 || true
run "$WORK/manifest.yaml" "$DIR" backend verify cuda | grep -q "ok" \
  && ok "the existing install survived a failed re-install" || bad "a failed re-install damaged the existing payload"

echo "5. a payload from another release is refused at LOAD time"
# The receipt is stamped from the manifest, so one install is enough to test the upgrade case — no
# second build needed. This is the requirement install-time pinning structurally cannot meet.
run "$WORK/manifest-stale.yaml" "$DIR" backend install cuda >/dev/null
run "$WORK/manifest.yaml" "$DIR" backend list | grep -q "STALE" && ok "list reports STALE" || bad "list did not report a stale payload"
run "$WORK/manifest.yaml" "$DIR" backend install cuda >/dev/null
run "$WORK/manifest.yaml" "$DIR" backend list | grep -q "installed" && ok "re-install clears it" || bad "re-install did not clear the stale state"

echo "6. remove"
run "$WORK/manifest.yaml" "$DIR" backend remove cuda >/dev/null
[ -z "$(ls -A "$DIR" 2>/dev/null || true)" ] && ok "remove emptied the directory" || bad "remove left files behind"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "PASS — the install path works against the real payload and the real checksums."
  echo
  echo "NOT covered here, and both need a machine with the GPU:"
  echo "  * the three backend cases (absent / present / present-with-no-usable-GPU) — see RELEASE.md."
  echo "    On Windows use CUDA_VISIBLE_DEVICES=\"-1\" for the third; an empty string reads as UNSET"
  echo "    there, so the case passes without testing anything."
  echo "  * the progress bar: indicatif draws nothing when stderr is not a terminal, so run"
  echo "    'knaif backend install cuda' by hand once to see it."
else
  echo "FAIL — see above. Nothing was published; the payload in dist/ is untouched."
fi
exit "$FAIL"
