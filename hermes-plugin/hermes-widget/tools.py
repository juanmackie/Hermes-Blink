"""Agent-facing tool handlers for the hermes-widget plugin.

Handlers return JSON strings rather than raising: a malformed layout or a hit
rate limit must be a normal, model-visible result so one bad push cannot abort
an otherwise healthy agent turn. All persistence goes through store.*.
"""
from __future__ import annotations

import base64
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
    item_id = args.get("item_id", args.get("itemId"))
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
            priority=args.get("priority", "normal"),
            item_id=item_id,
            actions=args.get("actions"),
        )
    except store.StoreError as exc:
        return _store_error(exc)
    return _dumps(
        {
            "ok": True,
            **result,
            "delivery": "pending_wake" if result.get("priority") == "high" else "pending_periodic_fetch",
            "visibility": "not_claimed",
            "next": (
                "The revision is stored. The phone will fetch it on its next poll or "
                "content-free wake; use widget_status to distinguish nudge_sent, fetched, "
                "downloaded, and render_submitted."
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


def _preview_publication(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Prepare a current or proposed publication without writing a revision."""
    widget_id = args.get("widget_id") or store.DEFAULT_WIDGET_ID
    if not isinstance(widget_id, str):
        raise store.PublicationError("widget_id must be a string")
    proposed = args.get("publication")
    if proposed is None:
        publication = store.get_publication(widget_id)
        if publication is None:
            raise store.PublicationError(f"no publication for widget {widget_id!r}")
        return publication, widget_id
    if not isinstance(proposed, dict):
        raise store.PublicationError("publication must be a JSON object")
    if isinstance(proposed.get("content"), dict) and not any(
        key in proposed for key in ("text", "svg", "file_path", "filePath")
    ):
        return {**proposed, "widgetId": widget_id}, widget_id
    prepared = store.prepare_publication(
        title=proposed.get("title"),
        summary=proposed.get("summary"),
        text=proposed.get("text"),
        svg=proposed.get("svg"),
        file_path=proposed.get("file_path", proposed.get("filePath")),
        expires_at=proposed.get("expires_at", proposed.get("expiresAt")),
        ttl_seconds=proposed.get("ttl_seconds", proposed.get("ttlSeconds")),
        max_age_seconds=proposed.get("max_age_seconds", proposed.get("maxAgeSeconds")),
        priority=proposed.get("priority", "normal"),
        item_id=proposed.get("item_id", proposed.get("itemId")),
        actions=proposed.get("actions"),
    )
    if prepared.kind == "text":
        content: dict[str, Any] = {
            "type": "text", "mediaType": "text/plain; charset=utf-8", "text": prepared.text,
        }
    else:
        assert prepared.asset is not None
        content = {
            "type": "image", "mediaType": prepared.asset.media_type,
            "width": prepared.asset.width, "height": prepared.asset.height,
            "bytes": len(prepared.asset.data), "sha256": prepared.asset.sha256,
            "data": base64.b64encode(prepared.asset.data).decode("ascii"),
        }
    return {
        "version": 1,
        "widgetId": widget_id,
        "publicationId": "preview",
        "revision": 0,
        "kind": prepared.kind,
        "title": prepared.title,
        "summary": prepared.summary,
        "publishedAt": store._now(),
        "expiresAt": prepared.expires_at,
        "priority": prepared.priority,
        "itemId": prepared.item_id,
        "actions": list(prepared.actions),
        "content": content,
    }, widget_id


def widget_preview(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Render bounded PNG previews without changing the current publication."""
    args = args or {}
    try:
        publication, widget_id = _preview_publication(args)
        from . import preview  # type: ignore
    except ImportError:  # pragma: no cover
        import preview  # type: ignore
    except store.StoreError as exc:
        return _store_error(exc)
    except ValueError as exc:
        return _error("invalid_preview", str(exc))
    try:
        rendered = preview.render_publication_previews(
            publication,
            sizes=args.get("sizes"),
            inventory=store.list_widget_instances(widget_id),
            asset_loader=lambda asset_id: store.read_asset(asset_id)[1],
        )
    except (ValueError, store.StoreError) as exc:
        return _store_error(exc) if isinstance(exc, store.StoreError) else _error("invalid_preview", str(exc))
    return _dumps({
        "ok": True,
        "widgetId": widget_id,
        "revision": publication.get("revision"),
        "previews": rendered,
        "warnings": store._capacity_warnings_for_publication(publication, widget_id),
        "note": "Preview is advisory; the Android device remains authoritative.",
    })


def widget_read_intents(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    args = args or {}
    widget_id = args.get("widget_id", args.get("widgetId"))
    status = args.get("status")
    limit = args.get("limit", 200)
    if widget_id is not None and not isinstance(widget_id, str):
        return _error("invalid_widget_id", "widget_id must be a string")
    if status is not None and not isinstance(status, str):
        return _error("invalid_status", "status must be a string")
    if isinstance(limit, bool) or not isinstance(limit, int):
        return _error("invalid_limit", "limit must be an integer")
    try:
        return _dumps({
            "intents": store.get_intents(widget_id, status=status, limit=limit),
            "audit": store.get_action_audit(widget_id, limit=limit),
        })
    except store.StoreError as exc:
        return _store_error(exc)


def widget_resolve_intent(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    args = args or {}
    intent_id = args.get("intent_id", args.get("intentId"))
    outcome = args.get("outcome")
    if not isinstance(intent_id, str) or not isinstance(outcome, str):
        return _error("invalid_action_intent", "intent_id and outcome are required")
    try:
        result = store.resolve_intent(
            intent_id,
            outcome,
            result=args.get("result"),
            confirmed=args.get("confirmed", False),
        )
    except store.StoreError as exc:
        return _store_error(exc)
    return _dumps({"ok": True, "intent": result})


def widget_set_quiet_hours(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    args = args or {}
    widget_id = args.get("widget_id", args.get("widgetId"))
    if not isinstance(widget_id, str) or not widget_id:
        return _error("invalid_widget_id", "widget_id is required")
    quiet = args.get("quiet_hours", args.get("quietHours"))
    try:
        return _dumps({"ok": True, **store.set_widget_settings(widget_id, quiet)})
    except store.StoreError as exc:
        return _store_error(exc)


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
        if any(item.get("nudgeStatus") == "sent" for item in publication.get("delivery", [])):
            delivery_state = "nudge_sent"
        if any(
            any(receipt.get("state") == "fetched" for receipt in item.get("receipts", []))
            for item in publication.get("delivery", [])
        ):
            delivery_state = "fetched"
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
                    "pushEndpointRegistered": d.get("pushEndpointRegistered", False),
                    "lastSeenAt": d.get("lastSeenAt"),
                }
                for d in devices
            ],
            "publication": publication.get("publication"),
            "publicationState": publication.get("state"),
            "stale": publication.get("stale", False),
            "revisions": publication.get("revisions", []),
            "revisionHistory": publication.get("revisionHistory", {}),
            "warnings": publication.get("warnings", []),
            "delivery": publication.get("delivery", []),
            "deliveryState": delivery_state,
            "inventory": publication.get("inventory", []),
            "intents": publication.get("intents", []),
            "actionAudit": publication.get("actionAudit", []),
            "pollIntervalSeconds": publication.get("pollIntervalSeconds"),
            "capabilities": publication.get("capabilities"),
            "dataDir": str(store.data_dir()),
        }
    )
