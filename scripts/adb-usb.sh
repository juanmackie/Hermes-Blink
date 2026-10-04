#!/usr/bin/env bash
# Hermes Blink USB helper (Linux/macOS). Mirrors scripts/adb-usb.ps1.
set -euo pipefail
PORT=8788
DEVICE=""
APK=""
ACTION="usb-up"

usage() {
  echo "usage: adb-usb.sh [--port 8788] [--device SERIAL] [--apk PATH] {devices|reverse|remove|list|install|logcat|usb-up}" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --apk) APK="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    devices|reverse|remove|list|install|logcat|usb-up) ACTION="$1"; shift ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

dev_flag=()
if [[ -n "$DEVICE" ]]; then dev_flag=(--device "$DEVICE"); fi

case "$ACTION" in
  devices) hermes widget adb-devices ;;
  reverse) hermes widget adb-reverse --port "$PORT" "${dev_flag[@]}" ;;
  remove) hermes widget adb-reverse --port "$PORT" --remove "${dev_flag[@]}" ;;
  list) hermes widget adb-reverse --list "${dev_flag[@]}" ;;
  install)
    if [[ -n "$APK" ]]; then hermes widget adb-install --apk "$APK" "${dev_flag[@]}";
    else hermes widget adb-install "${dev_flag[@]}"; fi ;;
  logcat) hermes widget adb-logcat --dump "${dev_flag[@]}" ;;
  usb-up) hermes widget adb-devices; hermes widget usb-up --port "$PORT" "${dev_flag[@]}" ;;
esac
