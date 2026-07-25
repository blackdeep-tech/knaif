<#
  bench_llm_backend.ps1 — CUDA vs Vulkan llama.cpp comparison on Windows.

  Downloads llama.cpp PREBUILT release binaries (win-cuda + cudart, win-vulkan),
  runs llama-bench on the same model(s) with full GPU offload, prints a table.
  No compiler/toolchain needed — just an NVIDIA driver (which ships both CUDA and
  a Vulkan ICD on Windows).

  Usage (PowerShell) — current promoted lanes (knaif-qwen3-4b-v1 / knaif-qwen3-1.7b-v1):
    ./scripts/bench_llm_backend.ps1 -Model "C:\models\knaif-qwen3-4b-v1-q4_k_m.gguf"
    ./scripts/bench_llm_backend.ps1 -Model "\\wsl$\Ubuntu\home\<user>\knaif\models\knaif-qwen3-4b-v1-q4_k_m.gguf","\\wsl$\Ubuntu\home\<user>\knaif\models\knaif-qwen3-1.7b-v1-q6_k.gguf"

  Tip: copy the GGUF to a local NVMe path first; benchmarking off the \\wsl$ bridge
  adds load-time noise (compute numbers are still valid once loaded).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)] [string[]] $Model,   # one or more .gguf paths
  [int] $Prompt = 512,
  [int] $Gen    = 128,
  [int] $Ngl    = 99,      # GPU layers (99 = all)
  [int] $Reps   = 3,
  [string] $WorkDir = "$env:TEMP\llama-bench-cmp"
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

Write-Host "Querying latest llama.cpp release..." -ForegroundColor Cyan
$rel    = Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" `
            -Headers @{ "User-Agent" = "knaif-bench" }
$assets = $rel.assets
Write-Host "  release: $($rel.tag_name)"

function Get-Asset($pattern) {
  $a = $assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
  if (-not $a) { throw "No release asset matching /$pattern/ (naming may have changed; check $($rel.html_url))" }
  return $a
}

# win x64 assets. cudart is a separate zip that must sit next to the cuda binaries.
# Anchor the cuda binary on ^llama- so it doesn't also match the cudart zip
# (cudart-llama-bin-win-cuda-*.zip contains "bin-win-cuda...x64.zip" too). Recent
# releases ship two CUDA toolkits (12.4 + 13.3); -First 1 pairs the first of each.
$targets = @{
  cuda   = @( (Get-Asset '^llama-.*bin-win-cuda-.*x64\.zip$'), (Get-Asset '^cudart-.*win.*x64\.zip$') )
  vulkan = @( (Get-Asset 'bin-win-vulkan.*x64\.zip$') )
}

$bin = @{}
foreach ($backend in $targets.Keys) {
  $dest = Join-Path $WorkDir $backend
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  foreach ($a in $targets[$backend]) {
    $zip = Join-Path $WorkDir $a.name
    if (-not (Test-Path $zip)) {
      Write-Host "Downloading $($a.name) ..." -ForegroundColor Cyan
      Invoke-WebRequest $a.browser_download_url -OutFile $zip -Headers @{ "User-Agent" = "knaif-bench" }
    }
    Expand-Archive -Path $zip -DestinationPath $dest -Force
  }
  $exe = Get-ChildItem $dest -Recurse -Filter llama-bench.exe | Select-Object -First 1
  if (-not $exe) { throw "llama-bench.exe not found for $backend under $dest" }
  $bin[$backend] = $exe.FullName
}

foreach ($backend in @('cuda','vulkan')) {
  Write-Host "`n==================== $($backend.ToUpper()) ====================" -ForegroundColor Green
  & $bin[$backend] --version 2>$null | Select-Object -First 2
  foreach ($m in $Model) {
    if (-not (Test-Path $m)) { Write-Warning "missing model: $m"; continue }
    Write-Host "`n--- $backend :: $(Split-Path $m -Leaf) ---" -ForegroundColor Yellow
    & $bin[$backend] -m $m -p $Prompt -n $Gen -ngl $Ngl -r $Reps
  }
}

Write-Host "`nCompare the pp$Prompt (prompt) and tg$Gen (generation) t/s rows between CUDA and VULKAN." -ForegroundColor Cyan
