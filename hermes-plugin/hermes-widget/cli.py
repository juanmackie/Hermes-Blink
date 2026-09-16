"""The "hermes widget ..." command surface for the hermes-widget plugin.

Kept separate from the tool handlers so a broken CLI import can never disable the
agent tools: the plugin entrypoint imports this module lazily and falls back to
an inert subcommand when the import fails.
"""
from __future__ import annotations

import socket
from typing import Any

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import proactive, store
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import proactive  # type: ignore
    import store  # type: ignore

DEFAULT_PORT = 8788
_PLACEHOLDER_TEXT = "Your Hermes agent is connected. Ask it to update this widget."


def _placeholder_layout(widget_id: str) -> dict[str, Any]:
    """A valid first layout so a freshly paired device shows something useful."""
    return {
        "version": 1,
        "widgetId": widget_id,
        "title": "Hermes",
        "root": {
            "type": "column",
            "spacing": 8,
            "children": [
                {"type": "text", "value": "Hermes", "style": "title"},
                {"type": "text", "value": _PLACEHOLDER_TEXT, "style": "body"},
                {
                    "type": "button",
                    "label": "Ask Hermes to refresh",
                    "style": "tonal",
                    "action": {"kind": "event", "event": "widget_refresh_requested"},
                },
            ],
        },
    }


def _reachable_url(bind_host: str, port: int) -> str:
    if bind_host in {"0.0.0.0", "::", "*"}:
        return f"http://<this-machine-ip>:{port}"
    return f"http://{bind_host}:{port}"


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _print_usage() -> None:
    print("usage: hermes widget {serve,setup,code,status,routine,devices,install-skill} ...")
    print("Run 'hermes widget <command> --help' for details.")


# ---------------------------------------------------------------------------
# Argument wiring
# ---------------------------------------------------------------------------

def add_parser(parser: Any) -> None:
    """Attach the nested widget subcommands to the parser Hermes created for us.

    Hermes builds the top-level "widget" parser and then calls this with that
    parser, so only the subcommands belong here. Calling add_parser("widget")
    on it would raise AttributeError and silently abort plugin CLI discovery.
    """
    parser.description = (
        "Manage the Hermes home-screen widget: serve layouts over HTTP, pair "
        "devices, and install the background refresh routine."
    )
    commands = parser.add_subparsers(dest="widget_command")

    serve = commands.add_parser("serve", help="Run the widget HTTP server.")
    serve.add_argument("--host", default="0.0.0.0", help="Interface to bind (default 0.0.0.0).")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind (default 8788).")
    serve.add_argument("--certfile", default=None, help="TLS certificate for HTTPS.")
    serve.add_argument("--keyfile", default=None, help="TLS private key for HTTPS.")
    serve.add_argument("--quiet", action="store_true", help="Do not print the listen URL.")

    setup = commands.add_parser("setup", help="Create the agent token, skill, and a pairing code.")
    setup.add_argument("--host", default="0.0.0.0")
    setup.add_argument("--port", type=int, default=DEFAULT_PORT)
    setup.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    setup.add_argument("--routine", action="store_true", help="Also install the background refresh job.")
    setup.add_argument("--schedule", default=proactive.DEFAULT_SCHEDULE)

    commands.add_parser("code", help="Mint a pairing code for a new device.")

    status = commands.add_parser("status", help="Show widget, device, and server status.")
    status.add_argument("--port", type=int, default=DEFAULT_PORT)

    routine = commands.add_parser("routine", help="Install or remove the background refresh job.")
    routine.add_argument("--schedule", default=proactive.DEFAULT_SCHEDULE)
    routine.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    routine.add_argument("--remove", action="store_true")

    devices = commands.add_parser("devices", help="List paired devices, or revoke one.")
    devices.add_argument("--revoke", default=None, metavar="DEVICE_ID")

    up = commands.add_parser("up", help="Idempotent setup: configure, start services, install routine, prepare pairing, and return structured progress.")
    up.add_argument("--json", action="store_true", help="Output machine-readable JSON only.")
    up.add_argument("--host", default="0.0.0.0")
    up.add_argument("--port", type=int, default=DEFAULT_PORT)
    up.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    up.add_argument("--schedule", default=proactive.DEFAULT_SCHEDULE)

    pair = commands.add_parser("pair", help="Generate a single-use pairing code with QR payload and same-phone link.")
    pair.add_argument("--server-url", default="http://127.0.0.1:8788", help="Server URL for QR payload.")
    pair.add_argument("--label", default="unknown", help="Device label.")
    pair.add_argument("--qr", action="store_true", help="Print QR payload for scanning.")

    commands.add_parser("install-skill", help="Copy the widget skill into the Hermes skill tree.")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(args: Any) -> int:
    """Handler installed as args.func by the Hermes CLI."""
    handlers = {
        "serve": _serve,
        "setup": _setup,
        "up": _up,
        "code": _code,
        "pair": _pair,
        "status": _status,
        "routine": _routine,
        "devices": _devices,
        "install-skill": _install_skill,
    }
    command = getattr(args, "widget_command", None)
    handler = handlers.get(command)
    if handler is None:
        _print_usage()
        return 1
    return handler(args)


def _serve(args: Any) -> int:
    try:
        from . import server
    except ImportError:  # pragma: no cover - direct import from tests/scripts
        import server  # type: ignore
    server.run_server(
        args.host,
        args.port,
        certfile=args.certfile,
        keyfile=args.keyfile,
        quiet=args.quiet,
    )
    return 0


def _setup(args: Any) -> int:
    token = store.get_agent_token()
    skill_path = proactive.install_skill_file()
    widget_id = args.widget_id or store.DEFAULT_WIDGET_ID
    if store.get_widget(widget_id) is None:
        store.put_widget(widget_id, _placeholder_layout(widget_id))
    pairing = store.mint_pairing_code()

    print()
    print("Hermes widget setup complete")
    print("----------------------------")
    print(f"Widget id:  {widget_id}")
    print(f"Data dir:   {store.data_dir()}")
    print(f"Skill:      {skill_path}")
    print()
    print("1. Start the server and keep it running:")
    print(f"     hermes widget serve --host {args.host} --port {args.port}")
    print()
    print("2. On the phone, open Hermes Widget > Connect and enter:")
    print(f"     Hermes agent URL: {_reachable_url(args.host, args.port)}")
    print(f"     Agent token:      {token}")
    print()
    print("3. If the app asks for a pairing code instead, use:")
    print(f"     {pairing['code']}   (expires {pairing['expiresAt']})")
    print()
    if args.routine:
        _install_routine_or_report(args.schedule, widget_id)
    return 0


def _up(args: Any) -> int:
    import json
    # Idempotent setup with structured progress states (Phase B).
    # Steps: prerequisites, token, skill, widget, pairing, routine.
    states = {"state": "starting", "steps": []}
    token_present = store.get_agent_token(create=False) is not None
    states["steps"].append({"step": "prerequisites", "ok": token_present, "detail": "agent_token" if token_present else "missing"})
    if not token_present:
        store.get_agent_token()
        states["state"] = "configured"
    skill_path = proactive.install_skill_file()
    states["steps"].append({"step": "skill", "ok": True, "path": skill_path})
    widget_id = args.widget_id or store.DEFAULT_WIDGET_ID
    if store.get_widget(widget_id) is None:
        store.put_widget(widget_id, _placeholder_layout(widget_id))
    states["steps"].append({"step": "widget", "ok": True, "widget_id": widget_id})
    pairing = store.mint_pairing_code()
    states["steps"].append({"step": "pairing", "ok": True, "code": pairing["code"], "expires_at": pairing["expiresAt"]})
    if args.schedule:
        try:
            job = proactive.install_routine(args.schedule, widget_id)
            states["steps"].append({"step": "routine", "ok": True, "schedule": args.schedule})
        except Exception as exc:
            states["steps"].append({"step": "routine", "ok": False, "error": str(exc)})
    states["state"] = "needs_user_action"
    states["pairing_url_hint"] = _reachable_url(args.host, args.port)
    states["next_actions"] = ["hermes widget serve --host {} --port {}".format(args.host, args.port), "pair device with pairing code"]
    if args.json:
        print(json.dumps(states, indent=2))
    else:
        print("up complete:", states["state"])
        for s in states["steps"]:
            print(" - {}: {}".format(s["step"], "ok" if s.get("ok") else "FAIL"))
    return 0


def _code(_args: Any) -> int:
    pairing = store.mint_pairing_code()
    print(f"Pairing code: {pairing['code']}")
    print(f"Expires:      {pairing['expiresAt']}")
    print("Enter it on the phone's pairing screen, or use the app's Connect flow.")
    return 0


def _pair(args: Any) -> int:
    """Generate a single-use pairing code with QR payload and same-phone link."""
    import json as _json
    pairing = store.mint_pairing_code()
    code = pairing["code"]
    server_url = args.server_url
    qr_payload = _json.dumps({"url": server_url, "code": code, "ttl": 600}, separators=(",", ":"))
    same_phone_link = f"{server_url}/v1/pair?code={code}"
    print(f"Pairing code: {code}")
    print(f"Expires:      {pairing['expiresAt']}")
    print(f"QR payload:   {qr_payload}")
    print(f"Same-phone:   {same_phone_link}")
    print("Scan the QR payload or visit the same-phone link to pair.")
    return 0


def _status(args: Any) -> int:
    widgets = store.list_widgets()
    devices = store.list_devices()
    token_present = store.get_agent_token(create=False) is not None
    port = getattr(args, "port", DEFAULT_PORT)
    listening = _port_listening(port)

    print("Hermes widget status")
    print("--------------------")
    print(f"Data dir:     {store.data_dir()}")
    print(f"Database:     {store.db_path()}")
    print(f"Agent token:  {'configured' if token_present else 'MISSING - run hermes widget setup'}")
    print(f"Widgets:      {', '.join(widgets) if widgets else '(none)'}")
    print(f"Devices:      {len(devices)}")
    if listening:
        print(f"Server:       listening on port {port}")
    else:
        print(f"Server:       not listening on port {port} (start with: hermes widget serve)")
    for device in devices:
        state = "revoked" if device.get("revoked") else "active"
        print(f"  - {device.get('label')} [{state}] last seen {device.get('lastSeenAt') or 'never'}")
    return 0


def _routine(args: Any) -> int:
    if args.remove:
        removed = proactive.remove_routine()
        print("Background refresh removed." if removed else "No background refresh job was installed.")
        return 0
    _install_routine_or_report(args.schedule, args.widget_id)
    return 0


def _install_routine_or_report(schedule: str, widget_id: str) -> None:
    try:
        job = proactive.install_routine(schedule, widget_id)
    except Exception as exc:  # noqa: BLE001 - report, never traceback at the user
        print(f"Could not install the background refresh: {exc}")
        print("The widget still works; run the refresh from a chat or retry once the gateway is running.")
        return
    display = job.get("schedule_display") or schedule
    print(f"Background refresh installed: {job.get('id')} ({display})")
    print("The agent rebuilds the widget on that schedule while your Hermes gateway/cron runs.")


def _devices(args: Any) -> int:
    if args.revoke:
        ok = store.revoke_device(args.revoke)
        print("Device revoked." if ok else "No device with that id.")
        return 0 if ok else 1
    devices = store.list_devices()
    if not devices:
        print("No paired devices.")
        return 0
    for device in devices:
        state = "revoked" if device.get("revoked") else "active"
        print(
            f"{device.get('deviceId')}  {device.get('label')}  [{state}]  "
            f"last seen {device.get('lastSeenAt') or 'never'}"
        )
    return 0


def _install_skill(_args: Any) -> int:
    print(f"Skill installed: {proactive.install_skill_file()}")
    return 0
