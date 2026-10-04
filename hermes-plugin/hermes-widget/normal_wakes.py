"""Durable normal-priority coalescing; wakes carry only the existing fetch hint."""

from __future__ import annotations

import time
from datetime import datetime, timezone

try:
    from .db import _LOCK, _connect
    from .timeutil import _iso_from_epoch, _in_quiet_window
except ImportError:
    from db import _LOCK, _connect
    from timeutil import _iso_from_epoch, _in_quiet_window

COALESCE_SECONDS = 60
MAX_PER_HOUR = 30


def queue_wake(widget_id: str, revision: int, conn=None) -> None:
    owns = conn is None
    conn = conn or _connect()
    try:
        conn.execute(
            "INSERT INTO widget_normal_wakes(widget_id,revision,due_at) VALUES (?,?,?) ON CONFLICT(widget_id) DO UPDATE SET revision=excluded.revision",
            (widget_id, revision, _iso_from_epoch(time.time())),
        )
        if owns:
            conn.commit()
    finally:
        if owns:
            conn.close()
    if owns:
        flush()


def flush(*, now: float | None = None) -> int:
    try:
        from . import publications
    except ImportError:
        import publications
    current = time.time() if now is None else now
    now_iso = _iso_from_epoch(current)
    claimed = []
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM widget_normal_wake_attempts WHERE attempted_at < ?",
                (_iso_from_epoch(current - 3600),),
            )
            rows = conn.execute(
                "SELECT * FROM widget_normal_wakes WHERE due_at<=? AND (claim_until IS NULL OR claim_until<=?)",
                (now_iso, now_iso),
            ).fetchall()
            for row in rows:
                widget_id = row["widget_id"]
                settings_row = conn.execute(
                    "SELECT quiet_start,quiet_end,quiet_timezone FROM widget_settings WHERE widget_id=?",
                    (widget_id,),
                ).fetchone()
                quiet_now = settings_row and _in_quiet_window(
                    settings_row[0],
                    settings_row[1],
                    settings_row[2],
                    datetime.fromtimestamp(current, timezone.utc),
                )
                attempts = conn.execute(
                    "SELECT attempted_at FROM widget_normal_wake_attempts WHERE widget_id=? ORDER BY attempted_at DESC",
                    (widget_id,),
                ).fetchall()
                last = attempts[0][0] if attempts else None
                cutoff = _iso_from_epoch(current - COALESCE_SECONDS)
                if quiet_now or len(attempts) >= MAX_PER_HOUR or (last and last > cutoff):
                    due = current + COALESCE_SECONDS
                    if last and last > cutoff:
                        due = (
                            datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
                            + COALESCE_SECONDS
                        )
                    conn.execute(
                        "UPDATE widget_normal_wakes SET due_at=? WHERE widget_id=?",
                        (_iso_from_epoch(due), widget_id),
                    )
                    continue
                conn.execute(
                    "INSERT INTO widget_normal_wake_attempts VALUES (?,?)", (widget_id, now_iso)
                )
                conn.execute(
                    "UPDATE widget_normal_wakes SET claim_until=? WHERE widget_id=?",
                    (_iso_from_epoch(current + 300), widget_id),
                )
                claimed.append((widget_id, row["revision"]))
            conn.commit()
        finally:
            conn.close()
    for widget_id, revision in claimed:
        dispatch_failed = False
        try:
            publications._dispatch_priority_nudges(widget_id, revision)
        except Exception:
            dispatch_failed = True
            raise
        finally:
            conn = _connect()
            try:
                failed = conn.execute(
                    "SELECT 1 FROM publication_nudges WHERE widget_id=? AND revision=? AND status='failed' AND next_attempt_at IS NOT NULL",
                    (widget_id, revision),
                ).fetchone()
                if failed or dispatch_failed:
                    conn.execute(
                        "UPDATE widget_normal_wakes SET claim_until=NULL,due_at=? WHERE widget_id=?",
                        (_iso_from_epoch(current + COALESCE_SECONDS), widget_id),
                    )
                else:
                    conn.execute(
                        "DELETE FROM widget_normal_wakes WHERE widget_id=? AND revision=?",
                        (widget_id, revision),
                    )
                    conn.execute(
                        "UPDATE widget_normal_wakes SET claim_until=NULL WHERE widget_id=?",
                        (widget_id,),
                    )
                conn.commit()
            finally:
                conn.close()
    return len(claimed)
