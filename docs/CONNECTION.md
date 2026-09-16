# Hermes Widget <-> Hermes Agent Connection (v1)

Status: implementation spec (frozen interfaces). Read this before writing any file in
hermes-plugin/hermes-widget/.

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
    |     - tools: widget_update / widget_list / widget_read_events / ...          |
    |                        |                                                     |
    |                        v                                                     |
    |   ~/.hermes/widget/widget.db   (SQLite: layouts, devices, events)            |
    |                        ^                                                     |
    |                        |                                                     |
    |   hermes widget serve  (stdlib ThreadingHTTPServer, plugin-owned)            |
    |         host:port, bearer auth                                              |
    +------------------------------------|----------------------------------------+
                                         |  Tailscale / LAN / tunnel (HTTP or HTTPS)
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

The dashboard's /api/plugins/<name>/ routes are gated by an ephemeral session token or an OAuth
gate, and the token-auth seam only accepts EXACT registered paths, so dynamic paths like
/v1/widgets/<id> cannot be token-authed. A headless device therefore gets its own small,
dependency-free HTTP server started with: hermes widget serve

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

    GET  /v1/widgets
         Auth: DEVICE or AGENT token.
         -> 200 {"widgets":["hermes-brief", ...]}

    GET  /v1/widgets/<widget_id>
         Auth: DEVICE or AGENT token.
         -> 200 <layout envelope, application/json>
         -> 404 {"error":"unknown_widget"}

    PUT  /v1/widgets/<widget_id>
         Auth: AGENT token.
         Body: <layout envelope>
         -> 200 {"ok":true,"widgetId":"...","updatedAt":"<iso8601>"}
         -> 400 {"error":"invalid_layout","detail":"..."} | 413 size cap | 429 rate limit

    POST /v1/widgets/<widget_id>/events
         Auth: DEVICE or AGENT token.
         Body: {"event":"toggle_focus","payload":{...}}
         -> 200 {"ok":true,"id":<int>}

    GET  /v1/events?since=<iso8601>&widget_id=<id>&limit=<int>
         Auth: AGENT token.
         -> 200 {"events":[{"id":..,"widgetId":..,"deviceId":..,"event":..,"payload":..,"createdAt":..}]}

All error bodies are JSON: {"error":"<code>","detail":"<human text>"}. Auth failures are
401 {"error":"unauthorized"}. Missing route is 404 {"error":"not_found"}. Wrong method is 405.

## 5. Auth model

Two credentials, both bearer tokens in the Authorization header.

1. AGENT token - the agent / operator credential. Created on first setup, stored at
   <data_dir>/agent_token (chmod 600), or supplied via env HERMES_WIDGET_AGENT_TOKEN.
   Verified with hmac.compare_digest.
2. DEVICE token - per paired device. Format "dvc_" + secrets.token_urlsafe(16-ish).
   Only its sha256 is stored in the devices table. Verified by hashing the presented token.

Loopback requests (127.0.0.1 / ::1) may be treated as trusted for agent-level calls so local
tooling works without a token, but device routes still require a device token. Keep this rule
centralized in server.py (_is_loopback).

## 6. Data model (SQLite)

    widgets(widget_id TEXT PRIMARY KEY, layout_json TEXT NOT NULL, updated_at TEXT)
    devices(device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, label TEXT,
            created_at TEXT, last_seen_at TEXT, revoked INTEGER DEFAULT 0)
    events(id INTEGER PRIMARY KEY AUTOINCREMENT, widget_id TEXT, device_id TEXT,
           event TEXT, payload TEXT, created_at TEXT)
    pairing_codes(code TEXT PRIMARY KEY, device_id TEXT, expires_at INTEGER, consumed INTEGER)
    widget_devices(widget_id TEXT, device_id TEXT, PRIMARY KEY(widget_id, device_id))

Timestamps are ISO-8601 UTC strings via _now(). Enable WAL and foreign_keys off; use a module
lock (threading.RLock) around writes; open a fresh connection per operation (ThreadingHTTPServer).

## 7. Security rules (enforced in store/validate)

- Layout payload <= 64 KB UTF-8. Node count <= 100. Reject with LayoutError.
- image.url and action.url must start with https://. action.deeplink must start with hermes://.
- action.kind is one of event|url|refresh|open_app.
- version == 1 (int, not bool). widgetId 1..128 chars. accentColor hex #RGB/#RRGGBB.
- text.value <= 500; button.label <= 200; badge.text <= 50; chart.series <= 100; calendar.events <= 50.
- Per-container child caps: column/box 100, row 20, list 100.
- Push rate limit: 30 pushes per widget per 3600s -> RateLimitError.
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
    get_widget(widget_id: str) -> dict | None
    list_widgets() -> list[str]
    delete_widget(widget_id: str) -> bool
    post_event(widget_id: str, device_id: str, event: str, payload: dict | None) -> int
    get_events(since: str | None = None, widget_id: str | None = None, limit: int = 200) -> list[dict]
    mint_pairing_code(ttl_minutes: int = 10) -> dict            # {"code":..,"expiresAt":..}
    register_device(code: str, label: str) -> dict | None       # {"deviceId","token","widgets"} | None
    list_devices() -> list[dict]
    revoke_device(device_id: str) -> bool
    device_for_token(token: str) -> dict | None                 # {"deviceId","label"} | None
    check_push_rate(widget_id: str) -> None                     # raises RateLimitError
    validate_layout(layout: dict) -> None                       # raises LayoutError

validate.py:
    class ValidationError(ValueError)
    def validate_layout(layout: dict) -> None

## 9. File ownership map (parallel workstreams)

OWNER FOUNDATION (already written):
    docs/CONNECTION.md, hermes-plugin/hermes-widget/store.py, validate.py,
    layout.schema.json, plugin.yaml

WORKSTREAM A - server:
    hermes-plugin/hermes-widget/server.py
    Exposes: make_server(host, port, *, certfile=None, keyfile=None) -> ThreadingHTTPServer
             run_server(host, port, *, certfile=None, keyfile=None, quiet=False) -> None
    stdlib only (http.server, socketserver, json, ssl, hmac). Uses store.* for all data.

WORKSTREAM B - agent surface:
    hermes-plugin/hermes-widget/schemas.py
    hermes-plugin/hermes-widget/tools.py
    hermes-plugin/hermes-widget/__init__.py
    Tool schemas + handlers; register(ctx) wiring tools, slash command, CLI command, skill.

WORKSTREAM C - proactive + CLI + skill:
    hermes-plugin/hermes-widget/proactive.py
    hermes-plugin/hermes-widget/cli.py
    hermes-plugin/hermes-widget/skills/widget/SKILL.md
    CLI subcommands: serve, setup, code, status, routine, devices, install-skill.

WORKSTREAM D - Android + docs:
    android/app/src/main/java/com/you/hermeswidget/**  (repoint + labels)
    README.md, RELEASE.md, docs/HERMES_AGENT_SETUP.md

WORKSTREAM E - verification:
    hermes-plugin/hermes-widget/tests/test_widget_plugin.py
    An end-to-end stdlib unittest that starts the server and exercises pair -> push -> fetch -> event.

Cross-workstream symbol contract:
    Hermes builds the top-level "widget" parser itself, then calls setup_fn(parser) with THAT
    parser and installs handler_fn as args.func. So cli.add_parser(parser) must only add
    subcommands; calling parser.add_parser("widget", ...) raises AttributeError and aborts
    plugin CLI discovery silently (this caused a real bug during the build).
    cli.py calls server.run_server(host, port, certfile=..., keyfile=...) and proactive.install_routine(...)
    __init__.py calls cli.add_parser / cli.dispatch, tools.*, schemas.*, proactive.*
    tools.py calls store.*
    server.py calls store.*
    proactive.py calls store.DEFAULT_WIDGET_ID and cron.jobs.create_job

## 10. Ground truth (read these, do not guess)

- Hermes install: <Hermes install>
- Plugin API: hermes_cli/plugins.py  (class PluginContext; register_tool, register_command,
  register_cli_command, register_skill, register_hook)
- Plugin loading: hermes_cli/plugins.py _load_directory_module (module name hermes_plugins.<slug>,
  so RELATIVE IMPORTS work: from . import store)
- Cron API: cron/jobs.py create_job(prompt, schedule, name, skills, deliver, ...)
- Hermes home: hermes_constants.get_hermes_home()
- Existing backend contract: backend/app/main.py, backend/app/db.py, docs/SCHEMA.md
- Existing Android endpoints: android/app/src/main/java/com/you/hermeswidget/net/HermesApi.kt
- A working example plugin: <Hermes home>/plugins/hermes-live (tools.py, __init__.py)

## 11. Coding standards

- Python 3.11, stdlib only. No fastapi/uvicorn/requests/pydantic in the plugin.
- from __future__ import annotations; type hints; small functions; no bare except.
- Relative imports inside the plugin package.
- Docs/comments explain WHY, not what. No placeholder or TODO-only bodies.
- Never commit secrets or tokens.

## 12. Verification

Run with the Hermes venv python:
    <Hermes install>\venv\Scripts\python.exe
Set HERMES_WIDGET_DIR to a temp dir so tests never touch the real widget DB.
    python -m unittest discover -s hermes-plugin/hermes-widget/tests -v
Manual end-to-end:
    hermes plugin enable hermes-widget   (after copying the dir into ~/.hermes/plugins/)
    hermes widget setup
    hermes widget serve --host 0.0.0.0 --port 8788
    curl -H "Authorization: Bearer <agent token>" http://127.0.0.1:8788/v1/health
