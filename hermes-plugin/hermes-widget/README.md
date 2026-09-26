# hermes-widget (Hermes Agent plugin)

Connect a Hermes agent to one personal Android home-screen widget. The agent can
publish accessible text, safe static SVG, or a validated local raster image; the legacy
v2 layout flow remains available. Nothing talks to a developer-hosted backend: the endpoint and immutable assets live inside the
user's Hermes home. A phone may be on a different physical network from the host; connect both
to the same Tailscale tailnet and use Tailscale Serve HTTPS rather than exposing the raw port.

## What it registers

- Tools: widget_publish, widget_preview, widget_status, widget_update, widget_validate (dry run), widget_list, widget_read_events, widget_read_intents, widget_resolve_intent, widget_set_quiet_hours, widget_mint_pairing_code, widget_setup
- Slash command: /widget (status)
- CLI: hermes widget serve | setup | code | status | routine | devices | install-skill | preview
- Bundled skill: hermes-widget:widget (layout authoring, design tokens, proactive refresh)
- A private HTTP server started with `hermes widget serve`; secure SVG parsing uses the pinned `defusedxml` dependency, and publication PNG previews use optional Pillow/CairoSVG backends with a bounded fallback.

## Proactive agent behavior

On current Hermes hosts the plugin registers a bounded, cache-safe
`hermes-widget.proactive-guidance` system-prompt section after memory. It tells the agent when
an ambient home-screen update is useful, when to stay quiet, and how to use status, previews,
and action intents without turning every turn into a publish. Older hosts fall back to the
legacy `pre_llm_call` reminder. The recurring cron job attaches the widget skill directly, so
unattended runs do not load it twice.

## Architecture

    Hermes agent turn (chat / cron / heartbeat)
        -> widget_publish -> immutable asset + atomic publication revision
        -> widget_update  -> legacy v2 layout (unchanged)
        -> ~/.hermes/widget/widget.db + assets/
        <- hermes widget serve (HTTP, short-lived pairing/device bearer auth)
        <- Android widget (conditional publication GET, asset GET, render ack)
        <- Android UnifiedPush distributor (content-free `fetch` wake)

The agent process and the server share one SQLite file, so a cron job or a chat turn can update
the widget while the phone polls. Device tokens are stored only as sha256 hashes.

## Install

Use the capability-based bootstrap from the repository root:

    bash scripts/bootstrap-linux.sh --json
    bash scripts/bootstrap-linux.sh --json       # idempotent second run

It detects the actual Hermes executable/version/profile/home, installs the plugin,
skill, gateway startup hook, and one six-hour routine, then starts one healthy
server. Use `--restart-gateway` to reload the agent after installation, or
`--skip-start` for install-only operations. `scripts/install-service.sh` remains
as a compatibility wrapper.

## Run

    hermes widget serve --host 127.0.0.1 --port 8788

The loopback default is intentional; put Tailscale Serve or a private HTTPS reverse proxy in
front of it. The phone pulls from the host and does not need to share a LAN with it. Release
builds require HTTPS. The diagnostic command remains available even when
the generated systemd user unit is unavailable (as in many TrueNAS containers).

## Background refreshes

    hermes widget routine --schedule "every 6h" --widget-id hermes-brief

This installs one Hermes cron job that runs the agent with the hermes-widget skill
and calls `widget_publish` only when there is a genuinely useful supported update.
It leaves the current publication unchanged when nothing useful changed and never
fabricates data or a visual. Manage or remove it with
`hermes widget routine --remove`.

## Security

- Two bearer credentials: an agent token (operator/agent) and per-device tokens.
- Only token hashes are persisted; tokens are compared in constant time.
- Publication input is validated before storage: required accessible summary, 32 KiB text,
  256 KiB SVG, 5 MiB raster, 16-megapixel decoded cap, PNG/JPEG/WebP magic checks, and a
  closed static SVG subset. Scripts, external resources, DOCTYPE/entity declarations,
  unsupported elements, and malformed media are rejected without replacing the prior display.
- Visual bytes are immutable content-addressed files. A publication revision changes only
  after its asset and metadata commit together. Device fetch and render acknowledgement
  are tracked separately; neither is reported as user visibility.
- Layouts retain their 64 KiB / 100-node closed contract. Push rate limit: 30 per widget
  per hour across layout and publication writes. High-priority wakes are separately limited
  to 6/hour and 30/day; over-limit and quiet-hour requests degrade to normal and are visible.
- UnifiedPush endpoints are device-registered secrets. Wake bodies contain only `fetch`;
  publication content is never sent through the distributor.
- Action taps are queue-not-authorise: allowlisted intents are durable, idempotent, audited,
  and resolved by an agent tool. Destructive/external classes require confirmation.
- Bind the server to loopback and use private Tailscale Serve HTTPS; never expose
  the raw port publicly. The Android phone pairs with a short-lived code and never
  receives the operator token.

## Publication API

- `GET /v1/widgets/<id>/publication` returns the current revision with an ETag; devices
  receive `304` when unchanged. Text marks a complete download at metadata fetch; images
  mark it only after the immutable asset is fetched.
- `GET /v1/assets/<id>` serves the validated immutable visual. Assets are device-authenticated,
  immutable-cacheable, integrity checked, and never interpreted as active content.
- `POST /v1/widgets/<id>/publication` is agent-authenticated and accepts text or inline SVG;
  `widget_publish` additionally accepts a bounded local raster path on the host.
- `POST /v1/widgets/<id>/publication/ack` records `render_submitted` (or an explicit
  `rendered` pass) plus the usable widget dimensions. It is not a claim that the user saw or
  understood the publication.
- `PUT /v1/device/instances` records every hosted widget instance and its current size class.
  `POST /v1/widgets/<id>/preview` renders exact current/proposed publications to bounded PNGs;
  `hermes widget preview --sizes ... --out DIR` writes the same previews locally.
- `GET /v1/capabilities` reports the active format, size, priority, inventory, and action limits.
- `widget_status` reports host state separately from ordered `nudge_sent`, `fetched`,
  `downloaded`, and `render_submitted` receipts.

## Contract

- `publication.py` defines the visual input and static SVG subset.
- `../../docs/SCHEMA.md` — the **layout** contract (v2): node registry, typography scale,
  colour rule, caps.
- `layout.schema.json` — the same contract as the machine-readable registry. `validate.py`,
  `LayoutParser.kt`, `Typo.kt`, `skills/widget/SKILL.md` and `docs/` all mirror it;
  `../../scripts/check-contract-parity.py` fails the build when any of them drift.
- `../../docs/CONNECTION.md` — architecture, the REST transport (`/v1/`, still v1), auth model,
  and the frozen module interfaces.
- `skills/widget/SKILL.md` — what the agent is taught: which fields render, the type scale, the
  spacing rhythm, the widget sizes, and the reference layouts in `../../fixtures/golden/`.
- `preview.py` — `hermes widget preview <layout.json>` renders a layout to HTML; publication
  mode rasterises exact text/SVG/raster previews. Raster images use Pillow independently of
  CairoSVG; text falls back to a built-in Pillow text path when libcairo is absent; SVG still
  requires CairoSVG. A deterministic bounded PNG placeholder remains the last-resort fallback.
