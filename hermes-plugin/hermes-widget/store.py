"""SQLite-backed widget store shared by the agent tools and the plugin HTTP server.

Everything the widget owns lives in one SQLite file under the Hermes home so the
agent process (tools) and the serving process (hermes widget serve) see the same
state with no extra service. Device tokens are only ever persisted as sha256
hashes.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import importlib
import json
import logging
import os
import re
import secrets
import sqlite3
import string
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import push as _push
    from .publication import (
        ACTION_CLASSES,
        ACTION_KINDS,
        MAX_ACTION_PAYLOAD_BYTES,
        PRIORITY_HIGH_MAX_PER_DAY,
        PRIORITY_HIGH_MAX_PER_HOUR,
        SENSITIVE_ACTION_CLASSES,
        PublicationInputError,
        prepare_publication,
    )
    from .publication import PublicationTooLarge as _PublicationInputTooLarge
    from .publication import capabilities as _publication_capabilities
    from .validate import ValidationError
    from .validate import inspect_layout as _inspect_layout
    from .validate import validate_layout as _validate
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import push as _push  # type: ignore
    from publication import (  # type: ignore
        ACTION_CLASSES,
        ACTION_KINDS,
        MAX_ACTION_PAYLOAD_BYTES,
        PRIORITY_HIGH_MAX_PER_DAY,
        PRIORITY_HIGH_MAX_PER_HOUR,
        SENSITIVE_ACTION_CLASSES,
        PublicationInputError,
        prepare_publication,
    )
    from publication import (  # type: ignore
        PublicationTooLarge as _PublicationInputTooLarge,
    )
    from publication import capabilities as _publication_capabilities  # type: ignore
    from validate import ValidationError  # type: ignore
    from validate import inspect_layout as _inspect_layout  # type: ignore
    from validate import validate_layout as _validate  # type: ignore

DEFAULT_WIDGET_ID = "hermes-brief"
MAX_LAYOUT_BYTES = 64 * 1024
MAX_NODES = 100
PUSH_WINDOW_SECONDS = 3600
PUSH_MAX_PER_WINDOW = 30
HIGH_PRIORITY_WINDOW_SECONDS = 3600
HIGH_PRIORITY_DAY_SECONDS = 24 * 3600
HIGH_PRIORITY_MAX_PER_HOUR = PRIORITY_HIGH_MAX_PER_HOUR
HIGH_PRIORITY_MAX_PER_DAY = PRIORITY_HIGH_MAX_PER_DAY
ACTION_INTENT_TTL_SECONDS = 7 * 24 * 3600
ACTION_RATE_WINDOW_SECONDS = 3600
ACTION_MAX_PER_WINDOW = 30
MAX_WIDGET_INSTANCES = 32
PAIRING_TTL_MINUTES = 10
DEVICE_TOKEN_PREFIX = "dvc" + "_"
AGENT_TOKEN_ENV = "HERMES_WIDGET_" + "AGENT_TOKEN"

_LOCK = threading.RLock()
_initialised: set[str] = set()
_rate: dict[str, list[float]] = {}
logger = logging.getLogger(__name__)


class StoreError(Exception):
    """Base class for widget-store failures."""

    code = "store_error"


class LayoutError(StoreError):
    """The layout is malformed or violates the v1 contract."""

    code = "invalid_layout"


class PublicationError(StoreError):
    """A publication is malformed or violates the publication contract."""

    code = "invalid_publication"


class PublicationTooLarge(PublicationError):
    """A publication or decoded image exceeds a hard limit."""

    code = "publication_too_large"


class AssetNotFound(StoreError):
    """An immutable asset does not exist."""

    code = "asset_not_found"


class AssetUnavailable(StoreError):
    """An asset record exists but its immutable file is unavailable or altered."""

    code = "asset_unavailable"


class RenderNotReady(PublicationError):
    """A device acknowledged rendering before downloading the revision."""

    code = "render_not_downloaded"


class RateLimitError(StoreError):
    """Too many layout pushes for one widget in the rate window."""

    code = "rate_limited"


class PairingError(StoreError):
    """A pairing code was unknown, already used, or expired."""

    code = "invalid_or_expired_code"


class ActionIntentError(StoreError):
    """An action tap could not be safely queued."""

    code = "invalid_action_intent"


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def _default_hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".hermes"


def _hermes_home() -> Path:
    try:
        # Optional Hermes-host module; absent in tests, scripts, and the Android build.
        get_hermes_home = importlib.import_module("hermes_constants").get_hermes_home
    except ImportError:
        return _default_hermes_home()
    try:
        return Path(get_hermes_home())
    except (OSError, RuntimeError, TypeError, ValueError):
        return _default_hermes_home()


def data_dir() -> Path:
    """Directory holding the widget DB and tokens (override: HERMES_WIDGET_DIR)."""
    override = os.environ.get("HERMES_WIDGET_DIR", "").strip()
    base = Path(override) if override else _hermes_home() / "widget"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / "widget.db"


def agent_token_path() -> Path:
    return data_dir() / "agent_token"


def assets_dir() -> Path:
    """Directory holding immutable publication assets."""
    path = data_dir() / "assets"
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def asset_path(asset_id: str) -> Path:
    """Resolve an asset id without allowing path traversal."""
    if not isinstance(asset_id, str) or not re.fullmatch(r"asset_[0-9a-f]{24}", asset_id):
        raise AssetNotFound("asset does not exist")
    root = assets_dir().resolve()
    path = (root / asset_id).resolve()
    if path.parent != root:
        raise AssetNotFound("asset does not exist")
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch_now() -> int:
    try:
        return int(time.time())
    except (OSError, OverflowError, ValueError) as exc:
        raise StoreError("system clock is unavailable") from exc


def _as_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise StoreError(f"{label} is invalid") from exc


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the store schema without relying on process lifetime."""
    with _LOCK:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS widgets ("
            "widget_id TEXT PRIMARY KEY, layout_json TEXT NOT NULL, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS devices ("
            "device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, label TEXT, "
            "created_at TEXT, last_seen_at TEXT, revoked INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, widget_id TEXT NOT NULL, "
            "device_id TEXT NOT NULL, event TEXT NOT NULL, payload TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pairing_codes ("
            "code TEXT PRIMARY KEY, device_id TEXT, expires_at INTEGER NOT NULL, "
            "consumed INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS widget_devices ("
            "widget_id TEXT NOT NULL, device_id TEXT NOT NULL, "
            "PRIMARY KEY (widget_id, device_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS assets ("
            "asset_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, media_type TEXT NOT NULL, "
            "byte_length INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS assets_digest_idx "
            "ON assets(sha256, media_type, byte_length, width, height)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS publications ("
            "widget_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "publication_id TEXT NOT NULL, payload_json TEXT NOT NULL, "
            "published_at TEXT NOT NULL, expires_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS publication_fetches ("
            "widget_id TEXT NOT NULL, device_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "fetched_at TEXT NOT NULL, downloaded_at TEXT, "
            "PRIMARY KEY (widget_id, device_id, revision))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS publication_acks ("
            "widget_id TEXT NOT NULL, device_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "rendered_at TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, "
            "PRIMARY KEY (widget_id, device_id, revision))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS publication_revisions ("
            "widget_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "published_at TEXT NOT NULL, expires_at TEXT, max_age_seconds INTEGER, "
            "kind TEXT, title TEXT, summary TEXT, payload_json TEXT NOT NULL, "
            "superseded_at TEXT, superseded_reason TEXT, "
            "PRIMARY KEY (widget_id, revision))"
        )
        # Additive tables keep upgrades safe for existing widget.db files.  Priority
        # is intentionally separate from the legacy revision table: old rows have no
        # priority and must remain readable.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS widget_settings ("
            "widget_id TEXT PRIMARY KEY, quiet_start TEXT, quiet_end TEXT, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS publication_priorities ("
            "widget_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "requested_priority TEXT NOT NULL, effective_priority TEXT NOT NULL, "
            "degraded_reason TEXT, created_at TEXT NOT NULL, "
            "PRIMARY KEY (widget_id, revision))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS publication_nudges ("
            "widget_id TEXT NOT NULL, revision INTEGER NOT NULL, device_id TEXT NOT NULL, "
            "status TEXT NOT NULL, attempted_at TEXT NOT NULL, sent_at TEXT, detail TEXT, "
            "PRIMARY KEY (widget_id, revision, device_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS delivery_receipts ("
            "widget_id TEXT NOT NULL, device_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "state TEXT NOT NULL, occurred_at TEXT NOT NULL, detail TEXT, "
            "PRIMARY KEY (widget_id, device_id, revision, state))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_push_endpoints ("
            "device_id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, endpoint_hash TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS widget_instances ("
            "device_id TEXT NOT NULL, widget_id TEXT NOT NULL, instance_id TEXT NOT NULL, "
            "size_class TEXT NOT NULL, width_dp INTEGER NOT NULL, height_dp INTEGER NOT NULL, "
            "width_px INTEGER NOT NULL, height_px INTEGER NOT NULL, reported_at TEXT NOT NULL, "
            "PRIMARY KEY (device_id, widget_id, instance_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS action_intents ("
            "intent_id TEXT PRIMARY KEY, widget_id TEXT NOT NULL, device_id TEXT NOT NULL, "
            "event_id INTEGER NOT NULL, item_id TEXT NOT NULL, action_class TEXT NOT NULL, "
            "status TEXT NOT NULL, source_revision INTEGER NOT NULL, payload_json TEXT NOT NULL, "
            "result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "expires_at TEXT NOT NULL, UNIQUE (event_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS action_audit ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT, widget_id TEXT NOT NULL, "
            "device_id TEXT NOT NULL, item_id TEXT NOT NULL, action_class TEXT NOT NULL, "
            "revision INTEGER NOT NULL, event TEXT NOT NULL, outcome TEXT NOT NULL, "
            "detail TEXT, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS action_intents_status_idx "
            "ON action_intents(widget_id, status, created_at)"
        )
        conn.commit()
        _initialised.add(str(db_path()))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=15)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def init_db() -> None:
    """Create the schema if needed. Safe to call repeatedly."""
    with _LOCK:
        conn = _connect()
        try:
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def get_agent_token(create: bool = True) -> str | None:
    """Return the agent bearer token, generating and persisting one if allowed."""
    env = os.environ.get(AGENT_TOKEN_ENV, "").strip()
    if env:
        return env
    path = agent_token_path()
    with _LOCK:
        if path.exists():
            token = path.read_text(encoding="utf-8").strip()
            if token:
                return token
        if not create:
            return None
        token = secrets.token_urlsafe(32)
        path.write_text(token + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        return token


def verify_agent_token(token: str) -> bool:
    if not token:
        return False
    expected = get_agent_token(create=False)
    if not expected:
        return False
    return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------

def check_push_rate(widget_id: str) -> None:
    now = time.time()
    with _LOCK:
        stamps = _rate.setdefault(widget_id, [])
        stamps[:] = [t for t in stamps if now - t < PUSH_WINDOW_SECONDS]
        if len(stamps) >= PUSH_MAX_PER_WINDOW:
            raise RateLimitError(
                f"push rate limit exceeded for {widget_id!r}: "
                f"max {PUSH_MAX_PER_WINDOW} per {PUSH_WINDOW_SECONDS}s"
            )
        stamps.append(now)


def validate_layout(layout: dict) -> None:
    """Store-level wrapper: turn a validate.ValidationError into a LayoutError."""
    try:
        _validate(layout)
    except ValidationError as exc:
        raise LayoutError(str(exc)) from exc


def backup_db() -> Path | None:
    """Copy widget.db to widget.db.bak.<utc> and return the copy.

    `hermes widget rollback` restores the newest of these, so whichever command is about to
    change the database takes the copy first. Returns None when there is no DB yet,
    and never raises: a failed backup must not block the command that requested it.
    """
    try:
        src = db_path()
        if not src.exists():
            return None
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dst = src.with_name(f"widget.db.bak.{ts}")
        import shutil
        shutil.copy2(str(src), str(dst))
        return dst
    except Exception:  # noqa: BLE001 - best effort, the caller proceeds regardless
        logger = logging.getLogger(__name__)
        logger.warning("hermes-widget: database backup failed", exc_info=True)
        return None


def inspect_widget(widget_id: str, layout: dict) -> dict:
    """Dry run: validate and describe a layout **without storing or rate-limiting**.

    Mirrors put_widget's order (inject widgetId, then validate) so a green dry run means the
    push would have succeeded. The only difference is that nothing is written and
    check_push_rate is never called.
    """
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise LayoutError("widget_id must be a non-empty string of at most 128 characters")
    if not isinstance(layout, dict):
        raise LayoutError("layout must be a JSON object")

    candidate = dict(layout)
    candidate["widgetId"] = widget_id

    try:
        report = _inspect_layout(candidate, list_widget_instances(widget_id))
    except ValidationError as exc:
        raise LayoutError(str(exc)) from exc

    report["widgetId"] = widget_id
    return report


def put_widget(widget_id: str, layout: dict) -> dict:
    """Validate and store a layout, returning the small success envelope."""
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise LayoutError("widget_id must be a non-empty string of at most 128 characters")
    if not isinstance(layout, dict):
        raise LayoutError("layout must be a JSON object")

    stored = dict(layout)
    stored["widgetId"] = widget_id
    # v2 is enforced: a layout that is not version 2 is rejected, not migrated.
    validate_layout(stored)
    check_push_rate(widget_id)

    updated_at = stored.get("updatedAt") or _now()
    stored["updatedAt"] = updated_at
    payload = json.dumps(stored, separators=(",", ":"), ensure_ascii=False)

    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO widgets (widget_id, layout_json, updated_at) "
                "VALUES (?, ?, ?)",
                (widget_id, payload, updated_at),
            )
            for row in conn.execute(
                "SELECT device_id FROM devices WHERE revoked = 0"
            ).fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO widget_devices (widget_id, device_id) VALUES (?, ?)",
                    (widget_id, row["device_id"]),
                )
            conn.commit()
        finally:
            conn.close()
    try:
        report = _inspect_layout(stored, list_widget_instances(widget_id))
        warnings = report.get("capacityWarnings", [])
    except Exception:  # noqa: BLE001 - warnings are advisory after the write committed
        warnings = []
    return {"ok": True, "widgetId": widget_id, "updatedAt": updated_at, "warnings": warnings}


def get_widget(widget_id: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT layout_json FROM widgets WHERE widget_id = ?", (widget_id,)
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    try:
        layout = json.loads(row["layout_json"])
    except (TypeError, ValueError) as exc:
        raise StoreError("stored layout is corrupt") from exc
    if not isinstance(layout, dict):
        raise StoreError("stored layout is corrupt")
    return layout


def _publication_semantics(publication: dict) -> dict:
    content = publication.get("content")
    if isinstance(content, dict):
        content = {key: value for key, value in content.items() if key != "assetId"}
    return {
        "version": publication.get("version"),
        "widgetId": publication.get("widgetId"),
        "kind": publication.get("kind"),
        "title": publication.get("title"),
        "summary": publication.get("summary"),
        "expiresAt": publication.get("expiresAt"),
        "maxAgeSeconds": publication.get("maxAgeSeconds"),
        "priority": publication.get("priority", "normal"),
        "requestedPriority": publication.get("requestedPriority", publication.get("priority", "normal")),
        "itemId": publication.get("itemId"),
        "actions": publication.get("actions", []),
        "content": content,
    }


def _priority_counts(widget_id: str, now: float | None = None) -> tuple[int, int]:
    current = time.time() if now is None else now
    hour = _iso_from_epoch(current - HIGH_PRIORITY_WINDOW_SECONDS)
    day = _iso_from_epoch(current - HIGH_PRIORITY_DAY_SECONDS)
    with _LOCK:
        conn = _connect()
        try:
            hour_count = conn.execute(
                "SELECT COUNT(*) FROM publication_priorities WHERE widget_id = ? "
                "AND requested_priority = 'high' AND created_at > ?",
                (widget_id, hour),
            ).fetchone()[0]
            day_count = conn.execute(
                "SELECT COUNT(*) FROM publication_priorities WHERE widget_id = ? "
                "AND requested_priority = 'high' AND created_at > ?",
                (widget_id, day),
            ).fetchone()[0]
            return int(hour_count), int(day_count)
        finally:
            conn.close()


def _priority_effective(widget_id: str, requested: str) -> tuple[str, str | None]:
    if requested != "high":
        return "normal", None
    settings = get_widget_settings(widget_id)
    quiet = settings.get("quietHours")
    if isinstance(quiet, dict) and _in_quiet_hours(quiet, datetime.now(timezone.utc)):
        return "normal", "quiet_hours"
    hour_count, day_count = _priority_counts(widget_id)
    if hour_count >= HIGH_PRIORITY_MAX_PER_HOUR:
        return "normal", "high_priority_hour_limit"
    if day_count >= HIGH_PRIORITY_MAX_PER_DAY:
        return "normal", "high_priority_day_limit"
    return "high", None


def _in_quiet_hours(quiet: dict, current: datetime) -> bool:
    try:
        start = datetime.strptime(str(quiet["start"]), "%H:%M").time()
        end = datetime.strptime(str(quiet["end"]), "%H:%M").time()
    except (KeyError, TypeError, ValueError):
        return False
    value = current.timetz().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= value < end
    return value >= start or value < end


def _receipt(
    conn: sqlite3.Connection,
    widget_id: str,
    device_id: str,
    revision: int,
    state: str,
    *,
    detail: str | None = None,
    occurred_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO delivery_receipts "
        "(widget_id, device_id, revision, state, occurred_at, detail) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(widget_id, device_id, revision, state) DO UPDATE SET "
        "occurred_at=excluded.occurred_at, detail=excluded.detail",
        (widget_id, device_id, revision, state, occurred_at or _now(), detail),
    )


def _capacity_warnings_for_publication(publication: dict, widget_id: str) -> list[dict[str, str]]:
    """Advisory fit checks against the smallest size a device registered."""
    instances = list_widget_instances(widget_id)
    if not instances:
        return []
    smallest = min(instances, key=lambda item: (item["widthDp"], item["heightDp"]))
    warnings: list[dict[str, str]] = []
    if publication.get("kind") == "text":
        text = (publication.get("content") or {}).get("text", "")
        if smallest["sizeClass"] == "2x2" and len(text) > 180:
            warnings.append({
                "code": "TEXT_MAY_CLIP_2X2",
                "detail": f"text is {len(text)} characters and may clip on the smallest registered {smallest['sizeClass']} instance",
            })
    else:
        content = publication.get("content") or {}
        width, height = content.get("width"), content.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            target_ratio = smallest["widthDp"] / max(1, smallest["heightDp"])
            source_ratio = width / height
            if abs(source_ratio - target_ratio) / target_ratio > 0.35:
                warnings.append({
                    "code": "IMAGE_MAY_LETTERBOX",
                    "detail": f"image aspect {source_ratio:.2f} differs from the smallest registered {smallest['sizeClass']} aspect {target_ratio:.2f}; it will be letterboxed",
                })
    return warnings


def _publication_expired(publication: dict | None, now: datetime | None = None) -> bool:
    if not publication or not publication.get("expiresAt"):
        return False
    current = now or datetime.now(timezone.utc)
    try:
        expiry = datetime.fromisoformat(str(publication["expiresAt"]).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if expiry.tzinfo is None:
        return True
    return expiry <= current.astimezone(timezone.utc)


def _publication_stale(publication: dict | None, now: datetime | None = None) -> bool:
    """True once a publication is past its maxAgeSeconds freshness window."""
    if not publication:
        return False
    raw = publication.get("maxAgeSeconds")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        return False
    published = publication.get("publishedAt")
    if not published:
        return False
    try:
        published_at = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if published_at.tzinfo is None:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current - published_at.astimezone(timezone.utc) > timedelta(seconds=raw)


def _write_immutable_asset(conn: sqlite3.Connection, asset: Any) -> tuple[str, Path | None]:
    """Reuse an identical asset or atomically install a new immutable file."""
    row = conn.execute(
        "SELECT asset_id FROM assets WHERE sha256 = ? AND media_type = ? "
        "AND byte_length = ? AND width = ? AND height = ?",
        (asset.sha256, asset.media_type, len(asset.data), asset.width, asset.height),
    ).fetchone()
    if row:
        existing_id = str(row["asset_id"])
        with contextlib.suppress(AssetNotFound, OSError):
            existing = asset_path(existing_id)
            if existing.is_file() and existing.stat().st_size == len(asset.data):
                return existing_id, None

    asset_id = "asset_" + secrets.token_hex(12)
    final_path: Path | None = None
    temporary_path: str | None = None
    file_descriptor: int | None = None
    try:
        file_descriptor, temporary_path = tempfile.mkstemp(prefix=".asset-", dir=str(assets_dir()))
        with os.fdopen(file_descriptor, "wb") as output:
            file_descriptor = None
            output.write(asset.data)
            output.flush()
            os.fsync(output.fileno())
        with contextlib.suppress(OSError):
            os.chmod(temporary_path, 0o600)
        final_path = assets_dir() / asset_id
        os.replace(temporary_path, final_path)
        temporary_path = None
        conn.execute(
            "INSERT INTO assets (asset_id, sha256, media_type, byte_length, width, height, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                asset.sha256,
                asset.media_type,
                len(asset.data),
                asset.width,
                asset.height,
                _now(),
            ),
        )
        return asset_id, final_path
    except Exception:
        if file_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(file_descriptor)
        for path in (temporary_path, str(final_path) if final_path else None):
            if path:
                with contextlib.suppress(OSError):
                    os.unlink(path)
        raise


def _dispatch_priority_nudges(widget_id: str, revision: int) -> None:
    """Best-effort, content-free wake delivery for one high-priority revision."""
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT d.device_id, p.endpoint FROM devices d "
                "LEFT JOIN device_push_endpoints p ON p.device_id = d.device_id "
                "JOIN widget_devices wd ON wd.device_id = d.device_id "
                "WHERE d.revoked = 0 AND wd.widget_id = ? ORDER BY d.device_id",
                (widget_id,),
            ).fetchall()
        finally:
            conn.close()
    for row in rows:
        device_id = str(row["device_id"])
        endpoint = row["endpoint"]
        attempted_at = _now()
        if not endpoint:
            _record_nudge(widget_id, revision, device_id, "not_subscribed", attempted_at, None, "no UnifiedPush endpoint")
            continue
        try:
            _push.wake(str(endpoint))
        except (ValueError, _push.PushError) as exc:
            _record_nudge(widget_id, revision, device_id, "failed", attempted_at, None, str(exc))
            continue
        sent_at = _now()
        _record_nudge(widget_id, revision, device_id, "sent", attempted_at, sent_at, None)


def _record_nudge(
    widget_id: str,
    revision: int,
    device_id: str,
    status: str,
    attempted_at: str,
    sent_at: str | None,
    detail: str | None,
) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO publication_nudges "
                "(widget_id, revision, device_id, status, attempted_at, sent_at, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(widget_id, revision, device_id) DO UPDATE SET "
                "status=excluded.status, attempted_at=excluded.attempted_at, "
                "sent_at=excluded.sent_at, detail=excluded.detail",
                (widget_id, revision, device_id, status, attempted_at, sent_at, detail),
            )
            if status == "sent":
                _receipt(conn, widget_id, device_id, revision, "nudge_sent", occurred_at=sent_at)
            conn.commit()
        finally:
            conn.close()


def put_publication(
    widget_id: str,
    *,
    title: Any,
    summary: Any,
    text: str | None = None,
    svg: str | None = None,
    file_path: str | os.PathLike[str] | None = None,
    expires_at: str | datetime | None = None,
    ttl_seconds: int | None = None,
    max_age_seconds: int | None = None,
    priority: str = "normal",
    item_id: str | None = None,
    actions: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> dict:
    """Validate, store, and atomically publish one text or visual revision."""
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise PublicationError("widget_id must be a non-empty string of at most 128 characters")
    try:
        prepared = prepare_publication(
            title=title,
            summary=summary,
            text=text,
            svg=svg,
            file_path=file_path,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            max_age_seconds=max_age_seconds,
            priority=priority,
            item_id=item_id,
            actions=actions,
        )
    except _PublicationInputTooLarge as exc:
        raise PublicationTooLarge(str(exc)) from exc
    except PublicationInputError as exc:
        raise PublicationError(str(exc)) from exc

    check_push_rate(widget_id)
    effective_priority, degraded_reason = _priority_effective(widget_id, prepared.priority)
    content: dict[str, Any]
    if prepared.kind == "text":
        content = {"type": "text", "mediaType": "text/plain; charset=utf-8", "text": prepared.text}
    else:
        assert prepared.asset is not None
        content = {
            "type": "image",
            "mediaType": prepared.asset.media_type,
            "width": prepared.asset.width,
            "height": prepared.asset.height,
            "bytes": len(prepared.asset.data),
            "sha256": prepared.asset.sha256,
        }
    now = _now()
    new_asset_path: Path | None = None
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            old_row = conn.execute(
                "SELECT revision, payload_json FROM publications WHERE widget_id = ?",
                (widget_id,),
            ).fetchone()
            if old_row:
                try:
                    old_payload = json.loads(old_row["payload_json"])
                except (TypeError, ValueError) as exc:
                    raise StoreError("stored publication is corrupt") from exc
                candidate = {
                    "version": _publication_capabilities()["publicationVersion"],
                    "widgetId": widget_id,
                    "kind": prepared.kind,
                    "title": prepared.title,
                    "summary": prepared.summary,
                    "expiresAt": prepared.expires_at,
                    "maxAgeSeconds": prepared.max_age_seconds,
                    "priority": effective_priority,
                    "requestedPriority": prepared.priority,
                    "priorityDegradedReason": degraded_reason,
                    "itemId": prepared.item_id,
                    "actions": list(prepared.actions),
                    "content": content,
                }
                if (
                    not _publication_expired(old_payload)
                    and _publication_semantics(old_payload) == _publication_semantics(candidate)
                ):
                    conn.commit()
                    result = dict(old_payload)
                    result["unchanged"] = True
                    return result

            if prepared.asset is not None:
                asset_id, new_asset_path = _write_immutable_asset(conn, prepared.asset)
                content = {**content, "assetId": asset_id}
            revision = _as_int(old_row["revision"], "publication revision") + 1 if old_row else 1
            publication_id = "pub_" + uuid.uuid4().hex
            publication = {
                "version": _publication_capabilities()["publicationVersion"],
                "widgetId": widget_id,
                "publicationId": publication_id,
                "revision": revision,
                "kind": prepared.kind,
                "title": prepared.title,
                "summary": prepared.summary,
                "publishedAt": now,
                "expiresAt": prepared.expires_at,
                "maxAgeSeconds": prepared.max_age_seconds,
                "priority": effective_priority,
                "requestedPriority": prepared.priority,
                "priorityDegradedReason": degraded_reason,
                "itemId": prepared.item_id,
                "actions": list(prepared.actions),
                "content": content,
            }
            conn.execute(
                "INSERT INTO publications (widget_id, revision, publication_id, payload_json, published_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(widget_id) DO UPDATE SET revision=excluded.revision, "
                "publication_id=excluded.publication_id, payload_json=excluded.payload_json, "
                "published_at=excluded.published_at, expires_at=excluded.expires_at",
                (
                    widget_id,
                    revision,
                    publication_id,
                    json.dumps(publication, separators=(",", ":"), ensure_ascii=False),
                    now,
                    prepared.expires_at,
                ),
            )
            # Keep every revision as history instead of letting the current-row
            # upsert erase it; mark a superseded revision so "not polled yet" is
            # distinguishable from "lost".
            conn.execute(
                "INSERT INTO publication_revisions "
                "(widget_id, revision, published_at, expires_at, max_age_seconds, kind, title, summary, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(widget_id, revision) DO UPDATE SET payload_json=excluded.payload_json",
                (
                    widget_id,
                    revision,
                    now,
                    prepared.expires_at,
                    prepared.max_age_seconds,
                    prepared.kind,
                    prepared.title,
                    prepared.summary,
                    json.dumps(publication, separators=(",", ":"), ensure_ascii=False),
                ),
            )
            conn.execute(
                "INSERT INTO publication_priorities "
                "(widget_id, revision, requested_priority, effective_priority, degraded_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (widget_id, revision, prepared.priority, effective_priority, degraded_reason, now),
            )
            conn.execute(
                "UPDATE publication_revisions SET superseded_at = ?, "
                "superseded_reason = COALESCE(superseded_reason, 'superseded_by_revision_' || ?) "
                "WHERE widget_id = ? AND revision < ? AND superseded_at IS NULL",
                (now, revision, widget_id, revision),
            )
            for device in conn.execute("SELECT device_id FROM devices WHERE revoked = 0").fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO widget_devices (widget_id, device_id) VALUES (?, ?)",
                    (widget_id, device["device_id"]),
                )
            conn.commit()
            if effective_priority == "high":
                try:
                    _dispatch_priority_nudges(widget_id, revision)
                except Exception:  # noqa: BLE001 - a wake failure must not lose a publication
                    logger.warning("priority wake dispatch failed", exc_info=True)
            try:
                publication["warnings"] = _capacity_warnings_for_publication(publication, widget_id)
            except Exception:  # noqa: BLE001 - advisory warnings must not fail a commit
                publication["warnings"] = []
            return publication
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            if new_asset_path is not None:
                with contextlib.suppress(OSError):
                    new_asset_path.unlink()
            raise
        finally:
            conn.close()


def get_widget_settings(widget_id: str) -> dict:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT quiet_start, quiet_end, updated_at FROM widget_settings WHERE widget_id = ?",
                (widget_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row or not row["quiet_start"] or not row["quiet_end"]:
        return {"widgetId": widget_id, "quietHours": None, "updatedAt": row["updated_at"] if row else None}
    return {
        "widgetId": widget_id,
        "quietHours": {"start": row["quiet_start"], "end": row["quiet_end"]},
        "updatedAt": row["updated_at"],
    }


def set_widget_settings(widget_id: str, quiet_hours: Any) -> dict:
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise StoreError("widget_id is required")
    clean = None
    if quiet_hours is not None:
        if not isinstance(quiet_hours, dict):
            raise StoreError("quiet_hours must be an object or null")
        start, end = quiet_hours.get("start"), quiet_hours.get("end")
        time_pattern = r"(?:[01]\d|2[0-3]):[0-5]\d"
        if (not isinstance(start, str) or not isinstance(end, str)
                or not re.fullmatch(time_pattern, start)
                or not re.fullmatch(time_pattern, end)):
            raise StoreError("quiet_hours start/end must use HH:MM")
        clean = {"start": start, "end": end}
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO widget_settings (widget_id, quiet_start, quiet_end, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(widget_id) DO UPDATE SET quiet_start=excluded.quiet_start, "
                "quiet_end=excluded.quiet_end, updated_at=excluded.updated_at",
                (widget_id, clean["start"] if clean else None, clean["end"] if clean else None, now),
            )
            conn.commit()
        finally:
            conn.close()
    return get_widget_settings(widget_id)


def get_publication(widget_id: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT payload_json FROM publications WHERE widget_id = ?", (widget_id,)
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    try:
        publication = json.loads(row["payload_json"])
    except (TypeError, ValueError) as exc:
        raise StoreError("stored publication is corrupt") from exc
    if not isinstance(publication, dict):
        raise StoreError("stored publication is corrupt")
    publication["expired"] = _publication_expired(publication)
    publication["stale"] = _publication_stale(publication)
    # Add live intent outcomes without rewriting the immutable publication row.
    item_ids = {
        item for item in (
            [publication.get("itemId")] if isinstance(publication.get("itemId"), str) else []
        ) if item
    }
    item_ids.update(
        action.get("itemId") for action in publication.get("actions", [])
        if isinstance(action, dict) and isinstance(action.get("itemId"), str)
    )
    if item_ids:
        with _LOCK:
            conn = _connect()
            try:
                _expire_stale_intents(conn)
                conn.commit()
                placeholders = ",".join("?" for _ in item_ids)
                rows = conn.execute(
                    f"SELECT item_id, status, result, updated_at FROM action_intents "
                    f"WHERE widget_id = ? AND item_id IN ({placeholders}) ORDER BY updated_at DESC",
                    [widget_id, *item_ids],
                ).fetchall()
            finally:
                conn.close()
        latest: dict[str, dict] = {}
        for row in rows:
            latest.setdefault(str(row["item_id"]), {
                "status": row["status"], "result": row["result"], "updatedAt": row["updated_at"]
            })
        if latest:
            publication["actionStates"] = latest
    return publication


def record_publication_fetch(
    widget_id: str, device_id: str, revision: int, *, downloaded: bool = False
) -> None:
    """Record a successful authenticated metadata fetch for a device."""
    if not device_id:
        return
    revision_value = _as_int(revision, "publication revision")
    with _LOCK:
        conn = _connect()
        try:
            timestamp = _now()
            conn.execute(
                "INSERT INTO publication_fetches (widget_id, device_id, revision, fetched_at, downloaded_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(widget_id, device_id, revision) DO UPDATE SET "
                "fetched_at=excluded.fetched_at, downloaded_at=COALESCE(publication_fetches.downloaded_at, excluded.downloaded_at)",
                (widget_id, device_id, revision_value, timestamp, timestamp if downloaded else None),
            )
            _receipt(conn, widget_id, device_id, revision_value, "fetched", occurred_at=timestamp)
            if downloaded:
                _receipt(conn, widget_id, device_id, revision_value, "downloaded", occurred_at=timestamp)
            conn.commit()
        finally:
            conn.close()


def record_asset_download(widget_id: str, device_id: str, revision: int, asset_id: str) -> None:
    """Record a successful complete asset download after metadata validation."""
    publication = get_publication(widget_id)
    revision_value = _as_int(revision, "publication revision")
    current_revision = _as_int(publication.get("revision", 0), "publication revision") if publication else 0
    if not publication or revision_value != current_revision:
        raise RenderNotReady("publication revision is not current")
    if publication.get("content", {}).get("assetId") != asset_id:
        raise AssetNotFound("asset does not belong to this publication")
    with _LOCK:
        conn = _connect()
        try:
            timestamp = _now()
            conn.execute(
                "INSERT INTO publication_fetches (widget_id, device_id, revision, fetched_at, downloaded_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(widget_id, device_id, revision) DO UPDATE SET "
                "fetched_at=excluded.fetched_at, downloaded_at=excluded.downloaded_at",
                (widget_id, device_id, revision_value, timestamp, timestamp),
            )
            _receipt(conn, widget_id, device_id, revision_value, "fetched", occurred_at=timestamp)
            _receipt(conn, widget_id, device_id, revision_value, "downloaded", occurred_at=timestamp)
            conn.commit()
        finally:
            conn.close()


def acknowledge_publication_render(
    widget_id: str, device_id: str, revision: int, width: int, height: int,
    *, status: str = "render_submitted",
) -> dict:
    """Record a render submission, never a claim that the user saw it."""
    if not device_id:
        raise RenderNotReady("a device token is required")
    if status not in {"render_submitted", "rendered"}:
        raise PublicationError("render status must be render_submitted or rendered")
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
        raise PublicationError("render dimensions must be integers")
    if not 1 <= width <= 10_000 or not 1 <= height <= 10_000:
        raise PublicationError("render dimensions are out of range")
    publication = get_publication(widget_id)
    revision_value = _as_int(revision, "publication revision")
    current_revision = _as_int(publication.get("revision", 0), "publication revision") if publication else 0
    if not publication or revision_value > current_revision:
        raise RenderNotReady("publication revision is not available")
    with _LOCK:
        conn = _connect()
        try:
            fetched = conn.execute(
                "SELECT downloaded_at FROM publication_fetches WHERE widget_id = ? AND device_id = ? AND revision <= ? "
                "ORDER BY revision DESC LIMIT 1",
                (widget_id, device_id, revision_value),
            ).fetchone()
            if fetched is None or not fetched["downloaded_at"]:
                raise RenderNotReady("publication must be downloaded before render acknowledgement")
            timestamp = _now()
            conn.execute(
                "INSERT INTO publication_acks (widget_id, device_id, revision, rendered_at, width, height) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(widget_id, device_id, revision) DO UPDATE SET "
                "rendered_at=excluded.rendered_at, width=excluded.width, height=excluded.height",
                (widget_id, device_id, revision_value, timestamp, width, height),
            )
            _receipt(conn, widget_id, device_id, revision_value, "render_submitted", occurred_at=timestamp)
            if status == "rendered":
                _receipt(conn, widget_id, device_id, revision_value, "rendered", occurred_at=timestamp)
            conn.commit()
        finally:
            conn.close()
    return {
        "ok": True,
        "widgetId": widget_id,
        "revision": revision_value,
        "status": status,
        "renderedAt": timestamp,
        "width": width,
        "height": height,
    }


def get_asset(asset_id: str) -> dict:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT asset_id, sha256, media_type, byte_length, width, height, created_at "
                "FROM assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        raise AssetNotFound("asset does not exist")
    path = asset_path(asset_id)
    if not path.is_file():
        raise AssetUnavailable("asset is temporarily unavailable")
    return {
        "assetId": row["asset_id"],
        "sha256": row["sha256"],
        "mediaType": row["media_type"],
        "bytes": _as_int(row["byte_length"], "asset byte length"),
        "width": _as_int(row["width"], "asset width"),
        "height": _as_int(row["height"], "asset height"),
        "createdAt": row["created_at"],
        "path": path,
    }


def read_asset(asset_id: str) -> tuple[dict, bytes]:
    metadata = get_asset(asset_id)
    try:
        data = metadata["path"].read_bytes()
    except OSError as exc:
        raise AssetUnavailable("asset is temporarily unavailable") from exc
    if len(data) != metadata["bytes"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
        raise AssetUnavailable("asset integrity check failed")
    return metadata, data


def publication_status(widget_id: str = DEFAULT_WIDGET_ID) -> dict:
    publication = get_publication(widget_id)
    with _LOCK:
        conn = _connect()
        try:
            devices = conn.execute(
                "SELECT device_id, label, revoked FROM devices ORDER BY created_at"
            ).fetchall()
            fetches = {
                (row["device_id"], int(row["revision"])): row
                for row in conn.execute(
                    "SELECT device_id, revision, fetched_at, downloaded_at FROM publication_fetches "
                    "WHERE widget_id = ?",
                    (widget_id,),
                ).fetchall()
            }
            acks = {
                (row["device_id"], int(row["revision"])): row
                for row in conn.execute(
                    "SELECT device_id, revision, rendered_at, width, height FROM publication_acks "
                    "WHERE widget_id = ?",
                    (widget_id,),
                ).fetchall()
            }
            nudges = {
                (row["device_id"], int(row["revision"])): row
                for row in conn.execute(
                    "SELECT device_id, revision, status, attempted_at, sent_at, detail "
                    "FROM publication_nudges WHERE widget_id = ?", (widget_id,)
                ).fetchall()
            }
            receipts = conn.execute(
                "SELECT device_id, revision, state, occurred_at, detail FROM delivery_receipts "
                "WHERE widget_id = ? ORDER BY occurred_at", (widget_id,)
            ).fetchall()
            priorities = {
                int(row["revision"]): {
                    "requested_priority": row["requested_priority"],
                    "effective_priority": row["effective_priority"],
                    "degraded_reason": row["degraded_reason"],
                }
                for row in conn.execute(
                    "SELECT revision, requested_priority, effective_priority, degraded_reason "
                    "FROM publication_priorities WHERE widget_id = ?", (widget_id,)
                ).fetchall()
            }
            intent_rows = conn.execute(
                "SELECT intent_id, item_id, action_class, status, source_revision, result, created_at, updated_at "
                "FROM action_intents WHERE widget_id = ? ORDER BY created_at DESC LIMIT 200", (widget_id,)
            ).fetchall()
            revision_rows = conn.execute(
                "SELECT revision, published_at, expires_at, max_age_seconds, superseded_at, "
                "superseded_reason, kind, title FROM publication_revisions "
                "WHERE widget_id = ? ORDER BY revision DESC",
                (widget_id,),
            ).fetchall()
        finally:
            conn.close()
    current_revision = _as_int(publication.get("revision", 0), "publication revision") if publication else 0
    recorded_revisions = [
        _as_int(row["revision"], "publication revision") for row in revision_rows
    ]
    history_max = max(recorded_revisions, default=0)
    recorded_set = set(recorded_revisions)
    missing_history = (
        [revision for revision in range(1, current_revision + 1) if revision not in recorded_set]
        if 0 < current_revision <= 10_000
        else []
    )
    history_gap = bool(
        current_revision
        and (current_revision > history_max or len(recorded_set) < current_revision)
    )
    history_warnings: list[dict[str, Any]] = []
    if history_gap:
        history_warnings.append({
            "code": "revision_history_gap",
            "detail": (
                f"publications is at revision {current_revision}, but publication_revisions "
                f"only reaches {history_max} ({len(recorded_revisions)} rows); revision history "
                "may have been removed or truncated, so skipped revisions cannot be treated as complete"
            ),
            "currentRevision": current_revision,
            "maxRecordedRevision": history_max,
            "recordedCount": len(recorded_revisions),
            "missingRevisions": missing_history[:100],
            "missingRevisionCount": current_revision - len(recorded_set) if current_revision else 0,
        })
    revision_history = {
        "currentRevision": current_revision,
        "maxRecordedRevision": history_max,
        "recordedCount": len(recorded_revisions),
        "missingRevisions": missing_history[:100],
        "missingRevisionCount": current_revision - len(recorded_set) if current_revision else 0,
        "gap": history_gap,
    }
    stale = bool(publication) and _publication_stale(publication)
    delivery = []
    receipt_map: dict[tuple[str, int], list[dict[str, Any]]] = {}
    receipt_rank = {"nudge_sent": 0, "fetched": 1, "downloaded": 2, "render_submitted": 3, "rendered": 4}
    for row in receipts:
        receipt_map.setdefault((str(row["device_id"]), int(row["revision"])), []).append({
            "state": row["state"],
            "at": row["occurred_at"],
            "detail": row["detail"],
        })
    for items in receipt_map.values():
        items.sort(key=lambda item: (receipt_rank.get(item["state"], 99), item["at"]))
    for device in devices:
        device_id = str(device["device_id"])
        device_fetches = {
            revision: row for (owner, revision), row in fetches.items() if owner == device_id
        }
        current_fetch = device_fetches.get(current_revision)
        current_ack = acks.get((device_id, current_revision))
        current_nudge = nudges.get((device_id, current_revision))
        downloaded = bool(current_fetch and current_fetch["downloaded_at"])
        render_submitted = bool(current_ack)
        state = "render_submitted" if render_submitted else "downloaded" if downloaded else "not_downloaded"
        last_fetched_revision = max(device_fetches) if device_fetches else None
        last_poll_at = max(
            (row["fetched_at"] for row in device_fetches.values() if row["fetched_at"]),
            default=None,
        )
        # Revisions that were replaced before this device ever fetched them are
        # "skipped", not "not_downloaded": the publisher can tell they were lost.
        skipped_revisions = [
            _as_int(row["revision"], "publication revision")
            for row in revision_rows
            if row["superseded_at"]
            and _as_int(row["revision"], "publication revision") not in device_fetches
        ]
        delivery.append(
            {
                "deviceId": device_id,
                "label": device["label"],
                "revoked": bool(device["revoked"]),
                "state": state,
                "downloaded": downloaded,
                "downloadedAt": current_fetch["downloaded_at"] if current_fetch else None,
                "renderSubmitted": render_submitted,
                "renderSubmittedAt": current_ack["rendered_at"] if current_ack else None,
                "rendered": bool(current_ack and any(
                    receipt["state"] == "rendered"
                    for receipt in receipt_map.get((device_id, current_revision), [])
                )),
                "renderedAt": current_ack["rendered_at"] if current_ack and any(
                    receipt["state"] == "rendered"
                    for receipt in receipt_map.get((device_id, current_revision), [])
                ) else None,
                "renderedWidth": current_ack["width"] if current_ack else None,
                "renderedHeight": current_ack["height"] if current_ack else None,
                "nudgeStatus": current_nudge["status"] if current_nudge else "not_sent",
                "nudgeSentAt": current_nudge["sent_at"] if current_nudge else None,
                "fetchedAt": current_fetch["fetched_at"] if current_fetch else None,
                "receipts": receipt_map.get((device_id, current_revision), []),
                "lastFetchedRevision": last_fetched_revision,
                "lastPollAt": last_poll_at,
                "skippedRevisions": skipped_revisions,
            }
        )
    revisions = [
        {
            "revision": _as_int(row["revision"], "publication revision"),
            "publishedAt": row["published_at"],
            "expiresAt": row["expires_at"],
            "maxAgeSeconds": row["max_age_seconds"],
            "kind": row["kind"],
            "title": row["title"],
            "superseded": bool(row["superseded_at"]),
            "supersededAt": row["superseded_at"],
            "supersededReason": row["superseded_reason"],
            "priority": priorities.get(_as_int(row["revision"], "publication revision"), {}).get("effective_priority", "normal") if priorities else "normal",
            "requestedPriority": priorities.get(_as_int(row["revision"], "publication revision"), {}).get("requested_priority", "normal") if priorities else "normal",
            "priorityDegradedReason": priorities.get(_as_int(row["revision"], "publication revision"), {}).get("degraded_reason") if priorities else None,
        }
        for row in revision_rows
    ]
    anomalies = [
        {
            "type": "high_priority_skipped",
            "revision": revision,
            "detail": "high-priority revision was superseded before any device fetched it",
        }
        for revision, priority in priorities.items()
        if devices
        and priority.get("effective_priority") == "high"
        and any(
            int(row["revision"]) == revision and row["superseded_at"]
            for row in revision_rows
        )
        and not any(owner_revision[1] == revision for owner_revision in fetches)
    ]
    return {
        "widgetId": widget_id,
        "state": (
            "empty"
            if not publication
            else "stale"
            if stale
            else "expired"
            if publication.get("expired")
            else "published"
        ),
        "stale": stale,
        "publication": publication,
        "revisions": revisions,
        "revisionHistory": revision_history,
        "warnings": history_warnings,
        "delivery": delivery,
        "inventory": list_widget_instances(widget_id),
        "anomalies": anomalies,
        "intents": [
            {
                "intentId": row["intent_id"],
                "itemId": row["item_id"],
                "actionClass": row["action_class"],
                "status": row["status"],
                "sourceRevision": row["source_revision"],
                "result": row["result"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in intent_rows
        ],
        "actionAudit": get_action_audit(widget_id),
        "pollIntervalSeconds": _publication_capabilities().get("pollIntervalSeconds"),
        "capabilities": publication_capabilities(),
    }


def publication_for_asset(asset_id: str) -> dict | None:
    """Find the current publication that references an immutable asset."""
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute("SELECT payload_json FROM publications").fetchall()
        finally:
            conn.close()
    for row in rows:
        try:
            publication = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(publication, dict) and publication.get("content", {}).get("assetId") == asset_id:
            publication["expired"] = _publication_expired(publication)
            return publication
    return None


def _last_render_metrics() -> dict | None:
    """The most recent render acknowledgement's surface size, if any."""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT width, height FROM publication_acks ORDER BY rendered_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    width = _as_int(row["width"], "rendered width")
    height = _as_int(row["height"], "rendered height")
    if width <= 0 or height <= 0:
        return None
    return {"width": width, "height": height, "aspectRatio": round(width / height, 4)}


def publication_capabilities() -> dict:
    caps = dict(_publication_capabilities())
    last = _last_render_metrics()
    render = dict(caps.get("render") or {})
    render["lastRendered"] = last
    render["recommendedAspectRatio"] = last["aspectRatio"] if last else None
    caps["render"] = render
    inventory = list_widget_instances()
    caps["inventory"] = {
        "sizes": inventory,
        "registeredCount": len(inventory),
        "endpoint": "PUT /v1/device/instances",
    }
    return caps


def list_widgets() -> list[str]:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT widget_id FROM widgets "
                "UNION SELECT widget_id FROM publications ORDER BY widget_id"
            ).fetchall()
        finally:
            conn.close()
    return [row["widget_id"] for row in rows]


# ---------------------------------------------------------------------------
# Pairing and devices
# ---------------------------------------------------------------------------

def prune_expired_pairing_codes() -> int:
    """Delete expired unconsumed pairing codes. Returns pruned count."""
    now = _epoch_now()
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "DELETE FROM pairing_codes WHERE consumed = 0 AND expires_at < ?", (now,)
            )
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()


def get_valid_pairing_code() -> dict | None:
    """Return an existing unconsumed unexpired code, or None."""
    now = _epoch_now()
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT code, expires_at FROM pairing_codes "
                "WHERE consumed = 0 AND expires_at > ? "
                "ORDER BY expires_at DESC LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            return {"code": row["code"], "expiresAt": _iso_from_epoch(int(row["expires_at"]))}
        finally:
            conn.close()


def get_or_mint_pairing_code(ttl_minutes: int = PAIRING_TTL_MINUTES) -> dict:
    """Idempotent pairing code: reuse a valid one, otherwise mint."""
    prune_expired_pairing_codes()
    existing = get_valid_pairing_code()
    if existing is not None:
        return existing
    return mint_pairing_code(ttl_minutes=ttl_minutes)


def pairing_code_count() -> int:
    """Total pairing codes (valid+expired+consumed) for idempotency checks."""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM pairing_codes").fetchone()
            return int(row["c"] if row else 0)
        finally:
            conn.close()


def mint_pairing_code(ttl_minutes: int = PAIRING_TTL_MINUTES) -> dict:
    alphabet = string.ascii_uppercase + string.digits
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    code = f"{raw[:4]}-{raw[4:]}"
    try:
        ttl_minutes = int(ttl_minutes)
    except (TypeError, ValueError) as exc:
        raise PairingError("ttl_minutes must be an integer") from exc
    expires_at = _epoch_now() + max(1, ttl_minutes) * 60
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO pairing_codes (code, expires_at, consumed) VALUES (?, ?, 0)",
                (code, expires_at),
            )
            conn.commit()
        finally:
            conn.close()
    return {"code": code, "expiresAt": _iso_from_epoch(expires_at)}


def update_device_label(device_id: str, label: Any) -> dict | None:
    """Rename one device. Returns None when the device does not exist."""
    if not isinstance(device_id, str) or not device_id:
        raise StoreError("device_id is required")
    if not isinstance(label, str):
        raise StoreError("label must be a string")
    clean = label.strip()[:64]
    if not clean:
        raise StoreError("label must not be empty")
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT device_id FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE devices SET label = ? WHERE device_id = ?", (clean, device_id))
            conn.commit()
        finally:
            conn.close()
    return {"deviceId": device_id, "label": clean}


def register_device(code: str, label: str) -> dict | None:
    """Redeem a pairing code. Returns None when the code is unknown/used/expired."""
    if not isinstance(code, str) or not code.strip():
        return None
    code = code.strip().upper()
    label = (label or "unknown").strip()[:64] or "unknown"
    now = _epoch_now()
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT expires_at, consumed FROM pairing_codes WHERE code = ?", (code,)
            ).fetchone()
            if not row or row["consumed"] or row["expires_at"] < now:
                return None
            device_id = str(uuid.uuid4())
            token = DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:32]
            conn.execute(
                "INSERT INTO devices (device_id, token_hash, label, created_at, last_seen_at, revoked) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (device_id, _hash_token(token), label, _now(), _now()),
            )
            conn.execute(
                "UPDATE pairing_codes SET consumed = 1, device_id = ? WHERE code = ?",
                (device_id, code),
            )
            for widget in conn.execute(
                "SELECT widget_id FROM widgets "
                "UNION SELECT widget_id FROM publications"
            ).fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO widget_devices (widget_id, device_id) VALUES (?, ?)",
                    (widget["widget_id"], device_id),
                )
            conn.commit()
            widgets = [
                r["widget_id"]
                for r in conn.execute(
                    "SELECT widget_id FROM widget_devices WHERE device_id = ?", (device_id,)
                ).fetchall()
            ]
        finally:
            conn.close()
    return {"deviceId": device_id, "token": token, "widgets": widgets}


def device_for_token(token: str) -> dict | None:
    """Return the device for a valid, non-revoked token (updates last_seen)."""
    if not token:
        return None
    token_hash = _hash_token(token)
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT device_id, label FROM devices WHERE token_hash = ? AND revoked = 0",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE devices SET last_seen_at = ? WHERE device_id = ?",
                (_now(), row["device_id"]),
            )
            conn.commit()
            return {"deviceId": row["device_id"], "label": row["label"]}
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
            conn.commit()
        finally:
            conn.close()
    return {"deviceId": device_id, "pushEndpointRegistered": True, "updatedAt": now}


def _size_class(width_dp: int, height_dp: int, declared: Any = None) -> str:
    if declared in {"2x2", "4x2", "2x4", "4x4", "custom"}:
        return str(declared)
    if width_dp <= 160 and height_dp <= 160:
        return "2x2"
    if width_dp >= 220 and height_dp <= 180:
        return "4x2"
    if width_dp <= 180 and height_dp >= 220:
        return "2x4"
    if width_dp >= 220 and height_dp >= 220:
        return "4x4"
    return "custom"


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
    with _LOCK:
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


def list_devices() -> list[dict]:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT d.device_id, d.label, d.created_at, d.last_seen_at, d.revoked, "
                "CASE WHEN p.device_id IS NULL THEN 0 ELSE 1 END AS push_registered "
                "FROM devices d LEFT JOIN device_push_endpoints p ON p.device_id = d.device_id "
                "ORDER BY d.created_at"
            ).fetchall()
        finally:
            conn.close()
    return [
        {
            "deviceId": row["device_id"],
            "label": row["label"],
            "createdAt": row["created_at"],
            "lastSeenAt": row["last_seen_at"],
            "revoked": bool(row["revoked"]),
            "pushEndpointRegistered": bool(row["push_registered"]),
        }
        for row in rows
    ]


def revoke_device(device_id: str) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "UPDATE devices SET revoked = 1 WHERE device_id = ?", (device_id,)
            )
            if cur.rowcount:
                conn.execute("DELETE FROM device_push_endpoints WHERE device_id = ?", (device_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

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


def _action_item_ids(publication: dict | None, layout: dict | None) -> set[str]:
    ids: set[str] = set()
    if isinstance(publication, dict):
        if isinstance(publication.get("itemId"), str):
            ids.add(publication["itemId"])
        for action in publication.get("actions") or []:
            if isinstance(action, dict) and isinstance(action.get("itemId"), str):
                ids.add(action["itemId"])

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("itemId"), str):
                ids.add(node["itemId"])
            action = node.get("action")
            if isinstance(action, dict) and isinstance(action.get("itemId"), str):
                ids.add(action["itemId"])
            for child in node.get("children") or []:
                walk(child)
    if isinstance(layout, dict):
        walk(layout.get("root"))
    return ids


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
    confirm_on_device: bool = False,
) -> dict:
    """Durably record a tap and enqueue — never execute — an allowlisted intent."""
    if event not in ACTION_KINDS:
        raise ActionIntentError(f"action kind must be one of {list(ACTION_KINDS)}")
    body = _json_object(payload, "payload")
    resolved_item = item_id or body.get("itemId") or body.get("item_id")
    if not isinstance(resolved_item, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", resolved_item):
        raise ActionIntentError("action itemId is required and must be stable")
    resolved_class = action_class or body.get("actionClass") or body.get("action_class") or "reversible"
    if resolved_class not in ACTION_CLASSES:
        raise ActionIntentError(f"actionClass must be one of {list(ACTION_CLASSES)}")
    if not isinstance(confirm_on_device, bool):
        raise ActionIntentError("confirmOnDevice must be boolean")
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
            layout_row = conn.execute(
                "SELECT layout_json FROM widgets WHERE widget_id = ?", (widget_id,)
            ).fetchone()
            layout = None
            if layout_row:
                try:
                    layout = json.loads(layout_row["layout_json"])
                except (TypeError, ValueError):
                    layout = None
            if selected_revision is None:
                # Legacy v2 layouts have no publication revision; revision zero
                # still gives the queue a stable, auditable ordering key.
                selected_revision = 0 if layout is not None else None
            if selected_revision is None:
                raise ActionIntentError("no publication or layout revision is available for this action")
            if current_revision is not None and selected_revision != current_revision:
                raise ActionIntentError("action revision is not current")
            known_items = _action_item_ids(publication, layout)
            if (publication is not None or layout is not None) and resolved_item not in known_items:
                raise ActionIntentError("action itemId is not present in this revision")
            if client_event_id:
                prior = conn.execute(
                    "SELECT id, payload FROM events WHERE widget_id = ? AND device_id = ? ORDER BY id DESC",
                    (widget_id, device_id),
                ).fetchall()
                for prior_row in prior:
                    try:
                        prior_payload = json.loads(prior_row["payload"] or "{}")
                    except (TypeError, ValueError):
                        continue
                    if prior_payload.get("clientEventId") == client_event_id:
                        intent = conn.execute(
                            "SELECT * FROM action_intents WHERE event_id = ?", (prior_row["id"],)
                        ).fetchone()
                        if intent is not None:
                            return {"eventId": int(prior_row["id"]), "intent": _intent_dict(intent), "duplicate": True}
                        return {"eventId": int(prior_row["id"]), "duplicate": True}
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
                "confirmOnDevice": confirm_on_device,
            })
            event_payload = json.dumps(payload_for_storage, separators=(",", ":"), ensure_ascii=False)
            cur = conn.execute(
                "INSERT INTO events (widget_id, device_id, event, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (widget_id, device_id, event, event_payload, _now()),
            )
            event_id = int(cur.lastrowid or 0)
            if not event_id:
                raise StoreError("event insert did not return an id")
            intent_id = "intent_" + uuid.uuid4().hex
            status = "awaiting_confirmation" if confirm_on_device or resolved_class in SENSITIVE_ACTION_CLASSES else "queued"
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
    with _LOCK:
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


def resolve_intent(
    intent_id: str,
    outcome: str,
    *,
    result: str | None = None,
    confirmed: bool = False,
) -> dict:
    """Record an agent's terminal decision; this function never runs the operation."""
    if not isinstance(intent_id, str) or not intent_id or len(intent_id) > 160:
        raise ActionIntentError("intent_id is required")
    if outcome not in {"applied", "declined", "held", "expired"}:
        raise ActionIntentError("outcome must be applied, declined, held, or expired")
    if not isinstance(confirmed, bool):
        raise ActionIntentError("confirmed must be boolean")
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
            if needs_confirmation and outcome == "applied" and not confirmed:
                raise ActionIntentError("sensitive or device-confirmed action requires explicit confirmation")
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


def post_event(widget_id: str, device_id: str, event: str, payload: dict | None) -> int:
    if event in ACTION_KINDS:
        result = post_action_event(widget_id, device_id, event, payload)
        return int(result["eventId"])
    # E: durable, deduplicated tap storage — clientEventId + stable itemId are
    # preserved; duplicate submissions collapse to one state transition.
    if not isinstance(event, str) or not event or len(event) > 200:
        raise StoreError("event must be a non-empty string of at most 200 characters")
    if payload is not None and not isinstance(payload, dict):
        raise StoreError("event payload must be a JSON object")
    client_event_id = None
    if payload is not None:
        client_event_id = payload.get("clientEventId")
        if not isinstance(client_event_id, str) or not client_event_id:
            client_event_id = None
    payload_json = json.dumps(payload, separators=(",", ":")) if payload is not None else None
    with _LOCK:
        conn = _connect()
        try:
            # Deduplicate on (widget_id, device_id, clientEventId) when present
            if client_event_id is not None:
                existing = conn.execute(
                    "SELECT id, payload FROM events WHERE widget_id = ? AND device_id = ? ORDER BY id DESC LIMIT 50",
                    (widget_id, device_id),
                ).fetchall()
                for row in existing:
                    try:
                        prev = json.loads(row["payload"]) if row["payload"] else {}
                    except (TypeError, ValueError) as exc:
                        logger.debug("ignoring malformed prior widget event %s", exc)
                        continue
                    if isinstance(prev, dict) and prev.get("clientEventId") == client_event_id:
                        return int(row["id"])
            cur = conn.execute(
                "INSERT INTO events (widget_id, device_id, event, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (widget_id, device_id, event, payload_json, _now()),
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
    with _LOCK:
        conn = _connect()
        try:
            if since and widget_id:
                query = (
                    "SELECT id, widget_id, device_id, event, payload, created_at "
                    "FROM events WHERE created_at > ? AND widget_id = ? "
                    "ORDER BY id DESC LIMIT ?"
                )
            elif since:
                query = (
                    "SELECT id, widget_id, device_id, event, payload, created_at "
                    "FROM events WHERE created_at > ? ORDER BY id DESC LIMIT ?"
                )
            elif widget_id:
                query = (
                    "SELECT id, widget_id, device_id, event, payload, created_at "
                    "FROM events WHERE widget_id = ? ORDER BY id DESC LIMIT ?"
                )
            else:
                query = (
                    "SELECT id, widget_id, device_id, event, payload, created_at "
                    "FROM events ORDER BY id DESC LIMIT ?"
                )
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
    events = []
    intent_by_event: dict[int, dict] = {}
    if rows:
        with _LOCK:
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
            "payload": payload,
            "createdAt": row["created_at"],
        }
        if int(row["id"]) in intent_by_event:
            event["intent"] = intent_by_event[int(row["id"])]
        events.append(event)
    return events
