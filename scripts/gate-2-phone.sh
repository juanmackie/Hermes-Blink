#!/usr/bin/env bash
# Gate 2 — Real phone onboarding (USER-RUN ONLY — agent reports NOT RUN)
# Requires: signed APK, Android 8.0+, Tailscale or LAN, one instruction: "install Hermes Widget from this project, connect my phone, start my daily brief"
set -euo pipefail
echo "Gate 2: Phone onboarding — requires real device; NOT RUN by agent."
echo "Steps:"
echo "  1. Build signed APK: cd android && ./gradlew assembleRelease (keystore via env, see docs/APK_RELEASE.md)"
echo "  2. Install APK on phone, open Hermes Widget app, tap Connect"
echo "  3. From agent: one instruction 'install Hermes Widget from this project, connect my phone, start my daily brief'"
echo "  4. Agent runs: hermes widget up --json (prepares pairing), prints QR/same-phone link"
echo "  5. Scan QR or tap same-phone link on phone, enter 100.x.y.z:8788, agent token"
echo "  6. Pairing returns device token (encrypted on phone), pin widget, verify first brief shows real content (not 'Your Hermes agent is connected' placeholder)"
echo "  7. Test expired code: hermes widget code (wait 11min), try to pair — expect invalid_or_expired_code"
echo "  8. Test same-phone link: tap same-phone link directly — expect success"
echo "Expected: brief renders with stat/badge/progress/calendar-agenda/button; no chart/image/icon/toggle"
echo "Next user action: run this script on a phone + Tailscale network; report brief screenshot + device list"
