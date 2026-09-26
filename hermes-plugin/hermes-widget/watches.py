"""Bounded host-evaluated widget watches.

A watch is a small, durable condition → publication rule.  It deliberately does
not fetch arbitrary external sources: the agent/cron supplies a bounded source
snapshot, while date, revision, and always conditions need no network.  Every
fire is rate-limited, attributed, and recorded as a normal publication revision.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from . import store
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import store  # type: ignore

MAX_WATCHES = 100
MAX_CADENCE_SECONDS = 30 * 24 * 3600
MAX_PER_DAY = 50
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> Any:
    return datetime.strptime(value, "%H:%M").time()


def _in_quiet(start: str | None, end: str | None, now: datetime) -> bool:
    if not start or not end or not _TIME_RE.match(start) or not _TIME_RE.match(end):
        return False
    s, e, value = _parse_time(start), _parse_time(end), now.timetz().replace(tzinfo=None)
    if s == e:
        return False
    return (s <= value < e) if s < e else (value >= s or value < e)


def _row(row: Any) -> dict[str, Any]:
    try:
        condition = json.loads(row["condition_json"])
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError) as exc:
        raise store.StoreError("watch data is corrupt") from exc
    return {
        "watchId": row["watch_id"],
        "widgetId": row["widget_id"],
        "name": row["name"],
        "condition": condition,
        "payload": payload,
        "cadenceSeconds": row["cadence_seconds"],
        "quietHours": {"start": row["quiet_start"], "end": row["quiet_end"]}
        if row["quiet_start"] and row["quiet_end"] else None,
        "maxPerDay": row["max_per_day"],
        "enabled": bool(row["enabled"]),
        "lastState": None if row["last_state"] is None else bool(row["last_state"]),
        "lastFiredAt": row["last_fired_at"],
        "nextCheckAt": row["next_check_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "expiresAt": row["expires_at"],
        "clearOnResolve": True,
    }


def _validate_condition(condition: Any) -> dict[str, Any]:
    if not isinstance(condition, dict) or condition.get("type") not in {
        "always", "date_reached", "source_equals", "revision_gte",
    }:
        raise store.StoreError("condition.type must be always, date_reached, source_equals, or revision_gte")
    clean = {"type": condition["type"]}
    if clean["type"] == "date_reached":
        value = condition.get("at")
        if not isinstance(value, str):
            raise store.StoreError("date_reached requires an ISO-8601 at value")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise store.StoreError("date_reached at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise store.StoreError("date_reached at must include a timezone")
        clean["at"] = value
    elif clean["type"] == "source_equals":
        source = condition.get("source")
        if not isinstance(source, str) or not source or len(source) > 128:
            raise store.StoreError("source_equals requires a bounded source name")
        clean["source"] = source
        clean["equals"] = condition.get("equals")
    elif clean["type"] == "revision_gte":
        value = condition.get("revision")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise store.StoreError("revision_gte requires a positive revision")
        clean["revision"] = value
    return clean


def _validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise store.StoreError("watch payload must be an object")
    if not isinstance(payload.get("title"), str) or not isinstance(payload.get("summary"), str):
        raise store.StoreError("watch payload requires title and summary")
    sources = [payload.get(key) for key in ("text", "svg", "file_path")]
    if sum(value is not None for value in sources) != 1:
        raise store.StoreError("watch payload requires exactly one text, svg, or file_path")
    if payload.get("priority", "normal") not in ("normal", "high"):
        raise store.StoreError("watch payload priority must be normal or high")
    return dict(payload)


def create_watch(
    widget_id: str,
    *,
    name: str,
    condition: Any,
    payload: Any,
    cadence_seconds: int = 3600,
    quiet_hours: Any = None,
    max_per_day: int = 1,
    expires_at: str | None = None,
) -> dict[str, Any]:
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise store.StoreError("widget_id is required")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise store.StoreError("watch name must be 1-120 characters")
    if isinstance(cadence_seconds, bool) or not isinstance(cadence_seconds, int) or not 60 <= cadence_seconds <= MAX_CADENCE_SECONDS:
        raise store.StoreError("cadence_seconds must be between 60 and 30 days")
    if isinstance(max_per_day, bool) or not isinstance(max_per_day, int) or not 1 <= max_per_day <= MAX_PER_DAY:
        raise store.StoreError(f"max_per_day must be between 1 and {MAX_PER_DAY}")
    condition_value = _validate_condition(condition)
    payload_value = _validate_payload(payload)
    start = end = None
    if quiet_hours is not None:
        if not isinstance(quiet_hours, dict) or not _TIME_RE.match(str(quiet_hours.get("start", ""))) or not _TIME_RE.match(str(quiet_hours.get("end", ""))):
            raise store.StoreError("quiet_hours requires start/end HH:MM")
        start, end = str(quiet_hours["start"]), str(quiet_hours["end"])
    if expires_at is not None:
        if not isinstance(expires_at, str):
            raise store.StoreError("expires_at must be an ISO-8601 string")
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise store.StoreError("expires_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise store.StoreError("expires_at must include a timezone")
    watch_id = "watch_" + uuid.uuid4().hex[:24]
    now = datetime.now(timezone.utc)
    with store._LOCK:
        conn = store._connect()
        try:
            count = conn.execute("SELECT COUNT(*) FROM widget_watches WHERE widget_id = ?", (widget_id,)).fetchone()[0]
            if int(count) >= MAX_WATCHES:
                raise store.StoreError(f"a widget may have at most {MAX_WATCHES} watches")
            conn.execute(
                "INSERT INTO widget_watches (watch_id, widget_id, name, condition_json, payload_json, cadence_seconds, quiet_start, quiet_end, max_per_day, enabled, last_state, last_fired_at, next_check_at, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, NULL, ?, ?, ?, ?)",
                (watch_id, widget_id, name.strip(), json.dumps(condition_value, separators=(",", ":")), json.dumps(payload_value, separators=(",", ":")), cadence_seconds, start, end, max_per_day, _iso(now), _iso(now), _iso(now), expires_at),
            )
            conn.commit()
        finally:
            conn.close()
    return get_watch(watch_id)


def get_watch(watch_id: str) -> dict[str, Any] | None:
    with store._LOCK:
        conn = store._connect()
        try:
            row = conn.execute("SELECT * FROM widget_watches WHERE watch_id = ?", (watch_id,)).fetchone()
        finally:
            conn.close()
    return _row(row) if row else None


def list_watches(widget_id: str | None = None, *, enabled: bool | None = None) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if widget_id:
        clauses.append("widget_id = ?")
        params.append(widget_id)
    if enabled is not None:
        clauses.append("enabled = ?")
        params.append(1 if enabled else 0)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with store._LOCK:
        conn = store._connect()
        try:
            rows = conn.execute(f"SELECT * FROM widget_watches{where} ORDER BY created_at", params).fetchall()
        finally:
            conn.close()
    return [_row(row) for row in rows]


def pause_watch(watch_id: str, *, paused: bool = True) -> dict[str, Any] | None:
    with store._LOCK:
        conn = store._connect()
        try:
            conn.execute("UPDATE widget_watches SET enabled = ?, updated_at = ? WHERE watch_id = ?", (0 if paused else 1, _now(), watch_id))
            conn.commit()
        finally:
            conn.close()
    return get_watch(watch_id)


def remove_watch(watch_id: str) -> bool:
    with store._LOCK:
        conn = store._connect()
        try:
            cur = conn.execute("DELETE FROM widget_watches WHERE watch_id = ?", (watch_id,))
            conn.commit()
            return bool(cur.rowcount)
        finally:
            conn.close()


def _evaluate(condition: dict[str, Any], sources: dict[str, Any], widget_id: str) -> bool:
    kind = condition["type"]
    if kind == "always":
        return True
    if kind == "date_reached":
        return datetime.now(timezone.utc) >= datetime.fromisoformat(condition["at"].replace("Z", "+00:00"))
    if kind == "source_equals":
        return sources.get(condition["source"]) == condition.get("equals")
    if kind == "revision_gte":
        current = store.get_publication(widget_id)
        return bool(current and int(current.get("revision", 0)) >= condition["revision"])
    return False


def _fired_today(conn: Any, widget_id: str, watch_id: str, now: datetime) -> int:
    since = _iso(now - timedelta(days=1))
    rows = conn.execute(
        "SELECT payload_json, published_at FROM publication_revisions WHERE widget_id = ? AND published_at > ?",
        (widget_id, since),
    ).fetchall()
    return sum(1 for row in rows if f'"watchId":"{watch_id}"' in (row["payload_json"] or ""))


def tick_watches(*, sources: dict[str, Any] | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Evaluate due watches and publish only on a false→true transition."""
    current = now or datetime.now(timezone.utc)
    source_values = sources if isinstance(sources, dict) else {}
    results: list[dict[str, Any]] = []
    for watch in list_watches():
        if not watch["enabled"]:
            continue
        if watch["expiresAt"]:
            try:
                if current >= datetime.fromisoformat(watch["expiresAt"].replace("Z", "+00:00")):
                    pause_watch(watch["watchId"], paused=True)
                    results.append({"watchId": watch["watchId"], "state": "expired"})
                    continue
            except ValueError:
                continue
        next_check = watch.get("nextCheckAt")
        if next_check:
            try:
                if current < datetime.fromisoformat(next_check.replace("Z", "+00:00")):
                    continue
            except ValueError:
                pass
        with store._LOCK:
            conn = store._connect()
            try:
                today = _fired_today(conn, watch["widgetId"], watch["watchId"], current)
            finally:
                conn.close()
        state = _evaluate(watch["condition"], source_values, watch["widgetId"])
        previous = watch.get("lastState")
        due_state_change = state and previous is not True
        if not due_state_change:
            with store._LOCK:
                conn = store._connect()
                try:
                    conn.execute("UPDATE widget_watches SET last_state = ?, next_check_at = ?, updated_at = ? WHERE watch_id = ?", (int(state), _iso(current + timedelta(seconds=watch["cadenceSeconds"])), _iso(current), watch["watchId"]))
                    conn.commit()
                finally:
                    conn.close()
            if state is False and previous is True and watch.get("clearOnResolve", True):
                pause_watch(watch["watchId"], paused=True)
                results.append({"watchId": watch["watchId"], "state": "cleared"})
            continue
        quiet = watch.get("quietHours") or {}
        if _in_quiet(quiet.get("start"), quiet.get("end"), current) or today >= watch["maxPerDay"]:
            with store._LOCK:
                conn = store._connect()
                try:
                    conn.execute("UPDATE widget_watches SET last_state = ?, next_check_at = ?, updated_at = ? WHERE watch_id = ?", (int(state), _iso(current + timedelta(seconds=watch["cadenceSeconds"])), _iso(current), watch["watchId"]))
                    conn.commit()
                finally:
                    conn.close()
            results.append({"watchId": watch["watchId"], "state": "deferred", "reason": "quiet_hours" if _in_quiet(quiet.get("start"), quiet.get("end"), current) else "max_per_day"})
            continue
        payload = dict(watch["payload"])
        try:
            publication = store.put_publication(
                watch["widgetId"],
                title=payload.get("title"),
                summary=payload.get("summary"),
                text=payload.get("text"),
                svg=payload.get("svg"),
                file_path=payload.get("file_path", payload.get("filePath")),
                priority=payload.get("priority", "normal"),
                item_id=payload.get("item_id", payload.get("itemId")),
                actions=payload.get("actions"),
                watch_id=watch["watchId"],
            )
            fired = {"watchId": watch["watchId"], "state": "published", "revision": publication.get("revision")}
        except store.StoreError as exc:
            fired = {"watchId": watch["watchId"], "state": "error", "error": str(exc)}
        with store._LOCK:
            conn = store._connect()
            try:
                conn.execute("UPDATE widget_watches SET last_state = 1, last_fired_at = ?, next_check_at = ?, updated_at = ? WHERE watch_id = ?", (_iso(current), _iso(current + timedelta(seconds=watch["cadenceSeconds"])), _iso(current), watch["watchId"]))
                conn.commit()
            finally:
                conn.close()
        results.append(fired)
    return results


__all__ = ["create_watch", "get_watch", "list_watches", "pause_watch", "remove_watch", "tick_watches"]
