$pkg = 'com.you.hermeswidget'
$RepoRoot = Split-Path -Parent $PSScriptRoot
# Prefer local.properties; fall back to ANDROID_HOME/JAVA_HOME env
$localProps = Join-Path $RepoRoot 'android/local.properties'
if (Test-Path $localProps) {
  $sdkLine = (Get-Content $localProps | Where-Object { $_ -match '^sdk\.dir=' }) -replace '^sdk\.dir=',''
  if ($sdkLine) { $env:ANDROID_HOME = $sdkLine.Trim() }
  $jdkLine = (Get-Content $RepoRoot/android/gradle.properties | Where-Object { $_ -match 'java\.home' }) -replace '.*=',''
  if ($jdkLine) { $env:JAVA_HOME = $jdkLine.Trim() }
}
if ($env:ANDROID_HOME) { $adb = Join-Path $env:ANDROID_HOME 'platform-tools/adb.exe' } else { $adb = 'adb' }
$gradle = Join-Path $RepoRoot 'android/gradle-8.10.2/bin/gradle.bat'
if (-not (Test-Path $gradle)) { $gradle = 'gradle' }
Set-Location (Join-Path $RepoRoot 'android')

function Find-Dev {
  (& $adb devices 2>$null) -split "`n" |
    Where-Object { $_ -match '\S+\s+device$' } |
    ForEach-Object { ($_ -split '\s+')[0] }
}

$dev = @(Find-Dev)[0]
if (-not $dev) {
  Write-Host ''
  Write-Host 'No device found. Pick ONE:'
  Write-Host '  USB: plug in, tap "Allow" on the phone, done.'
  Write-Host '  Wireless (Tailscale or same Wi-Fi):'
  Write-Host '    1. Phone: Settings, Developer options, Wireless debugging, Pair device with pairing code'
  Write-Host "    2. Here:  $adb pair <IP>:PAIR_PORT   (code from the dialog)"
  Write-Host "    3. Here:  $adb connect <IP>:CONNECT_PORT   (main Wireless debugging screen)"
  exit 1
}
Write-Host "Device: $dev"

& $gradle assembleDebug --console=plain -q
if ($LASTEXITCODE) { Write-Host 'Build failed.'; exit $LASTEXITCODE }

& $adb -s $dev install -r 'app\build\outputs\apk\debug\app-debug.apk'
if ($LASTEXITCODE) { Write-Host 'Install failed.'; exit $LASTEXITCODE }

& $adb -s $dev shell am start -n $pkg/.MainActivity *>$null
Write-Host "Installed and launched $pkg."
