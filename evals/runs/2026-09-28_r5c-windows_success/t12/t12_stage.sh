#!/usr/bin/env bash
# Release 1.2.0 R5c T12 (docs/plans/2026-09-25-release-1.2.md; RELEASE.md §4): the Windows
# installer and clean room, on RC 71884fd's FROZEN bytes, inside Windows Sandbox so the host's own
# install is never touched. Stages the inputs, writes the .wsb (it names host paths, so it stays in
# the gitignored sandbox/ folder), and opens the Sandbox; t12_sandbox.ps1 runs there on logon.
#
#   bash evals/runs/2026-09-28_r5c-windows_success/t12/t12_stage.sh     then, after the Sandbox
#   uv run python evals/runs/2026-09-28_r5c-windows_success/t12/t12_grade.py              says DONE
#
# Inputs, each checked before staging: knaif-1.2.0-windows-x64-setup.exe (76328a0b...) and
# knaif-1.2.0-windows-x64.zip (929b2df0...), both built from 71884fd; knaif-1.1.0-windows-x64-setup.exe
# from the public v1.1.0 release (b0e2c7e2..., as its SHA256SUMS lists); the documents fixture
# sample-scanned.pdf. The Sandbox has no network and no vGPU: a box with no GPU and no developer
# tooling, which is the machine that reproduces a missing runtime.
#
# RULES, written 2026-09-28 before the run. T12 PASSES iff every check below passes:
#   cleanroom_version, cleanroom_skills_list   the unpacked zip runs: `--version` says 1.2.0 and
#       `skills list` exits 0 naming ffmpeg + documents (a missing runtime exits -1073741515, silent);
#   install_110, installed_110_runs            1.1.0 installs silently, one Add/Remove row at 1.1.0;
#   cli_running_holds_mutex                     a 1.1.0 CLI waiting at its first-run download prompt
#       is alive and holds `knaif-cli-running` (else the next check tests nothing);
#   upgrade_refused_while_running               1.2.0 setup, silent, exits non-zero within 300 s and
#       changes nothing: one row, still 1.1.0, the binary still 1.1.0 (AppMutex);
#   upgrade_installs, upgrade_same_folder, upgrade_no_folder_exists_prompt, upgraded_binary_runs
#       with the CLI closed, the upgrade exits 0; one row, now 1.2.0, the same InstallLocation; the
#       setup log shows no "already exists" message; the installed binary says 1.2.0;
#   upgrade_no_leftover_libraries               {app}\bin holds no file the 1.2.0 zip's bin lacks
#       ([InstallDelete] ran);
#   ocr_row_runs, ocr_no_pdfium_error           `knaif run documents --yes "run ocr on
#       sample-scanned.pdf"` (documents_077) with NO --model: the 4B model auto-selected from
#       ~/.knaif/models, CPU, KNAIF_PDFIUM_PATH unset, Tesseract on PATH as a user installs it;
#       exit 0 and an output file;
#   ocr_text (host, t12_grade.py)               the output PDF's text contains "Scanned image text",
#       documents_077's success criterion.
# Not covered, by design: the CUDA component on a machine with an NVIDIA card (T8 installed the
# payload with `backend install`); the wizard's task tree (a GUI check, only if the owner asks).
# PREDICTION: every check passes; the OCR row takes 1-3 min on the Sandbox CPU.
set -euo pipefail
cd "$(dirname "$0")/../../../.."
T=sandbox/r5c/t12
H=evals/runs/2026-09-28_r5c-windows_success/t12
check() { [ "$(sha256sum "$1" | cut -c1-64)" = "$2" ] || { echo "HASH MISMATCH: $1" >&2; exit 1; }; }
check dist/knaif-1.2.0-windows-x64-setup.exe 76328a0b6395ef1c0f3bd83c637d7a134310908cad3b64324c963668e2b6d0c6
check dist/knaif-1.2.0-windows-x64.zip 929b2df0fe7e3dc52f00c11e12cd7dd9125418cb61778b97ef1f19f4d6adfcea
check "$T/in/knaif-1.1.0-windows-x64-setup.exe" b0e2c7e22197d0a189ba2bc1b28b82206d2c54c46d96a2d464dac342b94e5c4b
rm -rf "$T/out"; mkdir -p "$T/in" "$T/out"
cp dist/knaif-1.2.0-windows-x64-setup.exe dist/knaif-1.2.0-windows-x64.zip \
  sandbox/fixtures/documents/sample-scanned.pdf "$H/t12_sandbox.ps1" "$T/in/"
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
    <Command>powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','C:\\t12\\in\\t12_sandbox.ps1'"</Command>
  </LogonCommand>
</Configuration>
EOF
echo "staged $(ls "$T/in" | wc -l) files; opening the Sandbox"
cmd.exe //c start "" "$(w "$T/t12.wsb")"
