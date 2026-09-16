# Hermes Widget — Release notes

## v0.2.0 — connects to your own Hermes agent

The widget now talks to **your own Hermes agent**, not a developer-hosted backend. The
`hermes-widget` Hermes plugin starts a small HTTP server on your machine (`hermes widget serve`)
that serves the same REST paths the app already uses, with bearer-token auth.

- **Setup guide:** see [docs/HERMES_AGENT_SETUP.md](docs/HERMES_AGENT_SETUP.md) — install the
  plugin, `hermes widget setup`, `hermes widget serve --host 0.0.0.0 --port 8788`, then enter
  the agent URL + agent token on the phone.
- Default widget id is now `hermes-brief` (was `fixture-morning-brief` / `demo-widget`).
- User-facing copy now says "Hermes agent" instead of "backend".
- **`backend/` is deprecated** for shipping and kept only as a local fixture. The shipped
  transport is the Hermes plugin.

---

## v0.1.0

- Signed release APK instructions: build with ./gradlew assembleRelease, sign with keystore
- Install: enable Unknown Sources, install APK, open app, enter backend URL (Tailscale supported — http:// or https://, no pairing code needed)
- Widget pin: long-press home screen → Widgets → Hermes, or use MainActivity 'Connect' button which triggers requestPinAppWidget
- Requirements: Android 8.0+ (API 26), Tailscale or public HTTP/HTTPS backend, no pairing code (closed network)
- Security: HTTPS for image/action URLs; transport allows HTTP on Tailscale; token hashed at rest; pairing removed
