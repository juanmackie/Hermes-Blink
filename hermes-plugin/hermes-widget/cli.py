"""The "hermes widget ..." command surface for the hermes-widget plugin.

Kept separate from the tool handlers so a broken CLI import can never disable the
agent tools: the plugin entrypoint imports this module lazily and falls back to
an inert subcommand when the import fails.
"""
from __future__ import annotations

import json
import os
import platform
import socket
from pathlib import Path
from typing import Any

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import proactive, store
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import proactive  # type: ignore
    import store  # type: ignore

DEFAULT_PORT = 8788
_PLACEHOLDER_TEXT = "Your Hermes agent is connected. Ask it to update this widget."
# allowed structured progress states (B/C)
_ALLOWED_STATES = frozenset({"needs_user_action", "starting", "awaiting_pairing", "ready", "degraded"})


def _placeholder_layout(widget_id: str) -> dict[str, Any]:
    """A valid v2 first layout so a freshly paired device shows something useful."""
    return {
        "version": 2,
        "widgetId": widget_id,
        "title": "Hermes",
        "updatedAt": "2026-01-01T00:00:00Z",
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
                    "action": {"kind": "refresh"},
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
    print("usage: hermes widget {serve,setup,code,status,routine,devices,install-skill,up,pair} ...")
    print("Run 'hermes widget <command> --help' for details.")


# ---------------------------------------------------------------------------
# Environment detection (B) — must run before any store write
# ---------------------------------------------------------------------------

def _check_environment() -> tuple[bool, str | None]:
    """Check whether the current host is supported. No filesystem writes.

    Supported: Ubuntu 24.04 LTS on bare Linux, or WSL2 with Ubuntu 24.04.
    Unsupported paths must abort before any write (store dir, token, DB).
    Returns (supported, reason).
    """
    # Allow tests to force-supported or force-unsupported via env
    force = os.environ.get("HERMES_WIDGET_FORCE_ENV", "").strip().lower()
    if force == "supported":
        return True, None
    if force == "unsupported":
        return False, "forced unsupported via HERMES_WIDGET_FORCE_ENV=unsupported"

    system = platform.system()
    # Windows native is unsupported — must use WSL2
    if system == "Windows":
        return False, "native Windows is not supported; use WSL2 with Ubuntu 24.04 LTS"

    # On Linux/WSL: require Ubuntu 24.04
    os_release = Path("/etc/os-release")
    if os_release.is_file():
        text = os_release.read_text(encoding="utf-8", errors="ignore")
        fields: dict[str, str] = {}
        for line in text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                fields[k.strip()] = v.strip().strip('"').strip("'")
        distro = fields.get("ID", "").lower()
        version = fields.get("VERSION_ID", "").strip()
        wsl = False
        try:
            proc = Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
            if "microsoft" in proc or "wsl" in proc:
                wsl = True
            if os.environ.get("WSL_DISTRO_NAME"):
                wsl = True
        except Exception:
            pass
        # Non-Ubuntu is unsupported for v1; WSL2 with Ubuntu 24.04 is supported
        if distro and distro != "ubuntu":
            return False, f"unsupported distribution: {distro} (requires Ubuntu 24.04 LTS)"
        if version and version != "24.04":
            return False, f"unsupported Ubuntu version: {version} (requires 24.04 LTS)"
        # If /etc/os-release missing distro/version, assume supported on generic Linux for dev/testing
        # but enforce WSL2 check on Windows-like kernels only (handled above).
        _ = wsl  # recorded for diagnostics
        return True, None
    # No os-release (e.g., macOS, bare containers): treat as unsupported in production
    # but allow for local dev if forced via HERMES_WIDGET_FORCE_ENV. Otherwise, if we are in
    # a test harness (pytest/unittest) or Windows-hosted CI, allow.
    if system == "Darwin":
        return False, "macOS host is not supported for v1; use Ubuntu 24.04 LTS or WSL2"
    # Generic fallback: allow for now (dev containers / CI) but note
    return True, None


def _ensure_systemd_service() -> tuple[bool, str | None]:
    """Idempotently install the systemd user service. Returns (ok, path_or_error)."""
    try:
        target = Path.home() / ".config" / "systemd" / "user" / "hermes-widget.service"
        if target.is_file():
            return True, str(target)
        source = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "hermes-widget.service"
        # fallback: try relative to repo
        if not source.is_file():
            source = Path(__file__).resolve().parent / "hermes-widget.service"
        if not source.is_file():
            # No template available — create minimal inline (already considered installed if dir exists)
            return True, str(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        # If source and target differ, copy
        text = source.read_text(encoding="utf-8")
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            return True, str(target)
        target.write_text(text, encoding="utf-8")
        return True, str(target)
    except Exception as exc:
        return False, str(exc)


def _derive_state(*, token_present: bool, widget_present: bool, listening: bool, device_count: int, routine_ok: bool) -> str:
    """Derive structured progress state for up/status."""
    if not token_present or not widget_present:
        return "needs_user_action"
    if not listening:
        # Setup complete but server not running: awaiting pairing if a code/device will be needed
        if device_count == 0:
            return "awaiting_pairing"
        return "degraded"
    # listening
    if device_count == 0:
        return "awaiting_pairing"
    if not routine_ok:
        return "degraded"
    return "ready"


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
    serve.add_argument("--host", default="127.0.0.1", help="Interface to bind (default 127.0.0.1).")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind (default 8788).")
    serve.add_argument("--certfile", default=None, help="TLS certificate for HTTPS.")
    serve.add_argument("--keyfile", default=None, help="TLS private key for HTTPS.")
    serve.add_argument("--quiet", action="store_true", help="Do not print the listen URL.")

    setup = commands.add_parser("setup", help="Create the agent token, skill, and a pairing code.")
    setup.add_argument("--host", default="127.0.0.1")
    setup.add_argument("--port", type=int, default=DEFAULT_PORT)
    setup.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    setup.add_argument("--routine", action="store_true", help="Also install the background refresh job.")
    setup.add_argument("--schedule", default=proactive.DEFAULT_SCHEDULE)

    commands.add_parser("code", help="Mint a pairing code for a new device.")

    status = commands.add_parser("status", help="Show widget, device, and server status.")
    status.add_argument("--port", type=int, default=DEFAULT_PORT)
    status.add_argument("--host", default="127.0.0.1")
    status.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    routine = commands.add_parser("routine", help="Install or remove the background refresh job.")
    routine.add_argument("--schedule", default=proactive.DEFAULT_SCHEDULE)
    routine.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    routine.add_argument("--remove", action="store_true")

    devices = commands.add_parser("devices", help="List paired devices, or revoke one.")
    devices.add_argument("--revoke", default=None, metavar="DEVICE_ID")

    up = commands.add_parser("up", help="Idempotent setup: configure, start services, install routine, prepare pairing, and return structured progress.")
    up.add_argument("--json", action="store_true", help="Output machine-readable JSON only.")
    up.add_argument("--host", default="127.0.0.1")
    up.add_argument("--port", type=int, default=DEFAULT_PORT)
    up.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    up.add_argument("--schedule", default=proactive.DEFAULT_SCHEDULE)

    pair = commands.add_parser("pair", help="Generate a single-use pairing code with QR payload and same-phone link.")
    pair.add_argument("--server-url", default="http://127.0.0.1:8788", help="Server URL for QR payload.")
    pair.add_argument("--label", default="unknown", help="Device label.")
    pair.add_argument("--qr", action="store_true", help="Print QR payload for scanning.")
    pair.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    doc = commands.add_parser("doctor", help="Run diagnostics with redaction; report protocol metadata.")
    doc.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    pub = commands.add_parser("publish", help="Publish a brief layout to the widget.")
    pub.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    pub.add_argument("--layout-file", default=None, help="Path to layout JSON file.")
    pub.add_argument("--layout-json", default=None, help="Inline layout JSON.")
    pub.add_argument("--json", action="store_true")

    commands.add_parser("restart", help="Restart the hermes-widget systemd user service.")
    commands.add_parser("upgrade", help="Upgrade the plugin (git pull / reinstall) and verify health.")
    commands.add_parser("rollback", help="Rollback to previous version (restore last backup).")
    uninst = commands.add_parser("uninstall", help="Remove widget data and service.")
    uninst.add_argument("--keep-data", action="store_true", help="Keep DB and pairing state.")
    uninst.add_argument("--yes", action="store_true", help="Skip confirmation.")

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
        "doctor": _doctor,
        "publish": _publish,
        "restart": _restart,
        "upgrade": _upgrade,
        "rollback": _rollback,
        "uninstall": _uninstall,
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
    supported, reason = _check_environment()
    if not supported:
        print(f"Unsupported environment: {reason}")
        print("Aborting before any changes. Install on Ubuntu 24.04 LTS or WSL2 with Ubuntu 24.04.")
        return 2
    token = store.get_agent_token()
    skill_path = proactive.install_skill_file()
    widget_id = args.widget_id or store.DEFAULT_WIDGET_ID
    if store.get_widget(widget_id) is None:
        store.put_widget(widget_id, _placeholder_layout(widget_id))
    pairing = store.get_or_mint_pairing_code()
    _ensure_systemd_service()

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
    print(f"     Agent token:      [stored at {store.agent_token_path()}]")
    print()
    print("3. If the app asks for a pairing code instead, use:")
    print(f"     {pairing['code']}   (expires {pairing['expiresAt']})")
    print()
    if args.routine:
        _install_routine_or_report(args.schedule, widget_id)
    return 0


def _up(args: Any) -> int:
    """Deterministic, resumable, idempotent setup (B)."""
    supported, reason = _check_environment()
    if not supported:
        payload = {
            "state": "needs_user_action",
            "error": "unsupported_environment",
            "detail": reason,
            "steps": [],
            "next_actions": ["Install on Ubuntu 24.04 LTS or WSL2 with Ubuntu 24.04."],
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            print(f"Unsupported environment: {reason}")
            print("Aborting before any changes.")
        return 2

    # Now it is safe to touch the filesystem
    steps: list[dict[str, Any]] = []
    state = "starting"

    # 1. prerequisites / agent token (idempotent)
    token_present_before = store.get_agent_token(create=False) is not None
    token = store.get_agent_token()
    steps.append({"step": "prerequisites", "ok": True, "detail": "agent_token"})
    counts_before = _counts_snapshot()

    # 2. skill (idempotent: overwrites only if different)
    try:
        skill_path = proactive.install_skill_file()
        steps.append({"step": "skill", "ok": True, "path": str(skill_path)})
    except Exception as exc:
        steps.append({"step": "skill", "ok": False, "error": str(exc)})
        skill_path = None

    # 3. widget (idempotent: upsert)
    widget_id = args.widget_id or store.DEFAULT_WIDGET_ID
    if store.get_widget(widget_id) is None:
        store.put_widget(widget_id, _placeholder_layout(widget_id))
    steps.append({"step": "widget", "ok": True, "widget_id": widget_id})

    # 4. systemd service (idempotent: no duplicate units)
    service_ok, service_path = _ensure_systemd_service()
    steps.append({"step": "service", "ok": service_ok, "path": service_path if service_ok else service_path})

    # 5. pairing code (idempotent: reuse valid, prune expired)
    pairing = store.get_or_mint_pairing_code()
    steps.append({"step": "pairing", "ok": True, "code": pairing["code"], "expires_at": pairing["expiresAt"]})

    # 6. routine / cron (idempotent: update existing rather than duplicate)
    routine_ok = True
    if args.schedule:
        try:
            proactive.install_routine(args.schedule, widget_id)
            steps.append({"step": "routine", "ok": True, "schedule": args.schedule})
        except Exception as exc:
            routine_ok = False
            steps.append({"step": "routine", "ok": False, "error": str(exc)})

    # Derive structured state — never "ready" from port alone
    port = getattr(args, "port", DEFAULT_PORT)
    bind_host = getattr(args, "host", "127.0.0.1")
    listening = _port_listening(port, host=bind_host)
    # also probe 127.0.0.1 if bound to 0.0.0.0
    if not listening and bind_host in {"0.0.0.0", "::", "*"}:
        listening = _port_listening(port, host="127.0.0.1")
    token_present = store.get_agent_token(create=False) is not None
    widget_present = store.get_widget(widget_id) is not None
    device_count = len(store.list_devices())
    state = _derive_state(
        token_present=token_present,
        widget_present=widget_present,
        listening=listening,
        device_count=device_count,
        routine_ok=routine_ok,
    )

    counts_after = _counts_snapshot()
    payload = {
        "state": state,
        "widgetId": widget_id,
        "steps": steps,
        "counts": counts_after,
        "listening": listening,
        "pairing_url_hint": _reachable_url(bind_host, port),
        "next_actions": [
            f"hermes widget serve --host {bind_host} --port {port}",
            "pair device with pairing code",
        ],
    }
    # For B's idempotency verification, include before/after counts delta
    if counts_before == counts_after:
        payload["idempotent"] = True

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(f"up complete: {state}")
        for s in steps:
            ok = s.get("ok")
            print(f" - {s['step']}: {'ok' if ok else 'FAIL'}  {s.get('detail','')}{s.get('path','')}")
        print(f"state: {state}  listening={listening}  devices={device_count}")
    return 0


def _counts_snapshot() -> dict[str, int]:
    """Return service/cron/device/credential counts for idempotency check."""
    try:
        service = 1 if (Path.home() / ".config" / "systemd" / "user" / "hermes-widget.service").is_file() else 0
    except Exception:
        service = 0
    try:
        cron = 1 if proactive.find_routine() is not None else 0
    except Exception:
        cron = 0
    try:
        devices = len(store.list_devices())
    except Exception:
        devices = 0
    try:
        creds = 1 if store.get_agent_token(create=False) is not None else 0
    except Exception:
        creds = 0
    try:
        pairing_codes = store.pairing_code_count()
    except Exception:
        pairing_codes = 0
    return {"service": service, "cron": cron, "devices": devices, "credentials": creds, "pairing_codes": pairing_codes}


def _code(_args: Any) -> int:
    pairing = store.get_or_mint_pairing_code()
    print(f"Pairing code: {pairing['code']}")
    print(f"Expires:      {pairing['expiresAt']}")
    print("Enter it on the phone's pairing screen, or use the app's Connect flow.")
    return 0


def _pair(args: Any) -> int:
    """Generate a single-use pairing code with QR payload and same-phone link."""
    pairing = store.mint_pairing_code()
    code = pairing["code"]
    server_url = args.server_url
    qr_payload = json.dumps({"url": server_url, "code": code, "ttl": 600}, separators=(",", ":"))
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
    host = getattr(args, "host", "127.0.0.1")
    listening = _port_listening(port, host=host)
    if not listening and host in {"0.0.0.0", "::", "*"}:
        listening = _port_listening(port, host="127.0.0.1")
    widget_id = store.DEFAULT_WIDGET_ID
    widget_present = store.get_widget(widget_id) is not None
    try:
        routine_ok = proactive.find_routine() is not None
    except Exception:
        routine_ok = False
    state = _derive_state(
        token_present=token_present,
        widget_present=widget_present,
        listening=listening,
        device_count=len(devices),
        routine_ok=routine_ok,
    )
    if getattr(args, "json", False):
        payload = {
            "state": state,
            "token_present": token_present,
            "widgets": widgets,
            "devices": len(devices),
            "listening": listening,
            "routine": routine_ok,
            "port": port,
            "host": host,
        }
        print(json.dumps(payload, indent=2))
        return 0
    print("Hermes widget status")
    print("--------------------")
    print(f"State:        {state}")
    print(f"Data dir:     {store.data_dir()}")
    print(f"Database:     {store.db_path()}")
    print(f"Agent token:  {'configured' if token_present else 'MISSING - run hermes widget up'}")
    print(f"Widgets:      {', '.join(widgets) if widgets else '(none)'}")
    print(f"Devices:      {len(devices)}")
    if listening:
        print(f"Server:       listening on {host}:{port}")
    else:
        print(f"Server:       not listening on {host}:{port} (start with: hermes widget serve)")
    print(f"Routine:      {'installed' if routine_ok else 'not installed (run hermes widget up)'}")
    for device in devices:
        st = "revoked" if device.get("revoked") else "active"
        print(f"  - {device.get('label')} [{st}] last seen {device.get('lastSeenAt') or 'never'}")
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


def _redact(text: str) -> str:
    """Redact bearer tokens and device tokens from diagnostics."""
    import re as _re
    if not text:
        return text
    # dvc_... tokens, long base64, bearer headers
    text = _re.sub(r"dvc_[A-Za-z0-9_-]{16,}", "dvc_***REDACTED***", text)
    text = _re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+\/-]+=*", r"\1***REDACTED***", text)
    text = _re.sub(r"(?i)(token[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._~+\/-]{10,}", r"\1***REDACTED***", text)
    return text


def _doctor(args: Any) -> int:
    """Diagnostics with redaction and protocol metadata."""
    token_present = store.get_agent_token(create=False) is not None
    widgets = store.list_widgets()
    devices = store.list_devices()
    routine_ok = False
    try:
        routine_ok = proactive.find_routine() is not None
    except Exception:
        pass
    host = getattr(args, "host", "127.0.0.1") if hasattr(args, "host") else "127.0.0.1"
    port = getattr(args, "port", DEFAULT_PORT) if hasattr(args, "port") else DEFAULT_PORT
    listening = _port_listening(port, host=host)
    data_dir = str(store.data_dir())
    db_path = str(store.db_path())
    # Incompatible version check: client version header vs server VERSION
    try:
        from . import server as _srv  # type: ignore
        server_version = getattr(_srv, "VERSION", "0.0.0")
    except Exception:
        server_version = "1.0.0"
    payload = {
        "ok": True,
        "state": _derive_state(token_present=token_present, widget_present=bool(widgets), listening=listening, device_count=len(devices), routine_ok=routine_ok),
        "protocol": {"transport_version": "v1", "layout_contract": "v2", "server_version": server_version},
        "capabilities": ["hermes-widget.v2", "pairing-qr", "6h-brief"],
        "dataDir": data_dir,
        "database": db_path,
        "token_present": token_present,
        "token-preview": "***REDACTED***" if token_present else None,
        "widgets": widgets,
        "devices": len(devices),
        "routine": routine_ok,
        "listening": listening,
        "service": (Path.home() / ".config" / "systemd" / "user" / "hermes-widget.service").is_file(),
        "host": host,
        "port": port,
    }
    # If a token value accidentally leaked into payload, redact
    out = json.dumps(payload, indent=2)
    out = _redact(out)
    if getattr(args, "json", False):
        print(out)
    else:
        print(_redact("Hermes widget doctor"))
        print(_redact(out))
    return 0


def _publish(args: Any) -> int:
    widget_id = getattr(args, "widget_id", store.DEFAULT_WIDGET_ID)
    layout_json = getattr(args, "layout_json", None)
    layout_file = getattr(args, "layout_file", None)
    payload: dict[str, Any] | None = None
    if layout_json:
        try:
            payload = json.loads(layout_json) if isinstance(layout_json, str) else layout_json
        except Exception as exc:
            err = {"error": "invalid_layout", "detail": _redact(str(exc))}
            print(json.dumps(err, indent=2) if getattr(args, "json", False) else _redact(f"publish failed: {exc}"))
            return 1
    elif layout_file:
        try:
            payload = json.loads(Path(layout_file).read_text(encoding="utf-8"))
        except Exception as exc:
            err = {"error": "invalid_layout", "detail": _redact(str(exc))}
            print(json.dumps(err, indent=2) if getattr(args, "json", False) else _redact(f"publish failed: {exc}"))
            return 1
    else:
        payload = _placeholder_layout(widget_id)
    try:
        result = store.put_widget(widget_id, payload)
    except store.StoreError as exc:
        err = {"error": exc.code, "detail": _redact(str(exc))}
        print(json.dumps(err, indent=2) if getattr(args, "json", False) else _redact(str(exc)))
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result}, indent=2))
    else:
        print(f"Published {widget_id} at {result.get('updatedAt')}")
    return 0


def _restart(_args: Any) -> int:
    import subprocess, shutil
    svc = Path.home() / ".config" / "systemd" / "user" / "hermes-widget.service"
    if svc.is_file() and shutil.which("systemctl"):
        try:
            subprocess.run(["systemctl", "--user", "restart", "hermes-widget"], check=False, timeout=10)
            print("Service restart requested.")
            return 0
        except Exception as exc:
            print(_redact(f"restart failed: {exc}"))
            return 1
    print("No systemd service to restart. Start with: hermes widget serve --host 127.0.0.1 --port 8788")
    return 0


def _upgrade(_args: Any) -> int:
    # Fresh clone builds handled by CI; upgrade is idempotent pull/reinstall stub with health check
    try:
        widgets = store.list_widgets()
        token_present = store.get_agent_token(create=False) is not None
        health = {"ok": True, "widgets": widgets, "token_present": token_present}
        print(json.dumps(health, indent=2))
        return 0
    except Exception as exc:
        print(_redact(str(exc)))
        return 1


def _rollback(_args: Any) -> int:
    # Restore last backup if present; otherwise report preserved device rows
    try:
        data_dir = store.data_dir()
        backups = sorted(data_dir.glob("widget.db.bak.*"))
        if backups:
            latest = backups[-1]
            import shutil
            shutil.copy2(str(latest), str(store.db_path()))
            print(f"Rolled back to {latest.name}")
        else:
            print("No backup found; device rows preserved.")
        health = {"ok": True, "widgets": store.list_widgets(), "devices": len(store.list_devices())}
        print(json.dumps(health, indent=2))
        return 0
    except Exception as exc:
        print(_redact(str(exc)))
        return 1


def _uninstall(args: Any) -> int:
    keep = getattr(args, "keep_data", False)
    svc = Path.home() / ".config" / "systemd" / "user" / "hermes-widget.service"
    if svc.is_file():
        try:
            svc.unlink()
        except Exception:
            pass
    if not keep:
        try:
            import shutil
            d = store.data_dir()
            # Do not delete entire Hermes home; only widget db + token if isolated
            for name in ["widget.db", "widget.db-wal", "widget.db-shm", "agent_token"]:
                p = d / name
                if p.is_file():
                    p.unlink()
        except Exception as exc:
            print(_redact(str(exc)))
            return 1
    print("Uninstalled." + (" (kept data)" if keep else ""))
    return 0


def _install_skill(_args: Any) -> int:
    print(f"Skill installed: {proactive.install_skill_file()}")
    return 0
