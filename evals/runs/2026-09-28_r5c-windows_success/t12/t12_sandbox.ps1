# T12 (R5c): runs INSIDE Windows Sandbox, launched by t12_stage.sh. Writes C:\t12\out\t12.log and
# C:\t12\out\t12_results.json; t12_grade.py reads them on the host. Rules: t12_stage.sh's header.
#
# Mapped folders (all but out read-only): C:\t12\in (frozen artifacts, 1.1.0 setup, fixture, this
# script), C:\t12\out, C:\t12\models (the host model folder), C:\t12\tesseract (the host's
# Tesseract install, standing in for the one a user installs themselves; knaif never bundles it).
$ErrorActionPreference = 'Continue'
$In = 'C:\t12\in'; $Out = 'C:\t12\out'; $Log = "$Out\t12.log"
$Setup110 = "$In\knaif-1.1.0-windows-x64-setup.exe"
$Setup120 = "$In\knaif-1.2.0-windows-x64-setup.exe"
$results = [ordered]@{}

function Log($m) { $l = "$(Get-Date -Format HH:mm:ss) $m"; Write-Host $l; Add-Content -Path $Log -Value $l }
function Check($id, $ok, $detail) {
  $results[$id] = [ordered]@{ pass = [bool]$ok; detail = "$detail" }
  Log ("{0} {1}: {2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $id, $detail)
}
function Rows { # every Add/Remove Programs row naming knaif, per-user and per-machine
  Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                   'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                   'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match 'knaif' }
}
function RunSetup($exe, $logName, $timeoutS) {
  $p = Start-Process $exe -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$Out\$logName`"" -PassThru
  $null = $p.Handle  # cache it, or Windows PowerShell 5.1 reports ExitCode as null
  if (-not $p.WaitForExit($timeoutS * 1000)) { $p | Stop-Process -Force; return 'timeout' }
  return $p.ExitCode
}
function Version($exe) { try { (& $exe --version 2>&1 | Out-String).Trim() } catch { "error: $_" } }

Set-Content -Path $Log -Value "T12 sandbox run $(Get-Date -Format s)"
Log ("machine: " + (Get-CimInstance Win32_OperatingSystem).Caption + "; GPUs: " +
     ((Get-CimInstance Win32_VideoController | ForEach-Object Name) -join ', ') +
     "; nvidia-smi: " + [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue))
Remove-Item Env:KNAIF_PDFIUM_PATH -ErrorAction SilentlyContinue
Set-Location C:\

# 1. Clean room: the unpacked zip on a machine with no developer tooling.
Expand-Archive "$In\knaif-1.2.0-windows-x64.zip" C:\t12\zip -Force
$zipExe = 'C:\t12\zip\knaif-1.2.0-windows-x64\bin\knaif.exe'
$v = Version $zipExe
Check 'cleanroom_version' ($v -match '^knaif 1\.2\.0') $v
$sl = & $zipExe skills list 2>&1 | Out-String; $code = $LASTEXITCODE
Check 'cleanroom_skills_list' (($code -eq 0) -and ($sl -match 'ffmpeg') -and ($sl -match 'documents')) "exit $code"

# 2. Baseline: 1.1.0 installed as a user would, silently.
$c = RunSetup $Setup110 'install-1.1.0.log' 600
$r = @(Rows)
$loc = if ($r.Count -eq 1) { $r[0].InstallLocation.TrimEnd('\') } else { '' }
Check 'install_110' (($c -eq 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.1.0')) "exit $c; rows $($r.Count); version $($r.DisplayVersion); at $loc"
$exe = "$loc\bin\knaif.exe"
$v = Version $exe
Check 'installed_110_runs' ($v -match '^knaif 1\.1\.0') $v

# 3. A running 1.1.0 CLI: a first documents run with no model asks "Download recommended model ...? [y/N]"
#    in its own console window and waits there, holding the knaif-cli-running mutex. (Not ffmpeg:
#    with no ffmpeg on PATH, 1.1.0 stops at its tool check before the prompt; first pass, T12.)
$holder = Start-Process $exe -ArgumentList 'run', 'documents', 'rotate', 'doc.pdf' -WorkingDirectory C:\t12 -PassThru
Start-Sleep -Seconds 8
$m = $null
$held = [System.Threading.Mutex]::TryOpenExisting('knaif-cli-running', [ref]$m)
Check 'cli_running_holds_mutex' ((-not $holder.HasExited) -and $held) "alive $(-not $holder.HasExited); mutex $held"

# 4. Upgrade while it runs: setup must refuse (AppMutex) and change nothing.
$c = RunSetup $Setup120 'upgrade-while-running.log' 300
$r = @(Rows)
$v = Version $exe
Check 'upgrade_refused_while_running' (($c -isnot [string]) -and ($c -ne 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.1.0') -and ($v -match '^knaif 1\.1\.0')) "exit $c; rows $($r.Count); version $($r.DisplayVersion); binary '$v'"

# 5. Close the CLI, upgrade: one row, advanced, same folder, no folder-exists prompt, no leftovers.
if (-not $holder.HasExited) { $holder | Stop-Process -Force; $holder.WaitForExit(10000) | Out-Null }
$c = RunSetup $Setup120 'upgrade.log' 900
$r = @(Rows)
$loc2 = if ($r.Count -eq 1) { $r[0].InstallLocation.TrimEnd('\') } else { '' }
Check 'upgrade_installs' (($c -eq 0) -and ($r.Count -eq 1) -and ($r[0].DisplayVersion -eq '1.2.0')) "exit $c; rows $($r.Count); version $($r.DisplayVersion)"
Check 'upgrade_same_folder' ($loc2 -eq $loc) "before $loc; after $loc2"
$dirWarn = Select-String -Path "$Out\upgrade.log" -Pattern 'already exists' -SimpleMatch -Quiet
Check 'upgrade_no_folder_exists_prompt' (-not $dirWarn) "log mentions 'already exists': $dirWarn"
$v = Version $exe
Check 'upgraded_binary_runs' ($v -match '^knaif 1\.2\.0') $v
$zipBin = Get-ChildItem 'C:\t12\zip\knaif-1.2.0-windows-x64\bin' -File | ForEach-Object Name
$instBin = Get-ChildItem "$loc2\bin" -File | ForEach-Object Name
$left = @($instBin | Where-Object { $zipBin -notcontains $_ })
$miss = @($zipBin | Where-Object { $instBin -notcontains $_ })
Check 'upgrade_no_leftover_libraries' ($left.Count -eq 0) "not in the 1.2.0 artifact: [$($left -join ', ')]; in the artifact but not installed (info): [$($miss -join ', ')]"

# 6. One OCR row through the installed 1.2.0, no --model (auto-select), GPU-less box, bundled PDFium.
New-Item -ItemType Directory -Force "$env:USERPROFILE\.knaif\models", C:\t12\work, "$Out\ocr" | Out-Null
Copy-Item 'C:\t12\models\knaif-qwen3-4b-v2-q4_k_m.gguf' "$env:USERPROFILE\.knaif\models\"
Copy-Item "$In\sample-scanned.pdf" C:\t12\work\
$env:PATH = "C:\t12\tesseract;$env:PATH"
Set-Location C:\t12\work
$t0 = Get-Date
& $exe run documents --yes --verbose 'run ocr on sample-scanned.pdf' *> "$Out\ocr.log"
$code = $LASTEXITCODE
$secs = [int]((Get-Date) - $t0).TotalSeconds
$made = @(Get-ChildItem C:\t12\work -File | Where-Object { $_.Name -ne 'sample-scanned.pdf' })
$made | Copy-Item -Destination "$Out\ocr\"
$placement = (Select-String -Path "$Out\ocr.log" -Pattern 'offloaded|compute:' | ForEach-Object Line) -join ' | '
Check 'ocr_row_runs' (($code -eq 0) -and ($made.Count -ge 1)) "exit $code in ${secs}s; produced [$($made.Name -join ', ')]; $placement"
$pdfiumErr = Select-String -Path "$Out\ocr.log" -Pattern 'pdfium' -Quiet
Check 'ocr_no_pdfium_error' (-not ($pdfiumErr -and ($code -ne 0))) "log mentions pdfium: $pdfiumErr"

$results | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 "$Out\t12_results.json"
$fails = @($results.Keys | Where-Object { -not $results[$_].pass })
Log "DONE: $($results.Count) checks, $($fails.Count) failed [$($fails -join ', ')]. The text check runs on the host (t12_grade.py). Close this Sandbox window when done; it discards everything."
