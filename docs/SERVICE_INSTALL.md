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

A direct host install keeps the default `127.0.0.1` bind and puts Tailscale Serve
(or another private HTTPS proxy) in front of it. A container install binds
`0.0.0.0` *inside the container* and the host publishes the port to loopback
only, because a runtime-published port cannot reach a loopback-only listener:

```sh
bash scripts/bootstrap-linux.sh --host 0.0.0.0 --port 8788 --json
```

```yaml
services:
  hermes:
    ports:
      - "127.0.0.1:8788:8788"   # host loopback -> container 0.0.0.0:8788
```

Verify the host side yourself; the container's `--host` is not the host's port
binding:

```sh
docker port <container> | grep 8788   # must show 127.0.0.1:8788, not 0.0.0.0:8788
ss -ltnp | grep 8788                  # no 0.0.0.0 or :: listener on the host
```

`--host` and `--port` are independent: an omitted flag keeps the value saved in
`widget/server.json`, and a first install defaults to `127.0.0.1:8788`. A
malformed `server.json` is reported instead of overwritten. Never expose the raw
widget port publicly.

## Verification

```sh
hermes widget status                        # configured bind vs health-probe address
hermes widget serve --host 127.0.0.1 --port 8788   # diagnostic/manual path
python scripts/bootstrap.py --dry-run --json       # detection only
```

For a restart/recreation check, restart the Hermes gateway or container and
confirm the hook starts one healthy server. The command remains idempotent;
the plugin and skill are updated in place and duplicate routine records are
removed.
