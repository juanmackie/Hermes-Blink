# Hermes Blink USB / ADB development loop

Use a USB cable for local development when Tailscale HTTPS is not set up yet.
Release builds still require private HTTPS; USB cleartext is **debug-only**.

## 0. Prerequisites

- Android platform-tools (`adb`) on PATH, or set `ADB=/path/to/adb`.
- Phone: Developer options -> **USB debugging** ON. Accept the RSA prompt on first connect.
- JDK 17+ for Gradle builds. Windows default Java 8 is too old; use the Adoptium JDK 25 install, e.g.:
  `& "C:\Program Files\Eclipse Adoptium\jdk-25.0.3.9-hotspot\bin\java.exe" -version`
  and set `JAVA_HOME` to that JDK before `./gradlew`.
- SDK at `C:/Users/juanm/android-tools/sdk` (see `android/local.properties`).

## 1. Confirm the USB device

```sh
adb devices -l
hermes widget adb-devices
hermes widget doctor --json   # includes .usb + java info
```

Expect one row: `58211FDCQ007VP device product:mustang model:Pixel_10_Pro_XL`.

## 2. Expose the host server over USB

```sh
hermes widget serve --host 127.0.0.1 --port 8788   # keep running
hermes widget usb-up --port 8788
# or granular:
hermes widget adb-reverse --port 8788
hermes widget adb-reverse --list
```

This runs `adb -s <serial> reverse tcp:8788 tcp:8788`, so the phone reaches
`http://127.0.0.1:8788` over the cable. Debug builds allow cleartext;
release builds forbid it (`usesCleartextTraffic=false`).

Remove later with `hermes widget adb-reverse --remove`.

## 3. Build + install the debug APK

```powershell
$env:JAVA_HOME="C:\Program Files\Eclipse Adoptium\jdk-25.0.3.9-hotspot"
cd android
.\gradlew.bat assembleDebug
hermes widget adb-install --apk app/build/outputs/apk/debug/app-debug.apk --launch
```

Or one step on Windows: `powershell -File scripts/adb-usb.ps1 -Install -Launch`.

## 4. Pair + fetch over USB (debug)

1. `hermes widget code` on the host.
2. In the debug app, Pair screen: server URL `http://127.0.0.1:8788`, enter code.
3. `hermes widget publish --title "USB check" --summary "via cable" --text "hello usb"`.
4. Phone fetches on open/refresh; `hermes widget status` shows fetch/render receipts.

## 5. Logs

```sh
hermes widget adb-logcat --dump
hermes widget adb-logcat --tag HermesWidget
adb -s <serial> logcat -c   # clear
```

## Scripts

- `scripts/adb-usb.ps1` (Windows) and `scripts/adb-usb.sh` (Linux): devices,
  reverse, install, logcat shortcuts.
- `hermes widget usb-up` is the idempotent one-shot used by the scripts.

## Troubleshooting Request update

A tap has three hops, each visible:

1. Phone tap → server: the app's **Diagnostics → Widget actions** shows the tap,
   HTTP status, and server code (`refresh_not_triggered` means Hermes stored it
   but the refresh never started). A `401/403` means re-pair; `429` is the
   5-taps-per-minute limit; `network_error` means the USB reverse or HTTPS path
   is down (`hermes widget usb-up` for USB).
2. Server → refresh spawn: `hermes widget requests` lists each tap with
   `status` (`triggered`, `failed` with `error`). A `failed` row names the
   spawn problem (missing `hermes` binary, missing cron job).
3. Refresh run → agent: `hermes cron list` shows `hermes-widget-refresh` and its
   last-run error (e.g. portal re-auth needed). The run must call
   `widget_status` with `consume_update_requests=true`, then publish or finish.

If the phone says success but nothing publishes, check hop 2 first: the tap hit
Hermes, but the cron did not run.

## Troubleshooting

- `no online adb device`: cable, USB debugging, RSA prompt, try another port,
  `adb kill-server; adb start-server`.
- `multiple online devices`: pass `--device <serial>`.
- Gradle `Unsupported class file major version` or `Java 8`: `JAVA_HOME` points
  at Java 8; point it at JDK 17+ (Adoptium 25 works).
- `Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]`: uninstall release/debug
  signature mismatch: `adb uninstall com.you.hermeswidget`, then reinstall.
- Release APK over USB cleartext fails by design; use Tailscale Serve HTTPS.
