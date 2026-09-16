"""SQLite-backed widget store shared by the agent tools and the plugin HTTP server.

Everything the widget owns lives in one SQLite file under the Hermes home so the
agent process (tools) and the serving process (hermes widget serve) see the same
state with no extra service. Device tokens are only ever persisted as sha256
hashes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import string
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # normal path: imported as part of the hermes-widget plugin package
    from .validate import ValidationError, validate_layout as _validate, migrate_layout
    try:
        from .validate import LAYOUT_VERSION
    except ImportError:
        LAYOUT_VERSION = 2  # type: ignore
    # ponytail: no extra import for migration — validate.migrate_layout is the single migration path
except ImportError:  # pragma: no cover - direct import from tests/scripts
    from validate import ValidationError, validate_layout as _validate, migrate_layout  # type: ignore
    try:
        from validate import LAYOUT_VERSION  # type: ignore
    except ImportError:
        LAYOUT_VERSION = 2  # type: ignore

DEFAULT_WIDGET_ID = "hermes-brief"
MAX_LAYOUT_BYTES = 64 * 1024
LAYOUT_VERSION = 2  # transport stays /v1/, layout contract is v2
MAX_NODES = 100
PUSH_WINDOW_SECONDS = 3600
PUSH_MAX_PER_WINDOW = 30
PAIRING_TTL_MINUTES = 10
DEVICE_TOKEN_PREFIX = "dvc_"
AGENT_TOKEN_ENV = "HERMES_WIDGET_AGENT_TOKEN"

_LOCK = threading.RLock()
_initialised: set[str] = set()
_rate: dict[str, list[float]] = {}


class StoreError(Exception):
    """Base class for widget-store failures."""

    code = "store_error"


class LayoutError(StoreError):
    """The layout is malformed or violates the v1 contract."""

    code = "invalid_layout"


class RateLimitError(StoreError):
    """Too many layout pushes for one widget in the rate window."""

    code = "rate_limited"


class PairingError(StoreError):
    """A pairing code was unknown, already used, or expired."""

    code = "invalid_or_expired_code"


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    key = str(db_path())
    if key in _initialised:
        return
    with _LOCK:
        if key in _initialised:
            return
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
        conn.commit()
        _initialised.add(key)


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
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
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

def _backup_db() -> None:
    """Copy widget.db to widget.db.bak.<timestamp> before migration. Best-effort."""
    try:
        src = db_path()
        if not src.exists():
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dst = src.with_name(f"widget.db.bak.{ts}")
        import shutil
        shutil.copy2(str(src), str(dst))
        try:
            os.chmod(dst, 0o600)
        except OSError:
            pass
    except Exception:
        pass  # backup is best-effort; migration still proceeds


def validate_layout(layout: dict) -> None:
    try:
        _validate(layout)
    except ValidationError as exc:
        raise LayoutError(str(exc)) from exc


def _migrate_if_needed(widget_id: str, layout: dict) -> tuple[dict, bool]:
    """If layout is v1, migrate to v2 (with backup). Returns (layout_v2, migrated)."""
    v = layout.get("version")
    if v == LAYOUT_VERSION:
        return layout, False
    if v == 1:
        _backup_db()
        migrated, _had_removed = migrate_layout(layout)
        migrated["widgetId"] = widget_id
        return migrated, True
    return layout, False


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


def put_widget(widget_id: str, layout: dict) -> dict:
    """Validate and store a layout, returning the small success envelope."""
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise LayoutError("widget_id must be a non-empty string of at most 128 characters")
    if not isinstance(layout, dict):
        raise LayoutError("layout must be a JSON object")

    stored = dict(layout)
    stored["widgetId"] = widget_id
    # Auto-migrate v1 -> v2 before validation (with backup). v2 is enforced.
    migrated, _was_migrated = _migrate_if_needed(widget_id, stored)
    stored = migrated
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
    layout = json.loads(row["layout_json"])
    # Lazily migrate stored v1 layouts on read (with backup + rewrite).
    if layout.get("version") == 1:
        migrated, _ = _migrate_if_needed(widget_id, layout)
        # Rewrite migrated layout so next read is fast; ignore errors.
        try:
            payload = json.dumps(migrated, separators=(",", ":"), ensure_ascii=False)
            with _LOCK:
                c2 = _connect()
                try:
                    c2.execute(
                        "UPDATE widgets SET layout_json = ?, updated_at = ? WHERE widget_id = ?",
                        (payload, migrated.get("updatedAt") or _now(), widget_id),
                    )
                    c2.commit()
                finally:
                    c2.close()
        except Exception:
            pass
        return migrated
    return layout


def list_widgets() -> list[str]:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute("SELECT widget_id FROM widgets ORDER BY widget_id").fetchall()
        finally:
            conn.close()
    return [row["widget_id"] for row in rows]


def delete_widget(widget_id: str) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM widgets WHERE widget_id = ?", (widget_id,))
            conn.execute("DELETE FROM widget_devices WHERE widget_id = ?", (widget_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Pairing and devices
# ---------------------------------------------------------------------------

def prune_expired_pairing_codes() -> int:
    """Delete expired unconsumed pairing codes. Returns pruned count."""
    now = int(time.time())
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
    now = int(time.time())
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
    expires_at = int(time.time()) + max(1, int(ttl_minutes)) * 60
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


def register_device(code: str, label: str) -> dict | None:
    """Redeem a pairing code. Returns None when the code is unknown/used/expired."""
    if not isinstance(code, str) or not code.strip():
        return None
    code = code.strip().upper()
    label = (label or "unknown").strip()[:64] or "unknown"
    now = int(time.time())
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
            for widget in conn.execute("SELECT widget_id FROM widgets").fetchall():
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
                    except Exception:
                        continue
                    if isinstance(prev, dict) and prev.get("clientEventId") == client_event_id:
                        return int(row["id"])
            cur = conn.execute(
                "INSERT INTO events (widget_id, device_id, event, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (widget_id, device_id, event, payload_json, _now()),
            )
            conn.commit()
            return int(cur.lastrowid)
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
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                f"SELECT id, widget_id, device_id, event, payload, created_at "
                f"FROM events {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
    events = []
    for row in rows:
        events.append(
            {
                "id": row["id"],
                "widgetId": row["widget_id"],
                "deviceId": row["device_id"],
                "event": row["event"],
                "payload": json.loads(row["payload"]) if row["payload"] else None,
                "createdAt": row["created_at"],
            }
        )
    return events
