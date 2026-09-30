"""Widget event intake and bounded telemetry queries."""
from __future__ import annotations

import json
import logging
from typing import Any

try:
    from .actions import post_action_event
    from .db import _LOCK, _connect
    from .errors import StoreError
    from .publication import ACTION_KINDS
    from .timeutil import _now
    from .store_utils import _short_str
except ImportError:  # direct import from scripts/tests
    from actions import post_action_event  # type: ignore
    from db import _LOCK, _connect  # type: ignore
    from errors import StoreError  # type: ignore
    from publication import ACTION_KINDS  # type: ignore
    from timeutil import _now  # type: ignore
    from store_utils import _short_str  # type: ignore

logger = logging.getLogger(__name__)

def post_event(
    widget_id: str,
    device_id: str,
    event: str,
    payload: dict | None,
    *,
    instance_id: Any = None,
) -> int:
    if event in ACTION_KINDS:
        result = post_action_event(widget_id, device_id, event, payload)
        return int(result["eventId"])
    # E: durable, deduplicated tap storage â€” clientEventId + stable itemId are
    # preserved; duplicate submissions collapse to one state transition.
    if not isinstance(event, str) or not event or len(event) > 200:
        raise StoreError("event must be a non-empty string of at most 200 characters")
    if payload is not None and not isinstance(payload, dict):
        raise StoreError("event payload must be a JSON object")
    client_event_id = None
    if payload is not None:
        client_event_id = payload.get("clientEventId")
        if client_event_id is not None and (
            not isinstance(client_event_id, str) or not client_event_id or len(client_event_id) > 160
        ):
            raise StoreError("clientEventId must be a non-empty string of at most 160 characters")
        if not isinstance(client_event_id, str):
            client_event_id = None
    payload_json = json.dumps(payload, separators=(",", ":")) if payload is not None else None
    instance = _short_str(instance_id, 64)
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Deduplicate on (widget_id, device_id, clientEventId) when present
            if client_event_id is not None:
                existing = conn.execute(
                    "SELECT id FROM events WHERE widget_id = ? AND device_id = ? AND client_event_id = ?",
                    (widget_id, device_id, client_event_id),
                ).fetchone()
                if existing is not None:
                    return int(existing["id"])
            cur = conn.execute(
                "INSERT INTO events (widget_id, device_id, event, payload, client_event_id, instance_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (widget_id, device_id, event, payload_json, client_event_id, instance, _now()),
            )
            conn.commit()
            event_id = cur.lastrowid
            if event_id is None:
                raise StoreError("event insert did not return an id")
            return int(event_id)
        finally:
            conn.close()

def get_events(
    since: str | None = None,
    widget_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    try:
        limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        limit = 200
    clauses: list[str] = []
    params: list[Any] = []
    if since:
        clauses.append("created_at > ?")
        params.append(since)
    if widget_id:
        clauses.append("widget_id = ?")
        params.append(widget_id)
    params.append(limit)
    conn = _connect()
    try:
        if since and widget_id:
            query = (
                "SELECT id, widget_id, device_id, event, payload, instance_id, created_at "
                "FROM events WHERE created_at > ? AND widget_id = ? "
                "ORDER BY id ASC LIMIT ?"
            )
        elif since:
            query = (
                "SELECT id, widget_id, device_id, event, payload, instance_id, created_at "
                "FROM events WHERE created_at > ? ORDER BY id ASC LIMIT ?"
            )
        elif widget_id:
            query = (
                "SELECT id, widget_id, device_id, event, payload, instance_id, created_at "
                "FROM events WHERE widget_id = ? ORDER BY id DESC LIMIT ?"
            )
        else:
            query = (
                "SELECT id, widget_id, device_id, event, payload, instance_id, created_at "
                "FROM events ORDER BY id DESC LIMIT ?"
            )
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    events = []
    intent_by_event: dict[int, dict] = {}
    if rows:
        conn = _connect()
        try:
            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            intent_rows = conn.execute(
                f"SELECT event_id, intent_id, status, result FROM action_intents "
                f"WHERE event_id IN ({placeholders})", ids
            ).fetchall()
        finally:
            conn.close()
        intent_by_event = {
            int(row["event_id"]): {
                "intentId": row["intent_id"],
                "status": row["status"],
                "result": row["result"],
            }
            for row in intent_rows
        }
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if row["payload"] else None
        except (TypeError, ValueError) as exc:
            logger.warning("ignoring malformed widget event payload %s", exc)
            payload = None
        event = {
            "id": row["id"],
            "widgetId": row["widget_id"],
            "deviceId": row["device_id"],
            "event": row["event"],
            # Which widget instance was tapped. null on rows written before the column
            # existed, and on any client that does not send it.
            "instanceId": row["instance_id"] if "instance_id" in row.keys() else None,
            "payload": payload,
            "createdAt": row["created_at"],
        }
        if int(row["id"]) in intent_by_event:
            event["intent"] = intent_by_event[int(row["id"])]
        events.append(event)
    return events
