"""Persistent push budgets, endpoint registration, and device push state."""
from __future__ import annotations

from typing import Any

try:
    from .db import _LOCK, _connect
    from .errors import RateLimitError, StoreError
    from . import push as _push
    from .timeutil import _epoch_now, _hash_token, _now
except ImportError:  # direct import from scripts/tests
    from db import _LOCK, _connect  # type: ignore
    from errors import RateLimitError, StoreError  # type: ignore
    import push as _push  # type: ignore
    from timeutil import _epoch_now, _hash_token, _now  # type: ignore

DEFAULT_WIDGET_ID = "hermes-brief"
PUSH_WINDOW_SECONDS = 3600
PUSH_MAX_PER_WINDOW = 30
PUSH_STATES = ("unknown", "unavailable", "registering", "registered", "failed", "unregistered")

def check_push_rate(widget_id: str) -> None:
    now = _epoch_now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cutoff = now - PUSH_WINDOW_SECONDS
            # Pruning globally keeps rows for inactive widget ids from accumulating.
            conn.execute("DELETE FROM push_rate_limits WHERE occurred_at <= ?", (cutoff,))
            count = int(conn.execute(
                "SELECT COUNT(*) FROM push_rate_limits WHERE widget_id = ? AND occurred_at > ?",
                (widget_id, cutoff),
            ).fetchone()[0])
            if count >= PUSH_MAX_PER_WINDOW:
                conn.rollback()
                raise RateLimitError(
                    f"push rate limit exceeded for {widget_id!r}: "
                    f"max {PUSH_MAX_PER_WINDOW} per {PUSH_WINDOW_SECONDS}s"
                )
            conn.execute(
                "INSERT INTO push_rate_limits(widget_id, occurred_at) VALUES (?, ?)",
                (widget_id, now),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

def set_device_push_endpoint(device_id: str, endpoint: Any) -> dict:
    """Register or clear the device's private UnifiedPush endpoint.

    Only a hash and timestamps are returned.  The raw endpoint remains in the
    database solely long enough to deliver a wake and is never exposed by
    status/events.
    """
    if not isinstance(device_id, str) or not device_id:
        raise StoreError("device_id is required")
    if endpoint is None or endpoint == "":
        with _LOCK:
            conn = _connect()
            try:
                conn.execute("DELETE FROM device_push_endpoints WHERE device_id = ?", (device_id,))
                conn.execute(
                    "INSERT INTO device_push_state "
                    "(device_id, state, distributor_present, failure_reason, updated_at) "
                    "VALUES (?, 'unregistered', NULL, NULL, ?) ON CONFLICT(device_id) DO UPDATE SET "
                    "state='unregistered', failure_reason=NULL, updated_at=excluded.updated_at",
                    (device_id, _now()),
                )
                conn.commit()
            finally:
                conn.close()
        return {"deviceId": device_id, "pushEndpointRegistered": False}
    try:
        clean = _push.validate_endpoint(endpoint)
    except ValueError as exc:
        raise StoreError(str(exc)) from exc
    now = _now()
    endpoint_hash = _hash_token(clean)
    with _LOCK:
        conn = _connect()
        try:
            active = conn.execute(
                "SELECT device_id FROM devices WHERE device_id = ? AND revoked = 0", (device_id,)
            ).fetchone()
            if active is None:
                raise StoreError("device is revoked or unknown")
            conn.execute(
                "INSERT INTO device_push_endpoints (device_id, endpoint, endpoint_hash, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET "
                "endpoint=excluded.endpoint, endpoint_hash=excluded.endpoint_hash, updated_at=excluded.updated_at",
                (device_id, clean, endpoint_hash, now),
            )
            conn.execute(
                "INSERT INTO device_push_state "
                "(device_id, state, distributor_present, failure_reason, updated_at) "
                "VALUES (?, 'registered', 1, NULL, ?) ON CONFLICT(device_id) DO UPDATE SET "
                "state='registered', distributor_present=1, failure_reason=NULL, updated_at=excluded.updated_at",
                (device_id, now),
            )
            conn.commit()
        finally:
            conn.close()
    return {"deviceId": device_id, "pushEndpointRegistered": True, "updatedAt": now}

def set_device_push_state(
    device_id: str,
    state: Any,
    *,
    distributor_present: Any = None,
    failure_reason: Any = None,
) -> dict:
    """Record what the phone's UnifiedPush registration is currently doing."""
    if not isinstance(device_id, str) or not device_id:
        raise StoreError("device_id is required")
    if state not in PUSH_STATES:
        raise StoreError(f"push state must be one of {list(PUSH_STATES)}")
    if distributor_present is not None and not isinstance(distributor_present, bool):
        raise StoreError("distributor_present must be boolean or null")
    if failure_reason is not None and (not isinstance(failure_reason, str) or len(failure_reason) > 200):
        raise StoreError("failure_reason must be a bounded string or null")
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            active = conn.execute(
                "SELECT device_id FROM devices WHERE device_id = ? AND revoked = 0", (device_id,)
            ).fetchone()
            if active is None:
                raise StoreError("device is revoked or unknown")
            conn.execute(
                "INSERT INTO device_push_state "
                "(device_id, state, distributor_present, failure_reason, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET "
                "state=excluded.state, distributor_present=excluded.distributor_present, "
                "failure_reason=excluded.failure_reason, updated_at=excluded.updated_at",
                (device_id, state, None if distributor_present is None else int(distributor_present), failure_reason, now),
            )
            conn.commit()
        finally:
            conn.close()
    return get_device_push_state(device_id)

def get_device_push_state(device_id: str) -> dict:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT d.device_id, d.label, d.revoked, p.endpoint, s.state, "
            "s.distributor_present, s.failure_reason, s.updated_at "
            "FROM devices d LEFT JOIN device_push_endpoints p ON p.device_id = d.device_id "
            "LEFT JOIN device_push_state s ON s.device_id = d.device_id WHERE d.device_id = ?",
            (device_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise StoreError("device is unknown")
    registered = bool(row["endpoint"])
    state = row["state"] or ("registered" if registered else "unknown")
    if row["state"] is None and not registered:
        state = "unknown"
    return {
        "deviceId": row["device_id"],
        "label": row["label"],
        "revoked": bool(row["revoked"]),
        "state": state,
        "distributorPresent": None if row["distributor_present"] is None else bool(row["distributor_present"]),
        "registered": registered,
        "failureReason": row["failure_reason"],
        "updatedAt": row["updated_at"],
    }

def list_push_states() -> list[dict]:
    conn = _connect()
    try:
        ids = [row["device_id"] for row in conn.execute("SELECT device_id FROM devices ORDER BY created_at").fetchall()]
    finally:
        conn.close()
    return [get_device_push_state(device_id) for device_id in ids]

def wake_test(widget_id: str = DEFAULT_WIDGET_ID) -> dict:
    """Send one content-free wake to registered device endpoints and report results.

    This deliberately does not create a publication revision or delivery receipt:
    it validates the wake lane, not publication delivery.
    """
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT d.device_id, d.label, p.endpoint FROM devices d "
                "JOIN device_push_endpoints p ON p.device_id = d.device_id "
                "LEFT JOIN widget_devices wd ON wd.device_id = d.device_id AND wd.widget_id = ? "
                "WHERE d.revoked = 0 AND (wd.widget_id IS NOT NULL OR ? = ?)",
                (widget_id, widget_id, widget_id),
            ).fetchall()
        finally:
            conn.close()
    results = []
    for row in rows:
        item = {
            "deviceId": row["device_id"],
            "label": row["label"],
            "state": "failed",
            "at": _now(),
        }
        try:
            _push.wake(str(row["endpoint"]))
            item["state"] = "nudge_sent"
        except (ValueError, _push.PushError) as exc:
            item["detail"] = str(exc)
        results.append(item)
    return {
        "ok": bool(results) and all(item["state"] == "nudge_sent" for item in results),
        "widgetId": widget_id,
        "contentFree": True,
        "receiptChain": results,
        "note": "No publication revision was created; this tests only the UnifiedPush lane.",
    }
