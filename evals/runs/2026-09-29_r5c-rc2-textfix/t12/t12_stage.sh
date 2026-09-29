#!/usr/bin/env bash
# RC2 re-run (2026-09-29): the same checks as T12 on the RC2 artifacts, the installer now naming
# the v2 model. Rules below are unchanged from T12 except the artifact hashes.
# Release 1.2.0 R5c T12 (docs/plans/2026-09-25-release-1.2.md; RELEASE.md §4): the Windows
# installer and clean room, on RC 71884fd's FROZEN bytes, inside Windows Sandbox so the host's own
# install is never touched. Stages the inputs, writes the .wsb (it names host paths, so it stays in
# the gitignored sandbox/ folder), and opens the Sandbox; t12_sandbox.ps1 runs there on logon.
#
#   bash evals/runs/2026-09-28_r5c-windows_success/t12/t12_stage.sh     then, after the Sandbox
#   uv run python evals/runs/2026-09-28_r5c-windows_success/t12/t12_grade.py              says DONE
#
# Inputs, each checked before staging: knaif-1.2.0-windows-x64-setup.exe (6df5f520...) and
# knaif-1.2.0-windows-x64.zip (b86cbfbf...), both RC2 (the text fix, 36f9651); knaif-1.1.0-windows-x64-setup.exe
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
# AMENDED 2026-09-28 after the first pass (11/13; the OCR text check passed): its mutex holder
#   was a 1.1.0 `run ffmpeg`, which exits at its tool check when ffmpeg is not on PATH, so nothing
#   held the mutex and the 'refused while running' attempt upgraded instead. The holder is now a
#   1.1.0 `run documents` at its first-run download prompt (reproduced on the host). Rules unchanged.
# AMENDED again after the second pass (the refusal PASSED): the script's own mutex check kept a
#   handle open, and Inno refuses while the mutex merely exists, so the upgrade after closing the
#   CLI was refused too. The handle is now closed right after the check. Rules unchanged.
set -euo pipefail
cd "$(dirname "$0")/../../../.."
T=sandbox/r5c/t12
H=evals/runs/2026-09-29_r5c-rc2-textfix/t12
check() { [ "$(sha256sum "$1" | cut -c1-64)" = "$2" ] || { echo "HASH MISMATCH: $1" >&2; exit 1; }; }
check dist/knaif-1.2.0-windows-x64-setup.exe 6df5f5209e8ecddbb8a29059caa97ae6bc0d23be5ad60935ff5ca8e9e74dba2f
check dist/knaif-1.2.0-windows-x64.zip b86cbfbf1da3b66ac1b94750c577821e1b5e3e57ad2e92bdcb23d26e33a983cf
check "$T/in/knaif-1.1.0-windows-x64-setup.exe" b0e2c7e22197d0a189ba2bc1b28b82206d2c54c46d96a2d464dac342b94e5c4b
# One Sandbox at a time: close any left open (its results are already on the host, in out/).
for id in $(wsb list --raw 2>/dev/null | grep -oE '[0-9a-fA-F-]{36}'); do wsb stop --id "$id" >/dev/null 2>&1 || true; done
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
