# Hermes Blink connection contract (transport v1, publication v1)

This document describes the current Android-to-agent boundary. The plugin server and Hermes
agent share one SQLite database on the Hermes host. Android fetches immutable publication
revisions over private HTTPS; there is no developer-hosted backend. The Android app does not
support the former v2 layout API.

For operator installation and pairing, see [SETUP.md](SETUP.md). Publication fields, payload
limits, SVG rules, and retention are in [SCHEMA.md](SCHEMA.md).

## Architecture and invariants

- `hermes widget serve` binds to loopback by default. Expose it through Tailscale Serve or another
  private HTTPS proxy. Container installs bind inside the container and publish to host loopback.
- All protected routes require a valid bearer token, including requests from loopback. No
  unauthenticated caller is elevated to the agent role based on its source address.
- The operator token may publish and administer the host. Each phone receives its own device
  token during one-time pairing. Never put the operator token in the app or a push message.
- Push notifications contain only the generic `fetch` wake. The phone retrieves publication
  content over the authenticated HTTPS connection.
- A tap queues an idempotent event or action intent. The HTTP server does not execute agent work.
- A fetch, download, or render receipt describes transport state; none proves that a person saw
  or understood the content.
- Publication ETags are hashes of the complete canonical envelope, including regions such as the
  ticker. `GET` records a fetch receipt even for `304`; `HEAD` never records one.

## HTTP surface

All routes are rooted at the server URL. `<id>` means a URL-encoded widget id.

| Method and path | Credential | Purpose |
| --- | --- | --- |
| `GET /v1/health` | None | Liveness and protocol/server version. |
| `GET /v1/capabilities` | Agent or device | Publication and client capabilities. |
| `POST /v1/pairing-codes` | Agent | Mint a short-lived, single-use pairing code. |
| `POST /v1/pair` | Pairing code | Exchange a code for a device id/token. |
| `PATCH /v1/device` | Device | Rename this device, update client info, or register push state. |
| `GET /v1/widgets` | Agent or device | List published widget ids. |
| `GET /v1/widgets/<id>/publication` | Agent or device | Fetch the current publication, with ETag/304 support. |
| `HEAD /v1/widgets/<id>/publication` | Agent or device | Check publication headers without a receipt. |
| `POST` or `PUT /v1/widgets/<id>/publication` | Agent | Publish or replace the current publication. |
| `GET` or `HEAD /v1/assets/<asset-id>` | Agent or device | Fetch an immutable media asset; `GET` records delivery. |
| `POST /v1/widgets/<id>/publication/ack` | Device | Acknowledge a downloaded/rendered revision. |
| `POST /v1/widgets/<id>/events` | Device (agent also accepted) | Submit a bounded, idempotent tap or update request. |
| `POST /v1/widgets/<id>/preview` | Agent | Render a proposed/current publication without publishing it. |
| `PUT /v1/device/instances` | Device | Replace this device's widget-size inventory. |
| `PUT /v1/device/attention` | Device | Report aggregate-only render/dwell/tap counters. |
| `POST /v1/device/action-intents/<intent-id>/confirm` | Owning device | Confirm a sensitive intent before the agent can resolve it as applied. |
| `GET` or `PUT /v1/widgets/<id>/settings` | Agent | Read or change quiet-hour settings. |
| `GET /v1/widgets/<id>/history` | Agent or device | Read a bounded revision summary. |
| `GET /v1/events` | Agent | Read device events with an ascending id cursor. |
| `GET /v1/intents` | Agent | Read queued action intents. |
| `POST /v1/intents` | Agent | Record a terminal agent decision for an intent; it cannot attest device confirmation. |

Unknown paths return JSON `404`. The former `GET`/`PUT /v1/widgets/<id>` layout endpoints are
removed and return `404`; use the `/publication` route. Authentication failures return `401`,
role violations return `403`, invalid bodies return `400`, rate limits return `429`, and stale
publications return `410`. Unexpected server failures return a request id with `500` and are
logged with that id.

## Credentials and pairing

The operator token is created by `hermes widget up`, stored in the widget data directory, or
provided with `HERMES_WIDGET_AGENT_TOKEN`. Server-side verification uses a constant-time compare.
The database stores only SHA-256 hashes of device tokens. A pairing code is the only credential
accepted by `POST /v1/pair`; it expires and can be redeemed once. Revoking a device invalidates its
token on the next request.

Client version/build/SDK/SHA headers are bounded metadata, not credentials. Malformed client
metadata is ignored rather than used to authorize a request. The proxy must preserve the
`Authorization: Bearer …` header unchanged.

## Delivery and idempotency

Publication revisions increase monotonically per widget. The server records fetch, asset download,
render acknowledgement, push attempt, and render-build details separately. A render acknowledgement
is accepted only for a revision that the same device fetched. `HEAD` requests are observational;
`GET` is receipt-bearing even when the body is omitted by a conditional `304`.

Event clients should send a stable `clientEventId`. Duplicate submissions are idempotent per
widget/device. `request_update` is rate-limited per device and coalesced while a refresh is already
pending. The routine explicitly consumes triggered requests through `widget_status`; status
returns bounded recent rows and reports whether additional rows were truncated.

## Verification

From the repository root:

```sh
python -m unittest discover -s hermes-plugin/hermes-widget/tests -v
python scripts/check-contract-parity.py
python scripts/verify-cli-local.py
```

The Android build and device pairing/fetch/render path require the Android SDK, a JDK, and a real
phone or emulator; see [SETUP.md](SETUP.md) for the host and device checklist.
