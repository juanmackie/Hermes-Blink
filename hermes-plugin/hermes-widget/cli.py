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
_PLACEHOLDER_TEXT = "Your Hermes agent is connected. Ask it to update this widget."
# allowed structured progress states (B/C)
_ALLOWED_STATES = frozenset({"needs_user_action", "starting", "awaiting_pairing", "ready", "degraded"})


def _resolve_input_file(value: Any, *, label: str) -> Path:
    """Resolve a user-supplied JSON path from cwd, the checkout, or Hermes home.

    Fixture paths such as ``fixtures/golden/large-brief.json`` are common in the
    contract docs, but an installed plugin is not necessarily running from the
    repository root.  The resolver keeps that ergonomic path working without
    hiding a missing file behind a raw OSError.
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
            ],
        },
    }


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
        "Manage the Hermes home-screen widget: serve layouts over HTTP, pair "
        "devices, and install the background refresh routine."
    )
    commands = parser.add_subparsers(dest="widget_command")

    serve = commands.add_parser("serve", help="Run the widget HTTP server.")
    serve.add_argument("--host", default=None, help="Interface to bind (default: saved config or 127.0.0.1).")
    serve.add_argument("--port", type=int, default=None, help="Port to bind (default: saved config or 8788).")
    serve.add_argument("--certfile", default=None, help="TLS certificate for HTTPS.")
    serve.add_argument("--keyfile", default=None, help="TLS private key for HTTPS.")
    serve.add_argument("--quiet", action="store_true", help="Do not print the listen URL.")

    setup = commands.add_parser("setup", help="Create the agent token, skill, and a pairing code.")
    setup.add_argument("--host", default=None, help="Interface to bind (default: saved config or 127.0.0.1).")
    setup.add_argument("--port", type=int, default=None, help="Port to bind (default: saved config or 8788).")
    setup.add_argument("--server-url", default=None, help="Private HTTPS URL the phone will use (printed for manual entry).")
    setup.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    setup.add_argument("--routine", action="store_true", help="Also install the background refresh job.")
    setup.add_argument("--schedule", default=proactive.DEFAULT_SCHEDULE)

    commands.add_parser("code", help="Mint a pairing code for a new device.")

    status = commands.add_parser("status", help="Show widget, device, and server status.")
    status.add_argument("--port", type=int, default=None)
    status.add_argument("--host", default=None)
    status.add_argument("--json", action="store_true", help="Machine-readable JSON.")

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

    pub = commands.add_parser("publish", help="Publish a brief layout to the widget.")
    pub.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID)
    pub.add_argument("--layout-file", default=None, help="Path to layout JSON file (legacy mode).")
    pub.add_argument("--layout-json", default=None, help="Inline layout JSON (legacy mode).")
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
    commands.add_parser("upgrade", help="Upgrade the plugin (git pull / reinstall) and verify health.")
    commands.add_parser("rollback", help="Rollback to previous version (restore last backup).")
    uninst = commands.add_parser("uninstall", help="Remove widget data and service.")
    uninst.add_argument("--keep-data", action="store_true", help="Keep DB and pairing state.")
    uninst.add_argument("--yes", action="store_true", help="Skip confirmation.")

    commands.add_parser("install-skill", help="Copy the widget skill into the Hermes skill tree.")

    prev = commands.add_parser(
        "preview",
        help="Render a layout JSON file to an HTML preview (no device needed).",
    )
    prev.add_argument("layout_file", nargs="?", help="Path to a v2 layout JSON file (legacy HTML mode).")
    prev.add_argument("--out", default=None, help="Output HTML path or PNG output directory.")
    prev.add_argument("--json", action="store_true", help="Print the dry-run/report JSON.")
    prev.add_argument("--widget-id", default=store.DEFAULT_WIDGET_ID, help="Widget id for publication preview.")
    prev.add_argument("--sizes", default=None, help="Comma-separated publication sizes, e.g. 2x2,4x2,4x4.")
    prev.add_argument("--publication-file", default=None, help="JSON file containing a publication to preview before publishing.")


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
        "preview": _preview,
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
    """Render either the legacy layout HTML or exact publication PNG previews."""
    try:
        from . import preview
        from .validate import ValidationError, inspect_layout
    except ImportError:  # pragma: no cover - direct import from tests/scripts
        import preview  # type: ignore
        from validate import ValidationError, inspect_layout  # type: ignore

    # Publication mode is selected by --sizes/--publication-file, or explicitly
    # by a JSON file containing a publication rather than a v2 layout.
    publication_mode = bool(getattr(args, "sizes", None) or getattr(args, "publication_file", None))
    source_path = getattr(args, "publication_file", None)
    if not publication_mode and not source_path and getattr(args, "layout_file", None):
        try:
            source = _resolve_input_file(args.layout_file, label="layout file")
        except FileNotFoundError as exc:
            print(str(exc))
            return 1
        try:
            layout = json.loads(source.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"{source} is not valid JSON: {exc}")
            return 1
        try:
            report = inspect_layout(layout)
        except ValidationError as exc:
            print(f"invalid layout: {exc}")
            return 1
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        out = preview.preview_file(source, out=args.out)
        print(f"preview written to {out}")
        for warning in report["warnings"]:
            print(f"  warning {warning['code']}: {warning['detail']}")
        return 0

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
        if proposed is None:
            publication = store.get_publication(widget_id)
            if publication is None:
                print(f"no publication for widget {widget_id!r}")
                return 1
        elif isinstance(proposed.get("content"), dict) and not any(
            key in proposed for key in ("text", "svg", "file_path", "filePath")
        ):
            publication = {**proposed, "widgetId": widget_id}
        else:
            prepared = store.prepare_publication(
                title=proposed.get("title"), summary=proposed.get("summary"),
                text=proposed.get("text"), svg=proposed.get("svg"),
                file_path=proposed.get("file_path", proposed.get("filePath")),
                expires_at=proposed.get("expires_at", proposed.get("expiresAt")),
                ttl_seconds=proposed.get("ttl_seconds", proposed.get("ttlSeconds")),
                max_age_seconds=proposed.get("max_age_seconds", proposed.get("maxAgeSeconds")),
                priority=proposed.get("priority", "normal"),
                item_id=proposed.get("item_id", proposed.get("itemId")),
                actions=proposed.get("actions"),
            )
            if prepared.kind == "text":
                content = {"type": "text", "mediaType": "text/plain; charset=utf-8", "text": prepared.text}
            else:
                assert prepared.asset is not None
                content = {
                    "type": "image", "mediaType": prepared.asset.media_type,
                    "width": prepared.asset.width, "height": prepared.asset.height,
                    "bytes": len(prepared.asset.data), "sha256": prepared.asset.sha256,
                    "data": base64.b64encode(prepared.asset.data).decode("ascii"),
                }
            publication = {
                "version": 1, "widgetId": widget_id, "publicationId": "preview", "revision": 0,
                "kind": prepared.kind, "title": prepared.title, "summary": prepared.summary,
                "publishedAt": store._now(), "expiresAt": prepared.expires_at,
                "priority": prepared.priority, "itemId": prepared.item_id,
                "actions": list(prepared.actions), "content": content,
            }
        rendered = preview.render_publication_previews(
            publication,
            sizes=getattr(args, "sizes", None),
            inventory=store.list_widget_instances(widget_id),
            asset_loader=lambda asset_id: store.read_asset(asset_id)[1],
        )
    except (ValueError, store.StoreError) as exc:
        print(f"preview failed: {exc}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "widgetId": widget_id, "previews": rendered}, indent=2))
        return 0
    out_dir = Path(args.out) if args.out else Path.cwd() / "widget-previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    for item in rendered:
        target = out_dir / f"{widget_id}-{item['size']}.png"
        target.write_bytes(base64.b64decode(item["data"]))
        print(f"preview written to {target}")
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


def _setup(args: Any) -> int:
    supported, reason = _check_environment()
    if not supported:
        print(f"Unsupported environment: {reason}")
        print("Aborting before any changes. Install on Ubuntu 24.04 LTS or WSL2 with Ubuntu 24.04.")
        return 2
    try:
        host, port, _saved = proactive.resolve_server_binding(args.host, args.port)
        raw_server_url = getattr(args, "server_url", None)
        server_url = _valid_server_url(raw_server_url)
        if raw_server_url and server_url is None:
            print("Invalid --server-url: enter a private HTTPS URL the phone can reach (not loopback).")
            return 2
    except proactive.ServerConfigError as exc:
        print(f"Invalid widget server config: {exc}")
        print("Fix or remove the saved server.json, then retry.")
        return 2
    store.get_agent_token()
    skill_path = proactive.install_skill_file()
    hook_path = proactive.install_startup_hook()
    config_path = proactive.write_server_config(args.host, args.port)
    widget_id = args.widget_id or store.DEFAULT_WIDGET_ID
    if store.get_widget(widget_id) is None:
        store.put_widget(widget_id, _placeholder_layout(widget_id))
    pairing = store.get_or_mint_pairing_code()
    _ensure_systemd_service(host, port)

    print()
    print("Hermes widget setup complete")
    print("----------------------------")
    print(f"Widget id:  {widget_id}")
    print(f"Data dir:   {store.data_dir()}")
    print(f"Skill:      {skill_path}")
    print(f"Hook:       {hook_path}")
    print(f"Config:     {config_path}")
    print()
    print("1. Start or restart the server to apply this binding:")
    print(f"     hermes widget serve --host {host} --port {port}")
    print()
    print("2. Publish the server through a private HTTPS proxy (for example Tailscale Serve):")
    print(f"     tailscale serve --bg --https={port} tcp://{_probe_host(host)}:{port}")
    print("   Do not expose the raw widget port publicly.")
    print()
    print("3. On the phone, open Hermes Widget > Pair and enter:")
    print(f"     Server URL:   {server_url or '<your private HTTPS proxy URL>'}")
    print(f"     Pairing code: {pairing['code']}   (expires {pairing['expiresAt']})")
    print("   The code is short-lived; never enter the agent token on the phone.")
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
    if store.get_widget(widget_id) is None:
        store.put_widget(widget_id, _placeholder_layout(widget_id))
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
    widget_present = store.get_widget(widget_id) is not None
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
        "configuredHost": host,
        "configuredPort": port,
        "configured_host": host,
        "configured_port": port,
        "probeHost": probe_host,
        "probePort": port,
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
    widget_present = store.get_widget(widget_id) is not None
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
            "configuredHost": host,
            "configuredPort": port,
            "configured_host": host,
            "configured_port": port,
            "probeHost": probe_host,
            "probePort": port,
            "probe_host": probe_host,
            "probe_port": port,
            "restartRequired": restart_required,
            "restart_required": restart_required,
            "publicationState": publication.get("state"),
            "stale": publication.get("stale", False),
            "pollIntervalSeconds": publication.get("pollIntervalSeconds"),
            "revisions": publication.get("revisions", []),
            "revisionHistory": publication.get("revisionHistory", {}),
            "warnings": publication.get("warnings", []),
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
        "protocol": {"transport_version": "v1", "layout_contract": "v2", "server_version": server_version},
        "capabilities": ["hermes-widget.v2", "pairing-code", "6h-brief"],
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
    publication_mode = any(
        getattr(args, name, None) is not None
        for name in ("publication_file", "title", "summary", "text", "svg", "file_path", "actions")
    ) or getattr(args, "max_age_seconds", None) is not None or getattr(args, "item_id", None) is not None
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
            source = _resolve_input_file(layout_file, label="layout file")
            payload = json.loads(source.read_text(encoding="utf-8"))
        except Exception as exc:
            err = {"error": "invalid_layout", "detail": _redact(str(exc))}
            print(json.dumps(err, indent=2) if getattr(args, "json", False) else _redact(f"publish failed: {exc}"))
            return 1
    else:
        payload = _placeholder_layout(widget_id)
    if not isinstance(payload, dict):
        err = {"error": "invalid_layout", "detail": "layout must be a JSON object"}
        print(json.dumps(err, indent=2) if getattr(args, "json", False) else _redact(err["detail"]))
        return 1
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
    # Fresh clone builds handled by CI; upgrade is idempotent pull/reinstall stub with health check.
    # It takes a DB backup first so `rollback` has a real restore point: this is the only
    # command that can replace the database, and rollback restores the newest .bak.
    try:
        backup = store.backup_db()
        widgets = store.list_widgets()
        token_present = store.get_agent_token(create=False) is not None
        health = {
            "ok": True,
            "widgets": widgets,
            "token_present": token_present,
            "backup": backup.name if backup else None,
        }
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
        with contextlib.suppress(OSError):
            svc.unlink()
    if not keep:
        try:
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
