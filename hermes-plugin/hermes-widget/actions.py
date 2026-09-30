"""Durable device-action intents and their audit trail."""
from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from .db import _LOCK, _connect
    from .errors import ActionIntentError, RateLimitError, StoreError
    from .publication import (
        ACTION_CLASSES, ACTION_KINDS, MAX_ACTION_PAYLOAD_BYTES,
        SENSITIVE_ACTION_CLASSES,
    )
    from .timeutil import _iso_from_epoch, _now
except ImportError:  # direct import from scripts/tests
    from db import _LOCK, _connect  # type: ignore
    from errors import ActionIntentError, RateLimitError, StoreError  # type: ignore
    from publication import (  # type: ignore
        ACTION_CLASSES, ACTION_KINDS, MAX_ACTION_PAYLOAD_BYTES,
        SENSITIVE_ACTION_CLASSES,
    )
    from timeutil import _iso_from_epoch, _now  # type: ignore

ACTION_INTENT_TTL_SECONDS = 7 * 24 * 3600
ACTION_RATE_WINDOW_SECONDS = 3600
ACTION_MAX_PER_WINDOW = 30

def _json_object(value: Any, name: str, limit: int = MAX_ACTION_PAYLOAD_BYTES) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ActionIntentError(f"{name} must be a JSON object")
    try:
        size = len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ActionIntentError(f"{name} is not JSON-safe") from exc
    if size > limit:
        raise ActionIntentError(f"{name} exceeds the {limit}-byte limit")
    return value

def _expire_stale_intents(conn: sqlite3.Connection) -> None:
    now = _now()
    stale = conn.execute(
        "SELECT intent_id, widget_id, device_id, item_id, action_class, source_revision "
        "FROM action_intents WHERE status IN ('queued', 'awaiting_confirmation') AND expires_at <= ?",
        (now,),
    ).fetchall()
    for row in stale:
        conn.execute(
            "INSERT INTO action_audit "
            "(intent_id, widget_id, device_id, item_id, action_class, revision, event, outcome, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'expire', 'expired', 'intent expired before agent action', ?)",
            (row["intent_id"], row["widget_id"], row["device_id"], row["item_id"],
             row["action_class"], row["source_revision"], now),
        )
    conn.execute(
        "UPDATE action_intents SET status='expired', updated_at=? "
        "WHERE status IN ('queued', 'awaiting_confirmation') AND expires_at <= ?",
        (now, now),
    )

def post_action_event(
    widget_id: str,
    device_id: str,
    event: str,
    payload: dict | None,
    *,
    revision: int | None = None,
    item_id: str | None = None,
    action_class: str | None = None,
) -> dict:
    """Durably record a tap and enqueue â€” never execute â€” an allowlisted intent."""
    if event not in ACTION_KINDS:
        raise ActionIntentError(f"action kind must be one of {list(ACTION_KINDS)}")
    body = _json_object(payload, "payload")
    resolved_item = item_id or body.get("itemId") or body.get("item_id")
    if not isinstance(resolved_item, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", resolved_item):
        raise ActionIntentError("action itemId is required and must be stable")
    requested_class = action_class or body.get("actionClass") or body.get("action_class")
    if requested_class is not None and requested_class not in ACTION_CLASSES:
        raise ActionIntentError(f"actionClass must be one of {list(ACTION_CLASSES)}")
    resolved_class = requested_class or "reversible"
    client_event_id = body.get("clientEventId")
    if client_event_id is not None and (
        not isinstance(client_event_id, str) or not client_event_id or len(client_event_id) > 160
    ):
        raise ActionIntentError("clientEventId must be a bounded string")
    if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
        raise ActionIntentError("revision must be a non-negative integer")
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _expire_stale_intents(conn)
            device = conn.execute(
                "SELECT device_id FROM devices WHERE device_id = ? AND revoked = 0", (device_id,)
            ).fetchone()
            if device is None:
                raise ActionIntentError("device is revoked or unknown")
            publication = None
            row = conn.execute(
                "SELECT payload_json, revision FROM publications WHERE widget_id = ?", (widget_id,)
            ).fetchone()
            if row:
                try:
                    publication = json.loads(row["payload_json"])
                except (TypeError, ValueError) as exc:
                    raise StoreError("stored publication is corrupt") from exc
            current_revision = int(row["revision"]) if row else None
            selected_revision = revision if revision is not None else current_revision
            if selected_revision is None:
                selected_revision = None
            if selected_revision is None:
                raise ActionIntentError("no publication revision is available for this action")
            if current_revision is not None and selected_revision != current_revision:
                raise ActionIntentError("action revision is not current")
            action_policy = next((
                action for action in (publication or {}).get("actions", [])
                if isinstance(action, dict) and action.get("itemId") == resolved_item
            ), None)
            if action_policy is None:
                raise ActionIntentError("action itemId is not an action in this publication")
            declared_event = action_policy.get("kind")
            if declared_event != event:
                raise ActionIntentError("action kind does not match the current publication")
            declared_class = action_policy.get("actionClass", "reversible")
            if requested_class is not None and requested_class != declared_class:
                raise ActionIntentError("actionClass does not match the current publication")
            resolved_class = declared_class
            action_policy_requires_confirmation = bool(
                action_policy.get("confirmOnDevice") is True
            )
            if client_event_id:
                prior = conn.execute(
                    "SELECT id FROM events WHERE widget_id = ? AND device_id = ? AND client_event_id = ?",
                    (widget_id, device_id, client_event_id),
                ).fetchone()
                if prior is not None:
                    intent = conn.execute(
                        "SELECT * FROM action_intents WHERE event_id = ?", (prior["id"],)
                    ).fetchone()
                    if intent is not None:
                        return {"eventId": int(prior["id"]), "intent": _intent_dict(intent), "duplicate": True}
                    return {"eventId": int(prior["id"]), "duplicate": True}
            cutoff = _iso_from_epoch(time.time() - ACTION_RATE_WINDOW_SECONDS)
            count = conn.execute(
                "SELECT COUNT(*) FROM action_audit WHERE device_id = ? "
                "AND event IN ('approve', 'snooze', 'open') AND created_at > ?",
                (device_id, cutoff),
            ).fetchone()[0]
            if int(count) >= ACTION_MAX_PER_WINDOW:
                raise RateLimitError(f"action rate limit exceeded for device {device_id!r}")
            now_dt = datetime.now(timezone.utc)
            expires = (now_dt + timedelta(seconds=ACTION_INTENT_TTL_SECONDS)).isoformat().replace("+00:00", "Z")
            payload_for_storage = dict(body)
            payload_for_storage.update({
                "itemId": resolved_item,
                "actionClass": resolved_class,
                "revision": selected_revision,
                "confirmOnDevice": action_policy_requires_confirmation,
                "deviceConfirmed": False,
            })
            event_payload = json.dumps(payload_for_storage, separators=(",", ":"), ensure_ascii=False)
            cur = conn.execute(
                "INSERT INTO events (widget_id, device_id, event, payload, client_event_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (widget_id, device_id, event, event_payload, client_event_id, _now()),
            )
            event_id = int(cur.lastrowid or 0)
            if not event_id:
                raise StoreError("event insert did not return an id")
            intent_id = "intent_" + uuid.uuid4().hex
            needs_confirmation = (
                action_policy_requires_confirmation or resolved_class in SENSITIVE_ACTION_CLASSES
            )
            status = "awaiting_confirmation" if needs_confirmation else "queued"
            conn.execute(
                "INSERT INTO action_intents "
                "(intent_id, widget_id, device_id, event_id, item_id, action_class, status, source_revision, payload_json, result, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    intent_id, widget_id, device_id, event_id, resolved_item, resolved_class,
                    status, selected_revision, event_payload, _now(), _now(), expires,
                ),
            )
            conn.execute(
                "INSERT INTO action_audit "
                "(intent_id, widget_id, device_id, item_id, action_class, revision, event, outcome, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (intent_id, widget_id, device_id, resolved_item, resolved_class, selected_revision, event, status, _now()),
            )
            row = conn.execute("SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)).fetchone()
            conn.commit()
            return {"eventId": event_id, "intent": _intent_dict(row), "duplicate": False}
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            raise
        finally:
            conn.close()

def _intent_dict(row: sqlite3.Row) -> dict:
    try:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
    except (TypeError, ValueError):
        payload = {}
    return {
        "intentId": row["intent_id"],
        "widgetId": row["widget_id"],
        "deviceId": row["device_id"],
        "eventId": int(row["event_id"]),
        "itemId": row["item_id"],
        "actionClass": row["action_class"],
        "status": row["status"],
        "sourceRevision": int(row["source_revision"]),
        "payload": payload,
        "result": row["result"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "expiresAt": row["expires_at"],
    }

def get_intents(
    widget_id: str | None = None,
    *,
    status: str | None = None,
    limit: int = 200,
) -> list[dict]:
    try:
        limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        limit = 200
    with _LOCK:
        conn = _connect()
        try:
            _expire_stale_intents(conn)
            clauses: list[str] = []
            params: list[Any] = []
            if widget_id:
                clauses.append("widget_id = ?")
                params.append(widget_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM action_intents{where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
            conn.commit()
        finally:
            conn.close()
    return [_intent_dict(row) for row in rows]

def get_action_audit(widget_id: str | None = None, *, limit: int = 200) -> list[dict]:
    try:
        limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        limit = 200
    conn = _connect()
    try:
        if widget_id:
            rows = conn.execute(
                "SELECT intent_id, device_id, item_id, action_class, revision, event, outcome, detail, created_at "
                "FROM action_audit WHERE widget_id = ? ORDER BY id DESC LIMIT ?",
                (widget_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT intent_id, device_id, item_id, action_class, revision, event, outcome, detail, created_at "
                "FROM action_audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    finally:
        conn.close()
    return [
        {
            "intentId": row["intent_id"],
            "deviceId": row["device_id"],
            "itemId": row["item_id"],
            "actionClass": row["action_class"],
            "revision": row["revision"],
            "event": row["event"],
            "outcome": row["outcome"],
            "detail": row["detail"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]

def confirm_action_intent(intent_id: str, device_id: str) -> dict:
    """Record explicit approval from the paired device that owns an intent."""
    if not isinstance(intent_id, str) or not intent_id or len(intent_id) > 160:
        raise ActionIntentError("intent_id is required")
    if not isinstance(device_id, str) or not device_id:
        raise ActionIntentError("device_id is required")
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _expire_stale_intents(conn)
            row = conn.execute(
                "SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                raise ActionIntentError("intent does not exist")
            if row["device_id"] != device_id:
                raise ActionIntentError("intent belongs to a different device")
            if row["status"] == "queued":
                conn.commit()
                return _intent_dict(row)
            if row["status"] != "awaiting_confirmation":
                raise ActionIntentError("intent is not awaiting device confirmation")
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            payload["deviceConfirmed"] = True
            now = _now()
            conn.execute(
                "UPDATE action_intents SET status='queued', payload_json=?, updated_at=? "
                "WHERE intent_id=? AND status='awaiting_confirmation'",
                (json.dumps(payload, separators=(",", ":"), ensure_ascii=False), now, intent_id),
            )
            conn.execute(
                "INSERT INTO action_audit "
                "(intent_id, widget_id, device_id, item_id, action_class, revision, event, outcome, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'device_confirm', 'queued', NULL, ?)",
                (intent_id, row["widget_id"], device_id, row["item_id"], row["action_class"],
                 row["source_revision"], now),
            )
            updated = conn.execute(
                "SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            conn.commit()
            return _intent_dict(updated)
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            raise
        finally:
            conn.close()

def resolve_intent(
    intent_id: str,
    outcome: str,
    *,
    result: str | None = None,
) -> dict:
    """Record an agent's terminal decision; this function never runs the operation."""
    if not isinstance(intent_id, str) or not intent_id or len(intent_id) > 160:
        raise ActionIntentError("intent_id is required")
    if outcome not in {"applied", "declined", "held", "expired"}:
        raise ActionIntentError("outcome must be applied, declined, held, or expired")
    if result is not None:
        if not isinstance(result, str) or len(result) > 2000:
            raise ActionIntentError("result must be a string of at most 2000 characters")
    with _LOCK:
        conn = _connect()
        try:
            _expire_stale_intents(conn)
            row = conn.execute("SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)).fetchone()
            if row is None:
                raise ActionIntentError("intent does not exist")
            try:
                stored_payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                stored_payload = {}
            needs_confirmation = (
                row["action_class"] in SENSITIVE_ACTION_CLASSES
                or stored_payload.get("confirmOnDevice") is True
            )
            if (
                needs_confirmation
                and outcome == "applied"
                and stored_payload.get("deviceConfirmed") is not True
            ):
                raise ActionIntentError("sensitive action requires confirmation from its paired device")
            if row["status"] in {"applied", "declined", "expired"}:
                return _intent_dict(row)
            now = _now()
            conn.execute(
                "UPDATE action_intents SET status=?, result=?, updated_at=? WHERE intent_id=?",
                (outcome, result, now, intent_id),
            )
            conn.execute(
                "INSERT INTO action_audit "
                "(intent_id, widget_id, device_id, item_id, action_class, revision, event, outcome, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'resolve', ?, ?, ?)",
                (intent_id, row["widget_id"], row["device_id"], row["item_id"], row["action_class"],
                 row["source_revision"], outcome, result, now),
            )
            updated = conn.execute("SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)).fetchone()
            conn.commit()
            return _intent_dict(updated)
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            raise
        finally:
            conn.close()
