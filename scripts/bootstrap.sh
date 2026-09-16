#!/usr/bin/env bash
# Hermes Widget Bootstrap Installer (v1)
# Usage: bash scripts/bootstrap.sh <hermes-profile> [repo-url]
# If no repo-url provided, uses the local checkout.
#
# Installs the Hermes Widget plugin, configures storage, starts services,
# generates pairing code, and prepares the first brief.
# Idempotent: safe to re-run.

set -euo pipefail

PROFILE="${1:-default}"
REPO_URL="${2:-}"
INSTALL_DIR="${HOME}"
PLUGIN_DIR="${INSTALL_DIR}/.hermes/plugins/hermes-widget"

# Use local checkout if no repo URL provided
if [ -z "$REPO_URL" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    if [ -d "${SCRIPT_DIR}/hermes-plugin/hermes-widget" ]; then
        REPO_URL="local"
        SRC_DIR="${SCRIPT_DIR}/hermes-plugin/hermes-widget"
    else
        echo "ERROR: No repo URL provided and no local hermes-plugin found."
        echo "Usage: $0 <profile> <repo-url>"
        exit 1
    fi
fi

echo "=== Hermes Widget Bootstrap ==="
echo "Profile: $PROFILE"
echo "Repo:    $REPO_URL"

# 1. Install plugin (local checkout or clone)
if [ "$REPO_URL" = "local" ]; then
    echo "[1/7] Using local checkout..."
    SRC_DIR="${SCRIPT_DIR}/hermes-plugin"
else
    if [ -d "${INSTALL_DIR}/hermes-widget" ]; then
        echo "[1/7] Repo already cloned, updating..."
        rm -rf "${INSTALL_DIR}/hermes-widget"
    fi
    echo "[1/7] Cloning repo..."
    git clone "$REPO_URL" "${INSTALL_DIR}/hermes-widget" 2>/dev/null || {
        echo "ERROR: Cannot clone $REPO_URL"
        echo "Verify the URL and that git is available."
        exit 1
    }
    SRC_DIR="${INSTALL_DIR}/hermes-widget"
fi
mkdir -p "${INSTALL_DIR}/.hermes/plugins"
if [ "$REPO_URL" = "local" ]; then
    cp -r "${SRC_DIR}/hermes-widget" "$PLUGIN_DIR"
else
    cp -r "${SRC_DIR}/hermes-plugin/hermes-widget" "$PLUGIN_DIR"
fi

# 3. Enable plugin
echo "[3/7] Enabling plugin..."
hermes plugins enable hermes-widget 2>/dev/null || {
    echo "WARNING: hermes CLI not found; enable manually: hermes plugins enable hermes-widget"
}

# 4. Configure storage
echo "[4/7] Configuring storage..."
hermes widget setup --host 127.0.0.1 --port 8788 --widget-id hermes-brief --routine --schedule "every 6h" 2>/dev/null || {
    echo "WARNING: widget setup failed; run manually: hermes widget setup"
}

# 5. Start server
echo "[5/7] Starting server..."
hermes widget serve --host 127.0.0.1 --port 8788 --quiet &
sleep 2

# 6. Generate pairing code
echo "[6/7] Generating pairing code..."
PAIRING=$(hermes widget code 2>/dev/null || echo "ERROR: pairing code generation failed")
PAIRING_CODE=$(echo "$PAIRING" | grep -o '[A-Z0-9]\{4\}-[A-Z0-9]\{4\}' | head -1)
PAIRING_EXP=$(echo "$PAIRING" | grep -o 'Expires.*' | head -1)
echo "Pairing code: $PAIRING_CODE"
echo "Expires:      $PAIRING_EXP"

# 7. Prepare first brief
echo "[7/7] Preparing first brief..."
hermes widget status 2>/dev/null || echo "WARNING: status check failed"

echo ""
echo "=== Bootstrap complete ==="
echo "Next steps:"
echo "  1. On your phone, open Hermes Widget > Connect"
echo "  2. Enter URL: http://<this-machine-ip>:8788"
echo "  3. Enter pairing code: $PAIRING"
echo "  4. Pin the widget to your home screen"
echo ""
echo "Agent tools available: widget_update, widget_list, widget_read_events, widget_mint_pairing_code"
