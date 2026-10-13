# knaif 1.3.0 Windows clean room (release plan, gate rules: "Clean room"; RELEASE.md §4), on the
# frozen, signed build. Runs INSIDE Windows Sandbox: no network, no GPU, no developer tooling.
# Writes C:\r130\out\cleanroom.log and results.json; grade.py reads them on the host.
#
# What the rule asks, and where it is checked:
#   fresh install                 -> 2 (install, a real request with no --model, uninstall)
#   upgrade over 1.2.1            -> 3 (the published, signed 1.2.1; it has no daemon)
#   reinstall over a daemon       -> 4 (a daemon started from the installed 1.3.0; setup must stop it)
#   uninstall over a daemon       -> 5 (the same, for the uninstaller)
#   "no knaif.exe running or locked" after each: no knaif.exe process, and the replaced/removed
#   files really were replaced/removed.
#   Tool choices, the finish page: the wizard pages, which /VERYSILENT never builds -> the manual
#   part (report.md); `knaif skills deps` naming the missing tools is checked here (6).
# Smart App Control is recorded; under enforcement everything of knaif's must still run.
$ErrorActionPreference = 'Continue'
$In = 'C:\r130\in'; $Out = 'C:\r130\out'; $Log = "$Out\cleanroom.log"
$SetupOld = "$In\knaif-1.2.1-windows-x64-setup.exe"
$SetupNew = "$In\knaif-1.3.0-windows-x64-setup.exe"
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
function RunExe($exe, $argList, $timeoutS) {
  try { $p = Start-Process $exe -ArgumentList $argList -PassThru -ErrorAction Stop }
  catch { return "blocked: $($_.Exception.Message)" }
  $null = $p.Handle
  if (-not $p.WaitForExit($timeoutS * 1000)) { $p | Stop-Process -Force; return 'timeout' }
  return $p.ExitCode
}
function RunSetup($exe, $logName, $timeoutS) {
  RunExe $exe @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$Out\$logName`"") $timeoutS
}
function Uninstall($logName) {
  $r = @(Rows)
  if ($r.Count -ne 1) { return "rows $($r.Count)" }
  $u = ($r[0].UninstallString -replace '"', '')
  RunExe $u @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$Out\$logName`"") 300
}
function Hash($f) { if (Test-Path $f) { (Get-FileHash -Algorithm SHA256 $f).Hash.ToLower() } else { 'missing' } }
function Sac { (Get-ItemProperty $SacKey -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState }
function Runs($exe) {
  try { $o = & $exe --version 2>&1 | Out-String; return ($LASTEXITCODE -eq 0), $o.Trim() }
  catch { return $false, "blocked: $($_.Exception.Message)" }
}
function KnaifProcs { @(Get-Process knaif -ErrorAction SilentlyContinue) }
function Loc { $r = @(Rows); if ($r.Count -eq 1) { $r[0].InstallLocation.TrimEnd('\') } else { '' } }
function DaemonStart($exe) {
  & $exe daemon start --model "$env:USERPROFILE\.knaif\models\knaif-qwen3-4b-v2-q4_k_m.gguf" *> "$Out\daemon-start.log"
  Start-Sleep -Seconds 2
  $s = & $exe daemon status 2>&1 | Out-String
  return ($s -match 'is running \(pid'), $s.Trim()
}
function Signed($root) {
  $sigs = @(Get-ChildItem $root -Recurse -File -Include *.exe, *.dll | Get-AuthenticodeSignature)
  $bad = @($sigs | Where-Object { $_.Status -ne 'Valid' } | ForEach-Object { Split-Path $_.Path -Leaf })
  $ours = @($sigs | Where-Object { $_.SignerCertificate.Subject -like 'CN=Blackdeep Technologies Ltd,*' }).Count
  $ms = @($sigs | Where-Object { $_.SignerCertificate.Subject -like '*Microsoft*' }).Count
  return $bad, $ours, $ms
}

Set-Content -Path $Log -Value "1.3.0 release clean-room run $(Get-Date -Format s)"
Log ("machine: " + (Get-CimInstance Win32_OperatingSystem).Caption + "; GPUs: " +
     ((Get-CimInstance Win32_VideoController | ForEach-Object Name) -join ', '))
Remove-Item Env:KNAIF_PDFIUM_PATH -ErrorAction SilentlyContinue
Set-Location C:\
Log "Smart App Control at start: $(Sac) (1 = enforcing)"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.knaif\models", C:\r130\work | Out-Null
Copy-Item 'C:\r130\models\knaif-qwen3-4b-v2-q4_k_m.gguf' "$env:USERPROFILE\.knaif\models\"

# 1. The unpacked zip.
Expand-Archive "$In\knaif-1.3.0-windows-x64.zip" C:\r130\zip -Force
$zipRoot = (Get-ChildItem C:\r130\zip -Directory | Select-Object -First 1).FullName
$zipExe = "$zipRoot\bin\knaif.exe"
$h = Hash $zipExe
Check 'zip_binary_is_frozen' ($h -eq $expect.new_exe) "sha256 $h"
$ok, $v = Runs $zipExe
Check 'zip_version_is_130' ($ok -and ($v -match '^knaif 1\.3\.0 ')) "$v"
$sl = & $zipExe skills list 2>&1 | Out-String; $code = $LASTEXITCODE
Check 'zip_skills_list' (($code -eq 0) -and ($sl -match 'ffmpeg') -and ($sl -match 'documents')) "exit $code"
$bad, $zipOurs, $zipMs = Signed $zipRoot
Check 'zip_every_binary_signed' ($bad.Count -eq 0) "not valid [$($bad -join ', ')]; Blackdeep $zipOurs; Microsoft $zipMs"

# 2. Fresh install, one real request (no --model, GPU-less box, CPU backends), uninstall.
$c = RunSetup $SetupNew 'fresh-install.log' 600
$r = @(Rows); $loc = Loc; $exe = "$loc\bin\knaif.exe"
Check 'fresh_install' (($c -eq 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.3.0')) "exit $c; rows $($r.Count); version $($r.DisplayVersion); at $loc"
Check 'fresh_publisher' (($r.Count -eq 1) -and ($r[0].Publisher -eq 'Blackdeep Technologies Ltd')) "publisher '$($r.Publisher)'"
$bad, $ours, $ms = Signed $loc
Check 'fresh_every_binary_signed' (($bad.Count -eq 0) -and ($ours -eq $zipOurs + 1) -and ($ms -eq $zipMs)) "not valid [$($bad -join ', ')]; Blackdeep $ours (zip $zipOurs + uninstaller); Microsoft $ms"
Copy-Item "$In\sample.pdf" C:\r130\work\
Set-Location C:\r130\work
& $exe run documents --yes 'rotate sample.pdf 90 degrees' *> "$Out\fresh-request.log"
$code = $LASTEXITCODE
$made = @(Get-ChildItem C:\r130\work -File | Where-Object { $_.Name -ne 'sample.pdf' })
$isPdf = ($made.Count -eq 1) -and ([System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes($made[0].FullName)[0..3]) -eq '%PDF')
Check 'fresh_real_request' (($code -eq 0) -and $isPdf) "exit $code; produced [$($made.Name -join ', ')]"
$made | Remove-Item
Set-Location C:\
$c = Uninstall 'fresh-uninstall.log'
Start-Sleep -Seconds 3
$r = @(Rows)
Check 'fresh_uninstall' (($c -eq 0) -and ($r.Count -eq 0) -and -not (Test-Path $exe) -and ((KnaifProcs).Count -eq 0)) "exit $c; rows $($r.Count); exe left $(Test-Path $exe); knaif processes $((KnaifProcs).Count)"

# 3. Upgrade over the published 1.2.1 (signed; no daemon), refused while its CLI runs.
$c = RunSetup $SetupOld 'install-1.2.1.log' 600
$r = @(Rows); $loc = Loc; $exe = "$loc\bin\knaif.exe"
Check 'install_121' (($c -eq 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.2.1')) "exit $c; rows $($r.Count); version $($r.DisplayVersion)"
$h = Hash $exe
Check 'installed_121_is_published' ($h -eq $expect.old_exe) "sha256 $h"
$holder = Start-Process $exe -ArgumentList 'run', 'documents', 'rotate', 'doc.pdf' -WorkingDirectory C:\r130 -PassThru
Start-Sleep -Seconds 8
$m = $null
$held = [System.Threading.Mutex]::TryOpenExisting('knaif-cli-running', [ref]$m)
if ($m) { $m.Dispose() }
Check 'cli_121_holds_mutex' ((-not $holder.HasExited) -and $held) "alive $(-not $holder.HasExited); mutex $held"
$c = RunSetup $SetupNew 'upgrade-while-running.log' 300
$r = @(Rows); $h = Hash $exe
Check 'upgrade_refused_while_running' (($c -isnot [string]) -and ($c -ne 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.2.1') -and ($h -eq $expect.old_exe)) "exit $c; rows $($r.Count); version $($r.DisplayVersion)"
if (-not $holder.HasExited) { $holder | Stop-Process -Force; $holder.WaitForExit(10000) | Out-Null }
$c = RunSetup $SetupNew 'upgrade.log' 900
$r = @(Rows); $loc2 = Loc
Check 'upgrade_installs' (($c -eq 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.3.0')) "exit $c; rows $($r.Count); version $($r.DisplayVersion)"
Check 'upgrade_same_folder' ($loc2 -eq $loc) "before $loc; after $loc2"
$dirWarn = Select-String -Path "$Out\upgrade.log" -Pattern 'already exists' -SimpleMatch -Quiet
Check 'upgrade_no_folder_exists_prompt' (-not $dirWarn) "log mentions 'already exists': $dirWarn"
$h = Hash $exe
Check 'upgraded_binary_is_frozen' ($h -eq $expect.new_exe) "sha256 $h"
$ok, $v = Runs $exe
Check 'upgraded_version_is_130' ($ok -and ($v -match '^knaif 1\.3\.0 ')) "$v"
$zipBin = Get-ChildItem "$zipRoot\bin" -File | ForEach-Object Name
$instBin = Get-ChildItem "$loc2\bin" -File | ForEach-Object Name
$left = @($instBin | Where-Object { $zipBin -notcontains $_ })
Check 'upgrade_no_leftover_libraries' ($left.Count -eq 0) "not in the 1.3.0 artifact: [$($left -join ', ')]"
$bad, $ours, $ms = Signed $loc2
Check 'upgraded_every_binary_signed' (($bad.Count -eq 0) -and ($ours -eq $zipOurs + 1) -and ($ms -eq $zipMs)) "not valid [$($bad -join ', ')]; Blackdeep $ours; Microsoft $ms"

# 4. Reinstall the same 1.3.0 over a running daemon: setup stops it, replaces the files.
$up, $s = DaemonStart $exe
Check 'daemon_started' $up ($s -replace "`r?`n", ' | ')
$c = RunSetup $SetupNew 'reinstall-over-daemon.log' 600
Start-Sleep -Seconds 3
$s = & $exe daemon status 2>&1 | Out-String
$procs = (KnaifProcs).Count
Check 'reinstall_over_daemon' (($c -eq 0) -and ($s -match 'is not running') -and ($procs -eq 0) -and ((Hash $exe) -eq $expect.new_exe)) "exit $c; status '$($s.Trim())'; knaif processes $procs"
$stopped = Select-String -Path "$Out\reinstall-over-daemon.log" -Pattern 'daemon' -SimpleMatch -Quiet
Log "INFO reinstall log mentions the daemon: $stopped"

# 5. Uninstall over a running daemon: the uninstaller stops it, removes the files, keeps ~\.knaif.
$up, $s = DaemonStart $exe
Check 'daemon_started_again' $up ($s -replace "`r?`n", ' | ')
$c = Uninstall 'uninstall-over-daemon.log'
Start-Sleep -Seconds 3
$r = @(Rows); $procs = (KnaifProcs).Count
Check 'uninstall_over_daemon' (($c -eq 0) -and ($r.Count -eq 0) -and ($procs -eq 0) -and -not (Test-Path $exe)) "exit $c; rows $($r.Count); knaif processes $procs; exe left $(Test-Path $exe)"
Check 'uninstall_keeps_user_models' (Test-Path "$env:USERPROFILE\.knaif\models\knaif-qwen3-4b-v2-q4_k_m.gguf") "models kept"

# 6. `skills deps` from the zip names the supporting tools this box lacks (no ffmpeg, no Tesseract).
$deps = & $zipExe skills deps 2>&1 | Out-String
$deps | Set-Content "$Out\skills-deps.txt"
Check 'skills_deps_names_missing_tools' (($deps -match '(?i)ffmpeg') -and ($deps -match '(?i)tesseract')) "see skills-deps.txt"

Log "Smart App Control at end: $(Sac)"
$results | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 "$Out\results.json"
$fails = @($results.Keys | Where-Object { -not $results[$_].pass })
Log "DONE: $($results.Count) checks, $($fails.Count) failed [$($fails -join ', ')]. Grade on the host (grade.py), then do the manual part (report.md). Close this Sandbox window when done; it discards everything."
