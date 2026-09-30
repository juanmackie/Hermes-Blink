"""Agent credentials, pairing codes, and paired-device lifecycle."""
from __future__ import annotations

import hmac
import os
import secrets
import string
import time
import uuid
from typing import Any

try:
    from .db import _LOCK, _connect, agent_token_path
    from .errors import PairingError, StoreError
    from .timeutil import _epoch_now, _hash_token, _iso_from_epoch, _now
except ImportError:  # direct import from scripts/tests
    from db import _LOCK, _connect, agent_token_path  # type: ignore
    from errors import PairingError, StoreError  # type: ignore
    from timeutil import _epoch_now, _hash_token, _iso_from_epoch, _now  # type: ignore

AGENT_TOKEN_ENV = "HERMES_WIDGET_AGENT_TOKEN"
PAIRING_TTL_MINUTES = 10
DEVICE_TOKEN_PREFIX = "dvc_"
_agent_token_cache: tuple[str, int, str] | None = None

def get_agent_token(create: bool = True) -> str | None:
    """Return the agent bearer token, generating and persisting one if allowed."""
    global _agent_token_cache
    env = os.environ.get(AGENT_TOKEN_ENV, "").strip()
    if env:
        return env
    path = agent_token_path()
    with _LOCK:
        try:
            stat = path.stat()
        except FileNotFoundError:
            stat = None
        if stat is not None and _agent_token_cache is not None:
            cached_path, cached_mtime, cached_token = _agent_token_cache
            if cached_path == str(path) and cached_mtime == stat.st_mtime_ns:
                return cached_token
        if stat is not None:
            token = path.read_text(encoding="utf-8").strip()
            if token:
                _agent_token_cache = (str(path), path.stat().st_mtime_ns, token)
                return token
        if not create:
            return None
        token = secrets.token_urlsafe(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Another process may have won the creation race. Re-read its token
            # after its exclusive create and write complete.
            for _ in range(20):
                try:
                    token = path.read_text(encoding="utf-8").strip()
                    if token:
                        _agent_token_cache = (str(path), path.stat().st_mtime_ns, token)
                        return token
                except OSError:
                    pass
                time.sleep(0.01)
            raise StoreError("agent token file exists but could not be read")
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(token + "\n")
            output.flush()
            os.fsync(output.fileno())
        _agent_token_cache = (str(path), path.stat().st_mtime_ns, token)
        return token

def verify_agent_token(token: str) -> bool:
    if not token:
        return False
    expected = get_agent_token(create=False)
    if not expected:
        return False
    return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))

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
            conn.execute("BEGIN IMMEDIATE")
            device_id = str(uuid.uuid4())
            token = DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:32]
            claimed = conn.execute(
                "UPDATE pairing_codes SET consumed = 1, device_id = ? "
                "WHERE code = ? AND consumed = 0 AND expires_at >= ?",
                (device_id, code, now),
            )
            if claimed.rowcount != 1:
                conn.rollback()
                return None
            conn.execute(
                "INSERT INTO devices (device_id, token_hash, label, created_at, last_seen_at, revoked) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (device_id, _hash_token(token), label, _now(), _now()),
            )
            for widget in conn.execute(
                "SELECT widget_id FROM publications "
                "UNION SELECT widget_id FROM widget_settings "
                "UNION SELECT widget_id FROM widget_instances"
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
            seen_at = _now()
            cutoff = _iso_from_epoch(time.time() - 60)
            changed = conn.execute(
                "UPDATE devices SET last_seen_at = ? WHERE device_id = ? "
                "AND (last_seen_at IS NULL OR last_seen_at < ?)",
                (seen_at, row["device_id"], cutoff),
            ).rowcount
            if changed:
                conn.commit()
            return {"deviceId": row["device_id"], "label": row["label"]}
        finally:
            conn.close()

def list_devices() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT d.device_id, d.label, d.created_at, d.last_seen_at, d.revoked, "
            "CASE WHEN p.device_id IS NULL THEN 0 ELSE 1 END AS push_registered, "
            "c.app_version, c.app_build_code, c.app_build_sha, c.os_sdk, "
            "c.updated_at AS client_updated_at "
            "FROM devices d "
            "LEFT JOIN device_push_endpoints p ON p.device_id = d.device_id "
            "LEFT JOIN device_client_info c ON c.device_id = d.device_id "
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
            # The build that last talked to the server, so support can ask for an
            # upgrade without guessing.
            "appVersion": row["app_version"],
            "appBuildCode": row["app_build_code"],
            "appBuildSha": row["app_build_sha"],
            "osSdk": row["os_sdk"],
            "clientReportedAt": row["client_updated_at"],
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
