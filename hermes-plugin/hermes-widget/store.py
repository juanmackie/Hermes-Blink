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
    from .publication import PublicationInputError, prepare_publication
    from .publication import PublicationTooLarge as _PublicationInputTooLarge
    from .publication import capabilities as _publication_capabilities
    from .validate import ValidationError
    from .validate import inspect_layout as _inspect_layout
    from .validate import validate_layout as _validate
except ImportError:  # pragma: no cover - direct import from tests/scripts
    from publication import (  # type: ignore
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
        report = _inspect_layout(candidate)
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
    return {"ok": True, "widgetId": widget_id, "updatedAt": updated_at}


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
    return {
        "version": publication.get("version"),
        "widgetId": publication.get("widgetId"),
        "kind": publication.get("kind"),
        "title": publication.get("title"),
        "summary": publication.get("summary"),
        "expiresAt": publication.get("expiresAt"),
        "maxAgeSeconds": publication.get("maxAgeSeconds"),
        "content": publication.get("content"),
    }


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
        )
    except _PublicationInputTooLarge as exc:
        raise PublicationTooLarge(str(exc)) from exc
    except PublicationInputError as exc:
        raise PublicationError(str(exc)) from exc

    check_push_rate(widget_id)
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
            conn.commit()
        finally:
            conn.close()


def acknowledge_publication_render(
    widget_id: str, device_id: str, revision: int, width: int, height: int
) -> dict:
    """Record a render submission, never a claim that the user saw it."""
    if not device_id:
        raise RenderNotReady("a device token is required")
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
            conn.commit()
        finally:
            conn.close()
    return {
        "ok": True,
        "widgetId": widget_id,
        "revision": revision_value,
        "status": "render_submitted",
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
            revision_rows = conn.execute(
                "SELECT revision, published_at, expires_at, max_age_seconds, superseded_at, "
                "superseded_reason, kind, title FROM publication_revisions "
                "WHERE widget_id = ? ORDER BY revision DESC",
                (widget_id,),
            ).fetchall()
        finally:
            conn.close()
    current_revision = _as_int(publication.get("revision", 0), "publication revision") if publication else 0
    stale = bool(publication) and _publication_stale(publication)
    delivery = []
    for device in devices:
        device_id = str(device["device_id"])
        device_fetches = {
            revision: row for (owner, revision), row in fetches.items() if owner == device_id
        }
        current_fetch = device_fetches.get(current_revision)
        current_ack = acks.get((device_id, current_revision))
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
                "renderedWidth": current_ack["width"] if current_ack else None,
                "renderedHeight": current_ack["height"] if current_ack else None,
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
        }
        for row in revision_rows
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
        "delivery": delivery,
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


def list_devices() -> list[dict]:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT device_id, label, created_at, last_seen_at, revoked "
                "FROM devices ORDER BY created_at"
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
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def post_event(widget_id: str, device_id: str, event: str, payload: dict | None) -> int:
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
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if row["payload"] else None
        except (TypeError, ValueError) as exc:
            logger.warning("ignoring malformed widget event payload %s", exc)
            payload = None
        events.append(
            {
                "id": row["id"],
                "widgetId": row["widget_id"],
                "deviceId": row["device_id"],
                "event": row["event"],
                "payload": payload,
                "createdAt": row["created_at"],
            }
        )
    return events
