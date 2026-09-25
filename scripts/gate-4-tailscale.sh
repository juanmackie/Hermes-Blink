#!/usr/bin/env bash
# Gate 4 — Security over Tailscale-private HTTPS — USER-RUN ONLY
set -euo pipefail
echo "Gate 4: Tailscale HTTPS proxy — requires Tailscale; NOT RUN by agent."
echo "Steps:"
echo "  1. tailscale up; tailscale funnel --bg https://<name>.ts.net --https=44 --set-path=/ --proxy http://127.0.0.1:8788 (or tailscale serve https / http://127.0.0.1:8788)"
echo "  2. curl -i https://<name>.ts.net/v1/widgets (no token) → 401"
echo "  3. curl -i -H 'Authorization: Bearer dvc_...' https://<name>.ts.net/v1/widgets/hermes-brief (device token on operator PUT) → 401"
echo "  4. curl -i -X POST https://<name>.ts.net/v1/pair -d '{\"code\":\"ABCD-1234\"}' → 400 for consumed/expired codes"
echo "  5. hermes widget devices --revoke <deviceId>; curl with that token → 401"
echo "  6. Check logs: grep -i token /var/log/... shows only ***REDACTED***"
echo "Expected: tokenless fails 401 through proxy; device cannot PUT widget; consumed/expired/revoked all fail"
echo "Next user action: enable Tailscale Serve/Funnel, run curl checks above, paste 401 responses"
