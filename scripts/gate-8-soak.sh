#!/usr/bin/env bash
# Gate 8 — 72-hour real-device soak — USER-RUN ONLY
set -euo pipefail
echo "Gate 8: 72h soak — requires real device and 72h; NOT RUN by agent."
echo "Steps:"
echo "  1. hermes widget up --json; ensure routine every 6h installed"
echo "  2. Leave phone on battery (Android Doze), vary network (Wi-Fi ↔ Tailscale), reboot host once per day"
echo "  3. Tap dismiss/review/refresh 20× per day; observe widget updates within 2min poll"
echo "  4. Collect logs: adb logcat, hermes widget status --json every 6h, server logs"
echo "  5. Verify: no silent stalls, scheduled briefs appear, taps deduped, stale content retained when offline"
echo "Expected: 12 briefs in 72h, no ANRs, no cache poisoning, no token leakage"
echo "Next user action: start 72h timer, run this script's checklist daily, attach logs"
