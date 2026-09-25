"""Agent-facing tool handlers for the hermes-widget plugin.

Handlers return JSON strings rather than raising: a malformed layout or a hit
rate limit must be a normal, model-visible result so one bad push cannot abort
an otherwise healthy agent turn. All persistence goes through store.*.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
from typing import Any

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import store
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import store  # type: ignore

logger = logging.getLogger(__name__)

_DEFAULT_EVENT_LIMIT = 200


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _error(code: str, detail: str) -> str:
    return _dumps({"error": code, "detail": detail})


def _store_error(exc: store.StoreError) -> str:
    # Every StoreError subclass carries a stable .code (invalid_layout,
    # rate_limited, ...) that the Android client and the model both expect.
    return _error(exc.code, str(exc))


def _coerce_layout(raw: Any) -> dict[str, Any] | str:
    """Accept the object or a JSON string of it. Returns a message string when unusable."""
    if isinstance(raw, str):
        # The model sometimes serializes the object; accept either shape.
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            return f"layout is not valid JSON: {exc}"
    if raw is None:
        return "layout is required"
    if not isinstance(raw, dict):
        return "layout must be a JSON object"
    return raw


def widget_update(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Validate and store a complete v2 layout envelope."""
    args = args or {}
    widget_id = args.get("widget_id") or store.DEFAULT_WIDGET_ID
    if not isinstance(widget_id, str):
        return _error("invalid_layout", "widget_id must be a string")

    layout = _coerce_layout(args.get("layout"))
    if isinstance(layout, str):
        return _error("invalid_layout", layout)

    try:
        result = store.put_widget(widget_id, layout)
    except store.RateLimitError as exc:
        return _store_error(exc)
    except store.LayoutError as exc:
        return _store_error(exc)
    except store.StoreError as exc:
        return _store_error(exc)

    return _dumps(
        {
            **result,
            "next": "The widget will pick this up on its next poll; use widget_read_events to see taps.",
        }
    )


def widget_validate(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Dry-run a layout: validate, count, warn. Never stores and never rate-limits."""
    args = args or {}
    widget_id = args.get("widget_id") or store.DEFAULT_WIDGET_ID
    if not isinstance(widget_id, str):
        return _error("invalid_layout", "widget_id must be a string")

    layout = _coerce_layout(args.get("layout"))
    if isinstance(layout, str):  # _coerce_layout returns an error message string
        return _error("invalid_layout", layout)

    try:
        report = store.inspect_widget(widget_id, layout)
    except store.StoreError as exc:
        return _store_error(exc)
    return _dumps(report)


def widget_list(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Return the ids of every widget that currently has a stored layout."""
    try:
        widgets = store.list_widgets()
    except store.StoreError as exc:
        return _store_error(exc)
    return _dumps({"widgets": widgets, "defaultWidgetId": store.DEFAULT_WIDGET_ID})


def widget_read_events(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Return recent widget interaction events, newest first."""
    args = args or {}
    since = args.get("since") or None
    widget_id = args.get("widget_id") or None
    if since is not None and not isinstance(since, str):
        return _error("invalid_since", "since must be an ISO-8601 string")
    if widget_id is not None and not isinstance(widget_id, str):
        return _error("invalid_widget_id", "widget_id must be a string")

    limit: int = _DEFAULT_EVENT_LIMIT
    raw_limit = args.get("limit")
    if raw_limit is not None:
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
            return _error("invalid_limit", "limit must be an integer")
        limit = raw_limit

    try:
        events = store.get_events(since=since, widget_id=widget_id, limit=limit)
    except store.StoreError as exc:
        return _store_error(exc)
    return _dumps({"events": events})


def widget_mint_pairing_code(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Mint a short-lived pairing code for manual entry on the phone.

    The Android app has no QR or link handler, so this never returns a QR payload
    or a same-phone link. An optional [server_url] is validated as a private HTTPS
    URL and echoed for manual entry; it never changes the code.
    """
    args = args or {}
    label = args.get("device_label") or "unknown"
    if not isinstance(label, str):
        return _error("invalid_device_label", "device_label must be a string")

    ttl = store.PAIRING_TTL_MINUTES
    raw_ttl = args.get("ttl_minutes")
    if raw_ttl is not None:
        if isinstance(raw_ttl, bool) or not isinstance(raw_ttl, int):
            return _error("invalid_ttl", "ttl_minutes must be an integer")
        ttl = raw_ttl

    server_url = args.get("server_url")
    if server_url is not None and not isinstance(server_url, str):
        return _error("invalid_server_url", "server_url must be a string")

    try:
        minted = store.mint_pairing_code(ttl_minutes=ttl)
    except store.StoreError as exc:
        return _store_error(exc)

    payload: dict[str, Any] = {
        **minted,
        "deviceLabel": label,
        "expiresInMinutes": ttl,
        "next": (
            f"On the device ({label}) open the Hermes widget, choose Pair, "
            f"and enter {minted.get('code', '')} before it expires."
        ),
    }
    if server_url:
        try:
            from . import cli as _cli  # type: ignore
        except ImportError:  # pragma: no cover - direct import from tests/scripts
            import cli as _cli  # type: ignore
        usable = _cli._valid_server_url(server_url)
        if usable is None:
            return _error(
                "invalid_server_url",
                "server_url must be a private HTTPS URL the phone can reach",
            )
        payload["serverUrl"] = usable
        payload["pairingLine"] = f"{usable}  code={minted.get('code', '')}"
    return _dumps(payload)


def widget_publish(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Publish one validated text/SVG/raster revision without claiming delivery."""
    args = args or {}
    widget_id = args.get("widget_id") or store.DEFAULT_WIDGET_ID
    if not isinstance(widget_id, str):
        return _error("invalid_publication", "widget_id must be a string")
    file_path = args.get("file_path")
    if file_path is None:
        file_path = args.get("image_path", args.get("path"))
    expires_at = args.get("expires_at", args.get("expiresAt"))
    ttl_seconds = args.get("ttl_seconds", args.get("ttlSeconds"))
    max_age_seconds = args.get("max_age_seconds", args.get("maxAgeSeconds"))
    try:
        result = store.put_publication(
            widget_id,
            title=args.get("title"),
            summary=args.get("summary"),
            text=args.get("text"),
            svg=args.get("svg"),
            file_path=file_path,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            max_age_seconds=max_age_seconds,
        )
    except store.StoreError as exc:
        return _store_error(exc)
    return _dumps(
        {
            "ok": True,
            **result,
            "delivery": "pending_periodic_fetch",
            "visibility": "not_claimed",
            "next": (
                "The revision is stored. The phone will fetch it on its next poll; "
                "use widget_status to distinguish downloaded from render_submitted."
            ),
        }
    )


def widget_setup(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Agent tool wrapper for deterministic setup (mirrors hermes widget up).

    Does NOT force HERMES_WIDGET_FORCE_ENV: an unsupported host must come back as the
    structured needs_user_action payload cli._up already produces, not be talked past a
    guard by the tool that is supposed to be reporting the truth.
    """
    args = args or {}
    try:
        from . import cli as _cli  # type: ignore
    except ImportError:
        import cli as _cli  # type: ignore
    host = args.get("host")
    raw_port = args.get("port")
    try:
        port = int(raw_port) if raw_port is not None else None
    except (TypeError, ValueError):
        port = None
    resolved_host = host if isinstance(host, str) and host else None
    resolved_port = port

    class _A:
        widget_id = args.get("widget_id") or store.DEFAULT_WIDGET_ID
        schedule = args.get("schedule") or "every 6h"
        # Omitted host/port preserve the saved binding; never clobber it here.
        host = resolved_host
        port = resolved_port
        server_url = args.get("server_url")
        json = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _cli._up(_A())
    try:
        data = json.loads(buf.getvalue())
        return _dumps(data)
    except Exception:
        output = buf.getvalue()
        if output:
            return output
        return _dumps({"ok": False, "error": "setup_failed"})


def widget_status(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Summarize host, publication, and per-device delivery state honestly."""
    args = args or {}
    widget_id = args.get("widget_id") or store.DEFAULT_WIDGET_ID
    if not isinstance(widget_id, str):
        return _error("invalid_widget_id", "widget_id must be a string")
    try:
        widgets = store.list_widgets()
        devices = store.list_devices()
        publication = store.publication_status(widget_id)
    except store.StoreError as exc:
        return _store_error(exc)

    delivery_state = "empty"
    if publication.get("publication"):
        delivery_state = "not_downloaded"
        if any(item.get("state") == "render_submitted" for item in publication.get("delivery", [])):
            delivery_state = "render_submitted"
        elif any(item.get("state") == "downloaded" for item in publication.get("delivery", [])):
            delivery_state = "downloaded"
    return _dumps(
        {
            "ok": True,
            "defaultWidgetId": store.DEFAULT_WIDGET_ID,
            "widgetId": widget_id,
            "widgets": widgets,
            "deviceCount": len(devices),
            "devices": [
                {
                    "deviceId": d.get("deviceId"),
                    "label": d.get("label"),
                    "revoked": d.get("revoked", False),
                    "lastSeenAt": d.get("lastSeenAt"),
                }
                for d in devices
            ],
            "publication": publication.get("publication"),
            "publicationState": publication.get("state"),
            "stale": publication.get("stale", False),
            "revisions": publication.get("revisions", []),
            "delivery": publication.get("delivery", []),
            "deliveryState": delivery_state,
            "pollIntervalSeconds": publication.get("pollIntervalSeconds"),
            "capabilities": publication.get("capabilities"),
            "dataDir": str(store.data_dir()),
        }
    )
