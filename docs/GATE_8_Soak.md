# Gate 8 - 72-hour real-device soak verification

This gate requires a real Android device and cannot be completed in a development session.

Requirements for verification:
- A paired Android device running the signed Hermes Widget APK
- Host running Linux (Ubuntu 24.04 LTS recommended) or WSL2 with systemd
- Tailscale network active for private HTTPS connectivity
- Continuous 72-hour period with:
  - Scheduled brief regeneration (every 6 hours) via Hermes cron
  - Android battery restrictions and background work scheduling
  - Network changes (Wi-Fi to mobile, Tailscale reconnects)
  - Host restarts (service restart, WSL distribution restart, Windows reboot)
  - Repeated tap interactions (refresh, dismiss, review) without stalls
  - No silent stalls in brief updates or event processing

Evidence to collect:
- System logs (`systemctl --user status hermes-widget`) showing restarts and no failures
- Android app activity showing widget updates and no connection errors after restarts
- Event log (the `widget_read_events` agent tool, or `GET /v1/events` with the agent token)
  showing tap events and refresh completions. There is no `hermes widget events` CLI verb.
- Widget display showing updated brief content after each 6-hour cycle
- Network change events handled gracefully (offline → stale → reconnect)
