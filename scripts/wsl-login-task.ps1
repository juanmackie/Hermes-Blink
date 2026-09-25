# Minimal WSL persistence: launch WSL distribution and start Hermes Widget
# Use Windows Task Scheduler to run this at user login.
# Requires: WSL2 with Ubuntu 24.04 installed.

$wslCmd = "wsl -d Ubuntu-24.04 -e bash -c 'systemctl --user start hermes-widget || echo \"WSL service not available; ensure systemd is enabled (MS docs)\"'"
Invoke-Expression $wslCmd
