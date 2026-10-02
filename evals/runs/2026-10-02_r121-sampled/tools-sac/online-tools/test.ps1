# Do knaif's supporting tools work for an ONLINE user with Smart App Control enforcing?
# Each tool as a user would get it (winget's FFmpeg zip, Artifex's Ghostscript installer since the
# winget id knaif names no longer exists, winget's LibreOffice MSI), run directly and through the
# signed knaif 1.2.1. Code Integrity events are recorded per tool.
$ErrorActionPreference = 'Continue'
$In = 'C:\t\in'; $Out = 'C:\t\out'; $Log = "$Out\result.log"
function Log($m) { $l = "$(Get-Date -Format HH:mm:ss) $m"; Write-Host $l; Add-Content -Path $Log -Value $l }
function Sac { (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState }
function Blocked($since, $name) {
  Start-Sleep -Seconds 2
  $ev = @(Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-CodeIntegrity/Operational'; StartTime = $since } -ErrorAction SilentlyContinue)
  $ev | ForEach-Object { "$($_.TimeCreated.ToString('s')) $($_.Id) $($_.Message)" } | Set-Content "$Out\ci-$name.txt"
  $b = @($ev | Where-Object { $_.Id -in 3033, 3077 } | ForEach-Object {
      if ($_.Message -match 'attempted to load (\S+) that did not meet') { Split-Path $Matches[1] -Leaf } } | Sort-Object -Unique)
  Log "  $name CI blocked: [$($b -join ', ')]"
}
function Sigs($dir, $name) {
  $s = @(Get-ChildItem $dir -Recurse -File -Include *.exe, *.dll -ErrorAction SilentlyContinue | Get-AuthenticodeSignature)
  $g = $s | Group-Object Status | ForEach-Object { "$($_.Name)=$($_.Count)" }
  Log "  $name signatures: $($g -join ' ')"
}
function Try1($label, [scriptblock]$sb) {
  try { $o = & $sb 2>&1 | Out-String; Log "  $label : exit $LASTEXITCODE :: $((($o -split "`n") | Where-Object { $_.Trim() } | Select-Object -First 1))" }
  catch { Log "  $label : BLOCKED $($_.Exception.Message)" }
}

Set-Content -Path $Log -Value "tools-online run $(Get-Date -Format s)"
Log "Smart App Control: $(Sac) (1 = enforcing)"
try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'https://www.microsoft.com' -TimeoutSec 20; Log "network: HTTP $($r.StatusCode)" }
catch { Log "network: FAILED $($_.Exception.Message)" }

Expand-Archive "$In\knaif-1.2.1-windows-x64.zip" C:\t\knaif -Force
$exe = (Get-ChildItem C:\t\knaif -Recurse -Filter knaif.exe | Select-Object -First 1).FullName
New-Item -ItemType Directory -Force "$env:USERPROFILE\.knaif\models", C:\t\work | Out-Null
Copy-Item 'C:\t\models\knaif-qwen3-4b-v2-q4_k_m.gguf' "$env:USERPROFILE\.knaif\models\"
Copy-Item "$In\clip.mp4", "$In\sample.pdf", "$In\sample.docx" C:\t\work\
Set-Location C:\t\work

# ── FFmpeg (winget portable zip, Gyan 9.0.2) ──
Log 'FFmpeg'
$t = Get-Date
Expand-Archive "$In\ffmpeg.zip" C:\tools -Force
$bin = (Get-ChildItem C:\tools -Recurse -Filter ffmpeg.exe | Select-Object -First 1).DirectoryName
Sigs $bin 'ffmpeg'
Try1 'ffmpeg -version' { & "$bin\ffmpeg.exe" -version }
$env:PATH = "$bin;$env:PATH"
& $exe run ffmpeg --yes 'convert clip.mp4 to mkv' *> "$Out\knaif-ffmpeg.log"
Log "  knaif ffmpeg run: exit $LASTEXITCODE; mkv exists $(Test-Path C:\t\work\clip_converted.mkv)"
Blocked $t 'ffmpeg'

# ── Ghostscript (Artifex installer 10.07.1) ──
Log 'Ghostscript'
$t = Get-Date
try { $p = Start-Process "$In\gs-setup.exe" -ArgumentList '/S' -PassThru -ErrorAction Stop; $p.WaitForExit(300000) | Out-Null; Log "  installer: exit $($p.ExitCode)" }
catch { Log "  installer: BLOCKED $($_.Exception.Message)" }
$gs = Get-ChildItem 'C:\Program Files\gs' -Recurse -Filter gswin64c.exe -ErrorAction SilentlyContinue | Select-Object -First 1
Log "  installed: $([bool]$gs)"
if ($gs) {
  Sigs $gs.Directory.Parent.FullName 'ghostscript'
  Try1 'gswin64c -version' { & $gs.FullName -version }
  & $exe run documents --yes 'compress sample.pdf as small as possible' *> "$Out\knaif-gs.log"
  Log "  knaif compress run: exit $LASTEXITCODE; made [$((Get-ChildItem C:\t\work -Filter 'sample-compressed*').Name -join ', ')]"
}
Blocked $t 'ghostscript'

# ── LibreOffice (winget MSI 26.8.0.3) ──
Log 'LibreOffice'
$t = Get-Date
if (Test-Path "$In\libreoffice.msi") {
  $p = Start-Process msiexec.exe -ArgumentList '/i', "`"$In\libreoffice.msi`"", '/qn', '/norestart', "/l*v `"$Out\lo-msi.log`"" -PassThru
  $p.WaitForExit(1200000) | Out-Null
  Log "  msiexec: exit $($p.ExitCode)"
  $so = 'C:\Program Files\LibreOffice\program\soffice.com'
  Log "  installed: $(Test-Path $so)"
  if (Test-Path $so) {
    Sigs 'C:\Program Files\LibreOffice\program' 'libreoffice'
    Try1 'soffice --version' { & $so --version }
    & $exe run documents --yes 'convert sample.docx to pdf' *> "$Out\knaif-lo.log"
    Log "  knaif convert run: exit $LASTEXITCODE; made [$((Get-ChildItem C:\t\work -Filter 'sample*.pdf' | Where-Object Name -ne 'sample.pdf').Name -join ', ')]"
  }
} else { Log '  no msi staged' }
Blocked $t 'libreoffice'

& $exe skills deps *> "$Out\deps.log"
Log "Smart App Control at end: $(Sac)"
Log 'DONE'
