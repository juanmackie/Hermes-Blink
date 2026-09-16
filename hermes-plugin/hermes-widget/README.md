# hermes-widget (Hermes Agent plugin)

Connect a Hermes agent to the Hermes home-screen widget. The agent authors and pushes
server-driven layouts; the widget renders them and sends interaction events back. Nothing
talks to a developer-hosted backend - the endpoint runs inside YOUR Hermes installation.

## What it registers

- Tools: widget_update, widget_list, widget_read_events, widget_mint_pairing_code
- Slash command: /widget (status)
- CLI: hermes widget serve | setup | code | status | routine | devices | install-skill
- Bundled skill: hermes-widget:widget (layout authoring + proactive refresh)
- A dependency-free HTTP server (stdlib only) started with "hermes widget serve"

## Architecture

    Hermes agent turn (chat / cron / heartbeat)
        -> widget_update tool
        -> ~/.hermes/widget/widget.db  (layouts + devices + events)
        <- hermes widget serve  (HTTP, bearer auth)
        <- Android widget (GET layout, POST events)

The agent process and the server share one SQLite file, so a cron job or a chat turn can update
the widget while the phone polls. Device tokens are stored only as sha256 hashes.

## Install

    # from a checkout
    copy hermes-plugin\hermes-widget to %USERPROFILE%\.hermes\plugins\hermes-widget
    hermes plugins enable hermes-widget
    hermes widget setup

Or use scripts/install-hermes-widget.ps1 from the repository root.

## Run

    hermes widget serve --host 0.0.0.0 --port 8788
    # TLS (optional; Tailscale already encrypts transport):
    hermes widget serve --host 0.0.0.0 --port 8788 --certfile cert.pem --keyfile key.pem

## Background refreshes

    hermes widget routine --schedule "every 6h" --widget-id hermes-brief

This installs a Hermes cron job that runs the agent with the hermes-widget skill and pushes a
fresh layout. Manage or remove it with "hermes widget routine --remove".

## Security

- Two bearer credentials: an agent token (operator/agent) and per-device tokens.
- Only token hashes are persisted; tokens are compared in constant time.
- Layouts are validated before storage: 64 KB cap, 100-node cap, https-only images,
  hermes:// deeplinks, closed action set, closed node set.
- Push rate limit: 30 per widget per hour.
- Prefer binding to a Tailscale interface rather than a public address.

## Contract

See ../../docs/CONNECTION.md (architecture + REST + frozen interfaces) and
../../docs/SCHEMA.md (layout v1). The HTTP contract is also kept as layout.schema.json here.
