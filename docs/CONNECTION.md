# Hermes Widget <-> Hermes Agent Connection (transport v1; layout v2 + publication v1)

Status: implementation spec (frozen interfaces). Read this before writing any file in
hermes-plugin/hermes-widget/.

Transport paths stay `/v1/`. The layout contract inside the payload is **v2** — see
`docs/SCHEMA.md` for the node registry, the typography scale and the colour rule.

## 1. Goal

The Android home-screen widget (android/) must talk to the USER'S OWN Hermes agent, not to a
central backend hosted on the developer's machine. The agent must be able to update the widget
proactively, in the background, with information it decides the user wants to see.

The standalone FastAPI service in backend/ is DEPRECATED for shipping. It stays only as a local
fixture. The shipped transport is a Hermes plugin.

## 2. Architecture

    +--------------------------- user's machine / VPS ----------------------------+
    |                                                                             |
    |   Hermes agent process                                                      |
    |     - agent turn (chat, cron, heartbeat)                                    |
    |     - tools: widget_publish / widget_update / widget_status / ...                      |
    |                        |                                                     |
    |                        v                                                     |
    |   ~/.hermes/widget/widget.db + assets/ (SQLite, publications, layouts, devices) |
    |                        ^                                                     |
    |                        |                                                     |
    |   hermes widget serve  (ThreadingHTTPServer, plugin-owned)                  |
    |         host:port, bearer auth                                              |
    +------------------------------------|----------------------------------------+
                                         |  Tailscale Serve / private HTTPS proxy
                                         v
                              +--------------------------+
                              |  Android widget (Glance) |
                              |  Config: agent base URL  |
                              |  SecureStore: device tkn |
                              +--------------------------+

Key properties:

- The server runs from the Hermes installation and reads the SAME SQLite DB the agent tools write.
- No dependency on the developer's machine and no dependency on hermes dashboard / OAuth.
- The agent is the intelligence: a bundled skill teaches it to author layouts, and a Hermes cron
  job (installed by the plugin) makes it refresh the widget in the background.

## 3. Why a dedicated server (not dashboard plugin routes)

The dashboard's `/api/plugins/<name>/` routes are gated by an ephemeral session token or an OAuth
gate, and the token-auth seam only accepts EXACT registered paths, so dynamic paths like
`/v1/widgets/<id>` cannot be token-authed. A headless device therefore gets its own
plugin-owned loopback HTTP server started with `hermes widget serve`, exposed to a phone only through Tailscale Serve or another private HTTPS proxy.

## 4. REST contract (served at the ROOT of the plugin server)

The Android client already speaks this contract. Do not change paths.

    GET  /v1/health
         -> 200 {"status":"ok","version":"<plugin version>","widgets":N,"time":"<iso8601>"}
         Auth: none.

    POST /v1/pairing-codes
         Auth: AGENT token.
         -> 200 {"code":"ABCD-1234","expiresAt":"<iso8601>"}

    POST /v1/pair
         Body: {"code":"ABCD-1234","deviceLabel":"Pixel 9"}
         Auth: none (the code is the credential).
         -> 200 {"deviceId":"...","token":"dvc_...","widgets":["hermes-brief"]}
         -> 400 {"error":"invalid_or_expired_code"}

    PATCH /v1/device
         Auth: paired DEVICE token only (a device may rename itself or register a
         UnifiedPush endpoint).
         Body: {"label":"Kitchen tablet"} or {"pushEndpoint":"https://ntfy.example/up/..."} or
               {"pushState":{"state":"failed","distributorPresent":true,"failureReason":"AUTH_FAILED"}}
         -> 200 {"deviceId":"...","label":"Kitchen tablet","pushEndpointRegistered":true,
                 "state":"registered","distributorPresent":true,"registered":true}
         -> 403 for an agent/operator token; 400 for an empty/missing field

    GET  /v1/widgets
         Auth: DEVICE or AGENT token.
         -> 200 {"widgets":["hermes-brief", ...]}

    GET  /v1/capabilities
         Auth: DEVICE or AGENT token.
         -> 200 {"publicationVersion":1,"kinds":["text","image"],...}

    GET  /v1/widgets/<widget_id>/publication
         Auth: DEVICE or AGENT token.
         -> 200 <publication envelope, ETag, Cache-Control: private, no-cache>
         -> 304 when If-None-Match matches the current publication revision
         -> 404 {"error":"unknown_publication"}
         -> 410 {"error":"publication_stale"} when maxAgeSeconds has elapsed

    POST /v1/widgets/<widget_id>/publication
         Auth: AGENT token.
         Body: {"title":"...","summary":"...","text":"...","max_age_seconds":600,
                "priority":"normal|high","itemId":"...","actions":[...]} or
               {"title":"...","summary":"...","svg":"<svg>...</svg>"}
         -> 200 <publication envelope with monotonically increasing revision; high
              priority includes requested/effective priority and any visible downgrade>
         -> 400 invalid_publication | 413 publication_too_large | 429 rate_limited

    GET  /v1/assets/<asset_id>
         Auth: DEVICE or AGENT token.
         -> 200 immutable image bytes with ETag, Content-Type, nosniff
         -> 304 when If-None-Match matches | 404 asset_not_found

    POST /v1/widgets/<widget_id>/publication/ack
         Auth: paired DEVICE token only.
         Body: {"revision":7,"status":"render_submitted",
                "renderedWidth":1080,"renderedHeight":920}
         -> 200 {"ok":true,"status":"render_submitted",...}
         -> 409 render_not_downloaded | 400 invalid_render_ack

    GET  /v1/widgets/<widget_id>
         Auth: DEVICE or AGENT token.
         -> 200 <legacy v2 layout envelope + additive publication pointer, application/json>
         -> 404 {"error":"unknown_widget"}

    PUT  /v1/widgets/<widget_id>
         Auth: AGENT token.
         Body: <layout envelope>
         -> 200 {"ok":true,"scope":"legacy_layout","publicationCreated":false,
                 "storedAt":"<iso8601>","layoutUpdatedAt":"<fixture timestamp or null>",
                 "warnings":[{"code":"legacy_layout_not_published",...}]}
         -> 400 {"error":"invalid_layout","detail":"..."} | 413 size cap | 429 rate limit

    POST /v1/widgets/<widget_id>/events
         Auth: DEVICE or AGENT token.
         Body: {"event":"toggle_focus","payload":{...}} or
               {"event":"approve","itemId":"task-1","revision":7,
                "actionClass":"reversible","clientEventId":"..."}
         -> 200 {"ok":true,"id":<int>} for an event, or
            {"ok":true,"eventId":<int>,"intent":{...}} for a queued action

    POST /v1/widgets/<widget_id>/preview
         Auth: AGENT token.
         Body: either {"sizes":["2x2","4x2"]} for the current publication, or
               {"publication": {title, summary, text|svg|file_path, ...}, "sizes":[...]}
         -> 200 {"ok":true,"previews":[{"size":"2x2","mediaType":"image/png","data":"<base64>",...}]}
         Does not publish or mutate the current revision. PNGs are an advisory server
         rasterisation; the Android device remains authoritative.

    PUT  /v1/device/instances
         Auth: paired DEVICE token.
         Body: {"widgetId":"hermes-brief","instances":[
           {"instanceId":"12","sizeClass":"4x2","widthDp":270,"heightDp":120,
            "widthPx":540,"heightPx":240}]}
         -> 200 {"ok":true,"instances":[...]}
         Inventory is replaced atomically for this device/widget; at most 32 instances
         are accepted.

    PUT  /v1/widgets/<widget_id>/settings
         Auth: AGENT token.
         Body: {"quietHours":{"start":"22:00","end":"07:00"}} or {"quietHours":null}
         -> 200 {"ok":true,"quietHours":...}
         Times are UTC. High-priority wakes degrade visibly to normal while quiet.

    GET  /v1/intents?widget_id=<id>&status=<queued|awaiting_confirmation|applied|declined|held|expired>
         Auth: AGENT token.
         -> 200 {"intents":[...]}
    POST /v1/intents
         Auth: AGENT token.
         Body: {"intentId":"intent_...","outcome":"applied|declined|held|expired",
                "result":"...","confirmed":true}
         -> 200 {"ok":true,"intent":{...}}
         Resolution records an agent decision; it never executes the operation.

    hermes widget wake-test [--widget-id <id>] [--json]
         Auth: operator CLI/agent path.
         Sends one content-free `fetch` wake to registered device endpoints and prints
         `receiptChain` with `nudge_sent`/`failed`. It creates no publication revision
         and does not claim device delivery.

    GET  /v1/events?since=<iso8601>&widget_id=<id>&limit=<int>
         Auth: AGENT token.
         -> 200 {"events":[{"id":..,"widgetId":..,"deviceId":..,"event":..,"payload":..,"createdAt":..}]}

All error bodies are JSON: `{"error":"<code>","detail":"<human text>"}`. Auth failures are
401 {"error":"unauthorized"}. Missing route is 404 {"error":"not_found"}. Wrong method is 405.

The v2 layout (`/v1/widgets/<id>`) and the publication (`/v1/widgets/<id>/publication`) are
separate stores. Publishing to one does not update the other; the layout response carries an
additive `publication` pointer so a raw API consumer does not mistake a stale layout for a lost
publish. Legacy layout writes explicitly report `scope: "legacy_layout"`,
`publicationCreated: false`, `storedAt`, and a `legacy_layout_not_published` warning; connected
Android devices fetch the publication channel only. Use `hermes widget status` to see both
channels and per-device revision history.

## 5. Auth model

Two credentials, both bearer tokens in the Authorization header.

1. AGENT token - the agent / operator credential. Created on first setup, stored at
   <data_dir>/agent_token (chmod 600), or supplied via env HERMES_WIDGET_AGENT_TOKEN.
   Verified with hmac.compare_digest.
2. DEVICE token - per paired device. Format "dvc_" + secrets.token_urlsafe(16-ish).
   Only its sha256 is stored in the devices table. Verified by hashing the presented token.

Loopback is not an authorization upgrade. Every protected route requires a valid agent or
paired device bearer token, including requests from 127.0.0.1. This prevents a local proxy
or accidentally exposed listener from bypassing the credential boundary.

## 6. Data model (SQLite)

    widgets(widget_id TEXT PRIMARY KEY, layout_json TEXT NOT NULL, updated_at TEXT)
    assets(asset_id TEXT PRIMARY KEY, sha256 TEXT, media_type TEXT, byte_length INTEGER,
           width INTEGER, height INTEGER, created_at TEXT)
    publications(widget_id TEXT PRIMARY KEY, revision INTEGER, publication_id TEXT,
                 payload_json TEXT, published_at TEXT, expires_at TEXT)
    publication_fetches(widget_id TEXT, device_id TEXT, revision INTEGER,
                        fetched_at TEXT, downloaded_at TEXT,
                        PRIMARY KEY(widget_id, device_id, revision))
    publication_acks(widget_id TEXT, device_id TEXT, revision INTEGER,
                     rendered_at TEXT, width INTEGER, height INTEGER,
                     PRIMARY KEY(widget_id, device_id, revision))
    publication_priorities(widget_id, revision, requested_priority, effective_priority,
                           degraded_reason, created_at)
    publication_nudges(widget_id, revision, device_id, status, attempted_at, sent_at, detail)
    delivery_receipts(widget_id, device_id, revision, state, occurred_at, detail)
    device_push_endpoints(device_id, endpoint, endpoint_hash, updated_at)
    device_push_state(device_id, state, distributor_present, failure_reason, updated_at)
    widget_instances(device_id, widget_id, instance_id, size_class,
                     width_dp, height_dp, width_px, height_px, reported_at)
    action_intents(intent_id, widget_id, device_id, event_id, item_id, action_class,
                   status, source_revision, payload_json, result, created_at,
                   updated_at, expires_at)
    action_audit(id, intent_id, widget_id, device_id, item_id, action_class,
                 revision, event, outcome, detail, created_at)
    widget_settings(widget_id, quiet_start, quiet_end, updated_at)
    devices(device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, label TEXT,
            created_at TEXT, last_seen_at TEXT, revoked INTEGER DEFAULT 0)
    events(id INTEGER PRIMARY KEY AUTOINCREMENT, widget_id TEXT, device_id TEXT,
           event TEXT, payload TEXT, created_at TEXT)
    pairing_codes(code TEXT PRIMARY KEY, device_id TEXT, expires_at INTEGER, consumed INTEGER)
    widget_devices(widget_id TEXT, device_id TEXT, PRIMARY KEY(widget_id, device_id))

Timestamps are ISO-8601 UTC strings via _now(). Enable WAL and foreign_keys off; use a module
lock (threading.RLock) around writes; open a fresh connection per operation (ThreadingHTTPServer).

## 7. Security rules (enforced in store/validate)

Publication rules are enforced by `publication.py` and `store.put_publication`:

- title is required and bounded; summary is required and bounded.
- exactly one of plain text, inline static SVG, or a local PNG/JPEG/WebP path.
- text <= 32 KiB, SVG <= 256 KiB, raster bytes <= 5 MiB.
- raster dimensions <= 16,384 per side and <= 16,000,000 decoded pixels; PNG/JPEG/WebP
  headers and chunk bounds are checked before storage.
- SVG uses a closed static subset; scripts, external resources, processing instructions,
  DOCTYPE/entity declarations, foreignObject, animation, filters, and unsupported attributes
  are rejected. XML is parsed with the pinned `defusedxml` dependency.
- publication and layout writes share the 30-per-hour push limit. Expired publications
  remain auditable but are marked expired; they are not silently called visible.

Layout contract rules live in `docs/SCHEMA.md`. The legacy push-time guardrails remain:

- Layout payload <= 64 KB UTF-8. Node count <= 100. Reject with LayoutError.
- version == 2 (int, not bool). widgetId 1..128 chars.
- Colours are 3- or 6-digit hex only (#RGB / #RRGGBB): accentColor, text.color, divider.color,
  badge.color, stat.color, calendar.events[].color. 8-digit alpha hex and named colours are
  rejected at push time (the device ignores them rather than failing to paint).
- action.kind is one of event|refresh|dismiss|review|approve|snooze|open. The latter three
  require a stable itemId and enqueue an intent; the HTTP server never executes them.
- High-priority publication wakes are content-free `fetch` messages sent to the paired device's
  UnifiedPush endpoint. The endpoint is stored as a secret, never returned by status, and the
  high lane is limited to six per hour and thirty per day. Quiet hours and rate-limit
  degradation are recorded rather than hidden.
- text.value <= 500; button.label <= 200; badge.text <= 50; stat.label/stat.value <= 50;
  list_item.title/subtitle <= 200; calendar.events <= 50.
- Per-container child caps: column/box 100, row 20, list 100.
- Push rate limit: 30 pushes per widget per 3600s -> RateLimitError. A dry run
  (`inspect_widget`) does NOT consume one.
- Never log or return a raw token. Only token hashes are persisted.

## 8. Frozen module interfaces (DO NOT CHANGE without updating this file)

These are written already and are dependencies for the workstreams below.

store.py:
    class StoreError(Exception)
    class LayoutError(StoreError)
    class RateLimitError(StoreError)
    class PairingError(StoreError)
    DEFAULT_WIDGET_ID: str = "hermes-brief"
    MAX_LAYOUT_BYTES, MAX_NODES, PUSH_MAX_PER_WINDOW
    data_dir() -> Path
    db_path() -> Path
    agent_token_path() -> Path
    init_db() -> None
    get_agent_token(create: bool = True) -> str | None
    verify_agent_token(token: str) -> bool
    put_widget(widget_id: str, layout: dict) -> dict            # {"ok":True,"widgetId":..,"updatedAt":..}
    put_publication(widget_id: str, *, title, summary, text=None, svg=None,
                    file_path=None, expires_at=None, ttl_seconds=None,
                    max_age_seconds=None, priority="normal", item_id=None,
                    actions=None) -> dict
    get_publication(widget_id: str) -> dict | None
    publication_status(widget_id: str = DEFAULT_WIDGET_ID) -> dict
    publication_for_asset(asset_id: str) -> dict | None
    inspect_widget(widget_id: str, layout: dict) -> dict        # dry run: validate + report, no store, no rate limit
    get_widget(widget_id: str) -> dict | None
    list_widgets() -> list[str]
    backup_db() -> Path | None                                  # widget.db.bak.UTC_TIME; producer for `rollback`
    post_event(widget_id: str, device_id: str, event: str, payload: dict | None) -> int
    get_events(since: str | None = None, widget_id: str | None = None, limit: int = 200) -> list[dict]
    record_publication_fetch(widget_id, device_id, revision, \*, downloaded=False) -> None
    record_asset_download(widget_id, device_id, revision, asset_id) -> None
    acknowledge_publication_render(widget_id, device_id, revision, width, height, *, status="render_submitted") -> dict
    report_widget_instances(device_id, widget_id, instances) -> list[dict]
    list_widget_instances(widget_id=None, *, device_id=None) -> list[dict]
    set_device_push_endpoint(device_id, endpoint) -> dict
    get_widget_settings(widget_id) -> dict
    set_widget_settings(widget_id, quiet_hours) -> dict
    post_action_event(widget_id, device_id, event, payload, *, revision=None,
                      item_id=None, action_class=None, confirm_on_device=False) -> dict
    get_intents(widget_id=None, *, status=None, limit=200) -> list[dict]
    get_action_audit(widget_id=None, *, limit=200) -> list[dict]
    resolve_intent(intent_id, outcome, *, result=None, confirmed=False) -> dict
    get_asset(asset_id: str) -> dict
    read_asset(asset_id: str) -> tuple[dict, bytes]
    mint_pairing_code(ttl_minutes: int = 10) -> dict            # {"code":..,"expiresAt":..}
    register_device(code: str, label: str) -> dict | None       # {"deviceId","token","widgets"} | None
    list_devices() -> list[dict]
    revoke_device(device_id: str) -> bool
    device_for_token(token: str) -> dict | None                 # {"deviceId","label"} | None
    check_push_rate(widget_id: str) -> None                     # raises RateLimitError
    validate_layout(layout: dict) -> None                       # raises LayoutError

publication.py:
    prepare_publication(...) -> PreparedPublication
    validate_svg(raw: bytes) -> tuple[int, int]
    validate_raster(data: bytes, suffix: str = "") -> tuple[str, int, int]
    capabilities() -> dict

validate.py:
    class ValidationError(ValueError)
    def validate_layout(layout: dict) -> None
    def inspect_layout(layout: dict, inventory: list[dict] | None = None) -> dict      # validates, then reports nodeCount/bytes/textStyles/warnings

v2 is the only accepted contract. A layout that is not version 2 is rejected, not migrated:
`migrate_layout` and the v1 compatibility path were removed once it was clear nothing had
shipped a v1 database (see RELEASE.md v2.1.0).

preview.py:
    def render_html(layout: dict, *, title: str = ...) -> str     # reads typography/palette from layout.schema.json
    def preview_file(path, out=None) -> Path
    def is_stale(layout: dict, now=None) -> bool                  # mirrors WidgetLayout.isStale in Layout.kt

Layout tokens (the single source for type scale, palette and spacing):
    layout.schema.json -> definitions.typography / definitions.palette / definitions.spacing
    android/.../widget/Typo.kt reads the same values; scripts/check-contract-parity.py compares them.

## 9. File ownership map (parallel workstreams)

OWNER FOUNDATION (already written):
    docs/CONNECTION.md, hermes-plugin/hermes-widget/store.py, validate.py,
    layout.schema.json, plugin.yaml

WORKSTREAM A - server:
    hermes-plugin/hermes-widget/server.py
    Exposes: make_server(host, port, *, certfile=None, keyfile=None) -> ThreadingHTTPServer
             run_server(host, port, *, certfile=None, keyfile=None, quiet=False) -> None
    Standard-library HTTP plus pinned defusedxml; uses store.* for all data.

WORKSTREAM B - agent surface:
    hermes-plugin/hermes-widget/schemas.py
    hermes-plugin/hermes-widget/tools.py
    `hermes-plugin/hermes-widget/__init__.py`
    Tool schemas + handlers; register(ctx) wiring tools, slash command, CLI command, skill.

WORKSTREAM C - proactive + CLI + skill:
    hermes-plugin/hermes-widget/proactive.py
    hermes-plugin/hermes-widget/cli.py
    hermes-plugin/hermes-widget/preview.py
    hermes-plugin/hermes-widget/skills/widget/SKILL.md
    CLI subcommands: serve, setup, code, status, routine, devices, install-skill, preview.

WORKSTREAM D - Android + docs + contract:
    android/app/src/main/java/com/you/hermeswidget/**
    hermes-plugin/hermes-widget/layout.schema.json   (the registry; everything else mirrors it)
    hermes-plugin/hermes-widget/validate.py
    scripts/check-contract-parity.py
    README.md, RELEASE.md, docs/SCHEMA.md, docs/CONNECTION.md, docs/HERMES_AGENT_SETUP.md

WORKSTREAM E - verification:
    hermes-plugin/hermes-widget/tests/test_widget_plugin.py
    An end-to-end stdlib unittest that starts the server and exercises pair -> push -> fetch -> event.

Cross-workstream symbol contract:
    Hermes builds the top-level "widget" parser itself, then calls setup_fn(parser) with THAT
    parser and installs handler_fn as args.func. So cli.add_parser(parser) must only add
    subcommands; calling parser.add_parser("widget", ...) raises AttributeError and aborts
    plugin CLI discovery silently (this caused a real bug during the build).
    cli.py calls server.run_server(host, port, certfile=..., keyfile=...) and proactive.install_routine(...)
    `__init__.py` calls cli.add_parser / cli.dispatch, tools.*, schemas.*, proactive.*
    tools.py calls store.*
    server.py calls store.*
    proactive.py calls store.DEFAULT_WIDGET_ID and cron.jobs.create_job

## 10. Ground truth (read these, do not guess)

- Hermes install: `<Hermes install>` (the path reported by `hermes config path`)
- Plugin API: hermes_cli/plugins.py  (class PluginContext; register_tool, register_command,
  register_cli_command, register_skill, register_hook)
- Plugin loading: `hermes_cli/plugins.py` `_load_directory_module` (module name
  `hermes_plugins.MODULE_NAME`,
  so RELATIVE IMPORTS work: from . import store)
- Cron API: cron/jobs.py create_job(prompt, schedule, name, skills, deliver, ...)
- Hermes home: hermes_constants.get_hermes_home()
- Layout registry: hermes-plugin/hermes-widget/layout.schema.json (the contract; all mirrors are checked)
- Android endpoints: android/app/src/main/java/com/you/hermeswidget/net/HermesApi.kt
- A working example plugin: `<Hermes home>/plugins/hermes-live`
  (`tools.py`, `__init__.py`)
- backend/ holds no code: the FastAPI service that used to live there is gone (see
  backend/DEPRECATED.md for where its rules moved).

**Host API documentation is not vendored here.** `docs/hermes-notes/` used to hold 4,925 lines of
copied upstream Hermes docs (api-server, cron, dashboard, hooks, plugins, programmatic,
web-dashboard). They were unreferenced by any file and would have drifted silently from upstream,
so they were deleted in favour of the install itself being the authority:

    <Hermes install>/docs/                 # the upstream docs for whichever version is installed
    hermes_cli/plugins.py, cron/jobs.py    # the actual API surfaces

If you need offline copies, generate them from the installed version rather than committing a
snapshot that ages.

## 11. Coding standards

- Python 3.11. The HTTP server uses the standard library; `defusedxml` remains the
  security-critical parser dependency. `Pillow`/`CairoSVG` are bounded publication-preview
  backends and have a deterministic PNG fallback when unavailable. No fastapi/uvicorn/requests/
  pydantic in the plugin.
- from __future__ import annotations; type hints; small functions; no bare except.
- Relative imports inside the plugin package.
- Docs/comments explain WHY, not what. No placeholder or TODO-only bodies.
- Never commit secrets or tokens.

## 12. Verification

Run the Python tests with the Hermes venv or another Python 3.11+ interpreter. Use the
unittest discovery command because the plugin directory is hyphenated and is not a pytest
import root:

    python -m pip install -r hermes-plugin/hermes-widget/requirements.txt
    python -m unittest discover -s hermes-plugin/hermes-widget/tests -v
    python scripts/check-contract-parity.py       # schema vs validator vs parser vs renderer vs skill vs docs
    python scripts/verify-cli-local.py            # CLI verbs, redaction, upgrade/rollback

Android (JDK 17+; the current local build uses JDK 21) and the Android SDK:

    cd android && ./gradlew testDebugUnitTest lintDebug assembleDebug

See a layout without a phone:

    hermes widget preview fixtures/golden/large-brief.json   # -> .preview.html

Host integration and remote-phone end-to-end setup:

    bash scripts/bootstrap-linux.sh --json
    hermes widget status
    tailscale serve --bg --https=8788 tcp://127.0.0.1:8788
    hermes widget code

The phone joins the same Tailscale tailnet, pairs with the short-lived code,
and pulls the publication over private HTTPS. It never receives the agent
token and does not need to share a LAN with the host. A direct host install
keeps the widget server on loopback (`127.0.0.1`); only a container binds
`0.0.0.0` inside the container behind a host-side loopback port publish, so the
host itself is never listening on `0.0.0.0`.
