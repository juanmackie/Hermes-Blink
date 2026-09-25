# Hermes Widget persistent installation

The supported installer is capability-based; it does not assume Ubuntu,
systemd, `/usr/bin/python3`, or a particular Hermes home.

## Install

From the repository checkout on the Hermes host:

```sh
bash scripts/bootstrap-linux.sh --json
bash scripts/bootstrap-linux.sh --json       # safe second run
```

Use `--restart-gateway` when the agent process must reload the newly installed
plugin. Use `--skip-start` to install the persistent files without starting the
server immediately. `scripts/install-service.sh` remains as a compatibility
entry point and delegates to the same bootstrap.

The installer:

1. resolves the actual `hermes` executable (or `HERMES_BIN`);
2. records its version, active profile, and the home reported by
   `hermes config path`;
3. copies one plugin, one skill, one `gateway:startup` hook, and one bounded
   six-hour routine into that home;
4. enables the `hermes-widget` toolset when the Hermes CLI supports it;
5. uses a generated systemd user unit only when a user manager is available;
6. otherwise starts one detached server and relies on the idempotent gateway
   startup hook after a container/host restart;
7. checks `/v1/health` and exits non-zero with a diagnostic if the server cannot
   start.

It never creates a second device, token, routine, hook, or server when the
port is already serving a healthy Hermes widget server.

## TrueNAS/container requirements

Persist the detected Hermes home across container recreation. At minimum keep
these paths on the persistent dataset:

- `config.yaml`, `.env`, and the active profile directory;
- `widget/widget.db` and SQLite WAL files;
- `widget/assets/`, `widget/server.json`, and `widget/server.log`;
- `plugins/hermes-widget/`, `skills/hermes-widget/`, and
  `hooks/hermes-widget-startup/`.

Set the container's Hermes home/profile explicitly if autodetection would point
at an ephemeral layer. The bootstrap report prints the exact home it used.
Expose TCP `8788` only to the private host/tailnet. The default server bind is
`127.0.0.1`, which is intended for Tailscale Serve or a local reverse proxy.

## Verification

```sh
hermes widget status
hermes widget serve --host 127.0.0.1 --port 8788   # diagnostic/manual path
python scripts/bootstrap.py --dry-run --json       # detection only
```

For a restart/recreation check, restart the Hermes gateway or container and
confirm the hook starts one healthy server. The command remains idempotent;
the plugin and skill are updated in place and duplicate routine records are
removed.
