# Hermes Widget - service installation verification (Phase B)
# Run as the Hermes user on Ubuntu 24.04 or WSL2 with systemd.

1. Install service: cp scripts/hermes-widget.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now hermes-widget
2. Verify: systemctl --user status hermes-widget
3. Resumes safely after restart: no duplicate services (idempotent install), no duplicate cron jobs, no duplicate devices.
4. WSL: run the login task via Windows Task Scheduler; ensure systemd is enabled in WSL (MS docs).
5. Idempotency: repeated `hermes widget up --json` does not duplicate services/cron/devices/credentials.
