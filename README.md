# Hermes personal widget

Hermes can send one-way, accessible visual updates to one persistent Android
home-screen widget. The existing `hermes-brief` identity and legacy v2 layout
endpoints remain supported; new updates use `widget_publish` with text, safe
static SVG, or a bounded local PNG/JPEG/WebP.

## Agent entry point

Install the plugin, skill, gateway hook, refresh routine, and server on the machine that runs Hermes:

```sh
bash scripts/bootstrap-linux.sh --json
hermes widget status
```

On Windows, run `python scripts/bootstrap.py --json` from the repository root. Set `HERMES_BIN` when Hermes is not on `PATH`. The bootstrap is capability-based and reports the detected Hermes home, profile, version, toolsets, and any missing host requirements.

Use `hermes widget serve --host 127.0.0.1 --port 8788` as the manual diagnostic
server. It is also the command used by the generated service and gateway
startup hook. The server is not exposed directly to the phone; publish it through Tailscale Serve or another private HTTPS proxy.

## Linux and TrueNAS bootstrap

From a checkout on the machine that runs Hermes:

```sh
bash scripts/bootstrap-linux.sh --json
```

The installer detects the actual `hermes` executable, version, active profile,
persistent Hermes home, plugin/skill/gateway/cron capabilities, and available
toolsets. It copies the plugin and skill into the detected home, enables the
plugin, installs one idempotent `gateway:startup` hook, creates a bounded
six-hour refresh routine, and starts the widget server only when the configured
port is not already healthy. Re-running it is safe and reports failures rather
than creating duplicate services, jobs, devices, or credentials.

Useful options:

```sh
bash scripts/bootstrap-linux.sh --restart-gateway --json
bash scripts/bootstrap-linux.sh --skip-start       # install without launching now
bash scripts/bootstrap-linux.sh --host 0.0.0.0 --port 8788 --json  # container bind
bash scripts/install-service.sh                    # compatibility entry point
```

`--host` and `--port` are independent: an omitted flag keeps the value already
saved in `widget/server.json`, and a first install defaults to
`127.0.0.1:8788`. A malformed `server.json` is reported instead of overwritten.

Set `HERMES_BIN` when Hermes is not on `PATH`. Set `HERMES_HOME` only when the
Hermes command's detected home is not the persistent volume you intend to use;
the reported home is the one persisted.

### TrueNAS persistence and remote phone networking

Mount the Hermes home (including `widget/widget.db`, `widget/assets/`,
`widget/server.json`, `widget/server.log`, `plugins/`, `skills/`, and `hooks/`)
on persistent storage. Keep the container's Hermes profile and `HERMES_HOME`
stable across recreation. The phone does not need to be on the same LAN as the
host: both devices join the same Tailscale tailnet, and the phone pulls content
from the host over private HTTPS.

The server binds loopback by default. Leave it on loopback for a direct host
install and run Tailscale Serve (or another private HTTPS proxy) in front of it:

```sh
tailscale serve --bg --https=8788 tcp://127.0.0.1:8788
```

**TrueNAS/container:** a port published by the container runtime cannot reach a
loopback-only listener, so bind the server to `0.0.0.0` *inside the container*
while the host publishes that port to its own loopback only. Pass the container
binding to the bootstrap:

```sh
bash scripts/bootstrap-linux.sh --host 0.0.0.0 --port 8788 --json
```

The paired Compose settings keep the host side on loopback:

```yaml
services:
  hermes:
    ports:
      - "127.0.0.1:8788:8788"   # host loopback -> container 0.0.0.0:8788
```

Then run Tailscale Serve on the host (or the namespace that owns the published
port):

```sh
tailscale serve --bg --https=8788 tcp://127.0.0.1:8788
```

Verify the host-side binding yourself; the container's `--host` value is not the
host's port binding:

```sh
docker port <container> | grep 8788   # must show 127.0.0.1:8788, not 0.0.0.0:8788
ss -ltnp | grep 8788                  # no 0.0.0.0 or :: listener on the host
```

Do not expose the raw widget port publicly and do not give the phone the
operator token.

Preserve the `Authorization` header through any reverse proxy. The phone uses
the Tailscale HTTPS URL and a short-lived code created with:

```sh
hermes widget code
```

The phone pairs with that code and stores only its device-scoped token. It does
not need the operator/agent token. It also does not need to be on the same local
network as the host; both devices only need access to the same tailnet. See
[docs/HERMES_AGENT_SETUP.md](docs/HERMES_AGENT_SETUP.md) and
[docs/TAILSCALE_HTTPS.md](docs/TAILSCALE_HTTPS.md) for the detailed flow.

## Android

The app is under `android/`. Build a debug APK with a JDK 17+ toolchain:

```sh
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

The release APK must be signed with the existing personal signing identity;
never commit that identity or its passwords. Until that release is built and
verified, the debug APK is for local testing only. The widget preserves the
last successful publication offline, renders visuals with fit scaling, and
opens a larger pinch-zoom view on tap. It reports `published`, `downloaded`,
`render_submitted`, and the higher-level `fetched`/`nudge_sent` receipts
separately; none of those states claims that the user saw or understood an update.

### Priority wakeups, previews, and action intents

`widget_publish` accepts `priority: "normal" | "high"`. A high publication sends only a
content-free `fetch` wake to a device-registered UnifiedPush endpoint (ntfy and other
self-hostable distributors work); the phone then pulls over the existing private HTTPS path.
The high lane is limited to six wakes per hour and thirty per day, with over-limit requests
visibly degraded to normal. A per-widget UTC quiet-hours window can be set with
`widget_set_quiet_hours`. Android requests battery-optimisation exemption only from the
user, then uses expedited WorkManager with an exact-alarm fallback where permitted. The app's
**Delivery diagnostics** screen shows last poll, fetch, render, and exemption state.

`widget_preview` and `POST /v1/widgets/{id}/preview` rasterise the exact proposed or current
publication at the device's registered `2x2`, `4x2`, `2x4`, `4x4`, or custom sizes. Capacity
findings are warnings, never silent truncation. The CLI writes local PNGs:

```sh
hermes widget preview --sizes 2x2,4x2,4x4 --out ./widget-previews
hermes widget preview --publication-file proposal.json --out ./widget-previews
```

Publication and v2 `button`/`list_item` actions may carry stable `itemId`s. `approve`, `snooze`,
and `open` taps are durably queued as allowlisted intents; they never execute on the HTTP
server. The phone keeps a bounded retry outbox when the private path is unavailable, and the
agent reads intents with `widget_read_intents` before recording a terminal decision with
`widget_resolve_intent`. Destructive/external actions wait for explicit confirmation.
