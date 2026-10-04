"""Device delivery reports, update requests, and support diagnostics."""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

try:
    from .db import _LOCK, _connect
    from .errors import RateLimitError, StoreError
    from .timeutil import _iso_from_epoch, _now
    from .publication import PRIORITY_HIGH_MAX_PER_DAY, PRIORITY_HIGH_MAX_PER_HOUR
    from .store_utils import _short_str
except ImportError:  # direct import from scripts/tests
    from db import _LOCK, _connect  # type: ignore
    from errors import RateLimitError, StoreError  # type: ignore
    from timeutil import _iso_from_epoch, _now  # type: ignore
    from publication import (  # type: ignore
        PRIORITY_HIGH_MAX_PER_DAY, PRIORITY_HIGH_MAX_PER_HOUR,
    )
    from store_utils import _short_str  # type: ignore

DEFAULT_WIDGET_ID = "hermes-brief"
NUDGE_MAX_ATTEMPTS = 5
NUDGE_RETRY_BASE_SECONDS = 60
NUDGE_RETRY_MAX_SECONDS = 3600
HIGH_PRIORITY_WINDOW_SECONDS = 3600
HIGH_PRIORITY_DAY_SECONDS = 24 * 3600
HIGH_PRIORITY_MAX_PER_HOUR = PRIORITY_HIGH_MAX_PER_HOUR
HIGH_PRIORITY_MAX_PER_DAY = PRIORITY_HIGH_MAX_PER_DAY
UPDATE_REQUEST_WINDOW_SECONDS = 60
UPDATE_REQUEST_MAX_PER_DEVICE = 5
UPDATE_REQUEST_SINGLE_FLIGHT_SECONDS = 30
MAX_WIDGET_INSTANCES = 32

# Client build reporting. Deliberately narrow: an app version, a build code and an OS
# API level are what "which build was this?" needs, and nothing that identifies a person
# or a place. Bounded so a hostile client cannot grow the row.
MAX_APP_VERSION_LEN = 32
MAX_APP_BUILD_CODE = 2_147_483_647
MAX_OS_SDK = 100
MAX_APP_SHA_LEN = 20
_APP_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$")
_APP_SHA_RE = re.compile(r"^[0-9a-f]{7,20}(-dirty)?$")
# Rejections are a diagnostic trail, not an archive: bounded so a flapping client cannot
# grow the file, and pruned oldest-first on insert.
MAX_REJECTION_ROWS = 2_000
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def _size_class(width_dp: int, height_dp: int, declared: Any = None) -> str:
    """Normalize a reported instance to the contract's five size classes.

    Mirrors android/.../widget/WidgetDimensions.sizeClass: the guide's dp ranges overlap
    (245-306 x 115-276 is both a 2x2 and a 4x2), so the wider reading wins and a device is
    never told it has less room than it does.
    """
    if declared in {"2x2", "4x2", "2x4", "4x4", "custom"}:
        return str(declared)
    if not (109 <= width_dp <= 624 and 56 <= height_dp <= 422):
        return "custom"
    if width_dp >= 245 and height_dp >= 300:
        return "4x4"
    if width_dp >= 245:
        return "4x2"
    if height_dp >= 300:
        return "2x4"
    return "2x2"


def _instance_int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise StoreError(f"{name} must be an integer between {lo} and {hi}")
    return value


def report_widget_instances(device_id: str, widget_id: str, instances: Any) -> list[dict]:
    """Replace a device's inventory for one widget with a bounded, validated list."""
    if not isinstance(device_id, str) or not device_id:
        raise StoreError("device_id is required")
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise StoreError("widget_id is required")
    if not isinstance(instances, list) or len(instances) > MAX_WIDGET_INSTANCES:
        raise StoreError(f"instances must be an array of at most {MAX_WIDGET_INSTANCES} items")
    cleaned: list[tuple[str, str, int, int, int, int]] = []
    seen: set[str] = set()
    for index, raw in enumerate(instances):
        if not isinstance(raw, dict):
            raise StoreError(f"instances[{index}] must be an object")
        instance_id = raw.get("instanceId", raw.get("instance_id"))
        if not isinstance(instance_id, str) or not instance_id or len(instance_id) > 128:
            raise StoreError(f"instances[{index}].instanceId is required")
        if instance_id in seen:
            raise StoreError(f"instances contains duplicate instanceId {instance_id!r}")
        seen.add(instance_id)
        width_dp = _instance_int(raw.get("widthDp", raw.get("width_dp")), f"instances[{index}].widthDp", 1, 4096)
        height_dp = _instance_int(raw.get("heightDp", raw.get("height_dp")), f"instances[{index}].heightDp", 1, 4096)
        width_px = _instance_int(raw.get("widthPx", raw.get("width_px", round(width_dp))), f"instances[{index}].widthPx", 1, 16384)
        height_px = _instance_int(raw.get("heightPx", raw.get("height_px", round(height_dp))), f"instances[{index}].heightPx", 1, 16384)
        cleaned.append((
            instance_id,
            _size_class(width_dp, height_dp, raw.get("sizeClass", raw.get("size_class"))),
            width_dp, height_dp, width_px, height_px,
        ))
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            active = conn.execute(
                "SELECT device_id FROM devices WHERE device_id = ? AND revoked = 0", (device_id,)
            ).fetchone()
            if active is None:
                raise StoreError("device is revoked or unknown")
            conn.execute("DELETE FROM widget_instances WHERE device_id = ? AND widget_id = ?", (device_id, widget_id))
            for instance_id, size_class, width_dp, height_dp, width_px, height_px in cleaned:
                conn.execute(
                    "INSERT INTO widget_instances "
                    "(device_id, widget_id, instance_id, size_class, width_dp, height_dp, width_px, height_px, reported_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (device_id, widget_id, instance_id, size_class, width_dp, height_dp, width_px, height_px, now),
                )
            conn.commit()
        finally:
            conn.close()
    return list_widget_instances(widget_id, device_id=device_id)


def request_update(
    widget_id: str,
    device_id: str | None,
    client_event_id: str | None = None,
    *,
    instance_id: Any = None,
) -> dict:
    """Record one generic 'poke'; the existing refresh routine decides the content."""
    now = _now()
    request_id = "update_request_" + uuid.uuid4().hex[:24]
    instance = _short_str(instance_id, 64)
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now_epoch = time.time()
            now = _now()
            cutoff = _iso_from_epoch(now_epoch - UPDATE_REQUEST_WINDOW_SECONDS)
            pending_cutoff = _iso_from_epoch(now_epoch - UPDATE_REQUEST_SINGLE_FLIGHT_SECONDS)
            if client_event_id:
                existing = conn.execute(
                    "SELECT * FROM widget_update_requests WHERE widget_id = ? AND client_event_id = ?",
                    (widget_id, client_event_id),
                ).fetchone()
                if existing is not None:
                    result = _update_request_row(existing)
                    if result["status"] == "pending" and result["createdAt"] < pending_cutoff:
                        conn.execute(
                            "UPDATE widget_update_requests SET created_at = ? WHERE request_id = ?",
                            (now, result["requestId"]),
                        )
                        conn.commit()
                        result["createdAt"] = now
                        result["_shouldTrigger"] = True
                    else:
                        result["_shouldTrigger"] = False
                    return result
            if device_id:
                count = conn.execute(
                    "SELECT COUNT(*) FROM widget_update_requests "
                    "WHERE device_id = ? AND created_at > ?",
                    (device_id, cutoff),
                ).fetchone()[0]
                if int(count) >= UPDATE_REQUEST_MAX_PER_DEVICE:
                    raise RateLimitError(
                        f"update request rate limit exceeded for device {device_id!r}"
                    )
            pending = conn.execute(
                "SELECT * FROM widget_update_requests WHERE widget_id = ? AND status = 'pending' "
                "AND created_at > ? ORDER BY created_at DESC LIMIT 1",
                (widget_id, pending_cutoff),
            ).fetchone()
            if pending is not None:
                result = _update_request_row(pending)
                result["_shouldTrigger"] = False
                return result
            conn.execute(
                "INSERT INTO widget_update_requests "
                "(request_id, widget_id, device_id, client_event_id, instance_id, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (request_id, widget_id, device_id, client_event_id, instance, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM widget_update_requests WHERE request_id = ?", (request_id,)).fetchone()
            result = _update_request_row(row)
            result["_shouldTrigger"] = True
            return result
        finally:
            conn.close()


def mark_update_request_triggered(request_id: str, *, error: str | None = None) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            status = "failed" if error else "triggered"
            conn.execute(
                "UPDATE widget_update_requests SET status = CASE WHEN consumed_at IS NULL AND completed_at IS NULL THEN ? ELSE status END, triggered_at = ?, trigger_error = CASE WHEN completed_at IS NULL THEN ? ELSE trigger_error END WHERE request_id = ?",
                (status, _now(), error, request_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM widget_update_requests WHERE request_id = ?", (request_id,)).fetchone()
            return _update_request_row(row) if row else None
        finally:
            conn.close()


def consume_update_requests(widget_id: str) -> list[dict]:
    """Acknowledge triggered refresh requests when an agent routine handles them."""
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT request_id FROM widget_update_requests WHERE widget_id = ? "
                "AND status = 'triggered' AND consumed_at IS NULL ORDER BY created_at ASC",
                (widget_id,),
            ).fetchall()
            ids = [str(row["request_id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE widget_update_requests SET status='consumed', consumed_at=? "
                    f"WHERE request_id IN ({placeholders})",
                    [now, *ids],
                )
                consumed = conn.execute(
                    f"SELECT * FROM widget_update_requests WHERE request_id IN ({placeholders}) "
                    "ORDER BY created_at ASC",
                    ids,
                ).fetchall()
            else:
                consumed = []
            conn.commit()
            return [_update_request_row(row) for row in consumed]
        finally:
            conn.close()


def has_unconsumed_update_requests(*, created_after: str | None = None) -> bool:
    conn = _connect()
    try:
        where = "status IN ('triggered','consumed') AND completed_at IS NULL AND refresh_id IS NULL"
        params: tuple[str, ...] = ()
        if created_after is not None:
            where += " AND created_at > ?"
            params = (created_after,)
        return conn.execute(
            f"SELECT 1 FROM widget_update_requests WHERE {where} LIMIT 1", params
        ).fetchone() is not None
    finally:
        conn.close()


def list_update_requests(widget_id: str | None = None, *, limit: int = 50) -> list[dict]:
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    conn = _connect()
    try:
        if widget_id:
            rows = conn.execute(
                "SELECT * FROM widget_update_requests WHERE widget_id = ? ORDER BY created_at DESC LIMIT ?",
                (widget_id, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM widget_update_requests ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [_update_request_row(row) for row in rows]


def _update_request_row(row: Any) -> dict:
    keys = row.keys() if hasattr(row, "keys") else []
    return {
        "requestId": row["request_id"],
        "widgetId": row["widget_id"],
        "deviceId": row["device_id"],
        "clientEventId": row["client_event_id"],
        # Which widget instance asked. Absent on rows written before the column existed,
        # and reported as null rather than guessed from the single-instance assumption.
        "instanceId": row["instance_id"] if "instance_id" in keys else None,
        "status": row["status"],
        "createdAt": row["created_at"],
        "triggeredAt": row["triggered_at"],
        "consumedAt": row["consumed_at"] if "consumed_at" in keys else None,
        "error": row["trigger_error"],
        "refreshId": row["refresh_id"] if "refresh_id" in keys else None,
        "completedAt": row["completed_at"] if "completed_at" in keys else None,
        "outcome": row["outcome"] if "outcome" in keys else None,
        "resultRevision": row["result_revision"] if "result_revision" in keys else None,
    }


def report_attention(device_id: str, widget_id: str, payload: Any) -> dict:
    """Accept only bounded aggregate counters; no content or screenshots."""
    if not isinstance(payload, dict):
        raise StoreError("attention report must be an object")
    allowed = {"revision", "rendered", "dwellLt5", "dwell5To60", "dwellGt60", "taps", "supersededBeforeFetch"}
    unknown = set(payload) - allowed
    if unknown:
        raise StoreError("attention reports must not contain content or unknown fields")
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise StoreError("attention revision must be a positive integer")
    fields = {
        "rendered": payload.get("rendered", 0),
        "dwellLt5": payload.get("dwellLt5", 0),
        "dwell5To60": payload.get("dwell5To60", 0),
        "dwellGt60": payload.get("dwellGt60", 0),
        "taps": payload.get("taps", 0),
        "supersededBeforeFetch": payload.get("supersededBeforeFetch", 0),
    }
    for name, value in fields.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
            raise StoreError(f"attention {name} must be a bounded non-negative integer")
    with _LOCK:
        conn = _connect()
        try:
            device = conn.execute("SELECT device_id FROM devices WHERE device_id = ? AND revoked = 0", (device_id,)).fetchone()
            if device is None:
                raise StoreError("device is revoked or unknown")
            conn.execute(
                "INSERT INTO attention_aggregates (device_id, widget_id, revision, rendered, dwell_lt5, dwell_5_60, dwell_gt60, taps, superseded_before_fetch, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(device_id, widget_id, revision) DO UPDATE SET "
                "rendered=rendered+excluded.rendered, dwell_lt5=dwell_lt5+excluded.dwell_lt5, "
                "dwell_5_60=dwell_5_60+excluded.dwell_5_60, dwell_gt60=dwell_gt60+excluded.dwell_gt60, "
                "taps=taps+excluded.taps, superseded_before_fetch=superseded_before_fetch+excluded.superseded_before_fetch, "
                "updated_at=excluded.updated_at",
                (device_id, widget_id, revision, fields["rendered"], fields["dwellLt5"], fields["dwell5To60"], fields["dwellGt60"], fields["taps"], fields["supersededBeforeFetch"], _now()),
            )
            conn.commit()
        finally:
            conn.close()
    return attention_summary(widget_id)


def attention_summary(widget_id: str | None = None) -> dict:
    clauses = []
    params: list[Any] = []
    if widget_id:
        clauses.append("widget_id = ?")
        params.append(widget_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _connect()
    try:
        rows = conn.execute(f"SELECT * FROM attention_aggregates{where}", params).fetchall()
    finally:
        conn.close()
    totals = {key: sum(int(row[key]) for row in rows) for key in ("rendered", "dwell_lt5", "dwell_5_60", "dwell_gt60", "taps", "superseded_before_fetch")}
    return {
        "widgetId": widget_id,
        "revisions": len(rows),
        **totals,
        "rendersPerPublish": round(totals["rendered"] / max(1, len(rows)), 3),
        "tapsPer10Publishes": round(totals["taps"] * 10 / max(1, len(rows)), 3),
        "supersededBeforeFetchRate": round(totals["superseded_before_fetch"] / max(1, totals["rendered"] + totals["superseded_before_fetch"]), 4),
    }


def list_widget_instances(widget_id: str | None = None, *, device_id: str | None = None) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if widget_id is not None:
        clauses.append("widget_id = ?")
        params.append(widget_id)
    if device_id is not None:
        clauses.append("device_id = ?")
        params.append(device_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT device_id, widget_id, instance_id, size_class, width_dp, height_dp, width_px, height_px, reported_at "
            f"FROM widget_instances{where} ORDER BY device_id, instance_id",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "deviceId": row["device_id"],
            "widgetId": row["widget_id"],
            "instanceId": row["instance_id"],
            "sizeClass": row["size_class"],
            "widthDp": row["width_dp"],
            "heightDp": row["height_dp"],
            "widthPx": row["width_px"],
            "heightPx": row["height_px"],
            "reportedAt": row["reported_at"],
        }
        for row in rows
    ]


def _clean_app_version(value: Any) -> str | None:
    """A version string is stored only if it is short and boring."""
    if value is None or not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed or len(trimmed) > MAX_APP_VERSION_LEN:
        return None
    return trimmed if _APP_VERSION_RE.match(trimmed) else None


def _clean_app_build_code(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            return None
        value = int(value)
    if not isinstance(value, int) or not 0 <= value <= MAX_APP_BUILD_CODE:
        return None
    return value


def _clean_app_build_sha(value: Any) -> str | None:
    """A short commit id, or the same id marked dirty. Anything else is dropped."""
    if value is None or not isinstance(value, str):
        return None
    trimmed = value.strip().lower()
    if len(trimmed) > MAX_APP_SHA_LEN:
        return None
    return trimmed if _APP_SHA_RE.match(trimmed) else None


def _clean_os_sdk(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            return None
        value = int(value)
    if not isinstance(value, int) or not 1 <= value <= MAX_OS_SDK:
        return None
    return value


def record_device_client(
    device_id: str,
    app_version: Any = None,
    app_build_code: Any = None,
    os_sdk: Any = None,
    app_build_sha: Any = None,
) -> dict | None:
    """Record which build a device is running, from the poll headers or a `client` block.

    Lenient by design: invalid values are dropped rather than raised, because this is
    metadata on the delivery path and a phone that cannot fetch a publication because its
    version string had a space in it would be a far worse bug than a missing version. An
    unchanged build writes nothing, so the periodic poll does not turn into a write per
    wakeup.
    """
    if not isinstance(device_id, str) or not device_id:
        raise StoreError("device_id is required")
    version = _clean_app_version(app_version)
    build = _clean_app_build_code(app_build_code)
    sdk = _clean_os_sdk(os_sdk)
    sha = _clean_app_build_sha(app_build_sha)
    if version is None and build is None and sdk is None and sha is None:
        return get_device_client_info(device_id)
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            active = conn.execute(
                "SELECT device_id FROM devices WHERE device_id = ? AND revoked = 0", (device_id,)
            ).fetchone()
            if active is None:
                # Unknown or revoked devices never get client rows: nothing reads them,
                # and a revoked token should not keep leaving traces.
                return None
            current = conn.execute(
                "SELECT app_version, app_build_code, os_sdk, app_build_sha FROM device_client_info "
                "WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if (
                current is not None
                and current["app_version"] == version
                and current["app_build_code"] == build
                and current["os_sdk"] == sdk
                and current["app_build_sha"] == sha
            ):
                return get_device_client_info(device_id)
            conn.execute(
                "INSERT INTO device_client_info "
                "(device_id, app_version, app_build_code, os_sdk, app_build_sha, "
                "first_reported_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET "
                "app_version=COALESCE(excluded.app_version, device_client_info.app_version), "
                "app_build_code=COALESCE(excluded.app_build_code, device_client_info.app_build_code), "
                "os_sdk=COALESCE(excluded.os_sdk, device_client_info.os_sdk), "
                "app_build_sha=COALESCE(excluded.app_build_sha, device_client_info.app_build_sha), "
                "updated_at=excluded.updated_at",
                (device_id, version, build, sdk, sha, now, now),
            )
            conn.commit()
        finally:
            conn.close()
    return get_device_client_info(device_id)


def get_device_client_info(device_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT app_version, app_build_code, os_sdk, app_build_sha, "
            "first_reported_at, updated_at FROM device_client_info WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "appVersion": row["app_version"],
        "appBuildCode": row["app_build_code"],
        "osSdk": row["os_sdk"],
        "appBuildSha": row["app_build_sha"],
        "firstReportedAt": row["first_reported_at"],
        "updatedAt": row["updated_at"],
    }


def record_render_build(
    widget_id: str,
    device_id: str,
    revision: int,
    app_version: Any = None,
    app_build_code: Any = None,
    instance_id: Any = None,
    app_build_sha: Any = None,
) -> None:
    """Note which build rendered a revision, alongside the render acknowledgement.

    Best effort: a client that reports no build (an older app) simply records nothing,
    which is why the columns are nullable rather than defaulted to a lie.
    """
    version = _clean_app_version(app_version)
    build = _clean_app_build_code(app_build_code)
    sha = _clean_app_build_sha(app_build_sha)
    if version is None and build is None and sha is None:
        return
    if not isinstance(device_id, str) or not device_id:
        return
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO publication_render_builds "
                "(widget_id, device_id, revision, app_version, app_build_code, app_build_sha, "
                "instance_id, reported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(widget_id, device_id, revision) "
                "DO UPDATE SET "
                "app_version=excluded.app_version, app_build_code=excluded.app_build_code, "
                "app_build_sha=excluded.app_build_sha, "
                "instance_id=COALESCE(excluded.instance_id, publication_render_builds.instance_id), "
                "reported_at=excluded.reported_at",
                (
                    widget_id, device_id, revision, version, build, sha,
                    _short_str(instance_id, 64), _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def get_render_builds(widget_id: str) -> dict[tuple[str, int], dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT device_id, revision, app_version, app_build_code, app_build_sha, "
            "instance_id, reported_at FROM publication_render_builds WHERE widget_id = ?",
            (widget_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        (str(row["device_id"]), int(row["revision"])): {
            "appVersion": row["app_version"],
            "appBuildCode": row["app_build_code"],
            "appBuildSha": row["app_build_sha"],
            "instanceId": row["instance_id"],
            "reportedAt": row["reported_at"],
        }
        for row in rows
    }


def record_rejected_event(
    widget_id: Any,
    device_id: str,
    event: Any,
    *,
    method: str = "",
    path: str = "",
    status: int = 0,
    code: str = "unknown",
    detail: str = "",
    request_id: str = "",
) -> dict:
    """Persist a refused device request so a missing row stops being ambiguous.

    Bounded and content-free: method, path shape, status, error code and the event name
    only. No token, no payload, no request body.
    """
    now = _now()
    row = {
        "widgetId": _short_str(widget_id, 128) or None,
        "deviceId": device_id,
        "event": _short_str(event, 64) or None,
        "method": _short_str(method, 8) or None,
        "path": _short_str(path, 256) or None,
        "status": int(status) if isinstance(status, int) and not isinstance(status, bool) else 0,
        "code": _short_str(code, 64) or "unknown",
        "detail": _short_str(detail, 256) or None,
        "requestId": _short_str(request_id, 32) or None,
        "occurredAt": now,
    }
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO event_rejections "
                "(widget_id, device_id, event, method, path, status, code, detail, request_id, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["widgetId"], row["deviceId"], row["event"], row["method"], row["path"],
                    row["status"], row["code"], row["detail"], row["requestId"], now,
                ),
            )
            conn.execute(
                "DELETE FROM event_rejections WHERE id <= ("
                "  SELECT MAX(id) - ? FROM event_rejections)",
                (MAX_REJECTION_ROWS,),
            )
            conn.commit()
        finally:
            conn.close()
    return row


def list_rejected_events(
    widget_id: str | None = None,
    *,
    device_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    clauses: list[str] = []
    params: list[Any] = []
    if widget_id is not None:
        clauses.append("(widget_id = ? OR widget_id IS NULL)")
        params.append(widget_id)
    if device_id is not None:
        clauses.append("device_id = ?")
        params.append(device_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT widget_id, device_id, event, method, path, status, code, detail, "
            f"request_id, occurred_at FROM event_rejections{where} "
            "ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "widgetId": row["widget_id"],
            "deviceId": row["device_id"],
            "event": row["event"],
            "method": row["method"],
            "path": row["path"],
            "status": row["status"],
            "code": row["code"],
            "detail": row["detail"],
            "requestId": row["request_id"],
            "occurredAt": row["occurred_at"],
        }
        for row in rows
    ]


def rejection_summary(widget_id: str | None = None) -> dict:
    """Counts a support answer needs: which failures, how often, since when."""
    recent = list_rejected_events(widget_id, limit=200)
    by_code: dict[str, int] = {}
    by_device: dict[str, int] = {}
    for row in recent:
        by_code[row["code"]] = by_code.get(row["code"], 0) + 1
        by_device[row["deviceId"]] = by_device.get(row["deviceId"], 0) + 1
    return {
        "recentCount": len(recent),
        "byCode": by_code,
        "byDevice": by_device,
        "lastOccurredAt": recent[0]["occurredAt"] if recent else None,
        "lastCode": recent[0]["code"] if recent else None,
        "lastEvent": recent[0]["event"] if recent else None,
        "lastStatus": recent[0]["status"] if recent else None,
        "rejected": recent,
    }








# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
