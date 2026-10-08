<#
.SYNOPSIS
  Sign Windows binaries with knaif's Azure Artifact Signing profile.

.DESCRIPTION
  The maintainer's signer for KNAIF_SIGN_CMD (installers/sign_stage.sh) and for Inno Setup's
  `knaifsign` tool (`just installer`). Endpoint, account and certificate profile come from
  installers/windows/signing.json, so nothing here changes when the profile does.

  Authenticates as whoever ran `az login` — no key or secret exists on disk. Needs:
    - signtool.exe from the Windows SDK (10.0.22621 or newer), or $env:KNAIF_SIGNTOOL
    - the Artifact Signing client (winget install -e --id Microsoft.Azure.ArtifactSigningClientTools),
      found in its per-user or per-machine location, or $env:KNAIF_SIGN_DLIB
    - the "Artifact Signing Certificate Profile Signer" role on the account

  Every signature is timestamped: the certificates Artifact Signing issues live for days, and an
  untimestamped signature stops validating when its certificate expires.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sign_windows.ps1 dist\setup.exe
#>
param(
  [switch]$PrintMetadata,
  [switch]$CheckTools,
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$Files
)
$ErrorActionPreference = 'Stop'

# Inno's ISCC is a 32-bit program, so the PowerShell it starts for `knaifsign` is 32-bit as well,
# and there $env:ProgramFiles is "Program Files (x86)" — which hides the 64-bit Azure CLI. Rather
# than special-case every path, re-run in 64-bit PowerShell (sysnative reaches the real System32
# from a 32-bit process).
if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
  $ps64 = Join-Path $env:WINDIR 'sysnative\WindowsPowerShell\v1.0\powershell.exe'
  $forward = @()
  if ($PrintMetadata) { $forward += '-PrintMetadata' }
  if ($CheckTools) { $forward += '-CheckTools' }
  & $ps64 -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath @forward @Files
  exit $LASTEXITCODE
}

$repo = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content -Raw -LiteralPath (Join-Path $repo 'installers\windows\signing.json') | ConvertFrom-Json
$metadata = [ordered]@{
  Endpoint               = $cfg.endpoint
  CodeSigningAccountName = $cfg.account
  CertificateProfileName = $cfg.certificate_profile
}
if ($PrintMetadata) { $metadata | ConvertTo-Json; exit 0 }
if (-not $Files -and -not $CheckTools) { throw 'usage: sign_windows.ps1 [-CheckTools] <file> [<file> ...]' }

function First-Existing([string[]]$candidates) {
  $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}

$signtool = First-Existing (@($env:KNAIF_SIGNTOOL) + @(
    Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.*\x64\signtool.exe" -ErrorAction SilentlyContinue |
      Sort-Object { [version]$_.Directory.Parent.Name } -Descending | ForEach-Object FullName))
if (-not $signtool) { throw 'signtool.exe not found: install the Windows SDK or set KNAIF_SIGNTOOL.' }

$dlib = First-Existing @(
  $env:KNAIF_SIGN_DLIB,
  "$env:LOCALAPPDATA\Microsoft\MicrosoftArtifactSigningClientTools\Azure.CodeSigning.Dlib.dll",
  "${env:ProgramFiles(x86)}\Microsoft\ArtifactSigningClientTools\bin\Azure.CodeSigning.Dlib.dll")
if (-not $dlib) {
  throw 'Azure.CodeSigning.Dlib.dll not found: winget install -e --id Microsoft.Azure.ArtifactSigningClientTools, or set KNAIF_SIGN_DLIB.'
}

# The dlib authenticates through the Azure CLI's login, so `az` must resolve. Its installer adds
# it to PATH, but only for shells started afterwards.
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
  $azDir = "$env:ProgramFiles\Microsoft SDKs\Azure\CLI2\wbin"
  if (Test-Path -LiteralPath $azDir) { $env:PATH = "$azDir;$env:PATH" }
  else { throw "Azure CLI not found: winget install -e --id Microsoft.AzureCLI, then 'az login'." }
}

if ($CheckTools) {
  "signtool: $signtool"
  "dlib:     $dlib"
  "az:       $((Get-Command az).Source)"
  exit 0
}

$meta = Join-Path ([IO.Path]::GetTempPath()) "knaif-sign-$PID.json"
try {
  $metadata | ConvertTo-Json | Set-Content -LiteralPath $meta -Encoding ASCII
  & $signtool sign /fd SHA256 /tr $cfg.timestamp_url /td SHA256 /dlib $dlib /dmdf $meta @Files
  if ($LASTEXITCODE) { throw "signtool sign failed with exit code $LASTEXITCODE." }
  & $signtool verify /pa /q @Files
  if ($LASTEXITCODE) { throw "signtool verify failed with exit code $LASTEXITCODE." }
}
finally {
  Remove-Item -LiteralPath $meta -ErrorAction SilentlyContinue
}
