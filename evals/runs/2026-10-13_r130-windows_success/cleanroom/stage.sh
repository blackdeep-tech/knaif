#!/usr/bin/env bash
# Stage and open the knaif 1.3.0 Windows clean room on the frozen, signed build (a port of 1.2.1's
# evals/runs/2026-10-02_r121-sampled/t12). The staged inputs, results and the .wsb (it names host
# paths) live in the gitignored sandbox/r130/cleanroom; the verdict is committed by grade.py.
#
#   bash evals/runs/2026-10-13_r130-windows_success/cleanroom/stage.sh
#   (open the printed .wsb; wait for the Sandbox window to say DONE)
#   uv run --no-sync python evals/runs/2026-10-13_r130-windows_success/cleanroom/grade.py
#   then the manual part: evals/runs/2026-10-13_r130-windows_success/cleanroom/report.md
#
# Baseline: the PUBLISHED 1.2.1 installer (hash = the GitHub v1.2.1 asset). Under test: the frozen
# 1.3.0 installer and zip (hash = the run script's pin). Real AppId: run it in the Sandbox only.
set -euo pipefail
cd "$(dirname "$0")/../../../.."
SRC=evals/runs/2026-10-13_r130-windows_success/cleanroom
T=sandbox/r130/cleanroom
check() { [ "$(sha256sum "$1" | cut -c1-64)" = "$2" ] || { echo "HASH MISMATCH: $1" >&2; exit 1; }; }
check dist/knaif-1.2.1-windows-x64-setup.exe 10d0e24df0a20f76f131ccf81ad41ae8a0347930a08e52de19bbd690603b1d58
check dist/knaif-1.2.1-windows-x64.zip ae2531a907652e852836c3458669277a377e6acab5f5da42f261c043d31d6e04
check dist/knaif-1.3.0-windows-x64.zip 7c289f3e5637b3d3c71fa8843666f1810b44ebbcdd458e23de9f42dba305d0ad
check dist/knaif-1.3.0-windows-x64-setup.exe c671a60a4d8c1bb2dd8104cb883d1025de0de449f850bd98e5f746edaf9edda2
OLD_EXE=$(unzip -p dist/knaif-1.2.1-windows-x64.zip '*/bin/knaif.exe' | sha256sum | cut -c1-64)
NEW_EXE=$(unzip -p dist/knaif-1.3.0-windows-x64.zip '*/bin/knaif.exe' | sha256sum | cut -c1-64)
for id in $(wsb list --raw 2>/dev/null | grep -oE '[0-9a-fA-F-]{36}'); do wsb stop --id "$id" >/dev/null 2>&1 || true; done
rm -rf "$T/out" "$T/in"; mkdir -p "$T/in" "$T/out"
cp dist/knaif-1.2.1-windows-x64-setup.exe dist/knaif-1.3.0-windows-x64-setup.exe dist/knaif-1.3.0-windows-x64.zip \
  sandbox/fixtures/documents/sample.pdf "$SRC/sandbox.ps1" "$T/in/"
printf '{"old_exe": "%s", "new_exe": "%s"}\n' "$OLD_EXE" "$NEW_EXE" > "$T/in/expected.json"
(cd "$T/in" && sha256sum *.exe *.zip) > "$T/out/inputs.sha256"
w() { cygpath -aw "$1"; }
cat > "$T/r130.wsb" <<EOF
<Configuration>
  <vGPU>Disable</vGPU>
  <Networking>Disable</Networking>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$(w "$T/in")</HostFolder><SandboxFolder>C:\\r130\\in</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$(w "$T/out")</HostFolder><SandboxFolder>C:\\r130\\out</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$(w models)</HostFolder><SandboxFolder>C:\\r130\\models</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','C:\\r130\\in\\sandbox.ps1'"</Command>
  </LogonCommand>
</Configuration>
EOF
echo "staged: $(ls "$T/in" | tr '\n' ' ')"
echo "open:   $(w "$T/r130.wsb")"
