# Hermes personal widget

Hermes can send one-way, accessible visual updates to one persistent Android
home-screen widget. The existing `hermes-brief` identity and legacy v2 layout
endpoints remain supported; new updates use `widget_publish` with text, safe
static SVG, or a bounded local PNG/JPEG/WebP.

## Agent entry point

Install the plugin and inspect the detected environment with:

```sh
hermes widget up --json
hermes widget status
```

Use `hermes widget serve --host 127.0.0.1 --port 8788` as the manual diagnostic
server. It is also the command used by the generated service and gateway
startup hook.

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
bash scripts/install-service.sh                    # compatibility entry point
```

Set `HERMES_BIN` when Hermes is not on `PATH`. Set `HERMES_HOME` only when the
Hermes command's detected home is not the persistent volume you intend to use;
the reported home is the one persisted.

### TrueNAS persistence and networking

Mount the Hermes home (including `widget/widget.db`, `widget/assets/`,
`widget/server.json`, `widget/server.log`, `plugins/`, `skills/`, and `hooks/`)
on persistent storage. Keep the container's Hermes profile and `HERMES_HOME`
stable across recreation. Expose only the private widget port to the host or
Tailscale network; the server itself binds loopback by default.

For a private Tailscale HTTPS URL, use Tailscale Serve in front of the bound
port, for example:

```sh
tailscale serve --bg --https=8788 tcp://127.0.0.1:8788
```

Preserve the `Authorization` header through any reverse proxy. The phone uses
the Tailscale HTTPS URL and a short-lived code created with:

```sh
hermes widget code
```

The phone pairs with that code and stores only its device-scoped token. It does
not need the operator/agent token. See [docs/HERMES_AGENT_SETUP.md](docs/HERMES_AGENT_SETUP.md)
and [docs/TAILSCALE_HTTPS.md](docs/TAILSCALE_HTTPS.md) for the detailed flow.

## Android

The app is under `android/`. Build a debug APK with a JDK 17+ toolchain:

```sh
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

The release APK must be signed with the existing personal signing identity;
never commit that identity or its passwords. The widget preserves the last
successful publication offline, renders visuals with fit scaling, and opens a
larger pinch-zoom view on tap. It reports `published`, `downloaded`, and
`render_submitted` separately; none of those states claims that the user saw
or understood an update.
