# Service verification - real Ubuntu 24.04 / WSL2 environment

This gap requires a real Linux or WSL2 environment and cannot be fully verified in this workspace.

Verification steps:
1. Fresh Ubuntu 24.04 LTS installation (Linux) or WSL2 with systemd enabled (MS docs reference)
2. Install Hermes plugin: `cp -r hermes-plugin/hermes-widget ~/.hermes/plugins/`
3. Enable plugin: `hermes plugins enable hermes-widget`
4. Run `hermes widget up --json` (idempotent; creates token/widget/skill if missing)
5. Verify output: `state: needs_user_action`, pairing code present, no duplicate services
6. Install service: `bash scripts/install-service.sh`
7. Verify service: `systemctl --user status hermes-widget` shows active (running)
8. Restart terminal/service/reboot; verify `systemctl --user status` still shows active with same device/widget state
9. Verify `hermes widget devices` shows paired device (no duplicates)
10. Verify `hermes widget status` reports listening on configured port with agent token present
11. Verify WSL login task: `scripts/wsl-login-task.ps1` runs on Windows login; Tailscale service starts

Evidence: exit codes (0 for success, non-zero for failures), service status output, device list without duplicates, no traceback on `up --json`.
