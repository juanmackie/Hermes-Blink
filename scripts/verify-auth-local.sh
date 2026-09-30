#!/usr/bin/env bash
# Gate 2/4 local subset — transport + authorization over HTTPS on this host.
#
# Proves the server refuses unauthenticated and unauthorised callers through TLS,
# that consumed/expired pairing codes and revoked device tokens fail, and that a
# device credential cannot perform operator actions. Needs no phone, no Tailscale,
# and no Hermes install, so it runs unattended before the user-run gates.
#
# The Tailscale-exposed portion of gate 4 (external HTTPS through the proxy) is
# still user-run: see scripts/gate-4-tailscale.sh.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

TMPD="$(mktemp -d 2>/dev/null || mktemp -d -t hermes-auth)"
cleanup() { rm -rf "$TMPD"; }
trap cleanup EXIT

# Windows/MSYS Python cannot resolve /tmp paths; hand it a native path when possible.
if command -v cygpath >/dev/null 2>&1; then
  TMPD="$(cygpath -w "$TMPD" 2>/dev/null || printf '%s' "$TMPD")"
fi

echo "=== Transport + authorization verification (HTTPS, local) ==="
echo "Scratch dir: $TMPD"

# Throwaway self-signed certificate. MSYS2 rewrites -subj values like /CN=... into
# paths unless argument conversion is disabled.
MSYS2_ARG_CONV_EXCL="*" openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$TMPD/key.pem" -out "$TMPD/cert.pem" \
  -subj "/CN=127.0.0.1" -addext "subjectAltName=IP:127.0.0.1" >/dev/null 2>&1

PYTHON="$(command -v python3 || command -v python)"
"$PYTHON" "$REPO/scripts/verify-auth-local.py" "$TMPD"

echo "Local transport/auth subset: PASS. External HTTPS via Tailscale remains user-run (scripts/gate-4-tailscale.sh)."
