"""Agent-facing tool handlers for the hermes-widget plugin.

Handlers return JSON strings rather than raising: a malformed layout or a hit
rate limit must be a normal, model-visible result so one bad push cannot abort
an otherwise healthy agent turn. All persistence goes through store.*.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import store

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


def widget_update(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Validate and store a complete v2 layout envelope."""
    args = args or {}
    widget_id = args.get("widget_id") or store.DEFAULT_WIDGET_ID
    if not isinstance(widget_id, str):
        return _error("invalid_layout", "widget_id must be a string")

    layout = args.get("layout")
    if isinstance(layout, str):
        # The model sometimes serializes the object; accept either shape.
        try:
            layout = json.loads(layout)
        except (TypeError, ValueError) as exc:
            return _error("invalid_layout", f"layout is not valid JSON: {exc}")
    if layout is None:
        return _error("invalid_layout", "layout is required")

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
    """Mint a short-lived pairing code and tell the model what to do next."""
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

    try:
        minted = store.mint_pairing_code(ttl_minutes=ttl)
    except store.StoreError as exc:
        return _store_error(exc)

    return _dumps(
        {
            **minted,
            "deviceLabel": label,
            "expiresInMinutes": ttl,
            "next": (
                f"On the device ({label}) open the Hermes widget, choose Pair, "
                f"and enter {minted.get('code', '')} before it expires."
            ),
        }
    )


def widget_setup(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Agent tool wrapper for deterministic setup (mirrors hermes widget up)."""
    args = args or {}
    import os as _os
    _os.environ.setdefault("HERMES_WIDGET_FORCE_ENV", "supported")
    try:
        from . import cli as _cli  # type: ignore
    except ImportError:
        import cli as _cli  # type: ignore
    class _A:
        widget_id = args.get("widget_id") or store.DEFAULT_WIDGET_ID
        schedule = args.get("schedule") or "every 6h"
        host = "127.0.0.1"
        port = 8788
        json = True
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _cli._up(_A())
    try:
        data = json.loads(buf.getvalue())
        return _dumps(data)
    except Exception:
        return buf.getvalue() or _dumps({"ok": False, "error": "setup_failed"})


def widget_pair(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Mint a QR-ready pairing payload (single-use 10-min)."""
    args = args or {}
    server_url = args.get("server_url") or "http://127.0.0.1:8788"
    if not isinstance(server_url, str):
        return _error("invalid_server_url", "server_url must be a string")
    label = args.get("device_label") or "unknown"
    try:
        minted = store.mint_pairing_code()
    except store.StoreError as exc:
        return _store_error(exc)
    code = minted["code"]
    return _dumps({
        **minted,
        "serverUrl": server_url,
        "qrPayload": json.dumps({"url": server_url, "code": code, "ttl": 600}, separators=(",", ":")),
        "samePhoneLink": f"{server_url.rstrip('/')}/v1/pair?code={code}",
        "deviceLabel": label,
    })


def widget_status(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Summarize widget/device state for the slash command and diagnostics."""
    try:
        widgets = store.list_widgets()
        devices = store.list_devices()
    except store.StoreError as exc:
        return _store_error(exc)

    return _dumps(
        {
            "ok": True,
            "defaultWidgetId": store.DEFAULT_WIDGET_ID,
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
            "dataDir": str(store.data_dir()),
        }
    )
