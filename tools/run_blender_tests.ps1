# Validate the razkroi add-on against a real Blender build.
#
#   .\tools\run_blender_tests.ps1                    # uses $env:BLENDER or the default path
#   .\tools\run_blender_tests.ps1 -Blender 'D:\blender\blender.exe'
#
# The run is isolated: --factory-startup plus a throw-away `scripts` folder
# (passed through ECK_ADDON_SCRIPTS) so your real Blender preferences and
# installed add-ons are never touched.

param(
    [string]$Blender = $(if ($env:BLENDER) { $env:BLENDER } else { 'V:\installed\blender-5.2.3-windows-x64\blender.exe' })
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $Blender)) {
    Write-Error "Blender not found: $Blender  (pass -Blender 'C:\path\to\blender.exe')"
}

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("eck_addon_scripts_" + [guid]::NewGuid().ToString('N'))
$addonSource = Join-Path $repo 'export_razkroi_addon'
if (Test-Path $addonSource) {
    # Link-free copy is enough: Blender only needs to import the package.
    # __pycache__ must NOT be copied: a stale .pyc silently shadows edited .py
    # files (this already produced a false test result once).
    $staging = Join-Path $scratch 'export_razkroi_addon'
    Copy-Item -Recurse -Force $addonSource $staging
    Get-ChildItem -Path $staging -Recurse -Directory -Filter '__pycache__' |
        Remove-Item -Recurse -Force
} else {
    Write-Host "note: $addonSource not found - add-on contract layer will be skipped" -ForegroundColor Yellow
}

$env:ECK_ADDON_SCRIPTS = $scratch
try {
    & $Blender --background --factory-startup --python (Join-Path $PSScriptRoot 'run_tests.py')
    $code = $LASTEXITCODE
} finally {
    Remove-Item Env:\ECK_ADDON_SCRIPTS -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
}

# Blender returns 0 for --python scripts even on error, so print a verdict that
# survives the pipe. The test script prints "N failed" / "contract layer skipped".
if ($code -ne 0) { Write-Host "blender exited with $code" -ForegroundColor Red }
