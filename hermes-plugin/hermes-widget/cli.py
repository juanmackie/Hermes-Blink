"""The "hermes widget ..." command surface for the hermes-widget plugin.

Kept separate from the tool handlers so a broken CLI import can never disable the
agent tools: the plugin entrypoint imports this module lazily and falls back to
an inert subcommand when the import fails.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import proactive, store
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import proactive  # type: ignore
    import store  # type: ignore

DEFAULT_PORT = 8788
_WILDCARD_HOSTS = frozenset((
    ".".join(("0", "0", "0", "0")),
    chr(58) * 2,
    chr(42),
))
# allowed structured progress states (B/C)
_ALLOWED_STATES = frozenset({"needs_user_action", "starting", "awaiting_pairing", "ready", "degraded"})


def _resolve_input_file(value: Any, *, label: str) -> Path:
    """Resolve a user-supplied JSON path from cwd, the checkout, or Hermes home.

    Relative publication JSON paths can come from the current directory, the
    repository, or Hermes home. The resolver keeps those paths usable in an
    installed plugin without hiding a missing file behind a raw OSError.
    """
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise FileNotFoundError(f"{label} path is required")
    raw = Path(value).expanduser()
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend((Path.cwd() / raw, raw, Path(__file__).parent / raw))
        home = os.environ.get("HERMES_HOME", "").strip()
        if home:
            candidates.extend((Path(home) / raw, Path(home) / "fixtures" / raw.name))
        # In a source checkout this is the repository root; in an installed copy
        # it is the Hermes home, while the plugin-local copy keeps fixtures available.
        candidates.append(Path(__file__).resolve().parents[2] / raw)
    seen: set[Path] = set()
    searched: list[str] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        searched.append(str(resolved))
        try:
            if resolved.is_file():
                return resolved
        except OSError:
            continue
    raise FileNotFoundError(
        f"{label} not found: {raw} (searched: {', '.join(searched)})"
    )


def _probe_host(bind_host: str) -> str:
    """Address the health probe connects to; wildcard binds are probed on loopback."""
    return "127.0.0.1" if bind_host in _WILDCARD_HOSTS else bind_host


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _binding_listening(bind_host: str, port: int) -> bool:
    return _port_listening(port, host=_probe_host(bind_host))


_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"}) | _WILDCARD_HOSTS


def _valid_server_url(value: Any) -> str | None:
    """Return a usable private HTTPS base URL, or None.

    The phone reaches the host through a private HTTPS proxy, so cleartext,
    relative, and loopback URLs cannot work from the device.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    if parsed.hostname.lower() in _LOOPBACK_HOSTNAMES:
        return None
    return value.strip().rstrip("/")


def _running_binding() -> tuple[str, int] | None:
    """Binding recorded for the running server, when the startup hook wrote it."""
    path = proactive.server_config_path().parent / "server-process.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    host, port = record.get("host"), record.get("port")
    if isinstance(host, str) and host and isinstance(port, int) and not isinstance(port, bool):
        return host, port
    return None


def _restart_required(
    configured: tuple[str, int],
    prior: tuple[str, int] | None,
    running: tuple[str, int] | None,
) -> bool:
    """Whether a running server still uses a binding other than the configured one.

    A recorded process binding is authoritative. Without one, only report a
    pending restart when this call changed the saved binding while the old
    binding still answers.
    """
    if running is not None:
        return running != configured
    if prior is None or prior == configured:
        return False
    return _binding_listening(*prior)


def _print_usage(verbs: Iterable[str] = ()) -> None:
    """The only usage text an operator sees when a verb is mistyped.

    [verbs] comes from the dispatcher's own handler map, so this can never advertise a verb
    that does not exist or omit one that does.
    """
    if verbs:
        print(f"usage: hermes widget {{{','.join(verbs)}}} ...")
    else:
        print("usage: hermes widget <command> ...")
    print("Run 'hermes widget <command> --help' for details.")


# ---------------------------------------------------------------------------
# Environment detection (B) — must run before any store write
# ---------------------------------------------------------------------------

def _check_environment() -> tuple[bool, str | None]:
    """Check Python capability without assuming an OS or init system."""
    force = os.environ.get("HERMES_WIDGET_FORCE_ENV", "").strip().lower()
    if force == "supported":
        return True, None
    if force == "unsupported":
        return False, "forced unsupported via HERMES_WIDGET_FORCE_ENV=unsupported"
    if sys.version_info < (3, 11):
        return False, f"Python 3.11+ is required (found {platform.python_version()})"
    return True, None


def _ensure_systemd_service(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> tuple[bool, str | None]:
    """Use a user service only when systemd is actually available."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return True, None
    try:
        probe = subprocess.run(
            [systemctl, "--user", "show-environment"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if probe.returncode != 0:
            return True, None
        binary = os.environ.get("HERMES_BIN") or shutil.which("hermes")
        if not binary:
            return False, "hermes executable is not available for the generated service"
        home = Path(os.environ.get("HERMES_HOME") or (Path(store.data_dir()).parent))
        target = Path.home() / ".config" / "systemd" / "user" / "hermes-widget.service"
        def quote(value: str) -> str:
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
        text = (
            "[Unit]\nDescription=Hermes personal widget server\nAfter=network-online.target\n\n"
            "[Service]\nType=simple\n"
            f"ExecStart={quote(binary)} widget serve --host {host} --port {port}\n"
            f"Environment=HERMES_HOME={quote(str(home))}\n"
            f"WorkingDirectory={quote(str(home))}\n"
            "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            target.write_text(text, encoding="utf-8")
        subprocess.run([systemctl, "--user", "daemon-reload"], check=False, timeout=10)
        subprocess.run([systemctl, "--user", "enable", "hermes-widget.service"], check=False, timeout=10)
        if not _port_listening(port, host=host):
            subprocess.run([systemctl, "--user", "start", "hermes-widget.service"], check=False, timeout=10)
        return True, str(target)
    except (OSError, subprocess.SubprocessError) as exc:
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
    "Manage the Hermes home-screen widget: serve publications over HTTP, pair "
        "devices, and install the background refresh routine."
    )
    commands = parser.add_subparsers(dest="widget_command")

    serve = commands.add_parser("serve", help="Run the widget HTTP server.")
    serve.add_argument("--host", default=None, help="Interface to bind (default: saved config or 127.0.0.1).")
    serve.add_argument("--port", type=int, default=None, help="Port to bind (default: saved config or 8788).")
    serve.add_argument("--certfile", default=None, help="TLS certificate for HTTPS.")
    serve.add_argument("--keyfile", default=None, help="TLS private key for HTTPS.")
    serve.add_argument("--quiet", action="store_true", help="Do not print the listen URL.")

    commands.add_parser("code", help="Mint a pairing code for a new device.")

    status = commands.add_parser("status", help="Show widget, device, and server status.")
    status.add_argument("--port", type=int, default=None)
    status.add_argument("--host", default=None)
    status.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    wake = commands.add_parser("wake-test", help="Send one content-free UnifiedPush wake and show receipts.")
    wake.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    wake.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    routine = commands.add_parser("routine", help="Install or remove the background refresh job.")
    routine.add_argument("--schedule", default=proactive.DEFAULT_SCHEDULE)
    routine.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    routine.add_argument("--remove", action="store_true")

    devices = commands.add_parser("devices", help="List paired devices, or revoke one.")
    devices.add_argument("--revoke", default=None, metavar="DEVICE_ID")

    up = commands.add_parser("up", help="Idempotent setup: configure, start services, install routine, prepare pairing, and return structured progress.")
    up.add_argument("--json", action="store_true", help="Output machine-readable JSON only.")
    up.add_argument("--host", default=None, help="Interface to bind (default: saved config or 127.0.0.1).")
    up.add_argument("--port", type=int, default=None, help="Port to bind (default: saved config or 8788).")
    up.add_argument("--server-url", default=None, help="Private HTTPS URL the phone will use; reported as a hint only.")
    up.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    up.add_argument("--schedule", default=proactive.DEFAULT_SCHEDULE)

    pair = commands.add_parser("pair", help="Mint a single-use pairing code for manual entry in the Android app.")
    pair.add_argument("--server-url", default=None, help="Private HTTPS URL the phone will use (required).")
    pair.add_argument("--label", default="unknown", help="Device label.")
    pair.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    doc = commands.add_parser("doctor", help="Run diagnostics with redaction; report protocol metadata.")
    doc.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    pub = commands.add_parser("publish", help="Publish a text, SVG, or raster update to the widget.")
    pub.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    pub.add_argument("--publication-file", default=None, help="JSON publication to publish (title/summary plus one source).")
    pub.add_argument("--title", default=None, help="Publication title.")
    pub.add_argument("--summary", default=None, help="Publication accessible summary.")
    pub.add_argument("--text", default=None, help="Publication plain text.")
    pub.add_argument("--svg", default=None, help="Inline static publication SVG.")
    pub.add_argument("--file-path", default=None, help="Local PNG/JPEG/WebP publication path.")
    pub.add_argument("--priority", choices=("normal", "high"), default="normal", help="Publication wake priority.")
    pub.add_argument("--max-age-seconds", type=int, default=None, help="Publication freshness window.")
    pub.add_argument("--item-id", default=None, help="Stable item identity for publication actions.")
    pub.add_argument("--actions", default=None, help="JSON array of allowlisted publication actions.")
    pub.add_argument("--json", action="store_true")

    commands.add_parser("restart", help="Restart the hermes-widget systemd user service.")
    commands.add_parser("upgrade", help="Update this plugin through Hermes and verify health.")
    commands.add_parser("rollback", help="Rollback to previous version (restore last backup).")
    uninst = commands.add_parser(
        "uninstall", help="Remove the widget service, hook, routine, skill, and optionally data."
    )
    uninst.add_argument("--keep-data", action="store_true", help="Keep DB and pairing state.")
    uninst.add_argument("--yes", action="store_true", help="Skip confirmation.")

    commands.add_parser("install-skill", help="Copy the widget skill into the Hermes skill tree.")

    prev = commands.add_parser(
        "preview",
        help="Render a proposed or current publication preview (no device needed).",
    )
    prev.add_argument("--out", default=None, help="Output PNG directory; with --json, PNGs are still written when set.")
    prev.add_argument("--json", action="store_true", help="Print JSON; publication PNGs are written when --out is also set.")
    prev.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID, help="Widget id for publication preview.")
    prev.add_argument("--sizes", default=None, help="Comma-separated publication sizes, e.g. 2x2,4x2,4x4.")
    prev.add_argument("--publication-file", default=None, help="JSON file containing a publication to preview before publishing.")

    adb_dev = commands.add_parser("adb-devices", help="List ADB devices on USB/network.")
    adb_dev.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    adb_rev = commands.add_parser(
        "adb-reverse",
        help="Expose host loopback to the phone over USB (debug builds use http://127.0.0.1:PORT).",
    )
    adb_rev.add_argument("--port", type=int, default=None, help="Host port (default: saved config or 8788).")
    adb_rev.add_argument("--device-port", type=int, default=None, help="Device loopback port (default: same as --port).")
    adb_rev.add_argument("--device", default=None, help="ADB serial (default: the single online device).")
    adb_rev.add_argument("--remove", action="store_true", help="Remove the reverse instead of creating it.")
    adb_rev.add_argument("--list", action="store_true", help="List active reverses and exit.")
    adb_rev.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    adb_ins = commands.add_parser("adb-install", help="Install a debug APK over USB.")
    adb_ins.add_argument("--apk", default=None, help="APK path (default: newest app-debug.apk under android/).")
    adb_ins.add_argument("--device", default=None, help="ADB serial.")
    adb_ins.add_argument("--launch", action="store_true", help="Launch MainActivity after install.")
    adb_ins.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    adb_log = commands.add_parser("adb-logcat", help="Capture or clear logcat over USB.")
    adb_log.add_argument("--device", default=None, help="ADB serial.")
    adb_log.add_argument("--clear", action="store_true", help="Clear the logcat buffer and exit.")
    adb_log.add_argument("--dump", action="store_true", help="Dump current buffer and exit (default streams briefly).")
    adb_log.add_argument("--tag", default="HermesWidget", help="Log tag filter for streaming (default: HermesWidget).")

    usb = commands.add_parser(
        "usb-up",
        help="One-shot USB dev setup: reverse host port to the phone and report status.",
    )
    usb.add_argument("--port", type=int, default=None, help="Host port (default: saved config or 8788).")
    usb.add_argument("--device", default=None, help="ADB serial.")
    usb.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    req = commands.add_parser(
        "requests",
        help="List Request-update taps with trigger state (did Hermes start a refresh?).",
    )
    req.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    req.add_argument("--limit", type=int, default=10)
    req.add_argument("--json", action="store_true", help="Machine-readable JSON.")

    trig = commands.add_parser(
        "trigger-refresh",
        help="Manually spawn the refresh cron (used when a tap recorded but never triggered).",
    )
    trig.add_argument("--json", action="store_true", help="Machine-readable JSON.")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(args: Any) -> int:
    """Handler installed as args.func by the Hermes CLI."""
    handlers = {
        "serve": _serve,
        "up": _up,
        "code": _code,
        "pair": _pair,
        "status": _status,
        "wake-test": _wake_test,
        "routine": _routine,
        "devices": _devices,
        "doctor": _doctor,
        "publish": _publish,
        "restart": _restart,
        "upgrade": _upgrade,
        "rollback": _rollback,
        "uninstall": _uninstall,
        "install-skill": _install_skill,
        "preview": _preview,
        "adb-devices": _adb_devices,
        "adb-reverse": _adb_reverse,
        "adb-install": _adb_install,
        "adb-logcat": _adb_logcat,
        "usb-up": _usb_up,
        "requests": _requests,
        "trigger-refresh": _trigger_refresh,
    }
    command = getattr(args, "widget_command", None)
    if not isinstance(command, str):
        _print_usage(handlers)
        return 1
    handler = handlers.get(command)
    if handler is None:
        _print_usage(handlers)
        return 1
    return handler(args)


def _preview(args: Any) -> int:
    """Render exact publication PNG previews."""
    try:
        from . import preview
    except ImportError:  # pragma: no cover - direct import from tests/scripts
        import preview  # type: ignore

    source_path = getattr(args, "publication_file", None)
    widget_id = getattr(args, "widget_id", None) or store.DEFAULT_WIDGET_ID
    proposed = None
    if source_path:
        try:
            source = _resolve_input_file(source_path, label="publication file")
        except FileNotFoundError as exc:
            print(str(exc))
            return 1
        try:
            proposed = json.loads(source.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"{source} is not valid JSON: {exc}")
            return 1
    if proposed is not None and not isinstance(proposed, dict):
        print("publication file must contain a JSON object")
        return 1
    try:
        publication: dict[str, Any] | None = None
        if proposed is None:
            publication = store.get_publication(widget_id)
            if publication is None:
                print(f"no publication for widget {widget_id!r}")
                return 1
        else:
            publication = preview.build_preview_publication(proposed, widget_id)
        rendered = preview.render_publication_previews(
            publication,
            sizes=getattr(args, "sizes", None),
            inventory=store.list_widget_instances(widget_id),
            asset_loader=lambda asset_id: store.read_asset(asset_id)[1],
        )
    except (ValueError, store.StoreError) as exc:
        print(f"preview failed: {exc}")
        return 1
    last_render = store._last_render_metrics()
    for item in rendered:
        if item.get("renderer") == "svg-renderer-unavailable" and last_render:
            item["note"] = (
                "No local SVG renderer (CairoSVG/libcairo); "
                f"last device render {last_render['width']}×{last_render['height']}px"
            )
    out_dir = Path(args.out) if args.out else (None if getattr(args, "json", False) else Path.cwd() / "widget-previews")
    files: list[str] = []
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for item in rendered:
            target = out_dir / f"{widget_id}-{item['size']}.png"
            target.write_bytes(base64.b64decode(item["data"]))
            files.append(str(target))
    if getattr(args, "json", False):
        print(json.dumps({
            "ok": True, "widgetId": widget_id, "previews": rendered, "files": files,
            "filesWritten": bool(files),
        }, indent=2))
        return 0
    for item, filename in zip(rendered, files):
        print(f"preview written to {filename}")
        if item.get("note"):
            print(f"  note {item['size']}: {item['note']}")
    return 0


def _serve(args: Any) -> int:
    try:
        host, port, _saved = proactive.resolve_server_binding(
            getattr(args, "host", None), getattr(args, "port", None)
        )
    except proactive.ServerConfigError as exc:
        print(f"Invalid widget server config: {exc}")
        print("Fix or remove the saved server.json, then retry.")
        return 2
    try:
        from . import server
    except ImportError:  # pragma: no cover - direct import from tests/scripts
        import server  # type: ignore
    server.run_server(
        host,
        port,
        certfile=args.certfile,
        keyfile=args.keyfile,
        quiet=args.quiet,
    )
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
    try:
        host, port, saved = proactive.resolve_server_binding(
            getattr(args, "host", None), getattr(args, "port", None)
        )
    except proactive.ServerConfigError as exc:
        payload = {
            "state": "needs_user_action",
            "error": "invalid_server_config",
            "detail": str(exc),
            "steps": [],
            "next_actions": [
                "Fix or remove <Hermes home>/widget/server.json, then rerun hermes widget up."
            ],
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            print(f"Invalid widget server config: {exc}")
        return 2
    raw_server_url = getattr(args, "server_url", None)
    server_url = _valid_server_url(raw_server_url)
    if raw_server_url and server_url is None:
        detail = "--server-url must be a private HTTPS URL the phone can reach (not loopback)."
        if getattr(args, "json", False):
            print(json.dumps({
                "state": "needs_user_action",
                "error": "invalid_server_url",
                "detail": detail,
                "steps": [],
                "next_actions": ["Pass your private HTTPS proxy URL, e.g. https://<tailnet-host>."],
            }, indent=2))
        else:
            print(detail)
        return 2
    prior: tuple[str, int] | None = None
    if saved:
        prior_host, prior_port, _ = proactive.resolve_server_binding()
        prior = (prior_host, prior_port)

    # 1. prerequisites / agent token (idempotent)
    store.get_agent_token()
    steps.append({"step": "prerequisites", "ok": True, "detail": "agent_token"})
    counts_before = _counts_snapshot()

    # 2. skill (idempotent: overwrites only if different)
    try:
        skill_path = proactive.install_skill_file()
        steps.append({"step": "skill", "ok": True, "path": str(skill_path)})
    except Exception as exc:
        steps.append({"step": "skill", "ok": False, "error": str(exc)})
        skill_path = None

    # 3. startup hook and non-secret server config (idempotent)
    try:
        hook_path = proactive.install_startup_hook()
        config_path = proactive.write_server_config(host, port)
        steps.append({"step": "startup_hook", "ok": True, "path": str(hook_path)})
        steps.append({"step": "server_config", "ok": True, "path": str(config_path)})
    except Exception as exc:
        steps.append({"step": "startup_hook", "ok": False, "error": str(exc)})

    # 4. widget (idempotent: upsert)
    widget_id = args.widget_id or store.DEFAULT_WIDGET_ID
    store.ensure_widget(widget_id)
    steps.append({"step": "widget", "ok": True, "widget_id": widget_id})

    # 4. systemd service (idempotent: no duplicate units)
    service_ok, service_path = _ensure_systemd_service(host, port)
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
    probe_host = _probe_host(host)
    listening = _port_listening(port, host=probe_host)
    running = _running_binding()
    restart_required = _restart_required((host, port), prior, running)
    token_present = store.get_agent_token(create=False) is not None
    widget_present = widget_id in store.list_widgets()
    device_count = len(store.list_devices())
    state = _derive_state(
        token_present=token_present,
        widget_present=widget_present,
        listening=listening,
        device_count=device_count,
        routine_ok=routine_ok,
    )
    if restart_required:
        state = "degraded"

    counts_after = _counts_snapshot()
    pairing_instructions = [
        "Publish the widget server through a private HTTPS proxy (for example Tailscale Serve).",
        "On the phone, open Hermes Widget > Pair and enter the private HTTPS URL and the short-lived code.",
        "Never enter the agent token on the phone.",
        "For high-priority wakes, install a UnifiedPush distributor (self-hosted ntfy is fine), pair, then run hermes widget wake-test; Diagnostics shows the live state.",
    ]
    if restart_required:
        pairing_instructions.insert(
            0, "Restart the widget server to apply the configured binding before pairing."
        )
    payload = {
        "state": state,
        "widgetId": widget_id,
        "steps": steps,
        "counts": counts_after,
        "listening": listening,
        "host": host,
        "port": port,
        "probe_host": probe_host,
        "probe_port": port,
        "restart_required": restart_required,
        "pairing_url_hint": server_url,
        "pairing": {
            "code": pairing["code"],
            "expiresAt": pairing["expiresAt"],
            "url": server_url,
            "instructions": pairing_instructions,
        },
        "next_actions": [
            f"hermes widget serve --host {host} --port {port}"
            + ("  # restart required to apply the configured binding" if restart_required else ""),
            "publish the server through a private HTTPS proxy and give the phone that HTTPS URL",
            "on the phone, enter the private HTTPS URL and the short-lived pairing code",
            "for high-priority wakes, install/configure a UnifiedPush distributor and run hermes widget wake-test",
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
    """Mint one single-use pairing code for manual entry in the Android app."""
    server_url = _valid_server_url(getattr(args, "server_url", None))
    if server_url is None:
        print("A private HTTPS --server-url is required, for example:")
        print("  hermes widget pair --server-url https://<tailnet-host>:<port>")
        print("Loopback and cleartext URLs cannot be used from the phone.")
        return 2
    pairing = store.mint_pairing_code()
    code = pairing["code"]
    instructions = [
        "Open the Hermes Widget app, tap Pair or update, and enter the server URL and code below.",
        "The code is short-lived and single-use; never enter the agent token on the phone.",
    ]
    if getattr(args, "json", False):
        print(json.dumps({
            "serverUrl": server_url,
            "code": code,
            "expiresAt": pairing["expiresAt"],
            "pairingLine": f"{server_url}  code={code}",
            "instructions": instructions,
        }, indent=2))
        return 0
    print(f"Server URL:   {server_url}")
    print(f"Pairing code: {code}")
    print(f"Expires:      {pairing['expiresAt']}")
    print(f"Pairing line: {server_url}  code={code}")
    for line in instructions:
        print(f"  - {line}")
    return 0


def _status(args: Any) -> int:
    try:
        host, port, _saved = proactive.resolve_server_binding(
            getattr(args, "host", None), getattr(args, "port", None)
        )
    except proactive.ServerConfigError as exc:
        print(f"Invalid widget server config: {exc}")
        return 2
    widgets = store.list_widgets()
    devices = store.list_devices()
    token_present = store.get_agent_token(create=False) is not None
    probe_host = _probe_host(host)
    listening = _port_listening(port, host=probe_host)
    running = _running_binding()
    restart_required = _restart_required((host, port), None, running)
    widget_id = store.DEFAULT_WIDGET_ID
    widget_present = widget_id in store.list_widgets()
    try:
        publication = store.publication_status(widget_id)
    except store.StoreError:
        publication = {"state": "unknown", "delivery": [], "revisions": []}
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
    if restart_required:
        state = "degraded"
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
            # host/port are the configured bind; probe_host/port identify the
            # address used to test it when the configured bind is wildcard.
            "probe_host": probe_host,
            "probe_port": port,
            "restart_required": restart_required,
            "publicationState": publication.get("state"),
            "stale": publication.get("stale", False),
            "pollIntervalSeconds": publication.get("pollIntervalSeconds"),
            "revisions": publication.get("revisions", []),
            "revisionHistory": publication.get("revisionHistory", {}),
            "warnings": publication.get("warnings", []),
            "wake": publication.get("wake", {"devices": [], "registeredCount": 0}),
            "attention": publication.get("attention", {}),
            "updateRequests": publication.get("updateRequests", []),
            "delivery": publication.get("delivery", []),
            "inventory": publication.get("inventory", []),
            "intents": publication.get("intents", []),
            "anomalies": publication.get("anomalies", []),
            "capabilities": publication.get("capabilities"),
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
        print(f"Probe:        {probe_host}:{port} (listening)")
    else:
        print(f"Probe:        {probe_host}:{port} (not listening; start with: hermes widget serve)")
    print(f"Configured:   {host}:{port}")
    if restart_required:
        print("Restart:      required - the running server still uses a different binding")
    else:
        print("Restart:      not required")
    print(f"Routine:      {'installed' if routine_ok else 'not installed (run hermes widget up)'}")
    print(f"Publication:  {publication.get('state', 'unknown')}" + (" (stale)" if publication.get("stale") else ""))
    for warning in publication.get("warnings", []):
        print(f"Warning:      {warning.get('code')}: {warning.get('detail')}")
    wake = publication.get("wake", {"devices": [], "registeredCount": 0})
    if not wake.get("devices"):
        print("Wake:         no paired devices")
    for item in wake.get("devices", []):
        state = item.get("state", "unknown")
        detail = f" failure={item.get('failureReason')}" if item.get("failureReason") else ""
        print(
            f"Wake:         {item.get('label') or item.get('deviceId')} "
            f"{state} (distributor={item.get('distributorPresent')}, "
            f"registered={item.get('registered')}){detail}"
        )
    attention = publication.get("attention", {})
    if attention.get("revisions"):
        print(
            "Attention:    renders/publish="
            f"{attention.get('rendersPerPublish', 0)} taps/10={attention.get('tapsPer10Publishes', 0)} "
            f"superseded-before-fetch={attention.get('supersededBeforeFetchRate', 0)}"
        )
    if publication.get("updateRequests"):
        print(f"Update reqs:  {len(publication['updateRequests'])} recorded")
        for req in publication["updateRequests"][:5]:
            err = f" error={req.get('error')}" if req.get("error") else ""
            print(f"  - {req.get('createdAt')} {req.get('requestId')} status={req.get('status')}{err}")
        print("  (detail: hermes widget requests)")
    print(f"Instances:    {len(publication.get('inventory', []))} registered")
    print(f"Intents:      {len(publication.get('intents', []))} recorded")
    if publication.get("anomalies"):
        print(f"Anomalies:    {len(publication['anomalies'])} (see JSON status)")
    poll = publication.get("pollIntervalSeconds")
    if poll:
        print(f"Poll:         nominally every {poll // 60} min (WorkManager periodic; actual gaps vary)")
    for item in publication.get("delivery", []):
        st = "revoked" if item.get("revoked") else "active"
        skipped = item.get("skippedRevisions") or []
        detail = (
            f" lastFetchedRev={item.get('lastFetchedRevision')}"
            f" lastPoll={item.get('lastPollAt') or 'never'}"
        )
        if skipped:
            detail += f" superseded-unfetched={skipped}"
        print(f"  - {item.get('label')} [{st}] {item.get('state')}{detail}")
    return 0


def _wake_test(args: Any) -> int:
    try:
        result = store.wake_test(getattr(args, "widget_id", store.DEFAULT_WIDGET_ID))
    except store.StoreError as exc:
        print(_redact(str(exc)))
        return 1
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    else:
        print("UnifiedPush wake test (content-free; no publication revision created)")
        for item in result.get("receiptChain", []):
            detail = f" ({item['detail']})" if item.get("detail") else ""
            print(f"  {item.get('label') or item.get('deviceId')}: {item.get('state')}{detail}")
        if not result.get("receiptChain"):
            print("  no registered push endpoint")
    return 0 if result.get("ok") else 1


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


def _jdk_candidates() -> list[str]:
    """JDK installs that can build the Android app (17+); PATH java may be 8."""
    seen: list[str] = []
    for raw in (
        os.environ.get("JAVA_HOME", ""),
        r"C:\Program Files\Eclipse Adoptium\jdk-25.0.3.9-hotspot",
        r"C:\Program Files\Java\jdk-17",
        r"C:\Program Files\Microsoft\jdk-17",
    ):
        candidate = str(raw).strip()
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen


def _java_version_string(java_bin: str) -> str | None:
    try:
        probe = subprocess.run(
            [java_bin, "-version"], capture_output=True, text=True, timeout=5
        )
        output = (probe.stderr or probe.stdout).splitlines()
        return output[0][:200] if output else "unknown"
    except Exception:
        return None


def _java_major_ok(version: str | None) -> bool | None:
    """True when the java -version line reports 17+; None when unparseable."""
    import re as _re
    if not version:
        return None
    match = _re.search(r'"(\d+)(?:\.(\d+))?', version)
    if not match:
        match = _re.search(r"(\d+)(?:\.(\d+))?", version)
    if not match:
        return None
    try:
        major = int(match.group(1))
        minor = int(match.group(2)) if match.group(2) else 0
    except ValueError:
        return None
    if major == 1:  # legacy 1.8 numbering
        return minor >= 17
    return major >= 17


def _usb_doctor_snapshot(port: int) -> dict[str, Any]:
    """Best-effort ADB/USB + JDK snapshot for doctor; never raises."""
    snapshot: dict[str, Any] = {"available": False}
    try:
        adb_mod = _adb_import()
        snapshot.update(adb_mod.usb_status(port))
        snapshot["available"] = bool(snapshot.get("ok"))
    except Exception as exc:
        snapshot = {"available": False, "error": str(exc)[:300]}
    try:
        java = shutil.which("java")
        snapshot["javaOnPath"] = java
        if java:
            version = _java_version_string(java)
            if version:
                snapshot["javaVersion"] = version
                snapshot["jdk17OnPath"] = _java_major_ok(version)
        jdks: dict[str, str] = {}
        for home in _jdk_candidates():
            java_bin = str(Path(home) / "bin" / "java.exe" if os.name == "nt" else Path(home) / "bin" / "java")
            if Path(java_bin).is_file():
                version = _java_version_string(java_bin)
                if version:
                    jdks[home] = version
        if jdks:
            snapshot["jdks"] = jdks
            snapshot["jdkOk"] = any(_java_major_ok(v) is True for v in jdks.values())
    except Exception:
        pass
    for key in ("JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(key, "").strip()
        if value:
            snapshot[key] = value
    return snapshot


def _doctor(args: Any) -> int:
    """Diagnostics with redaction and protocol metadata."""
    token_present = store.get_agent_token(create=False) is not None
    widgets = store.list_widgets()
    devices = store.list_devices()
    routine_ok = False
    with contextlib.suppress(Exception):
        routine_ok = proactive.find_routine() is not None
    try:
        host, port, _saved = proactive.resolve_server_binding(
            getattr(args, "host", None), getattr(args, "port", None)
        )
    except proactive.ServerConfigError:
        host, port = proactive.DEFAULT_HOST, proactive.DEFAULT_PORT
    listening = _port_listening(port, host=_probe_host(host))
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
        "protocol": {"transport_version": "v1", "server_version": server_version},
        "capabilities": ["hermes-widget.publication.v1", "pairing-code", "6h-brief"],
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
        "usb": _usb_doctor_snapshot(port),
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
    publication_mode = any(
        getattr(args, name, None) is not None
        for name in ("publication_file", "title", "summary", "text", "svg", "file_path", "actions")
    ) or getattr(args, "max_age_seconds", None) is not None or getattr(args, "item_id", None) is not None
    if not publication_mode:
        print("publish requires publication content; provide --title, --summary, and one source")
        return 2
    if publication_mode:
        payload: dict[str, Any] = {}
        publication_file = getattr(args, "publication_file", None)
        if publication_file:
            try:
                source = _resolve_input_file(publication_file, label="publication file")
                loaded = json.loads(source.read_text(encoding="utf-8"))
            except Exception as exc:
                err = {"error": "invalid_publication", "detail": _redact(str(exc))}
                print(json.dumps(err, indent=2) if getattr(args, "json", False) else _redact(f"publish failed: {exc}"))
                return 1
            if not isinstance(loaded, dict):
                err = {"error": "invalid_publication", "detail": "publication must be a JSON object"}
                print(json.dumps(err, indent=2) if getattr(args, "json", False) else _redact(err["detail"]))
                return 1
            payload.update(loaded)
        for name in ("title", "summary", "text", "svg", "file_path", "max_age_seconds", "item_id", "actions"):
            value = getattr(args, name, None)
            if value is not None:
                payload[name] = value
        if getattr(args, "priority", "normal") != "normal" or "priority" not in payload:
            payload["priority"] = getattr(args, "priority", "normal")
        if isinstance(payload.get("actions"), str):
            try:
                payload["actions"] = json.loads(payload["actions"])
            except Exception as exc:
                err = {"error": "invalid_publication", "detail": _redact(str(exc))}
                print(json.dumps(err, indent=2) if getattr(args, "json", False) else _redact(f"publish failed: {exc}"))
                return 1
        try:
            result = store.put_publication(
                widget_id,
                title=payload.get("title"),
                summary=payload.get("summary"),
                text=payload.get("text"),
                svg=payload.get("svg"),
                file_path=payload.get("file_path", payload.get("filePath")),
                expires_at=payload.get("expires_at", payload.get("expiresAt")),
                ttl_seconds=payload.get("ttl_seconds", payload.get("ttlSeconds")),
                max_age_seconds=payload.get("max_age_seconds", payload.get("maxAgeSeconds")),
                priority=payload.get("priority", "normal"),
                item_id=payload.get("item_id", payload.get("itemId")),
                actions=payload.get("actions"),
            )
        except store.StoreError as exc:
            err = {"error": exc.code, "detail": _redact(str(exc))}
            print(json.dumps(err, indent=2) if getattr(args, "json", False) else _redact(str(exc)))
            return 1
        if getattr(args, "json", False):
            print(json.dumps({"ok": True, **result}, indent=2))
        else:
            print(f"Published {widget_id} revision {result.get('revision')} ({result.get('priority', 'normal')})")
        return 0

def _restart(_args: Any) -> int:
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
    """Update the installed plugin via Hermes after making a database restore point."""
    hermes = os.environ.get("HERMES_BIN") or shutil.which("hermes")
    if not hermes:
        print("Hermes executable not found; run `hermes plugins update hermes-widget` from the host.")
        return 1
    try:
        backup = store.backup_db()
        update = subprocess.run(
            [hermes, "plugins", "update", "hermes-widget"],
            check=False,
            timeout=600,
        )
        if update.returncode != 0:
            print(
                "Hermes plugin update failed "
                f"(exit {update.returncode}); database backup is {backup.name if backup else 'unavailable'}."
            )
            return 1
        widgets = store.list_widgets()
        token_present = store.get_agent_token(create=False) is not None
        health = {
            "ok": True,
            "plugin_updated": True,
            "widgets": widgets,
            "token_present": token_present,
            "backup": backup.name if backup else None,
            "restart_required": True,
        }
        print(json.dumps(health, indent=2))
        return 0
    except Exception as exc:
        print(_redact(f"plugin update failed: {exc}"))
        return 1


def _rollback(_args: Any) -> int:
    # Replacing a live SQLite database while the server has open connections is unsafe.
    try:
        host, port, _saved = proactive.resolve_server_binding(None, None)
        if _port_listening(port, host=_probe_host(host)):
            print("Stop the widget server before rolling back its database.")
            return 1
        data_dir = store.data_dir()
        backups = sorted(data_dir.glob("widget.db.bak.*"))
        if backups:
            latest = backups[-1]
            store.restore_db(latest)
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
    yes = getattr(args, "yes", False)
    if not keep and not yes:
        if not sys.stdin.isatty():
            print("Data removal needs confirmation; rerun with --yes or use --keep-data.")
            return 1
        try:
            answer = input("Remove Hermes widget data, service, skill, hook, and routine? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in {"y", "yes"}:
            print("Uninstall cancelled.")
            return 1

    svc = Path.home() / ".config" / "systemd" / "user" / "hermes-widget.service"
    if svc.is_file():
        systemctl = shutil.which("systemctl")
        if systemctl:
            try:
                stopped = subprocess.run(
                    [systemctl, "--user", "disable", "--now", "hermes-widget.service"],
                    check=False,
                    timeout=20,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                print(_redact(f"Could not stop widget service: {exc}"))
                return 1
            if stopped.returncode != 0:
                print("Could not stop and disable hermes-widget.service; no data was removed.")
                return 1
        try:
            svc.unlink()
        except OSError as exc:
            print(_redact(f"Could not remove service file: {exc}"))
            return 1
        if systemctl:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run([systemctl, "--user", "daemon-reload"], check=False, timeout=10)

    try:
        host, port, _saved = proactive.resolve_server_binding(None, None)
    except proactive.ServerConfigError as exc:
        print(_redact(f"Cannot verify server state: {exc}"))
        return 1
    if _port_listening(port, host=_probe_host(host)):
        print("Widget server is still serving; stop the gateway or manual server before uninstalling.")
        return 1

    try:
        proactive.remove_routine()
        proactive.remove_startup_hook()
        proactive.remove_skill_file()
    except Exception as exc:
        print(_redact(f"Could not remove a widget integration: {exc}"))
        return 1

    removed = 0
    if not keep:
        try:
            data = store.data_dir()
            targets = [
                data / "widget.db", data / "widget.db-wal", data / "widget.db-shm",
                data / "agent_token", data / "server-process.json", data / "server-launch.pid",
                proactive.server_config_path(),
            ]
            targets.extend(data.glob("widget.db.bak.*"))
            for target in dict.fromkeys(targets):
                if target.is_file():
                    target.unlink()
                    removed += 1
            assets = data / "assets"
            if assets.is_dir():
                for target in assets.iterdir():
                    if target.is_file() and re.fullmatch(r"asset_[0-9a-f]{24}", target.name):
                        target.unlink()
                        removed += 1
                with contextlib.suppress(OSError):
                    assets.rmdir()
        except Exception as exc:
            print(_redact(str(exc)))
            return 1
    print(
        "Uninstalled."
        + (" (kept widget data)" if keep else f" Removed {removed} widget data file(s).")
    )
    return 0


def _install_skill(_args: Any) -> int:
    print(f"Skill installed: {proactive.install_skill_file()}")
    return 0


# ---------------------------------------------------------------------------
# ADB-over-USB (local dev transport; debug builds only for cleartext)
# ---------------------------------------------------------------------------

def _adb_import():
    try:
        from . import adb as _adb
    except ImportError:  # pragma: no cover - direct import from tests/scripts
        import adb as _adb  # type: ignore
    return _adb


def _adb_devices(args: Any) -> int:
    _adb = _adb_import()
    try:
        devices = _adb.list_devices()
    except Exception as exc:
        print(f"adb devices failed: {exc}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "devices": devices}, indent=2))
        return 0
    if not devices:
        print("No adb devices. Enable USB debugging, connect via USB, accept the RSA prompt.")
        return 1
    for item in devices:
        extra = " ".join(
            f"{key}={item[key]}" for key in ("model", "product", "transport") if key in item
        )
        print(f"{item['serial']}  {item['state']}  {extra}".rstrip())
    return 0


def _resolve_usb_port(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
        return value
    try:
        _, port, _ = proactive.resolve_server_binding(None, None)
        return port
    except Exception:
        return DEFAULT_PORT


def _adb_reverse(args: Any) -> int:
    _adb = _adb_import()
    port = _resolve_usb_port(getattr(args, "port", None))
    device_port = getattr(args, "device_port", None) or port
    serial = getattr(args, "device", None)
    as_json = getattr(args, "json", False)
    try:
        if getattr(args, "list", False):
            target = _adb.ensure_device(serial)
            specs = _adb.reverse_list(serial=target)
            if as_json:
                print(json.dumps({"ok": True, "serial": target, "reverses": specs}, indent=2))
            else:
                print(f"reverses for {target}:")
                for spec in specs or ["(none)"]:
                    print(f"  {spec}")
            return 0
        result = _adb.reverse(port, device_port, serial=serial, remove=bool(getattr(args, "remove", False)))
    except Exception as exc:
        print(f"adb reverse failed: {exc}")
        return 1
    if as_json:
        print(json.dumps({"ok": True, **result}, indent=2))
        return 0
    if result.get("removed"):
        print(f"Removed reverse {result.get('spec')} on {result.get('serial')}.")
    else:
        print(f"USB reverse ready: phone http://127.0.0.1:{device_port} -> host :{port} ({result.get('serial')}).")
        print("Debug builds only: release forbids cleartext; use Tailscale HTTPS for release.")
    return 0


def _default_debug_apk() -> Path | None:
    """Newest debug APK, searching cwd, the checkout, then the installed copy."""
    seen: set[Path] = set()
    roots: list[Path] = [Path.cwd()]
    try:
        roots.append(Path(__file__).resolve().parents[2])
    except Exception:
        pass
    # Installed plugin lives under <home>/plugins/hermes-widget; the checkout
    # is not рядом, so also try sibling checkouts and the documented SDK path.
    for root in list(roots):
        for parent in [root, root.parent]:
            candidate = parent / "Hermes_blink"
            if candidate.is_dir() and candidate not in seen:
                roots.append(candidate)
                seen.add(candidate)
    apk_candidates: list[Path] = []
    for root in roots:
        for pattern in (
            "android/app/build/outputs/apk/debug/*.apk",
            "app/build/outputs/apk/debug/*.apk",
        ):
            try:
                apk_candidates.extend(root.glob(pattern))
            except Exception:
                continue
    apk_candidates = [p for p in apk_candidates if p.is_file()]
    if not apk_candidates:
        return None
    apk_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return apk_candidates[0]


def _adb_install(args: Any) -> int:
    _adb = _adb_import()
    raw = getattr(args, "apk", None) or getattr(args, "apk_path", None)
    apk = Path(raw).expanduser() if raw else _default_debug_apk()
    as_json = getattr(args, "json", False)
    if apk is None or not apk.is_file():
        hint = "Build one with: cd android && ./gradlew assembleDebug (JDK 17+ required)."
        print(f"No APK found. {hint}" if as_json else f"No APK found at {apk or '(default debug output)'}. {hint}")
        return 1
    try:
        result = _adb.install_apk(apk, serial=getattr(args, "device", None))
        if getattr(args, "launch", False):
            launched = _adb.start_app(serial=result.get("serial"))
            result = {**result, "launched": launched.get("component")}
    except Exception as exc:
        print(f"adb install failed: {exc}")
        return 1
    if as_json:
        print(json.dumps({"ok": True, **{k: str(v) if isinstance(v, Path) else v for k, v in result.items()}}, indent=2))
        return 0
    print(f"Installed {apk.name} on {result.get('serial')}.")
    if result.get("launched"):
        print(f"Launched {result['launched']}.")
    return 0


def _adb_logcat(args: Any) -> int:
    _adb = _adb_import()
    serial = getattr(args, "device", None)
    try:
        target = _adb.ensure_device(serial)
    except Exception as exc:
        print(f"adb logcat failed: {exc}")
        return 1
    if getattr(args, "clear", False):
        try:
            _adb.run_adb(["-s", target, "logcat", "-c"])
        except Exception as exc:
            print(f"adb logcat -c failed: {exc}")
            return 1
        print(f"Cleared logcat on {target}.")
        return 0
    tag = getattr(args, "tag", None) or "HermesWidget"
    needle = tag.lower()
    try:
        if getattr(args, "dump", False):
            result = _adb.run_adb(["-s", target, "logcat", "-d", "-v", "brief"])
            if result.returncode != 0:
                print(f"adb logcat -d failed: {(result.stderr or result.stdout).strip()}")
                return 1
            lines = [ln for ln in result.stdout.splitlines() if needle in ln.lower()]
            print("\n".join(lines[-200:] or [f"(no logcat lines matching {tag!r}; use --tag HermesWidget)"]))
            return 0
        result = _adb.run_adb(["-s", target, "logcat", "-v", "brief", "-T", "200"], timeout=12)
        if result.returncode != 0:
            print(f"adb logcat failed: {(result.stderr or result.stdout).strip()}")
            return 1
        lines = [ln for ln in result.stdout.splitlines() if needle in ln.lower()]
        print("\n".join(lines[-200:] or [f"(no logcat lines matching {tag!r}; use --dump for full buffer)"]))
        return 0
    except Exception as exc:
        print(f"adb logcat failed: {exc}")
        return 1


def _usb_up(args: Any) -> int:
    """Reverse the host port to the USB phone and report server + device state."""
    _adb = _adb_import()
    port = _resolve_usb_port(getattr(args, "port", None))
    serial = getattr(args, "device", None)
    as_json = getattr(args, "json", False)
    try:
        host, saved_port, _ = proactive.resolve_server_binding(None, None)
        probe_host = _probe_host(host)
        listening = _port_listening(saved_port, host=probe_host)
    except Exception:
        host, saved_port, listening, probe_host = "127.0.0.1", port, False, "127.0.0.1"
    try:
        status = _adb.usb_status(port)
    except Exception as exc:
        print(f"usb-up failed: {exc}")
        return 1
    if status.get("ok") and status.get("onlineCount"):
        try:
            target = _adb.ensure_device(serial)
            reverse_result = _adb.reverse(port, port, serial=target)
            status = {**status, **_adb.usb_status(port), "reverse": reverse_result}
        except Exception as exc:
            status = {**status, "reverseError": str(exc)}
    payload = {
        "ok": bool(status.get("ok") and status.get("onlineCount")),
        "host": host,
        "port": saved_port,
        "listening": listening,
        "usb": status,
        "deviceUrl": f"http://127.0.0.1:{port}",
        "note": "Start the server (hermes widget serve) if listening=false; debug APKs use the USB URL, release uses HTTPS.",
    }
    if as_json:
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 1
    if not status.get("ok"):
        print(f"USB setup failed: {status.get('error')}")
        return 1
    if not status.get("onlineCount"):
        print("No online adb device. Enable USB debugging and reconnect.")
        return 1
    print(f"USB ready: phone http://127.0.0.1:{port} -> host :{port}")
    print(f"Server: {host}:{saved_port} listening={listening}")
    if not listening:
        print("Start it with: hermes widget serve --host 127.0.0.1 --port 8788")
    return 0


def _requests(args: Any) -> int:
    """List Request-update taps: did each one start a Hermes refresh?"""
    widget_id = getattr(args, "widget_id", None) or store.DEFAULT_WIDGET_ID
    try:
        limit = int(getattr(args, "limit", 10) or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))
    try:
        rows = store.list_update_requests(widget_id, limit=limit)
    except Exception as exc:
        print(f"requests failed: {exc}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "widgetId": widget_id, "requests": rows}, indent=2))
        return 0
    if not rows:
        print(f"No Request-update taps recorded for {widget_id!r}.")
        print("Tap Request update in the app/widget, then rerun this.")
        return 0
    print(f"Request-update taps for {widget_id!r} (newest first):")
    for row in rows:
        error = f" error={row.get('error')}" if row.get("error") else ""
        print(
            f"  {row.get('createdAt')} {row.get('requestId')} "
            f"status={row.get('status')} device={row.get('deviceId')}{error}"
        )
    stuck = [r for r in rows if r.get("status") == "failed" or r.get("error")]
    if stuck:
        print("A failed row means Hermes stored the tap but `hermes cron run` did not start.")
        print("Run: hermes widget trigger-refresh  (then `hermes cron list` to check the job)")
    return 0


def _trigger_refresh(args: Any) -> int:
    """Manually spawn the refresh cron and report whether Hermes was hit."""
    try:
        result = proactive.trigger_refresh()
    except Exception as exc:
        print(f"trigger-refresh failed: {exc}")
        return 1
    as_json = getattr(args, "json", False)
    if as_json:
        print(json.dumps({"ok": bool(result.get("triggered")), **result}, indent=2))
        return 0 if result.get("triggered") else 1
    if result.get("triggered") and not result.get("duplicate"):
        print(f"Refresh triggered: {result.get('job')} pid={result.get('pid')}.")
        print("The agent run must call widget_status with consume_update_requests=true.")
    elif result.get("duplicate"):
        print(f"Refresh already running ({result.get('job')}); coalesced.")
    else:
        print(f"Refresh NOT triggered: {result.get('error')}")
        print("Check `hermes cron list` for hermes-widget-refresh and re-auth (`hermes model`).")
        return 1
    return 0
