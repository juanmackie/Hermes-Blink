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
bash scripts/install-service.sh                    # compatibility entry point
```

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

The server binds loopback by default. If Hermes runs in a TrueNAS container,
run Tailscale Serve in the host/network namespace that can reach the container's
published private port, or use another private HTTPS proxy. Do not expose the raw
widget port publicly and do not give the phone the operator token.

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
and `render_submitted` separately; none of those states claims that the user
saw or understood an update.
