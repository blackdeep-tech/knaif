#!/usr/bin/env bash
# What is left of knaif 1.4.0's macOS work, as one queue (scripts/release_queue.sh) the owner approved
# on 2026-11-19 with the budgets below. It reports every step to the issue RELAY_TITLE and waits there
# only for the owner: a miss accepted.yaml does not cover, a step that fails, or the release being
# published (before the Homebrew check). One manual step, the Metal check from a fresh account, waits
# for its result file instead.
#
# On the Mac, from the evidence checkout's root (outside the home folder), once:
#   MAC_HEAD=<fork owner>:<branch> MAC_REMOTE=<the fork's remote> \
#     bash evals/runs/2026-11-02_r140-macos_success/queue.sh
# Started again after any stop, it skips the steps already done. Exit 4: the owner said stop.
#
# Needs: gh logged in as the Mac's account; tart with the base VM knaif-room-base (installers/macos/
# README.md); the CI files of the freeze in sandbox/r140/ci/ (run_all.sh's ARTIFACT and its .pkg).

set -uo pipefail
R="evals/runs/2026-11-02_r140-macos_success"
RELAY_REPO=blackdeep-tech/knaif
RELAY_TITLE="1.4.0 macOS evidence and clean room"
RELAY_AUTHORS="GalinDinkov,masterpigz"
: "${MAC_HEAD:?MAC_HEAD=<fork owner>:<branch> names the PR head}" "${MAC_REMOTE:?MAC_REMOTE names the fork remote}"
# shellcheck source=../../../scripts/release_queue.sh
source scripts/release_queue.sh

VERSION=1.4.0
FREEZE=4240522e8f3d71fb9769240ffa6d46a496edcf08
PKG="knaif-$VERSION-macos-arm64"
ZIP="sandbox/r140/ci/$PKG.zip"
ZIP_SHA256=d5a7bd13b07efc799742e179e84d4bd55c2e47eea565644cb4d95c210de10d63
PKGF="sandbox/r140/ci/$PKG.pkg"
OLD="knaif-1.3.0-macos-arm64.pkg"
IN="sandbox/r140/cleanroom-in"       # what goes into the VM: the freeze's scripts and the 1.3.0 .pkg
CR="$R/cleanroom"
MODEL=models/knaif-qwen3-4b-v2-q4_k_m.gguf
CPU_EXE="sandbox/r140/artifact-cpu/$PKG/bin/knaif"
METAL_DROP=/Users/Shared/knaif-metal-check     # where the fresh account writes its result
SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o LogLevel=ERROR"
SCP="scp -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }

# -- 1. preflight ----------------------------------------------------------------------------------
preflight() {
  local free
  free="$(df -g . | awk 'NR == 2 { print $4 }')"
  note "- free disk: ${free} GiB"
  [ "${free:-0}" -ge 20 ] || needs_owner "under 20 GiB free; the live cells and the VMs need it"
  gh auth status > "$STEP_DIR/gh.log" 2>&1 || needs_owner "gh is not logged in"
  [ "$(sha "$ZIP" 2> /dev/null)" = "$ZIP_SHA256" ] && note "- $ZIP: sha256 as run_all.sh names it" \
    || needs_owner "$ZIP is missing or not the frozen file"
  [ -f "$PKGF" ] && note "- $PKGF: sha256 $(sha "$PKGF")" || needs_owner "$PKGF is missing (the CI run's .pkg)"
  [ -f "$MODEL" ] || needs_owner "$MODEL is missing"
  [ -x "$CPU_EXE" ] || needs_owner "$CPU_EXE is missing (run_all.sh check unpacks it)"
  command -v tart > /dev/null && tart list 2> /dev/null | grep -q 'knaif-room-base' \
    && note "- tart and knaif-room-base: present" || needs_owner "tart or the base VM knaif-room-base is missing"
  mkdir -p "$IN"
  git show "$FREEZE:installers/macos/clean-room.sh" > "$IN/clean-room.sh" \
    && git show "$FREEZE:installers/smoke.sh" > "$IN/smoke.sh" \
    && git show "$FREEZE:Cargo.toml" > "$IN/Cargo.toml" || needs_owner "exporting the freeze's scripts failed"
  if [ ! -f "$IN/$OLD" ]; then
    gh release download v1.3.0 --repo "$RELAY_REPO" -p "$OLD" -p SHA256SUMS -D "$IN" --clobber \
      > "$STEP_DIR/download.log" 2>&1 || needs_owner "downloading the published $OLD failed"
  fi
  (cd "$IN" && grep " $OLD\$" SHA256SUMS | shasum -a 256 -c -) > "$STEP_DIR/old-pkg.log" 2>&1 \
    && note "- $OLD: the published file (SHA256SUMS)" || needs_owner "$OLD does not match the release's SHA256SUMS"
  [ -s sandbox/fixtures/documents/sample.pdf ] || needs_owner "sandbox/fixtures/documents/sample.pdf is missing"
  return 0
}

# -- 2, 3. the two 1.7B documents cells, live (I5) ---------------------------------------------------
live_cell() {  # $1 backend
  local rc
  FRESH="${RERUN:+1}" MODEL=1.7b BACKEND="$1" SKILL=documents bash "$R/run_all.sh" live
  rc=$?
  note_file "COMPLETE" "$R/COMPLETE" 8
  cell_check 1.7b "$1" documents "$R/1.7b/$1/live"
  return $rc
}

# -- 4. "beep" on the 4B CPU: does Python reject the reply the binary rejected? ----------------------
beep_check() {
  local rc d="$STEP_DIR/live"
  # The request once more, live on the CPU copy, recording its reply: the file's name is the prompt
  # key, the one the 4B CPU store namespace (e4476c3fc13b) holds the batch's reply under.
  rm -rf "$d" && mkdir -p "$d/rec"
  (cd "$d" && KNAIF_LLM_RECORD_DIR="$PWD/rec" "$OLDPWD/$CPU_EXE" run ffmpeg --verbose --model "$OLDPWD/$MODEL" -- beep) \
    > "$STEP_DIR/beep-live.log" 2>&1
  note "- \"beep\" live on the CPU copy: exit $?"
  note_file "beep, live" "$STEP_DIR/beep-live.log" 15
  $RELAY reply-check --prefix e4476c3fc13b --recorded "$d/rec" --out "$STEP_DIR/reply-check.json" \
    > "$STEP_DIR/reply-check.txt" 2>&1
  rc=$?
  note "- $(tail -1 "$STEP_DIR/reply-check.txt")"
  note_file "the stored reply, as Python reads it" "$STEP_DIR/reply-check.json" 12
  case $rc in
    0)
      # The owner's rule (2026-11-19): accepted like ffmpeg_291 when Python rejects the same reply.
      echo '{"step": "beep-check", "action": "continue", "author": "owner rule 2026-11-19", "note": "Python rejects the same reply, so ffmpeg_alert_005 is accepted like ffmpeg_291", "accept": [{"kind": "regression", "row": "ffmpeg_alert_005", "models": ["4b"], "backends": ["cpu"]}]}' \
        >> "$Q/decisions.jsonl"
      note "- **ffmpeg_alert_005 accepted** by the owner's rule: Python rejects the same reply." ;;
    3) needs_owner "Python ACCEPTS the reply the binary rejected: a parser difference in the binary (a release blocker)" ;;
    *) needs_owner "the stored reply for this prompt was not found (exit $rc); the live run may not have asked the model" ;;
  esac
  return 0
}

# -- 5. every Mac cell against what the owner accepted ----------------------------------------------
verdicts() {
  local m b s d
  for m in 4b 1.7b; do for b in metal cpu; do for s in ffmpeg documents; do
    d="$R/$m/$b"
    [ "$m/$s" = 1.7b/documents ] && d="$d/live"
    cell_check "$m" "$b" "$s" "$d"
  done; done; done
  note "Each Mac cell's misses against accepted.yaml and the decisions so far."
  return 0
}

# -- 6. L3: the release record keeps the Windows CUDA entries (owner, 2026-11-19) --------------------
restore_l3() {
  local f
  for f in evals/acceptance/ffmpeg.json evals/acceptance/documents.json; do
    $RELAY restore-layer --file "$f" --layer L3 --ref HEAD || { needs_owner "restoring L3 in $f failed"; return 1; }
  done
  git diff --stat -- evals/acceptance > "$STEP_DIR/diff.txt"
  note_file "evals/acceptance after restoring L3 (only the Mac L4 cells should differ)" "$STEP_DIR/diff.txt" 10
  return 0
}

# -- 7. commit the evidence (three commits, signed off) ---------------------------------------------
commit_evidence() {
  local c
  git add evals/store || return 1
  git commit -q -s -F - <<'EOF' || { needs_owner "the store commit failed (see the hooks' output in the log)"; return 1; }
eval(store): the second 1.4.0 candidate's macOS replies
EOF
  git add -f "$R/COMPLETE" "$R/TOTALS" "$R/verdicts.txt" "$R/accepted.yaml" "$R/queue.sh" \
    "$R"/*/*/*_safety.json "$R"/*/*/*_verdict.txt "$R"/1.7b/*/live/*_safety.json "$R"/1.7b/*/live/*_verdict.txt \
    "$Q/LOG" "$Q/decisions.jsonl" || return 1
  for c in store.log setup-local.log; do [ -f "$R/$c" ] && git add -f "$R/$c"; done
  git add evals/acceptance/ffmpeg.json evals/acceptance/documents.json evals/parity/2026-11-02_r140-mac-* || return 1
  git commit -q -s -F - <<'EOF' || { needs_owner "the evidence commit failed (see the hooks' output in the log)"; return 1; }
eval(release): macOS evidence of the second 1.4.0 candidate

Metal and CPU cells, the two 1.7B documents cells run again live, L3 on Metal and daemon plan
equality; the acceptance records keep the Windows CUDA L3, as in 1.3.0.
EOF
  git log --oneline -2 > "$STEP_DIR/commits.txt"
  note_file "commits" "$STEP_DIR/commits.txt" 2
  return 0
}

# -- 8. the clean room: three VM runs over ssh, the signatures on the host, PDFium on macOS 12 --------
vm_up() {  # $1 the run's folder: a fresh clone of the base, booted, reachable; sets IP
  local i=0
  tart stop knaif-room > /dev/null 2>&1; tart delete knaif-room > /dev/null 2>&1
  tart clone knaif-room-base knaif-room || return 1
  tart run --no-graphics knaif-room > /dev/null 2>&1 &
  while [ $i -lt 60 ]; do
    IP="$(tart ip knaif-room 2> /dev/null)" && $SSH admin@"$IP" true 2> /dev/null && break
    sleep 5; i=$((i + 1))
  done
  [ $i -lt 60 ] || return 1
  $SSH admin@"$IP" 'sw_vers; echo console=$(stat -f %Su /dev/console)
    for k in AutomaticCheckEnabled AutomaticDownload AutomaticallyInstallMacOSUpdates CriticalUpdateInstall; do
      echo "SoftwareUpdate $k=$(defaults read /Library/Preferences/com.apple.SoftwareUpdate $k 2>/dev/null)"; done' \
    > "$1/vm-info.txt" 2>&1
  $SSH admin@"$IP" mkdir -p room && $SCP "$ZIP" "$PKGF" "$IN/clean-room.sh" "$IN/smoke.sh" "$IN/Cargo.toml" \
    sandbox/fixtures/documents/sample.pdf "$MODEL" "$IN/$OLD" "$CR"/vm-*.sh admin@"$IP":room/
}
vm_down() { tart stop knaif-room > /dev/null 2>&1; tart delete knaif-room > /dev/null 2>&1; }
vm_fetch() {  # $1 the run's folder
  $SCP "admin@$IP:room/clean-room-results.txt" "admin@$IP:room/*.log" "$1/" 2> /dev/null
  [ -f "$1/clean-room-results.txt" ] && grep -q '^FAIL' "$1/clean-room-results.txt" && return 1
  grep -q 'CLEAN ROOM PASS' "$1/clean-room.out" 2> /dev/null
}
room() {  # $1 name, then clean-room.sh's options: one run over ssh on a fresh clone
  local d="$CR/$1"; shift
  mkdir -p "$d"
  vm_up "$d" || { vm_down; needs_owner "$(basename "$d"): the VM did not come up"; return 1; }
  $SSH admin@"$IP" "cd room && bash clean-room.sh $* --fixtures . --model ${MODEL##*/}" 2>&1 | scrubbed > "$d/clean-room.out"
  vm_fetch "$d" && note "- $(basename "$d"): **$(grep -m1 -o 'CLEAN ROOM PASS ([0-9]* checks)' "$d/clean-room.out")**" \
    || needs_owner "$(basename "$d"): $(grep -m1 'CLEAN ROOM' "$d/clean-room.out" || echo 'no result')"
}
room_offline() {  # run 2: the Ethernet service off for the whole run, from a detached job in the VM
  local d="$CR/run2-pkg-offline" i=0
  mkdir -p "$d"
  vm_up "$d" || { vm_down; needs_owner "run2-pkg-offline: the VM did not come up"; return 1; }
  $SSH admin@"$IP" 'nohup bash room/vm-offline.sh > room/offline.nohup 2>&1 < /dev/null &'
  sleep 30
  until $SSH admin@"$IP" test -f room/offline.rc 2> /dev/null; do
    sleep 30; i=$((i + 1))
    [ $i -lt 120 ] || { needs_owner "run2-pkg-offline: no result after an hour"; vm_down; return 1; }
  done
  $SCP "admin@$IP:room/clean-room.out" "admin@$IP:room/offline-net.txt" "$d/"
  cp "$CR/vm-offline.sh" "$d/offline.sh"
  vm_fetch "$d" && note "- run2-pkg-offline: **$(grep -m1 -o 'CLEAN ROOM PASS ([0-9]* checks)' "$d/clean-room.out")**" \
    || needs_owner "run2-pkg-offline: $(grep -m1 'CLEAN ROOM' "$d/clean-room.out" || echo 'no result')"
}
host_signature() {
  local d="$CR/host-signature" t f bad=""
  mkdir -p "$d"
  { pkgutil --check-signature "$PKGF"; xcrun stapler validate "$PKGF"; spctl -a -vv -t install "$PKGF"; } \
    > "$d/pkg.txt" 2>&1
  grep -q 'Developer ID Installer' "$d/pkg.txt" && grep -q 'worked' "$d/pkg.txt" && grep -q 'accepted' "$d/pkg.txt" \
    || bad="the .pkg"
  t="$(mktemp -d)" && ditto -x -k "$ZIP" "$t"
  for f in "$t/$PKG"/bin/knaif "$t/$PKG"/bin/*.dylib "$t/$PKG"/bin/*.so; do
    [ -f "$f" ] || continue
    codesign --verify --strict "$f" > /dev/null 2>&1 && echo "ok ${f##*/}" || { echo "FAIL ${f##*/}"; bad="$bad ${f##*/}"; }
  done > "$d/zip-codesign.txt"
  codesign -dv --verbose=2 "$t/$PKG/bin/knaif" 2>&1 | grep -E '^(Authority|TeamIdentifier|Timestamp|Runtime Version)=' >> "$d/zip-codesign.txt"
  $UV python scripts/check_macho_deps.py --min-os 12.0 "$t/$PKG/bin" 2>&1 | scrubbed > "$d/macho-min-os.txt"
  rm -rf "$t"
  [ -z "$bad" ] && note "- host signatures: the .pkg notarized and accepted; every Mach-O in the .zip verifies" \
    || needs_owner "host signatures failed: $bad"
  note_file "check_macho_deps --min-os 12.0 (information: TODO.md, the PDFium library declares 13)" "$d/macho-min-os.txt" 8
}
cleanroom() {
  local d
  mkdir -p "$CR"
  # Run 2's job (1.3.0's method): no network from start to end, then back on.
  cat > "$CR/vm-offline.sh" <<EOF
#!/bin/bash
# run 2 detached: the Ethernet service off for the whole run, then back on
cd ~/room
sudo networksetup -setnetworkserviceenabled Ethernet off
sleep 5
{ date; networksetup -listallnetworkservices; route -n get default 2>&1 | head -3; } > offline-net.txt 2>&1
bash clean-room.sh --pkg $PKG.pkg --offline --fixtures . --model ${MODEL##*/} > clean-room.out 2>&1
echo \$? > offline.rc
sudo networksetup -setnetworkserviceenabled Ethernet on
EOF
  # INFORMATION, never gating: does the smallest PDF compress (PDFium) run on macOS 12? (TODO.md)
  cat > "$CR/vm-pdfium.sh" <<EOF
#!/bin/bash
# the smallest compress on macOS 12, the CPU copy of the .zip's tree: it rasterizes through PDFium
cd ~/room && rm -rf pdfium && mkdir pdfium && ditto -x -k $PKG.zip pdfium && cp sample.pdf pdfium/
rm -f pdfium/$PKG/bin/libggml-metal.*
find pdfium -name 'libpdfium*.dylib' -exec sh -c 'echo "\$1"; otool -l "\$1" | grep -A4 LC_BUILD_VERSION' _ {} \;
cd pdfium && ./$PKG/bin/knaif run documents --yes --model ../${MODEL##*/} -- compress sample.pdf to the smallest size
echo "exit \$?"
ls -l *.pdf
EOF
  host_signature
  room run1-zip --zip "$PKG.zip"
  if [ -n "${IP:-}" ]; then
    $SSH admin@"$IP" 'bash room/vm-pdfium.sh' 2>&1 | scrubbed > "$CR/run1-zip/pdfium-macos12.log"
    note "- PDFium on macOS 12 (information): $(grep -m1 '^exit' "$CR/run1-zip/pdfium-macos12.log")"
    note_file "PDFium on macOS 12" "$CR/run1-zip/pdfium-macos12.log" 20
  fi
  vm_down
  room_offline; vm_down
  room run3-pkg-upgrade --pkg "$PKG.pkg" --upgrade-from "$OLD"; vm_down
  {
    echo "# knaif $VERSION macOS clean room, on the freeze's CI files"
    echo
    echo "Written by queue.sh. Files: \`$PKG.zip\` $ZIP_SHA256; \`$PKG.pkg\` $(sha "$PKGF");"
    echo "upgrade source: the published \`$OLD\` $(sha "$IN/$OLD"). Scripts: the freeze's ($FREEZE)."
    echo
    for d in run1-zip run2-pkg-offline run3-pkg-upgrade; do
      echo "- $d: $(grep -m1 'CLEAN ROOM' "$CR/$d/clean-room.out" 2> /dev/null || echo 'no result')"
    done
  } | scrubbed > "$CR/report.md"
  return 0
}

# -- 9. Metal from a fresh account (manual) ---------------------------------------------------------
metal_fresh_account() {
  local s="$CR/step-d-metal-fresh-account/summary.txt"
  mkdir -p "$CR/step-d-metal-fresh-account" "$METAL_DROP" && chmod 1777 "$METAL_DROP"
  cp "$ZIP" "$MODEL" sandbox/fixtures/documents/sample.pdf "$METAL_DROP/" && chmod a+r "$METAL_DROP"/*
  cat > "$METAL_DROP/metal-check.sh" <<EOF
#!/bin/bash
# Run from the fresh account, after Finder extracted ~/Downloads/$PKG.zip (double-click it there).
set -u
K=~/Downloads/$PKG/bin/knaif
out=$METAL_DROP/summary.txt
{ echo "Metal from a fresh standard account: \$(sw_vers -productVersion), \$(sysctl -n machdep.cpu.brand_string)"
  echo "quarantine on bin/knaif: \$(xattr -p com.apple.quarantine "\$K" 2>/dev/null | cut -d';' -f1,3 || echo none)"
  mkdir -p ~/work && cp $METAL_DROP/sample.pdf ~/work/ && cd ~/work
  for run in cold warm; do
    echo "== \$run"
    /usr/bin/time -p "\$K" run documents --yes --verbose --model $METAL_DROP/${MODEL##*/} "rotate sample.pdf 90 degrees" 2>&1 \
      | grep -E 'GPU name|offloaded|wrote|^real|rror'
    echo "exit \${PIPESTATUS[0]}"
  done; } 2>&1 | sed "s#\$HOME#~#g" > "\$out.tmp" && mv "\$out.tmp" "\$out"   # this account's home names it
EOF
  chmod a+rx "$METAL_DROP/metal-check.sh"
  cat > "$STEP_DIR/checklist.md" <<EOF
1. Create a new **standard** account (System Settings, Users & Groups), log in to it.
2. Copy \`$METAL_DROP/$PKG.zip\` to its \`~/Downloads\` with Safari's quarantine:
   \`cp $METAL_DROP/$PKG.zip ~/Downloads/ && xattr -w com.apple.quarantine "0083;\$(printf %x \$(date +%s));Safari;\$(uuidgen)" ~/Downloads/$PKG.zip\`
3. Double-click the zip in Finder (Archive Utility extracts it).
4. In Terminal: \`bash $METAL_DROP/metal-check.sh\`. It writes \`$METAL_DROP/summary.txt\`; the queue
   picks it up within a minute and carries on. Then log out; delete the account when you like.
EOF
  rm -f "$METAL_DROP/summary.txt"
  manual "$METAL_DROP/summary.txt" "$STEP_DIR/checklist.md"
  scrubbed < "$METAL_DROP/summary.txt" > "$s"
  note_file "summary.txt" "$s" 30
  [ "$(grep -c 'offloaded 37/37 layers' "$s")" -ge 2 ] && [ "$(grep -c '^exit 0' "$s")" -ge 2 ] \
    && note "- **Metal: 37/37 layers, exit 0, both runs**" || needs_owner "the Metal check did not show 37/37 layers and exit 0 twice"
  return 0
}

# -- 10. commit the clean room, push, open the PR ---------------------------------------------------
commit_cleanroom() {
  git add -f "$CR" "$Q/LOG" "$Q/decisions.jsonl" || return 1
  git commit -q -s -F - <<'EOF' || { needs_owner "the clean-room commit failed (see the hooks' output in the log)"; return 1; }
eval(release): macOS clean room of the second 1.4.0 candidate

Three runs on a fresh macOS 12 VM each, the host signatures, PDFium on macOS 12 as information and
Metal from a fresh account, all on the freeze's CI files.
EOF
  note "- $(git log --oneline -1)"
}
open_pr() {
  local url
  git push "$MAC_REMOTE" "HEAD:${MAC_HEAD#*:}" > "$STEP_DIR/push.log" 2>&1 || { needs_owner "the push failed"; return 1; }
  url="$(gh pr list --repo "$RELAY_REPO" --head "${MAC_HEAD#*:}" --base release/1.4.0 --json url -q '.[0].url')"
  if [ -z "$url" ]; then
    {
      echo "The macOS evidence and clean room of the second 1.4.0 candidate (freeze \`${FREEZE:0:7}\`),"
      echo "run by \`$R/queue.sh\`; every step's report is on the issue \"$RELAY_TITLE\"."
      echo
      cat "$CR/report.md"
    } | scrubbed > "$STEP_DIR/pr-body.md"
    url="$(gh pr create --repo "$RELAY_REPO" --base release/1.4.0 --head "$MAC_HEAD" \
      --title "eval(release): macOS evidence and clean room, 1.4.0" --body-file "$STEP_DIR/pr-body.md")" \
      || { needs_owner "gh pr create failed"; return 1; }
  fi
  note "- the PR: $url"
}

# -- 11. after the release is published: the Homebrew cask (RELEASE.md §5, step 9) ----------------
published() {
  needs_owner "answer \`continue\` once 1.4.0 is published and the tap's Casks/knaif.rb is pushed; the queue then checks the cask"
}
homebrew() {
  local d
  d="$(mktemp -d)"
  { brew update && { brew upgrade --cask blackdeep-tech/knaif/knaif || brew install --cask blackdeep-tech/knaif/knaif; }
    knaif --version; knaif skills list
    cp sandbox/fixtures/documents/sample.pdf "$d/" && (cd "$d" && knaif run documents --yes --model "$OLDPWD/$MODEL" "rotate sample.pdf 90 degrees")
    echo "exit $?"; } 2>&1 | scrubbed > "$STEP_DIR/brew.log"
  rm -rf "$d"
  note_file "brew" "$STEP_DIR/brew.log" 25
  grep -q "$VERSION" "$STEP_DIR/brew.log" && grep -q '^exit 0' "$STEP_DIR/brew.log" \
    && note "- **the cask installs $VERSION and a real request runs**" || needs_owner "the Homebrew check failed"
  return 0
}

step preflight 10 preflight
step live-metal 30 live_cell metal
step live-cpu 60 live_cell cpu
step beep-check 15 beep_check
step verdicts 5 verdicts
step restore-l3 5 restore_l3
step commit-evidence 15 commit_evidence
step cleanroom 120 cleanroom
step metal-fresh-account 60 metal_fresh_account
step commit-cleanroom 10 commit_cleanroom
step open-pr 10 open_pr
step published 5 published
step homebrew 20 homebrew
queue_end "The evidence PR is open; the Homebrew cask is checked."
