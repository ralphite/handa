#requires -version 5
<#
.SYNOPSIS
  Build a self-contained Handa release for Windows (x86_64).

.DESCRIPTION
  Mirrors scripts/package_release.sh but produces a Windows-native bundle:
  an embedded python-build-standalone runtime, vendored dependencies, the built
  frontend, and a run.cmd launcher, zipped into:
      tmp/release/dist/handa-<version>-windows-x86_64.zip

  Native wheels (pydantic-core, etc.) are platform-specific, so this MUST run on
  a real Windows host (e.g. the windows-latest GitHub Actions runner).
#>
[CmdletBinding()]
param(
  [string]$Version = "",
  [string]$PythonMajorMinor = "3.12",
  [string]$PythonStandaloneUrl = "",
  [switch]$SkipFrontendBuild,
  [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PublicRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$FeDir      = Join-Path $PublicRoot "src\web"
$TmpRoot    = Join-Path $PublicRoot "tmp\release"
$BuildRoot  = Join-Path $TmpRoot "build"
$DistDir    = Join-Path $TmpRoot "dist"
$Target     = "windows-x86_64"
$PyPlatform = "x86_64-pc-windows-msvc"

function Die([string]$msg) { Write-Error $msg; exit 1 }

function Get-ProjectVersion {
  if ($Version) { return $Version }
  $pp = Join-Path $PublicRoot "pyproject.toml"
  $m = Select-String -Path $pp -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if (-not $m) { Die "could not read project.version from pyproject.toml" }
  return $m.Matches[0].Groups[1].Value
}

function Build-Frontend {
  if ($SkipFrontendBuild) {
    if (-not (Test-Path (Join-Path $FeDir "dist\index.html"))) {
      Die "SkipFrontendBuild set but $FeDir\dist\index.html is missing"
    }
    return
  }
  Push-Location $FeDir
  try {
    & npm ci;        if ($LASTEXITCODE) { Die "npm ci failed" }
    & npm run build; if ($LASTEXITCODE) { Die "npm run build failed" }
  } finally { Pop-Location }
}

function Resolve-PythonUrl {
  if ($PythonStandaloneUrl) { return $PythonStandaloneUrl }
  if ($env:GITHUB_TOKEN) {
    $headers = @{ "User-Agent" = "handa-release"; "Authorization" = "Bearer $($env:GITHUB_TOKEN)" }
  } else {
    $headers = @{ "User-Agent" = "handa-release" }
  }
  $api = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
  $payload = Invoke-RestMethod -Uri $api -Headers $headers -TimeoutSec 60
  $line = [regex]::Escape($PythonMajorMinor)
  $plat = [regex]::Escape($PyPlatform)
  foreach ($suffix in @("install_only_stripped", "install_only")) {
    $pat = "cpython-$line\.\d+\+.*-$plat-$suffix\.tar\.gz$"
    $hit = $payload.assets | Where-Object { $_.name -match $pat } | Sort-Object name | Select-Object -Last 1
    if ($hit) { return $hit.browser_download_url }
  }
  Die "could not find python-build-standalone asset for Python $PythonMajorMinor $PyPlatform. Set -PythonStandaloneUrl."
}

function Install-PythonRuntime([string]$pyUrl, [string]$runtimeDir) {
  $archive = Join-Path $BuildRoot "python-standalone.tar.gz"
  New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
  Write-Host "Downloading Python runtime:`n  $pyUrl"
  Invoke-WebRequest -Uri $pyUrl -OutFile $archive
  # tar ships with windows-latest; install_only archives extract to python\python.exe
  & tar -xzf $archive -C $runtimeDir
  if ($LASTEXITCODE) { Die "failed to extract python runtime" }
  $py = Join-Path $runtimeDir "python\python.exe"
  if (-not (Test-Path $py)) { Die "python runtime did not contain python\python.exe" }
  return $py
}

function Install-Dependencies([string]$pythonBin, [string]$vendorDir) {
  New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
  & $pythonBin -m ensurepip --upgrade *> $null
  & $pythonBin -m pip install --no-cache-dir --upgrade pip setuptools wheel
  if ($LASTEXITCODE) { Die "pip bootstrap failed" }

  $reqFile = Join-Path $BuildRoot "requirements.txt"
  $pp = (Join-Path $PublicRoot "pyproject.toml")
  $extractor = @"
import tomllib, pathlib, sys
data = tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
sys.stdout.write('\n'.join(data['project']['dependencies']))
"@
  & $pythonBin -c $extractor $pp | Set-Content -Encoding ascii $reqFile
  & $pythonBin -m pip install --no-cache-dir --target $vendorDir -r $reqFile
  if ($LASTEXITCODE) { Die "pip install of dependencies failed" }
}

function Copy-AppFiles([string]$bundleDir) {
  $appDir = Join-Path $bundleDir "app"
  New-Item -ItemType Directory -Force -Path $appDir | Out-Null
  Copy-Item (Join-Path $PublicRoot "pyproject.toml") (Join-Path $appDir "pyproject.toml")

  $srcDst = Join-Path $appDir "src"
  New-Item -ItemType Directory -Force -Path $srcDst | Out-Null
  # robocopy exit codes 0-7 indicate success; >=8 is a real error.
  & robocopy (Join-Path $PublicRoot "src") $srcDst /E /NFL /NDL /NJH /NJS /NP `
      /XD node_modules dist test-results __pycache__ /XF *.pyc *> $null
  if ($LASTEXITCODE -ge 8) { Die "robocopy of src failed ($LASTEXITCODE)" }
  $global:LASTEXITCODE = 0

  Copy-Item (Join-Path $FeDir "dist") (Join-Path $appDir "web_dist") -Recurse
}

function Write-RunCmd([string]$bundleDir) {
  $runCmd = @'
@echo off
setlocal
set "SELF_DIR=%~dp0"
set "PYTHON_BIN=%SELF_DIR%runtime\python\python.exe"
set "APP_DIR=%SELF_DIR%app"
if not exist "%PYTHON_BIN%" (
  echo Handa runtime is missing: %PYTHON_BIN% 1>&2
  exit /b 1
)
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH=%APP_DIR%;%APP_DIR%\vendor"
set "HANDA_FRONTEND_DIST=%APP_DIR%\web_dist"
echo Starting Handa on http://127.0.0.1:5086
"%PYTHON_BIN%" -m src.api.app %*
'@
  Set-Content -Path (Join-Path $bundleDir "run.cmd") -Value $runCmd -Encoding ascii
}

# --- main -------------------------------------------------------------------

$resolvedVersion = Get-ProjectVersion
$bundleName = "handa-$resolvedVersion-$Target"
$bundleDir  = Join-Path $BuildRoot $bundleName
$payloadZip = Join-Path $DistDir "$bundleName.zip"

Remove-Item -Recurse -Force $BuildRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $BuildRoot, $DistDir | Out-Null

Build-Frontend
$pyUrl = Resolve-PythonUrl
$py = Install-PythonRuntime $pyUrl (Join-Path $bundleDir "runtime")
Copy-AppFiles $bundleDir
Install-Dependencies $py (Join-Path $bundleDir "app\vendor")
Write-RunCmd $bundleDir
Set-Content -Path (Join-Path $bundleDir "VERSION") -Value $resolvedVersion -Encoding ascii

if (Test-Path $payloadZip) { Remove-Item $payloadZip }
Compress-Archive -Path $bundleDir -DestinationPath $payloadZip
$sha = (Get-FileHash -Algorithm SHA256 $payloadZip).Hash.ToLower()
"$sha  $(Split-Path -Leaf $payloadZip)" | Set-Content -Encoding ascii "$payloadZip.sha256"

if (-not $SkipSmokeTest) {
  & (Join-Path $ScriptDir "smoke_artifact.ps1") -Zip $payloadZip
  if ($LASTEXITCODE) { Die "smoke test failed" }
}

Write-Host "Built:"
Write-Host "  $payloadZip"
Write-Host "  $payloadZip.sha256"
