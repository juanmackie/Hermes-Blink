"""Durable work handoff and leased refresh outcomes shared by cron and tools."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

try:
    from .db import _LOCK, _connect
    from .errors import PublicationError, StoreError
    from .timeutil import _now, _iso_from_epoch
except ImportError:
    from db import _LOCK, _connect
    from errors import PublicationError, StoreError
    from timeutil import _now, _iso_from_epoch

LEASE_SECONDS = 15 * 60
MAX_CONTEXT_BYTES = 40 * 1024


def normalize_context(raw: Any, presentation: dict | None = None) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {"sources", "session", "recheck"}:
        raise PublicationError("work_context accepts sources, session, and recheck")
    sources = raw.get("sources", [])
    if (
        not isinstance(sources, list)
        or len(sources) > 12
        or any(not isinstance(s, str) or len(s) > 512 for s in sources)
    ):
        raise PublicationError(
            "work_context.sources must contain up to 12 references of at most 512 characters"
        )
    result = {"sources": sources, "presentation": presentation}
    for field, limit in (("session", 256), ("recheck", 2000)):
        value = raw.get(field, "")
        if not isinstance(value, str) or len(value) > limit:
            raise PublicationError(f"work_context.{field} exceeds {limit} characters")
        result[field] = value
    if len(json.dumps(result).encode()) > MAX_CONTEXT_BYTES:
        raise PublicationError("work context exceeds 40 KiB")
    return result


def save_context(widget_id: str, revision: int, context: dict, conn=None) -> None:
    owns = conn is None
    conn = conn or _connect()
    try:
        conn.execute(
            "INSERT INTO widget_work_context VALUES (?, ?, ?, ?) ON CONFLICT(widget_id) DO UPDATE SET revision=excluded.revision, context_json=excluded.context_json, updated_at=excluded.updated_at",
            (widget_id, revision, json.dumps(context, ensure_ascii=False), _now()),
        )
        if owns:
            conn.commit()
    finally:
        if owns:
            conn.close()


def read_context(widget_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM widget_work_context WHERE widget_id=?", (widget_id,)
        ).fetchone()
        return (
            {
                "revision": row["revision"],
                "updatedAt": row["updated_at"],
                **json.loads(row["context_json"]),
            }
            if row
            else None
        )
    finally:
        conn.close()


def _recover(conn, active) -> None:
    _finish(
        conn,
        active["widget_id"],
        active["refresh_id"],
        "failed",
        "interrupted: refresh lease expired",
        None,
    )
    conn.execute(
        "UPDATE widget_update_requests SET refresh_id=NULL, outcome=NULL, completed_at=NULL, status='triggered' WHERE refresh_id=?",
        (active["refresh_id"],),
    )


def recover_expired() -> list[str]:
    """Make interrupted requested runs eligible for the server's next cron wake."""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            expired = conn.execute(
                "SELECT * FROM widget_refresh_runs WHERE status='running' AND lease_until<=?",
                (_now(),),
            ).fetchall()
            for active in expired:
                _recover(conn, active)
            conn.commit()
            return [row["widget_id"] for row in expired]
        finally:
            conn.close()


def claim(widget_id: str) -> dict:
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT * FROM widget_refresh_runs WHERE widget_id=? AND status='running' ORDER BY started_at DESC LIMIT 1",
                (widget_id,),
            ).fetchone()
            if active and active["lease_until"] > now:
                conn.commit()
                return {
                    "refreshId": None,
                    "busy": True,
                    "leaseUntil": active["lease_until"],
                    "requests": [],
                }
            if active:
                _recover(conn, active)
            refresh_id = "refresh_" + uuid.uuid4().hex
            lease = _iso_from_epoch(time.time() + LEASE_SECONDS)
            conn.execute(
                "INSERT INTO widget_refresh_runs (refresh_id,widget_id,status,lease_until,started_at) VALUES (?,?,'running',?,?)",
                (refresh_id, widget_id, lease, now),
            )
            conn.execute(
                "UPDATE widget_update_requests SET status='consumed', consumed_at=COALESCE(consumed_at,?), refresh_id=? WHERE widget_id=? AND completed_at IS NULL AND refresh_id IS NULL AND status IN ('triggered','consumed','pending')",
                (now, refresh_id, widget_id),
            )
            rows = conn.execute(
                "SELECT request_id FROM widget_update_requests WHERE refresh_id=?", (refresh_id,)
            ).fetchall()
            conn.execute(
                "DELETE FROM widget_refresh_runs WHERE widget_id=? AND status!='running' AND refresh_id NOT IN (SELECT refresh_id FROM widget_refresh_runs WHERE widget_id=? ORDER BY started_at DESC LIMIT 100)",
                (widget_id, widget_id),
            )
            conn.commit()
            return {
                "refreshId": refresh_id,
                "busy": False,
                "leaseUntil": lease,
                "requests": [r["request_id"] for r in rows],
            }
        finally:
            conn.close()


def _finish(conn, widget_id, refresh_id, outcome, reason, revision) -> None:
    row = conn.execute(
        "SELECT * FROM widget_refresh_runs WHERE refresh_id=? AND widget_id=?",
        (refresh_id, widget_id),
    ).fetchone()
    if not row:
        raise StoreError("unknown refresh lease")
    if row["status"] != "running":
        if row["status"] == outcome and row["revision"] == revision and row["reason"] == reason:
            return
        raise StoreError("refresh already completed")
    now = _now()
    conn.execute(
        "UPDATE widget_refresh_runs SET status=?, completed_at=?, revision=?, reason=? WHERE refresh_id=?",
        (outcome, now, revision, reason, refresh_id),
    )
    conn.execute(
        "UPDATE widget_update_requests SET completed_at=?,outcome=?,result_revision=?,trigger_error=? WHERE refresh_id=?",
        (now, outcome, revision, reason, refresh_id),
    )


def finish(
    widget_id: str, refresh_id: str, outcome: str, reason: str, revision=None, conn=None
) -> None:
    if (
        not isinstance(outcome, str)
        or outcome not in {"published", "unchanged", "failed"}
        or not isinstance(reason, str)
        or len(reason) > 2000
    ):
        raise StoreError("refresh outcome must be published/unchanged/failed with a bounded reason")
    if not isinstance(refresh_id, str) or not refresh_id or len(refresh_id) > 128:
        raise StoreError("refresh_id must be a bounded lease identifier")
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise StoreError("widget_id must be a bounded widget identifier")
    if outcome != "published" and not reason.strip():
        raise StoreError("unchanged/failed refreshes require a reason")
    owns = conn is None
    conn = conn or _connect()
    try:
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT lease_until,status FROM widget_refresh_runs WHERE refresh_id=? AND widget_id=?",
            (refresh_id, widget_id),
        ).fetchone()
        if row and row["status"] == "running" and row["lease_until"] <= _now():
            raise StoreError("refresh lease expired; claim a new refresh through widget_status")
        _finish(conn, widget_id, refresh_id, outcome, reason, revision)
        if owns:
            conn.commit()
    finally:
        if owns:
            conn.close()


def outcomes(widget_id: str) -> list[dict]:
    conn = _connect()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT refresh_id,status,lease_until,started_at,completed_at,revision,reason FROM widget_refresh_runs WHERE widget_id=? ORDER BY started_at DESC LIMIT 10",
                (widget_id,),
            )
        ]
    finally:
        conn.close()


def published_turn(tool_name=None, result=None, session_id=None, turn_id=None, **kwargs):
    if tool_name != "widget_publish" or not session_id or not turn_id:
        return
    try:
        value = json.loads(result) if isinstance(result, str) else result
        if not isinstance(value, dict) or not value.get("ok"):
            return
        conn = _connect()
        try:
            conn.execute(
                "DELETE FROM widget_published_turns WHERE created_at < ?",
                (_iso_from_epoch(time.time() - 86400),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO widget_published_turns VALUES (?,?,?,?,?)",
                (session_id, turn_id, value["widgetId"], value["revision"], _now()),
            )
            conn.commit()
        finally:
            conn.close()
    except (ValueError, KeyError, TypeError):
        return


def final_result(session_id=None, turn_id=None, assistant_response=None, **kwargs):
    if not session_id or not turn_id or not isinstance(assistant_response, str):
        return
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT widget_id,revision FROM widget_published_turns WHERE session_id=? AND turn_id=?",
                (session_id, turn_id),
            ).fetchall()
            for row in rows:
                current = conn.execute(
                    "SELECT revision,context_json FROM widget_work_context WHERE widget_id=?",
                    (row["widget_id"],),
                ).fetchone()
                if current and current["revision"] == row["revision"]:
                    context = json.loads(current["context_json"])
                    context.update(
                        session=str(session_id)[:256], finalResult=assistant_response[:3000]
                    )
                    save_context(row["widget_id"], row["revision"], context, conn)
            conn.execute(
                "DELETE FROM widget_published_turns WHERE session_id=? AND turn_id=?",
                (session_id, turn_id),
            )
            conn.commit()
        finally:
            conn.close()
