# knaif 1.3.0 Windows clean room, the wizard part (report.md, "Manual part", M0-M5), driven instead
# of clicked (owner, 2026-10-15: "make it automatic"). Runs INSIDE a fresh Windows Sandbox (no
# network, no GPU, Smart App Control enforcing), separate from sandbox.ps1's run.
#
# It runs the staged 1.3.0 installer WITH its wizard (no /SILENT, which never builds the pages),
# walks every page through UI Automation (accepts the licence, presses Next / Install / Finish),
# and for each page saves a screenshot (page-NN.png) and every control's name, type, enabled and
# checked state (page-NN.txt). The checks below read those dumps; the screenshots are for the
# wording and layout (M5), which a reviewer reads on the host. Writes C:\r130\wout\wizard.log and
# wizard.json; DONE ends the log.
#
# The model is copied into ~\.knaif first, as sandbox.ps1 does, so the wizard offers no download
# (there is no network) and the run needs no input.
$ErrorActionPreference = 'Continue'
$In = 'C:\r130\in'; $Out = 'C:\r130\wout'; $Log = "$Out\wizard.log"
$Setup = "$In\knaif-1.3.0-windows-x64-setup.exe"
$SetupSha = 'c671a60a4d8c1bb2dd8104cb883d1025de0de449f850bd98e5f746edaf9edda2'
$results = [ordered]@{}
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Drawing, System.Windows.Forms
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class Win {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
}
'@
$A = [System.Windows.Automation.AutomationElement]
$TS = [System.Windows.Automation.TreeScope]

function Log($m) { $l = "$(Get-Date -Format HH:mm:ss) $m"; Write-Host $l; Add-Content -Path $Log -Value $l }
function Check($id, $ok, $detail) {
  $results[$id] = [ordered]@{ pass = [bool]$ok; detail = "$detail" }
  Log ("{0} {1}: {2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $id, $detail)
}
function Finish($why) {
  if ($why) { Check 'run_completed' $false $why } else { Check 'run_completed' $true 'every page walked' }
  $results | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 "$Out\wizard.json"
  $fails = @($results.Keys | Where-Object { -not $results[$_].pass })
  Log "DONE: $($results.Count) checks, $($fails.Count) failed [$($fails -join ', ')]. Review the page-NN.png screenshots on the host (M5). Close this Sandbox window; it discards everything."
  exit
}
function Wizard {
  $c = New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, 'TWizardForm')
  $A::RootElement.FindFirst($TS::Children, $c)
}
# Every control under the wizard: type, name, enabled, and the toggle state where it has one.
function Controls($w) {
  $all = $w.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($e in $all) {
    $cur = $e.Current
    $state = ''
    $p = $null
    if ($e.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$p)) { $state = "$($p.Current.ToggleState)" }
    elseif ($e.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) { $state = "selected=$($p.Current.IsSelected)" }
    [pscustomobject]@{ el = $e; type = $cur.ControlType.ProgrammaticName -replace '^ControlType\.', ''; name = "$($cur.Name)" -replace '&', ''
                       enabled = $cur.IsEnabled; visible = -not $cur.IsOffscreen; state = $state }
  }
}
function Shot($w, $path) {
  try {
    [Win]::SetForegroundWindow([IntPtr]$w.Current.NativeWindowHandle) | Out-Null
    Start-Sleep -Milliseconds 400
    $r = $w.Current.BoundingRectangle
    $bmp = New-Object System.Drawing.Bitmap ([int]$r.Width), ([int]$r.Height)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen([int]$r.X, [int]$r.Y, 0, 0, $bmp.Size)
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()
  } catch { Log "screenshot failed: $($_.Exception.Message)" }
}
# Inno's VCL controls reach UI Automation as plain panes with no Invoke pattern (2026-10-15), but its
# buttons and radio buttons are Windows BUTTON controls, so BM_CLICK presses them as a click would.
function Press($ctl, $pattern) {
  $p = $null
  if ($pattern -eq 'invoke' -and $ctl.el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) { $p.Invoke(); return $true }
  if ($pattern -eq 'select' -and $ctl.el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) { $p.Select(); return $true }
  $h = $ctl.el.Current.NativeWindowHandle
  if ($h) { [Win]::SendMessage([IntPtr]$h, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null; return $true }   # BM_CLICK
  return $false
}

New-Item -ItemType Directory -Force $Out | Out-Null
Set-Content -Path $Log -Value "1.3.0 release clean-room wizard run $(Get-Date -Format s)"

# The Sandbox follows the host image: with the host's Smart App Control off, a fresh Sandbox starts
# in evaluation (2), which only watches (2026-10-15; on 2026-10-02 it started enforcing). This is a
# throwaway Windows, so switch it to enforcing (1) and refresh the code-integrity policy; M0 then
# checks the state actually took.
$SacKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy'
$sac0 = (Get-ItemProperty $SacKey -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
if ($sac0 -ne 1) {
  Set-ItemProperty $SacKey -Name VerifiedAndReputablePolicyState -Value 1 -Type DWord
  & CiTool.exe --refresh --json *> "$Out\citool-refresh.txt"
  Log "Smart App Control was $sac0; set to 1 and refreshed the policy (CiTool exit $LASTEXITCODE)"
}
# M0: Smart App Control enforcing, and the installer is the frozen one.
$sac =(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
$sha = (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLower()
Check 'M0_sac_enforcing_and_frozen_installer' (($sac -eq 1) -and ($sha -eq $SetupSha)) "SAC state $sac; installer sha256 $sha"
# The state value is only what Windows reports; prove enforcement: an unsigned program built here
# must be refused.
$probe = "$env:TEMP\sac-probe.exe"
try {
  Add-Type -OutputType ConsoleApplication -OutputAssembly $probe -TypeDefinition 'public static class P { public static void Main() { System.Console.WriteLine("ran"); } }' -ErrorAction Stop
  try { $o = & $probe 2>&1 | Out-String } catch { $o = $_.Exception.Message }
  # Fail closed (Codex audit 2026-10-15): only Windows' own Application Control refusal counts.
  Check 'M0_sac_blocks_unsigned' ($o -match 'Application Control policy has blocked') "unsigned probe: $(($o -split "`n")[0].Trim())"
} catch { Check 'M0_sac_blocks_unsigned' $false "could not build the probe: $($_.Exception.Message)" }

$models = "$env:USERPROFILE\.knaif\models"
New-Item -ItemType Directory -Force $models | Out-Null
Copy-Item 'C:\r130\models\knaif-qwen3-4b-v2-q4_k_m.gguf' $models

$proc = Start-Process $Setup -ArgumentList @("/LOG=`"$Out\wizard-setup.log`"") -PassThru
$pages = @(); $n = 0; $last = ''; $deadline = (Get-Date).AddMinutes(15); $finished = $false; $accepted = $false
while ((Get-Date) -lt $deadline) {
  $w = Wizard
  if (-not $w) {
    if ($finished -or $proc.HasExited) { break }
    Start-Sleep -Seconds 1; continue
  }
  $ctls = @(Controls $w)
  $sig = ($ctls | Where-Object { $_.visible } | ForEach-Object { "$($_.type)|$($_.name)|$($_.enabled)|$($_.state)" }) -join "`n"
  $next = $ctls | Where-Object { $_.type -ne 'Document' -and $_.visible -and $_.enabled -and $_.name -match '^\s*(Next|Install|Finish)\s*>?\s*$' } | Select-Object -First 1
  if ($sig -ne $last) {
    # A new page (or a page that changed, such as the progress bar): record it once it settles.
    $n++
    $f = "{0}\page-{1:D2}" -f $Out, $n
    Shot $w "$f.png"
    $ctls | Where-Object { $_.visible } | ForEach-Object { "{0}`t{1}`tenabled={2}`t{3}" -f $_.type, $_.name, $_.enabled, $_.state } |
      Set-Content -Encoding UTF8 "$f.txt"
    $pages += [pscustomobject]@{ n = $n; controls = @($ctls | Where-Object { $_.visible } | Select-Object type, name, enabled, state) }
    Log "page $n recorded ($(@($ctls).Count) controls; next button: $(if ($next) { $next.name } else { 'none' }))"
    $last = $sig
  }
  if (-not $next) { Start-Sleep -Seconds 2; continue }
  # The licence page: accept before Next is enabled.
  $accept = $ctls | Where-Object { $_.name -match '^I accept the agreement' } | Select-Object -First 1
  if ($accept -and -not $accepted) { Log 'accept the licence'; Press $accept 'select' | Out-Null; $accepted = $true; $last = ''; Start-Sleep -Seconds 1; continue }
  if ($next.name -match '^\s*Finish') { $finished = $true }
  Log "press $($next.name.Trim())"
  Press $next 'invoke' | Out-Null
  $last = ''   # force a record of whatever comes next, even if it looks the same
  Start-Sleep -Seconds 2
}
if (-not $finished) { Finish "the wizard did not reach the Finish page within 15 minutes ($n pages recorded)" }
if (-not $proc.WaitForExit(120000)) { Finish 'setup did not exit after Finish' }
Check 'setup_exit_0' ($proc.ExitCode -eq 0) "exit $($proc.ExitCode)"

$texts = $pages | ForEach-Object { $_.controls } | ForEach-Object { $_.name }
$taskPage = $pages | Where-Object { ($_.controls.name -join ' ') -match 'Select Additional Tasks' } | Select-Object -Last 1
$finishPage = $pages | Where-Object { ($_.controls.name -join ' ') -match 'Supporting tools' } | Select-Object -Last 1

# M1: FFmpeg and Tesseract offered disabled, each with its reason.
if ($taskPage) {
  $ff = $taskPage.controls | Where-Object { $_.name -match 'FFmpeg' -and $_.type -ne 'Document' }
  $ts = $taskPage.controls | Where-Object { $_.name -match 'Tesseract' -and $_.type -ne 'Document' }
  $ok = $ff -and $ts -and -not (@($ff + $ts) | Where-Object { $_.enabled }) -and
        -not (@($ff + $ts) | Where-Object { $_.name -notmatch 'Smart App Control|winget' })
  Check 'M1_tools_disabled_with_reason' $ok ("FFmpeg: {0} | Tesseract: {1}" -f (($ff | ForEach-Object { "$($_.name) enabled=$($_.enabled)" }) -join '; '), (($ts | ForEach-Object { "$($_.name) enabled=$($_.enabled)" }) -join '; '))
  # M2: no Ghostscript choice on the tasks page.
  $gs = $taskPage.controls | Where-Object { $_.name -match 'Ghostscript' -and $_.type -ne 'Document' }
  Check 'M2_no_ghostscript_choice' (-not $gs) "$(@($gs).Count) Ghostscript choices"
} else {
  Check 'M1_tools_disabled_with_reason' $false 'no tasks page recorded'
  Check 'M2_no_ghostscript_choice' $false 'no tasks page recorded'
}
# M1b: no CUDA offer anywhere in the wizard (the Sandbox has no NVIDIA GPU).
$cuda = $texts | Where-Object { $_ -match 'CUDA' }
Check 'M1b_no_cuda_offer' (-not $cuda) "$(@($cuda).Count) controls mention CUDA"
# M3: the finish page names the tools that could not be installed, and why.
if ($finishPage) {
  $ft = ($finishPage.controls.name) -join "`n"
  $ok = ($ft -match 'FFmpeg: not installed \(Smart App Control blocks it\)') -and ($ft -match 'Tesseract OCR: not installed') -and ($ft -match 'LibreOffice')
  Check 'M3_finish_page_names_tools' $ok (($ft -split "`n" | Where-Object { $_ -match 'FFmpeg|Ghostscript|LibreOffice|Tesseract|Smart App' }) -join ' / ')
} else { Check 'M3_finish_page_names_tools' $false 'no finish page with "Supporting tools" recorded' }
# M5 (the part a script can read): no page names 1.2.x. The wording and layout are in the screenshots.
$old = $texts | Where-Object { $_ -match '\b1\.2\.\d' }
Check 'M5_no_old_version_named' (-not $old) "$(@($old).Count) controls name 1.2.x: $($old -join ' / ')"

# M4: in a new process with the installed PATH, `knaif skills deps` marks the same tools.
$userPath = (Get-ItemProperty 'HKCU:\Environment').Path
$env:Path = "$userPath;$([Environment]::GetEnvironmentVariable('Path', 'Machine'))"
$deps = & knaif.exe skills deps 2>&1 | Out-String
$deps | Set-Content -Encoding UTF8 "$Out\skills-deps.txt"
$ok = ($deps -match '(?im)ffmpeg.*(missing|blocked|not)') -and ($deps -match '(?im)tesseract.*(missing|blocked|not)')
Check 'M4_skills_deps_marks_tools' $ok (($deps -split "`n" | Where-Object { $_ -match 'ffmpeg|tesseract|libreoffice|soffice' } | ForEach-Object { $_.Trim() }) -join ' / ')

$sac2 = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
Check 'sac_enforcing_at_end' ($sac2 -eq 1) "SAC state $sac2"
Finish $null
