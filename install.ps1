<#
.SYNOPSIS
  Handa one-line installer for Windows.

.DESCRIPTION
  Run from PowerShell:

      irm https://raw.githubusercontent.com/ralphite/handa/main/install.ps1 | iex

  Downloads the self-contained release (no Python/Node needed), unpacks it under
  %LOCALAPPDATA%\Handa, and writes a handa.cmd launcher shim.

  Env overrides: HANDA_REPO, HANDA_VERSION, HANDA_HOME.
#>

$ErrorActionPreference = "Stop"

$repo        = if ($env:HANDA_REPO)    { $env:HANDA_REPO }    else { "ralphite/handa" }
$version     = if ($env:HANDA_VERSION) { $env:HANDA_VERSION } else { "latest" }
# Note: $home is a reserved PowerShell automatic variable, so use $installHome.
$installHome = if ($env:HANDA_HOME)    { $env:HANDA_HOME }    else { Join-Path $env:LOCALAPPDATA "Handa" }
$target      = "windows-x86_64"
$asset       = "handa-$target.zip"

$url = if ($version -eq "latest") {
  "https://github.com/$repo/releases/latest/download/$asset"
} else {
  "https://github.com/$repo/releases/download/$version/$asset"
}

$releases = Join-Path $installHome "releases"
$binDir   = Join-Path $installHome "bin"
New-Item -ItemType Directory -Force -Path $releases, $binDir | Out-Null

$zip = Join-Path ([System.IO.Path]::GetTempPath()) "handa-download.zip"
Write-Host "Downloading $url"
Invoke-WebRequest -Uri $url -OutFile $zip

Write-Host "Unpacking..."
Get-ChildItem -Path $releases -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
Expand-Archive -Path $zip -DestinationPath $releases -Force
Remove-Item $zip -ErrorAction SilentlyContinue

$runCmd = Get-ChildItem -Path $releases -Recurse -Filter "run.cmd" | Select-Object -First 1
if (-not $runCmd) { throw "run.cmd not found in downloaded bundle" }

$shim = Join-Path $binDir "handa.cmd"
"@echo off`r`n`"$($runCmd.FullName)`" %*" | Set-Content -Encoding ascii $shim

Write-Host ""
Write-Host "Handa installed."
Write-Host "  launcher: $shim"
Write-Host "  release:  $($runCmd.Directory.FullName)"
Write-Host "  Add $binDir to PATH, then run: handa"
