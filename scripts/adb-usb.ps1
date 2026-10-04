<# Hermes Blink USB helper (Windows PowerShell).
Usage:
  powershell -File scripts/adb-usb.ps1 -Devices
  powershell -File scripts/adb-usb.ps1 -Reverse -Port 8788
  powershell -File scripts/adb-usb.ps1 -Install -Launch
  powershell -File scripts/adb-usb.ps1 -LogcatDump
  powershell -File scripts/adb-usb.ps1 -UsbUp
#>
param(
  [switch]$Devices,
  [switch]$Reverse,
  [switch]$RemoveReverse,
  [switch]$ListReverse,
  [switch]$Install,
  [switch]$Launch,
  [switch]$LogcatDump,
  [switch]$LogcatClear,
  [switch]$UsbUp,
  [int]$Port = 8788,
  [string]$Device = "",
  [string]$Apk = ""
)

$ErrorActionPreference = "Stop"

function Invoke-Hermes([string[]]$Extra) {
  $cmd = Get-Command hermes -ErrorAction SilentlyContinue
  if (-not $cmd) { throw "hermes CLI not on PATH" }
  & hermes @Extra
  if ($LASTEXITCODE -ne 0) { throw "hermes $($Extra -join ' ') exited $LASTEXITCODE" }
}

if (-not ($Devices -or $Reverse -or $RemoveReverse -or $ListReverse -or $Install -or $LogcatDump -or $LogcatClear -or $UsbUp)) {
  $Devices = $true; $UsbUp = $true
}

if ($Devices) {
  Write-Host "== adb devices =="
  Invoke-Hermes @("widget", "adb-devices")
}

if ($UsbUp) {
  Write-Host "== usb-up :$Port =="
  $args = @("widget", "usb-up", "--port", "$Port")
  if ($Device) { $args += @("--device", $Device) }
  Invoke-Hermes $args
}

if ($Reverse) {
  $args = @("widget", "adb-reverse", "--port", "$Port")
  if ($Device) { $args += @("--device", $Device) }
  Invoke-Hermes $args
}
if ($RemoveReverse) {
  $args = @("widget", "adb-reverse", "--port", "$Port", "--remove")
  if ($Device) { $args += @("--device", $Device) }
  Invoke-Hermes $args
}
if ($ListReverse) {
  $args = @("widget", "adb-reverse", "--list")
  if ($Device) { $args += @("--device", $Device) }
  Invoke-Hermes $args
}

if ($Install) {
  $args = @("widget", "adb-install")
  if ($Apk) { $args += @("--apk", $Apk) }
  if ($Device) { $args += @("--device", $Device) }
  if ($Launch) { $args += @("--launch") }
  Invoke-Hermes $args
}

if ($LogcatClear) { Invoke-Hermes (@("widget", "adb-logcat") + ($(if ($Device) { @("--device", $Device) } else { @() })) + @("--clear")) }
if ($LogcatDump) { Invoke-Hermes (@("widget", "adb-logcat", "--dump") + ($(if ($Device) { @("--device", $Device) } else { @() }))) }

Write-Host "Done. See docs/USB_ADB.md for the debug-vs-release transport note."
