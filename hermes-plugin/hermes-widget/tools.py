"""Agent-facing tool handlers for the hermes-widget plugin.

Handlers return JSON strings rather than raising. Errors remain model-visible so
one bad request cannot abort an otherwise healthy agent turn. All persistence
goes through store.*.
"""
from __future__ import annotations

import json
import logging
import base64
from pathlib import Path
from typing import Any

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import store, watches
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import store  # type: ignore
    import watches  # type: ignore

logger = logging.getLogger(__name__)

_DEFAULT_EVENT_LIMIT = 200


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _error(code: str, detail: str) -> str:
    return _dumps({"error": code, "detail": detail})


def _store_error(exc: store.StoreError) -> str:
    # Every StoreError subclass carries a stable .code for the client and model.
    return _error(exc.code, str(exc))


def widget_list(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Return configured and published widget ids."""
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
    ticker = args.get("ticker")
    if ticker is not None and not any(args.get(name) is not None for name in ("text", "svg", "presentation")) and file_path is None:
        if not isinstance(ticker, dict):
            return _error("invalid_publication", "ticker must be an object")
        try:
            result = store.put_ticker(
                widget_id,
                title=ticker.get("title", ""),
                summary=ticker.get("summary", ""),
                text=ticker.get("text"),
                svg=ticker.get("svg"),
                file_path=ticker.get("file_path", ticker.get("filePath")),
                expires_at=ticker.get("expires_at", ticker.get("expiresAt")),
                ttl_seconds=ticker.get("ttl_seconds", ticker.get("ttlSeconds")),
                max_age_seconds=ticker.get("max_age_seconds", ticker.get("maxAgeSeconds")),
                priority=ticker.get("priority", "normal"),
                item_id=ticker.get("item_id", ticker.get("itemId")),
                actions=ticker.get("actions"),
                provenance=ticker.get("provenance"),
                pinned=bool(ticker.get("pinned", False)),
                rotate=bool(ticker.get("rotate", False)),
            )
        except store.StoreError as exc:
            return _store_error(exc)
    else:
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
                provenance=args.get("provenance"),
                dark_palette=bool(args.get("dark_palette", args.get("darkPalette", False))),
                variants=args.get("variants"),
                presentation=args.get("presentation"),
                visual_variants=args.get("visual_variants", args.get("visualVariants")),
                work_context=args.get("work_context"),
                refresh_id=args.get("refresh_id"),
                ticker=ticker,
            )
        except store.StoreError as exc:
            return _store_error(exc)
    return _dumps(
        {
            "ok": True,
            **result,
            "delivery": "unchanged" if result.get("unchanged") else "wake_or_poll_pending",
            "visibility": "not_claimed",
            "next": (
                "The revision is stored. The phone will fetch it on its next poll or "
                "content-free wake; use widget_status to distinguish nudge_sent, fetched, "
                "downloaded, and render_submitted."
            ),
        }
    )
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
    try:
        from . import preview
    except ImportError:  # pragma: no cover - direct import from tests/scripts
        import preview  # type: ignore
    return preview.build_preview_publication(proposed, widget_id), widget_id


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
            palette=args.get("palette", "light"),
            font_scale=args.get("font_scale", 1.0),
        )
    except (ValueError, store.StoreError, ImportError, OSError) as exc:
        return _store_error(exc) if isinstance(exc, store.StoreError) else _error("invalid_preview", str(exc))
    last_render = store._last_render_metrics()
    for item in rendered:
        if item.get("renderer") == "svg-renderer-unavailable" and last_render:
            item["note"] = (
                "No local SVG renderer (CairoSVG/libcairo); "
                f"last device render {last_render['width']}×{last_render['height']}px"
            )
    images = []
    directory = store.data_dir() / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    for item in rendered:
        data = item.pop("data")
        path = directory / (item["sha256"] + ".png")
        path.write_bytes(base64.b64decode(data))
        item["path"] = str(path.resolve())
        images.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + data}})
    retained = {Path(item["path"]) for item in rendered}
    older = sorted((p for p in directory.glob("*.png") if p not in retained), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in older[32:]:
        path.unlink(missing_ok=True)
    result = _dumps({
        "ok": True,
        "widgetId": widget_id,
        "revision": publication.get("revision"),
        "previews": rendered,
        "warnings": store._capacity_warnings_for_publication(publication, widget_id),
        "note": "Preview is advisory; the Android device remains authoritative.",
    })
    try:
        from tools.vision_tools import _should_use_native_vision_fast_path
        native = _should_use_native_vision_fast_path()
    except (ImportError, AttributeError):
        native = False
    if native:
        return {"_multimodal": True, "content": [{"type": "text", "text": result}, *images], "text_summary": result}
    return result


def widget_finish_refresh(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    args = args or {}
    try:
        from . import refresh
    except ImportError:
        import refresh
    try:
        widget_id = args.get("widget_id") or store.DEFAULT_WIDGET_ID
        if args.get("outcome") not in ("unchanged", "failed"):
            return _error("invalid_outcome", "Use widget_publish for published outcomes; finish accepts unchanged or failed")
        refresh.finish(widget_id,args.get("refresh_id"),args.get("outcome"),args.get("reason"))
        return _dumps({"ok": True, "refreshOutcomes": refresh.outcomes(widget_id)})
    except store.StoreError as exc:
        return _store_error(exc)


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
    if "confirmed" in args:
        return _error(
            "device_confirmation_required",
            "confirmation must come from the paired device, not the agent",
        )
    try:
        result = store.resolve_intent(
            intent_id,
            outcome,
            result=args.get("result"),
        )
    except store.StoreError as exc:
        return _store_error(exc)
    return _dumps({"ok": True, "intent": result})


def widget_ask(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    args = args or {}
    try:
        return _dumps({"ok": True, "question": store.ask_question(
            args.get("widget_id", store.DEFAULT_WIDGET_ID),
            args.get("prompt", ""),
            item_id=args.get("item_id", args.get("itemId")),
            revision=args.get("revision"),
        )})
    except store.StoreError as exc:
        return _store_error(exc)


def widget_read_questions(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    args = args or {}
    try:
        return _dumps({"questions": store.list_questions(
            args.get("widget_id", args.get("widgetId")),
            status=args.get("status"),
            limit=args.get("limit", 100),
        )})
    except store.StoreError as exc:
        return _store_error(exc)


def _watch_create(args: dict[str, Any]) -> str:
    args = args or {}
    try:
        result = watches.create_watch(
            args.get("widget_id", store.DEFAULT_WIDGET_ID),
            name=args.get("name", ""),
            condition=args.get("condition"),
            payload=args.get("payload"),
            cadence_seconds=args.get("cadence_seconds", 3600),
            quiet_hours=args.get("quiet_hours"),
            max_per_day=args.get("max_per_day", 1),
            expires_at=args.get("expires_at"),
        )
    except (watches.store.StoreError, ValueError) as exc:
        return _error("invalid_watch", str(exc))
    return _dumps({"ok": True, "watch": result})


def _watch_list(args: dict[str, Any]) -> str:
    try:
        return _dumps({"watches": watches.list_watches(args.get("widget_id"), enabled=args.get("enabled"))})
    except watches.store.StoreError as exc:
        return _store_error(exc)


def _watch_pause(args: dict[str, Any]) -> str:
    watch_id = args.get("watch_id")
    if not isinstance(watch_id, str) or not watch_id:
        return _error("invalid_watch", "watch_id is required")
    result = watches.pause_watch(watch_id, paused=args.get("paused", True))
    if result is None:
        return _error("unknown_watch", "watch does not exist")
    return _dumps({"ok": True, "watch": result})


def _watch_tick(args: dict[str, Any]) -> str:
    try:
        return _dumps({"ok": True, "results": watches.tick_watches(sources=args.get("sources"))})
    except watches.store.StoreError as exc:
        return _store_error(exc)


def widget_watch(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    """Dispatch the compact watch tool to one bounded operation."""
    args = args or {}
    handlers = {
        "create": _watch_create,
        "list": _watch_list,
        "pause": _watch_pause,
        "tick": _watch_tick,
    }
    operation = args.get("operation")
    if not isinstance(operation, str):
        return _error("invalid_watch_operation", "operation must be create, list, pause, or tick")
    handler = handlers.get(operation)
    if handler is None:
        return _error("invalid_watch_operation", "operation must be create, list, pause, or tick")
    return handler(args)


def widget_wake_test(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
    args = args or {}
    widget_id = args.get("widget_id", args.get("widgetId")) or store.DEFAULT_WIDGET_ID
    try:
        return _dumps(store.wake_test(widget_id))
    except store.StoreError as exc:
        return _store_error(exc)


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
        consume_updates = args.get("consume_update_requests", False)
        if not isinstance(consume_updates, bool):
            return _error("invalid_consume_update_requests", "consume_update_requests must be boolean")
        summary = args.get("summary", True)
        if not isinstance(summary, bool):
            return _error("invalid_summary", "summary must be boolean")
        limit = args.get("limit", 10 if summary else 50)
        if isinstance(limit, bool) or not isinstance(limit, int):
            return _error("invalid_limit", "limit must be an integer")
        limit = max(1, min(limit, 10 if summary else 100))
        publication = store.publication_status(
            widget_id,
            consume_update_requests_now=False,
            limit=limit,
            summary=summary,
        )
        try:
            from . import refresh
        except ImportError:
            import refresh
        lease = refresh.claim(widget_id) if consume_updates else None
        if lease:
            requests = store.list_update_requests(widget_id, limit=200)
            publication["updateRequests"] = requests[:limit]
            publication["newlyConsumedUpdateRequests"] = [r for r in requests if r["requestId"] in lease["requests"]]
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
    result_limits = publication.get("resultLimits", {})
    result_limits.setdefault("totals", {})["deviceDetails"] = len(devices)
    result_limits.setdefault("truncated", {})["deviceDetails"] = len(devices) > limit
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
                    # Which build last talked to this server, so "the phone shows X but
                    # the widget looks like Y" is answerable without asking the user.
                    "appVersion": d.get("appVersion"),
                    "appBuildCode": d.get("appBuildCode"),
                    "appBuildSha": d.get("appBuildSha"),
                    "osSdk": d.get("osSdk"),
                }
                for d in devices[:limit]
            ],
            "publication": publication.get("publication"),
            "workContext": refresh.read_context(widget_id),
            "refreshLease": lease,
            "refreshOutcomes": refresh.outcomes(widget_id),
            "publicationState": publication.get("state"),
            "stale": publication.get("stale", False),
            "revisions": publication.get("revisions", []),
            "revisionHistory": publication.get("revisionHistory", {}),
            "warnings": publication.get("warnings", []),
            "wake": publication.get("wake", {"devices": [], "registeredCount": 0}),
            "attention": publication.get("attention", {}),
            "updateRequests": publication.get("updateRequests", []),
            "newlyConsumedUpdateRequests": publication.get("newlyConsumedUpdateRequests", []),
            # Refused device requests: the answer when a tap produced no row.
            "rejections": publication.get("rejections", {}),
            "delivery": publication.get("delivery", []),
            "deliveryState": delivery_state,
            "inventory": publication.get("inventory", []),
            "intents": publication.get("intents", []),
            "actionAudit": publication.get("actionAudit", []),
            "questions": publication.get("questions", []),
            "pollIntervalSeconds": publication.get("pollIntervalSeconds"),
            "capabilities": publication.get("capabilities"),
            "resultLimits": result_limits,
            "dataDir": str(store.data_dir()),
        }
    )
