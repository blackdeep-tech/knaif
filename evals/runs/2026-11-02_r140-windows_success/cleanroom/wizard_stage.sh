#!/usr/bin/env bash
# Stage the wizard part of the 1.4.0 Windows clean room (wizard.ps1; report.md, "Manual part"), after
# stage.sh has staged and hash-checked the inputs. A second, fresh Sandbox: Windows runs one at a
# time, so open it only after the first one has said DONE and been closed.
#
#   bash evals/runs/2026-11-02_r140-windows_success/cleanroom/wizard_stage.sh
#   (open the printed .wsb; wait for the Sandbox window to say DONE; screenshots land in wout/)
set -euo pipefail
cd "$(dirname "$0")/../../../.."
SRC=evals/runs/2026-11-02_r140-windows_success/cleanroom
T=sandbox/r140/cleanroom
[ -f "$T/in/knaif-1.4.0-windows-x64-setup.exe" ] || { echo "run stage.sh first" >&2; exit 1; }
[ "$(sha256sum "$T/in/knaif-1.4.0-windows-x64-setup.exe" | cut -c1-64)" = 993ebbd68c416d12595a0caa9ede683ab9589c3c7d9affa25f3b96f7f480e5bb ] \
  || { echo "HASH MISMATCH: the staged installer" >&2; exit 1; }
cp "$SRC/wizard.ps1" "$T/in/"
rm -rf "$T/wout"; mkdir -p "$T/wout"
w() { cygpath -aw "$1"; }
cat > "$T/wizard.wsb" <<EOF
<Configuration>
  <vGPU>Disable</vGPU>
  <Networking>Disable</Networking>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$(w "$T/in")</HostFolder><SandboxFolder>C:\\r140\\in</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$(w "$T/wout")</HostFolder><SandboxFolder>C:\\r140\\wout</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$(w models)</HostFolder><SandboxFolder>C:\\r140\\models</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','C:\\r140\\in\\wizard.ps1'"</Command>
  </LogonCommand>
</Configuration>
EOF
echo "open:   $(w "$T/wizard.wsb")"
