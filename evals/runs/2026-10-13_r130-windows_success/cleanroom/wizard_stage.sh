#!/usr/bin/env bash
# Stage the wizard part of the 1.3.0 Windows clean room (wizard.ps1; report.md, "Manual part"), after
# stage.sh has staged and hash-checked the inputs. A second, fresh Sandbox: Windows runs one at a
# time, so open it only after the first one has said DONE and been closed.
#
#   bash evals/runs/2026-10-13_r130-windows_success/cleanroom/wizard_stage.sh
#   (open the printed .wsb; wait for the Sandbox window to say DONE; screenshots land in wout/)
set -euo pipefail
cd "$(dirname "$0")/../../../.."
SRC=evals/runs/2026-10-13_r130-windows_success/cleanroom
T=sandbox/r130/cleanroom
[ -f "$T/in/knaif-1.3.0-windows-x64-setup.exe" ] || { echo "run stage.sh first" >&2; exit 1; }
[ "$(sha256sum "$T/in/knaif-1.3.0-windows-x64-setup.exe" | cut -c1-64)" = c671a60a4d8c1bb2dd8104cb883d1025de0de449f850bd98e5f746edaf9edda2 ] \
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
    <MappedFolder><HostFolder>$(w "$T/in")</HostFolder><SandboxFolder>C:\\r130\\in</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$(w "$T/wout")</HostFolder><SandboxFolder>C:\\r130\\wout</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$(w models)</HostFolder><SandboxFolder>C:\\r130\\models</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','C:\\r130\\in\\wizard.ps1'"</Command>
  </LogonCommand>
</Configuration>
EOF
echo "open:   $(w "$T/wizard.wsb")"
