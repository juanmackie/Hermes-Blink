#!/bin/bash
# Idempotent service install for hermes-widget (Phase B)
set -euo pipefail

SERVICE_FILE="$HOME/.config/systemd/user/hermes-widget.service"
mkdir -p "$(dirname "$SERVICE_FILE")"

if [ -f "$SERVICE_FILE" ]; then
    echo "Service already installed at $SERVICE_FILE"
    exit 0
fi

cp scripts/hermes-widget.service "$SERVICE_FILE"
echo "Installed $SERVICE_FILE"
echo "Run: systemctl --user daemon-reload && systemctl --user enable --now hermes-widget"
