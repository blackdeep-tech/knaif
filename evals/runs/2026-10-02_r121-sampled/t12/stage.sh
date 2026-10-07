#!/usr/bin/env bash
# Stage and open the 1.2.1 RELEASE clean room on the frozen, signed build. Lives in the gitignored
# sandbox/ because the .wsb names host paths.
#
#   bash sandbox/r121/final/stage.sh        then, once the Sandbox window says DONE:
#   uv run python sandbox/r121/final/grade.py
#
# Baseline: the PUBLISHED 1.2.0 installer (hash = the GitHub v1.2.0 asset). Upgrade: the frozen
# 1.2.1 installer (real AppId; run it in the Sandbox only). No network, no vGPU.
set -euo pipefail
cd "$(dirname "$0")/../../.."
T=sandbox/r121/final
check() { [ "$(sha256sum "$1" | cut -c1-64)" = "$2" ] || { echo "HASH MISMATCH: $1" >&2; exit 1; }; }
check dist/knaif-1.2.0-windows-x64-setup.exe c42ba04ddde2ec2a2624d1a9a19aae373427a0d34fd3f0a8fd1f6a6796f2ea38
ZIP=dist/knaif-1.2.1-windows-x64.zip
SETUP=dist/knaif-1.2.1-windows-x64-setup.exe
OLD_EXE=$(sha256sum dist/staging/knaif-1.2.0-windows-x64/bin/knaif.exe | cut -c1-64)
NEW_EXE=$(unzip -p "$ZIP" '*/bin/knaif.exe' | sha256sum | cut -c1-64)
[ "$(sha256sum dist/staging/knaif-1.2.1-windows-x64/bin/knaif.exe | cut -c1-64)" = "$NEW_EXE" ] \
  || { echo "zip binary differs from staging" >&2; exit 1; }
for id in $(wsb list --raw 2>/dev/null | grep -oE '[0-9a-fA-F-]{36}'); do wsb stop --id "$id" >/dev/null 2>&1 || true; done
rm -rf "$T/out" "$T/in"; mkdir -p "$T/in" "$T/out"
cp dist/knaif-1.2.0-windows-x64-setup.exe "$SETUP" "$ZIP" \
  sandbox/fixtures/documents/sample-scanned.pdf sandbox/fixtures/documents/sample.pdf \
  "$T/sandbox.ps1" "$T/in/"
printf '{"old_exe": "%s", "new_exe": "%s"}\n' "$OLD_EXE" "$NEW_EXE" > "$T/in/expected.json"
sha256sum "$T/in/"*.exe "$T/in/"*.zip > "$T/out/inputs.sha256"
w() { cygpath -aw "$1"; }
cat > "$T/t12.wsb" <<EOF
<Configuration>
  <vGPU>Disable</vGPU>
  <Networking>Disable</Networking>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$(w "$T/in")</HostFolder><SandboxFolder>C:\\t12\\in</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$(w "$T/out")</HostFolder><SandboxFolder>C:\\t12\\out</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$(w models)</HostFolder><SandboxFolder>C:\\t12\\models</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>C:\\Program Files\\Tesseract-OCR</HostFolder><SandboxFolder>C:\\t12\\tesseract</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','C:\\t12\\in\\sandbox.ps1'"</Command>
  </LogonCommand>
</Configuration>
EOF
echo "staged: $(ls "$T/in" | tr '\n' ' ')"
echo "open:   $(w "$T/t12.wsb")"
