Tailscale / private HTTPS backend URLs
------------------------------------------
Most users connect the Android widget to the Hermes backend over Tailscale (or a similar mesh VPN) rather than a public domain.

Supported patterns (https:// preferred; http:// allowed on Tailscale / closed network — no domain restriction):
- https://machine.ts.net
- https://100.x.y.z
- https://hermes-widget.local
- http://100.x.y.z (cleartext allowed in app via usesCleartextTraffic)

No pairing flow is required (`POST /v1/pair` removed). The Android `SettingsActivity` validates it responds to `/v1/health`. No strict domain allowlist is applied — Tailscale IP/host URLs are fully supported.
