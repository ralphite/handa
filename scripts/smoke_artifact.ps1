#requires -version 5
<#
.SYNOPSIS
  Real-launch smoke test for a packaged Handa Windows artifact.

.DESCRIPTION
  Extracts the bundle zip, starts the bundled server with its embedded Python
  (no system Python required), and verifies it actually serves /api/health and
  the frontend. With -Browser it also installs Chromium and drives a real
  headless page against the live app.

.EXAMPLE
  scripts\smoke_artifact.ps1 -Zip tmp\release\dist\handa-0.1.0-windows-x86_64.zip -Browser
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Zip,
  [int]$Port = 5099,
  [switch]$Browser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path $Zip)) { Write-Error "artifact not found: $Zip"; exit 2 }

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("handa-smoke-" + [System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Force -Path $work | Out-Null

$proc = $null
try {
  Write-Host "==> Extracting artifact"
  Expand-Archive -Path $Zip -DestinationPath $work -Force
  $runCmd = Get-ChildItem -Path $work -Recurse -Filter "run.cmd" | Select-Object -First 1
  if (-not $runCmd) { throw "extract failed: run.cmd not found" }
  $bundleDir = $runCmd.Directory.FullName

  Write-Host "==> Starting server on 127.0.0.1:$Port"
  $proc = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c", "`"$($runCmd.FullName)`" --host 127.0.0.1 --port $Port" `
    -PassThru -NoNewWindow `
    -RedirectStandardOutput (Join-Path $work "server.out.log") `
    -RedirectStandardError  (Join-Path $work "server.err.log")

  Write-Host "==> Waiting for /api/health"
  $ok = $false
  for ($i = 0; $i -lt 120; $i++) {
    try {
      $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 5
      if ($r.Content -match '"ok"\s*:\s*true') { $ok = $true; break }
    } catch { Start-Sleep -Seconds 1 }
  }
  if (-not $ok) {
    Write-Host "Server stdout:"; Get-Content (Join-Path $work "server.out.log") -ErrorAction SilentlyContinue
    Write-Host "Server stderr:"; Get-Content (Join-Path $work "server.err.log") -ErrorAction SilentlyContinue
    throw "health check failed"
  }
  Write-Host "    /api/health -> ok"

  $rootResp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/" -TimeoutSec 15
  if ($rootResp.StatusCode -ne 200) { throw "GET / returned HTTP $($rootResp.StatusCode)" }
  Write-Host "    GET / -> 200 (frontend served)"

  $py = Join-Path $bundleDir "runtime\python\python.exe"
  if (-not (Test-Path $py)) { throw "bundled python missing: $py" }
  $vendor = Join-Path $bundleDir "app\vendor"
  $appDir = Join-Path $bundleDir "app"

  # Import every worker/agent-runtime entrypoint the app spawns, so a Unix-only
  # import anywhere in that graph fails here instead of at runtime on Windows.
  Write-Host "==> Verifying worker/runtime imports (bundled Python)"
  $env:PYTHONPATH = "$appDir;$vendor"
  & $py (Join-Path $ScriptDir "import_check.py")
  if ($LASTEXITCODE) { throw "worker/runtime import check failed" }

  if ($Browser) {
    Write-Host "==> Installing Chromium (bundled Playwright)"
    $env:PYTHONPATH = $vendor
    & $py -m playwright install chromium
    if ($LASTEXITCODE) { throw "playwright install chromium failed" }
    Write-Host "==> Driving a real headless page against the live app"
    $env:PYTHONPATH = "$vendor;$appDir"
    & $py (Join-Path $ScriptDir "browser_smoke.py") "http://127.0.0.1:$Port/"
    if ($LASTEXITCODE) { throw "browser smoke failed" }
  }

  Write-Host "SMOKE OK ($Zip)"
}
finally {
  if ($proc -and -not $proc.HasExited) {
    & taskkill /T /F /PID $proc.Id *> $null
  }
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
