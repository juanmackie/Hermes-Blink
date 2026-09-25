# Install the hermes-widget plugin into a Hermes home and enable it.
[CmdletBinding()]
param(
    [string]$PluginsDir = (Join-Path $env:USERPROFILE ".hermes\plugins"),
    [switch]$NoEnable
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "hermes-plugin\hermes-widget"
$target = Join-Path $PluginsDir "hermes-widget"

if (-not (Test-Path (Join-Path $source "plugin.yaml"))) {
    throw "Plugin source not found at $source"
}

New-Item -ItemType Directory -Force -Path $PluginsDir | Out-Null
if (Test-Path $target) {
    Write-Host "Updating existing plugin at $target"
    Remove-Item -Recurse -Force $target
}
Copy-Item -Recurse -Force $source $target
Write-Host "Copied plugin to $target"

if (-not $NoEnable) {
    $hermes = Get-Command hermes -ErrorAction SilentlyContinue
    if ($null -eq $hermes) {
        Write-Warning "hermes not on PATH; enable manually with: hermes plugins enable hermes-widget"
    }
    else {
        & hermes plugins enable hermes-widget
        Write-Host ""
        Write-Host "Next:"
        Write-Host "  hermes widget setup"
        Write-Host "  hermes widget serve --host 0.0.0.0 --port 8788"
    }
}
