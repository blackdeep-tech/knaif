# 1.2.1 RELEASE clean room + upgrade, on the frozen signed build (adapted from the pre-freeze run in
# sandbox/r121/t12_sandbox.ps1). Runs INSIDE Windows Sandbox: no network, no GPU, no developer
# tooling. Writes C:\t12\out\t12.log and t12_results.json; grade.py reads them on the host.
#
# Smart App Control: the Sandbox on the owner's build enforces it, which blocks the UNSIGNED 1.2.0
# installer outright. The run records the state, proves that block, turns enforcement off only to
# install the 1.2.0 baseline, turns it back ON, proves the old binary is now blocked, and does the
# whole 1.2.1 half (upgrade, OCR inference on the CPU backends, B5) under enforcement.
$ErrorActionPreference = 'Continue'
$In = 'C:\t12\in'; $Out = 'C:\t12\out'; $Log = "$Out\t12.log"
$SetupOld = "$In\knaif-1.2.0-windows-x64-setup.exe"
$SetupNew = "$In\knaif-1.2.1-windows-x64-setup.exe"
$expect = Get-Content "$In\expected.json" -Raw | ConvertFrom-Json
$results = [ordered]@{}
$SacKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy'

function Log($m) { $l = "$(Get-Date -Format HH:mm:ss) $m"; Write-Host $l; Add-Content -Path $Log -Value $l }
function Check($id, $ok, $detail) {
  $results[$id] = [ordered]@{ pass = [bool]$ok; detail = "$detail" }
  Log ("{0} {1}: {2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $id, $detail)
}
function Rows {
  Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                   'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                   'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match 'knaif' }
}
function RunSetup($exe, $logName, $timeoutS) {
  try {
    $p = Start-Process $exe -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$Out\$logName`"" -PassThru -ErrorAction Stop
  } catch { return "blocked: $($_.Exception.Message)" }
  $null = $p.Handle
  if (-not $p.WaitForExit($timeoutS * 1000)) { $p | Stop-Process -Force; return 'timeout' }
  return $p.ExitCode
}
function Hash($f) { if (Test-Path $f) { (Get-FileHash -Algorithm SHA256 $f).Hash.ToLower() } else { 'missing' } }
function Sac { (Get-ItemProperty $SacKey -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState }
function SetSac($v) {
  Set-ItemProperty $SacKey VerifiedAndReputablePolicyState $v
  # CiTool ends with "Press Enter to Continue"; answer it, or the unattended run waits forever.
  cmd /c "echo.| CiTool.exe --refresh" *> "$Out\citool-$v.log"
  Start-Sleep -Seconds 3
}
function Runs($exe) {
  try { $o = & $exe --version 2>&1 | Out-String; return ($LASTEXITCODE -eq 0), $o.Trim() }
  catch { return $false, "blocked: $($_.Exception.Message)" }
}

Set-Content -Path $Log -Value "1.2.1 release clean-room run $(Get-Date -Format s)"
Log ("machine: " + (Get-CimInstance Win32_OperatingSystem).Caption + "; GPUs: " +
     ((Get-CimInstance Win32_VideoController | ForEach-Object Name) -join ', '))
Remove-Item Env:KNAIF_PDFIUM_PATH -ErrorAction SilentlyContinue
Set-Location C:\
$sac0 = Sac
Log "Smart App Control at start: $sac0 (1 = enforcing)"
$enforcing = ($sac0 -eq 1)

# 1. Clean room: the unpacked 1.2.1 zip, under whatever enforcement the box has.
Expand-Archive "$In\knaif-1.2.1-windows-x64.zip" C:\t12\zip -Force
$zipRoot = (Get-ChildItem C:\t12\zip -Directory | Select-Object -First 1).FullName
$zipExe = "$zipRoot\bin\knaif.exe"
$h = Hash $zipExe
Check 'cleanroom_binary_is_new' ($h -eq $expect.new_exe) "sha256 $h"
$ok, $v = Runs $zipExe
Check 'cleanroom_version_is_121' ($ok -and ($v -match '^knaif 1\.2\.1 ')) "$v"
$sl = & $zipExe skills list 2>&1 | Out-String; $code = $LASTEXITCODE
Check 'cleanroom_skills_list' (($code -eq 0) -and ($sl -match 'ffmpeg') -and ($sl -match 'documents')) "exit $code"
$unsigned = @(Get-ChildItem $zipRoot -Recurse -File -Include *.exe, *.dll | Get-AuthenticodeSignature |
  Where-Object { $_.Status -ne 'Valid' } | ForEach-Object { Split-Path $_.Path -Leaf })
Check 'zip_every_binary_signed' ($unsigned.Count -eq 0) "not valid: [$($unsigned -join ', ')]"

# 2. The unsigned 1.2.0 installer under enforcement is blocked (only meaningful when enforcing).
if ($enforcing) {
  $c = RunSetup $SetupOld 'install-1.2.0-blocked.log' 120
  $r = @(Rows)
  Check 'sac_blocks_unsigned_120' (($c -is [string]) -and ($c -like 'blocked*') -and ($r.Count -eq 0)) "result $c; rows $($r.Count)"
  SetSac 0
  Log "Smart App Control set off for the 1.2.0 baseline: $(Sac)"
}

# 3. Baseline: the published 1.2.0, installed silently.
$c = RunSetup $SetupOld 'install-1.2.0.log' 600
$r = @(Rows)
$loc = if ($r.Count -eq 1) { $r[0].InstallLocation.TrimEnd('\') } else { '' }
Check 'install_120' (($c -eq 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.2.0')) "exit $c; rows $($r.Count); version $($r.DisplayVersion); at $loc"
$exe = "$loc\bin\knaif.exe"
$h = Hash $exe
Check 'installed_120_is_published' ($h -eq $expect.old_exe) "sha256 $h"

# 4. A running 1.2.0 CLI waiting at its first-run model download prompt holds the mutex.
$holder = Start-Process $exe -ArgumentList 'run', 'documents', 'rotate', 'doc.pdf' -WorkingDirectory C:\t12 -PassThru
Start-Sleep -Seconds 8
$m = $null
$held = [System.Threading.Mutex]::TryOpenExisting('knaif-cli-running', [ref]$m)
if ($m) { $m.Dispose() }
Check 'cli_running_holds_mutex' ((-not $holder.HasExited) -and $held) "alive $(-not $holder.HasExited); mutex $held"

# 5. Upgrade while it runs: refused, nothing changed.
$c = RunSetup $SetupNew 'upgrade-while-running.log' 300
$r = @(Rows)
$h = Hash $exe
Check 'upgrade_refused_while_running' (($c -isnot [string]) -and ($c -ne 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.2.0') -and ($h -eq $expect.old_exe)) "exit $c; rows $($r.Count); version $($r.DisplayVersion)"
if (-not $holder.HasExited) { $holder | Stop-Process -Force; $holder.WaitForExit(10000) | Out-Null }

# 6. Enforcement back on: the installed, unsigned 1.2.0 binary no longer runs.
if ($enforcing) {
  SetSac 1
  Log "Smart App Control back on: $(Sac)"
  $ok, $v = Runs $exe
  Check 'sac_blocks_installed_120' (-not $ok) "$v"
}

# 7. Upgrade (under enforcement when the box enforces): one row at 1.2.1, same folder, no leftovers.
$c = RunSetup $SetupNew 'upgrade.log' 900
$r = @(Rows)
$loc2 = if ($r.Count -eq 1) { $r[0].InstallLocation.TrimEnd('\') } else { '' }
Check 'upgrade_installs' (($c -eq 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.2.1')) "exit $c; rows $($r.Count); version $($r.DisplayVersion)"
Check 'upgrade_publisher' (($r.Count -eq 1) -and ($r[0].Publisher -eq 'Blackdeep Technologies Ltd')) "publisher '$($r.Publisher)'"
Check 'upgrade_same_folder' ($loc2 -eq $loc) "before $loc; after $loc2"
$dirWarn = Select-String -Path "$Out\upgrade.log" -Pattern 'already exists' -SimpleMatch -Quiet
Check 'upgrade_no_folder_exists_prompt' (-not $dirWarn) "log mentions 'already exists': $dirWarn"
$h = Hash $exe
Check 'upgraded_binary_is_new' ($h -eq $expect.new_exe) "sha256 $h"
$ok, $v = Runs $exe
Check 'upgraded_version_is_121' ($ok -and ($v -match '^knaif 1\.2\.1 ')) "$v"
$zipBin = Get-ChildItem "$zipRoot\bin" -File | ForEach-Object Name
$instBin = Get-ChildItem "$loc2\bin" -File | ForEach-Object Name
$left = @($instBin | Where-Object { $zipBin -notcontains $_ })
$miss = @($zipBin | Where-Object { $instBin -notcontains $_ })
Check 'upgrade_no_leftover_libraries' ($left.Count -eq 0) "not in the 1.2.1 artifact: [$($left -join ', ')]; in the artifact but not installed (info): [$($miss -join ', ')]"
$sigs = @(Get-ChildItem $loc2 -Recurse -File -Include *.exe, *.dll | Get-AuthenticodeSignature)
$bad = @($sigs | Where-Object { $_.Status -ne 'Valid' } | ForEach-Object { Split-Path $_.Path -Leaf })
$ours = @($sigs | Where-Object { $_.SignerCertificate.Subject -like 'CN=Blackdeep Technologies Ltd,*' }).Count
$ms = @($sigs | Where-Object { $_.SignerCertificate.Subject -like '*Microsoft*' }).Count
Check 'installed_every_binary_signed' (($bad.Count -eq 0) -and ($ours -eq 17) -and ($ms -eq 4)) "not valid [$($bad -join ', ')]; Blackdeep $ours; Microsoft $ms"

# 8. One OCR row through the installed 1.2.1, no --model, GPU-less box, bundled PDFium: real
#    inference, so the signed ggml-cpu backends load (under enforcement when the box enforces).
New-Item -ItemType Directory -Force "$env:USERPROFILE\.knaif\models", C:\t12\work, "$Out\ocr", "$Out\b5" | Out-Null
Copy-Item 'C:\t12\models\knaif-qwen3-4b-v2-q4_k_m.gguf' "$env:USERPROFILE\.knaif\models\"
Copy-Item "$In\sample-scanned.pdf", "$In\sample.pdf" C:\t12\work\
$env:PATH = "C:\t12\tesseract;$env:PATH"
Set-Location C:\t12\work
function Made { @(Get-ChildItem C:\t12\work -File | Where-Object { $_.Name -notin 'sample-scanned.pdf', 'sample.pdf' }) }
function Ocr($logName) {
  $t0 = Get-Date
  & $exe run documents --yes --verbose 'run ocr on sample-scanned.pdf' *> "$Out\$logName"
  return $LASTEXITCODE, [int]((Get-Date) - $t0).TotalSeconds
}

# 8a. B5 through the installed binary, under enforcement: lock with a backslash password.
& $exe run documents --yes 'password-protect sample.pdf with the password p\ss' *> "$Out\b5.log"
$code = $LASTEXITCODE
$locked = Made
$locked | Copy-Item -Destination "$Out\b5\"
Check 'b5_protect_runs' (($code -eq 0) -and ($locked.Count -eq 1)) "exit $code; produced [$($locked.Name -join ', ')]"
$locked | Remove-Item

# 8b. OCR under enforcement — INFORMATION: Tesseract (UB-Mannheim build, mapped from the host) ships
#     unsigned DLLs, which Smart App Control blocks; knaif's own binaries are what this run checks.
if ($enforcing) {
  $since = Get-Date
  $code, $secs = Ocr 'ocr-enforced.log'
  Made | Remove-Item
  Log "INFO ocr_under_enforcement: exit $code in ${secs}s"
  Start-Sleep -Seconds 2
  $events = @(Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-CodeIntegrity/Operational'; StartTime = $since } -ErrorAction SilentlyContinue)
  "events since $($since.ToString('s')): $($events.Count)" | Set-Content "$Out\ci-events.txt"
  $events | ForEach-Object { "$($_.TimeCreated.ToString('s')) $($_.Id) $($_.Message)" } | Add-Content "$Out\ci-events.txt"
  # Every file a blocking event names (3033 / 3077: "...attempted to load <path> that did not meet...").
  $blocked = @($events | Where-Object { $_.Id -in 3033, 3077 } | ForEach-Object {
      if ($_.Message -match 'attempted to load (\S+) that did not meet') { Split-Path $Matches[1] -Leaf } } |
    Sort-Object -Unique)
  $ours = @($blocked | Where-Object { $_ -match '^(knaif|llama|ggml|pdfium)' })
  # Not vacuous: the OCR failure must be explained by blocked Tesseract files, and none of them ours.
  $explained = @($blocked | Where-Object { $_ -match 'tesseract|^lib' }).Count -gt 0
  Check 'sac_blocks_nothing_of_knaif' ($explained -and ($ours.Count -eq 0)) "$($events.Count) CI events; blocked: [$($blocked -join ', ')]; ours: [$($ours -join ', ')]"
  SetSac 0
  Log "Smart App Control set off for the OCR check (third-party Tesseract): $(Sac)"
}

# 8c. The OCR row: no --model, GPU-less box, bundled PDFium, real inference on the CPU backends.
$code, $secs = Ocr 'ocr.log'
$made = Made
$made | Copy-Item -Destination "$Out\ocr\"
Check 'ocr_row_runs' (($code -eq 0) -and ($made.Count -ge 1)) "exit $code in ${secs}s; produced [$($made.Name -join ', ')]"
$pdfiumErr = Select-String -Path "$Out\ocr.log" -Pattern 'pdfium' -Quiet
Check 'ocr_no_pdfium_error' (-not ($pdfiumErr -and ($code -ne 0))) "log mentions pdfium: $pdfiumErr"
Log "Smart App Control at end: $(Sac)"

$results | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 "$Out\t12_results.json"
$fails = @($results.Keys | Where-Object { -not $results[$_].pass })
Log "DONE: $($results.Count) checks, $($fails.Count) failed [$($fails -join ', ')]. Grade on the host (grade.py). Close this Sandbox window when done; it discards everything."
