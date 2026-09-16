$RepoRoot = Split-Path -Parent $PSScriptRoot
$localProps = Join-Path $RepoRoot 'android/local.properties'
if (Test-Path $localProps) {
  $sdkLine = (Get-Content $localProps | Where-Object { $_ -match '^sdk\.dir=' }) -replace '^sdk\.dir=',''
  if ($sdkLine) { $env:ANDROID_HOME = $sdkLine.Trim() }
}
if ($env:ANDROID_HOME) { $adb = Join-Path $env:ANDROID_HOME 'platform-tools/adb.exe' } else { $adb = 'adb' }
Set-Location (Join-Path $RepoRoot 'android')

function Find-Dev {
  (& $adb devices 2>$null) -split "`n" |
    Where-Object { $_ -match '\S+\s+device$' } |
    ForEach-Object { ($_ -split '\s+')[0] }
}

$dev = @(Find-Dev)[0]
if (-not $dev) { Write-Host 'No device found.'; exit 1 }

$out = Join-Path (Get-Location) ('shot-{0:HHmmss}.png' -f (Get-Date))
Start-Process -FilePath $adb -ArgumentList "-s $dev exec-out screencap -p" `
  -RedirectStandardOutput $out -NoNewWindow -Wait
Write-Host "Saved $out"
