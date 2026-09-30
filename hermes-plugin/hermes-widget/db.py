"""Widget database paths and connection lifecycle."""

from __future__ import annotations

import contextlib
import logging
import importlib
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .errors import AssetNotFound, StoreError
    from .schema import ensure_schema
except ImportError:  # direct import from scripts/tests
    from errors import AssetNotFound, StoreError  # type: ignore
    from schema import ensure_schema  # type: ignore

_LOCK = threading.RLock()
_CONNECTION_SCOPE = threading.local()
CONNECT_TIMEOUT_SECONDS = 15


class _WidgetConnection(sqlite3.Connection):
    """Let legacy `_connect()` callers borrow a request-scoped connection safely."""

    def close(self) -> None:
        if (
            getattr(_CONNECTION_SCOPE, "depth", 0) > 0
            and getattr(_CONNECTION_SCOPE, "connection", None) is self
        ):
            # Match sqlite3.close()'s rollback semantics at each legacy borrow
            # boundary while keeping the physical request connection reusable.
            if self.in_transaction:
                self.rollback()
            return
        super().close()

def _default_hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".hermes"


def _hermes_home() -> Path:
    try:
        get_hermes_home = importlib.import_module("hermes_constants").get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        configured_home = os.environ.get("HERMES_HOME")
        if configured_home:
            return Path(configured_home)
        return Path.home() / ".hermes"


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

def _open_connection(timeout: float = CONNECT_TIMEOUT_SECONDS) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=timeout, factory=_WidgetConnection)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _connect(*, timeout: float = CONNECT_TIMEOUT_SECONDS) -> sqlite3.Connection:
    """Open a connection, or borrow the current thread's scoped connection."""
    if getattr(_CONNECTION_SCOPE, "depth", 0) > 0:
        conn = getattr(_CONNECTION_SCOPE, "connection", None)
        if conn is None:
            conn = _open_connection()
            _CONNECTION_SCOPE.connection = conn
        return conn
    return _open_connection(timeout)


@contextmanager
def db() -> Any:
    """Share one SQLite connection across nested work on this thread.

    HTTP requests use this around route dispatch. Existing store functions can keep
    their local `_connect()`/`close()` structure: close releases the borrowed handle
    logically, while the outer scope owns its physical lifetime.
    """
    depth = getattr(_CONNECTION_SCOPE, "depth", 0)
    if depth == 0:
        _CONNECTION_SCOPE.connection = _open_connection()
    _CONNECTION_SCOPE.depth = depth + 1
    try:
        yield _CONNECTION_SCOPE.connection
    finally:
        _CONNECTION_SCOPE.depth -= 1
        if _CONNECTION_SCOPE.depth == 0:
            conn = _CONNECTION_SCOPE.connection
            del _CONNECTION_SCOPE.connection
            sqlite3.Connection.close(conn)


logger = logging.getLogger(__name__)

def backup_db() -> Path | None:
    """Create a consistent SQLite backup and prune backups older than the newest ten."""
    src = db_path()
    if not src.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dst = src.with_name(f"widget.db.bak.{ts}")
    source = target = None
    try:
        source = sqlite3.connect(str(src), timeout=15)
        target = sqlite3.connect(str(dst), timeout=15)
        source.backup(target)
        target.close()
        target = None
        source.close()
        source = None
        check = sqlite3.connect(str(dst), timeout=15)
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise StoreError(f"SQLite backup integrity check failed: {result[0] if result else 'no result'}")
        finally:
            check.close()
        backups = sorted(src.parent.glob("widget.db.bak.*"), key=lambda path: path.name)
        for old in backups[:-10]:
            old.unlink()
        return dst
    except Exception:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        with contextlib.suppress(OSError):
            dst.unlink()
        logger.warning("hermes-widget: database backup failed", exc_info=True)
        raise


def restore_db(backup: Path) -> None:
    """Restore one valid SQLite backup atomically, discarding stale WAL sidecars."""
    target = db_path()
    source_path = Path(backup).resolve()
    backup_dir = data_dir().resolve()
    if source_path.parent != backup_dir or not source_path.is_file():
        raise StoreError("backup must be an existing file in the widget data directory")
    temporary = target.with_name(f"{target.name}.restore")
    with contextlib.suppress(OSError):
        temporary.unlink()
    source = destination = None
    try:
        source = sqlite3.connect(str(source_path), timeout=15)
        destination = sqlite3.connect(str(temporary), timeout=15)
        source.backup(destination)
        destination.close()
        destination = None
        source.close()
        source = None
        check = sqlite3.connect(str(temporary), timeout=15)
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise StoreError("backup failed SQLite integrity check")
        finally:
            check.close()
        for suffix in ("-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                target.with_name(target.name + suffix).unlink()
        os.replace(temporary, target)
    except Exception:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise
