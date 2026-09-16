#!/usr/bin/env bash
# Gate 3 — Persistence (reboot/service-restart) — USER-RUN ONLY
set -euo pipefail
echo "Gate 3: Persistence — requires host reboot; NOT RUN by agent."
echo "Steps:"
echo "  1. hermes widget up --json; note counts {service,cron,devices,credentials,pairing_codes}"
echo "  2. systemctl --user restart hermes-widget (or hermes widget restart); hermes widget status --json"
echo "  3. sudo reboot (Linux) or wsl --shutdown && wsl -d Ubuntu-24.04 (WSL2); after login, hermes widget status --json"
echo "  4. Verify counts unchanged (no duplicates), brief still serves, pairing still works"
echo "Expected: status state ready or awaiting_pairing, no duplicate systemd units or cron entries"
echo "Next user action: reboot host, run hermes widget status --json twice, diff counts"
