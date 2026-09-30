"""Immutable publication asset storage and integrity checks."""
from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

try:
    from .db import _connect, asset_path, assets_dir
    from .errors import AssetNotFound, AssetUnavailable
    from .timeutil import _as_int, _now
except ImportError:  # direct import from scripts/tests
    from db import _connect, asset_path, assets_dir  # type: ignore
    from errors import AssetNotFound, AssetUnavailable  # type: ignore
    from timeutil import _as_int, _now  # type: ignore

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

def _publication_asset_ids(publication: dict) -> set[str]:
    ids: set[str] = set()
    content = publication.get("content")
    if isinstance(content, dict) and isinstance(content.get("assetId"), str):
        ids.add(content["assetId"])
    regions = publication.get("regions")
    if isinstance(regions, dict):
        for region in regions.values():
            if isinstance(region, dict) and isinstance(region.get("content"), dict):
                value = region["content"].get("assetId")
                if isinstance(value, str):
                    ids.add(value)
    return ids

def get_asset(asset_id: str) -> dict:
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
