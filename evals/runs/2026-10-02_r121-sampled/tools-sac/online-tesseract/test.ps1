# Does Smart App Control block Tesseract for a normal, ONLINE user? (The release clean room ran
# offline, where SAC cannot ask Microsoft's cloud for a file's reputation.) Networking is ON here.
# Installs the exact winget installer (UB-Mannheim 5.4.0.20240606, sha256 c885fff6...), then runs
# tesseract directly and through the signed knaif 1.2.1, recording the Code Integrity events.
$ErrorActionPreference = 'Continue'
$In = 'C:\t\in'; $Out = 'C:\t\out'; $Log = "$Out\result.log"
function Log($m) { $l = "$(Get-Date -Format HH:mm:ss) $m"; Write-Host $l; Add-Content -Path $Log -Value $l }
function Sac { (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState }
Set-Content -Path $Log -Value "tesseract-online run $(Get-Date -Format s)"
$since = Get-Date
Log "Smart App Control: $(Sac) (1 = enforcing)"
try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'https://www.microsoft.com' -TimeoutSec 20; Log "network: HTTP $($r.StatusCode)" }
catch { Log "network: FAILED $($_.Exception.Message)" }

# 1. The installer itself (NSIS; signature expired and untimestamped).
try {
  $p = Start-Process "$In\tesseract-setup.exe" -ArgumentList '/S' -PassThru -ErrorAction Stop
  $p.WaitForExit(300000) | Out-Null
  Log "installer: exit $($p.ExitCode)"
} catch { Log "installer: BLOCKED $($_.Exception.Message)" }
$tess = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
Log "installed: $(Test-Path $tess)"

# 2. tesseract directly.
if (Test-Path $tess) {
  try { $v = & $tess --version 2>&1 | Out-String; Log "tesseract --version: exit $LASTEXITCODE :: $(($v -split "`n")[0].Trim())" }
  catch { Log "tesseract --version: BLOCKED $($_.Exception.Message)" }
}

# 3. Through knaif 1.2.1 (signed), as a user would: OCR a scanned PDF, CPU, no --model.
Expand-Archive "$In\knaif-1.2.1-windows-x64.zip" C:\t\knaif -Force
$exe = (Get-ChildItem C:\t\knaif -Recurse -Filter knaif.exe | Select-Object -First 1).FullName
New-Item -ItemType Directory -Force "$env:USERPROFILE\.knaif\models", C:\t\work | Out-Null
Copy-Item 'C:\t\models\knaif-qwen3-4b-v2-q4_k_m.gguf' "$env:USERPROFILE\.knaif\models\"
Copy-Item "$In\sample-scanned.pdf" C:\t\work\
Set-Location C:\t\work
& $exe skills deps *> "$Out\deps.log"
& $exe run documents --yes 'run ocr on sample-scanned.pdf' *> "$Out\ocr.log"
$code = $LASTEXITCODE
$made = @(Get-ChildItem C:\t\work -File | Where-Object Name -ne 'sample-scanned.pdf')
Log "knaif OCR: exit $code; produced [$($made.Name -join ', ')]"
$made | Copy-Item -Destination $Out

# 4. What Code Integrity blocked, and what it allowed on reputation.
Start-Sleep -Seconds 2
$ev = @(Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-CodeIntegrity/Operational'; StartTime = $since } -ErrorAction SilentlyContinue)
$ev | ForEach-Object { "$($_.TimeCreated.ToString('s')) $($_.Id) $($_.Message)" } | Set-Content "$Out\ci-events.txt"
$blocked = @($ev | Where-Object { $_.Id -in 3033, 3077 } | ForEach-Object {
    if ($_.Message -match 'attempted to load (\S+) that did not meet') { Split-Path $Matches[1] -Leaf } } | Sort-Object -Unique)
Log "CI events: $($ev.Count); blocked: [$($blocked -join ', ')]"
Log "Smart App Control at end: $(Sac)"
Log 'DONE'
