"""Bound historical widget data and remove assets no retained publication uses."""
from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import store
except ImportError:  # pragma: no cover - direct import from scripts/tests
    import store  # type: ignore

logger = logging.getLogger(__name__)

KEEP_REVISIONS = 100
RETENTION_DAYS = 90
RETENTION_INTERVAL_SECONDS = 24 * 60 * 60

_JOB_LOCK = threading.Lock()
_JOB_STARTED = False


def _cutoff(days: int, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return (current.astimezone(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def prune(
    *,
    keep_revisions: int = KEEP_REVISIONS,
    retention_days: int = RETENTION_DAYS,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Prune old telemetry/revisions and orphan assets; return exact row counts.

    Files are removed only after their asset rows commit. A dry run reports what would
    be removed without changing the database, assets, or WAL.
    """
    keep_revisions = max(1, min(int(keep_revisions), 10_000))
    retention_days = max(1, min(int(retention_days), 3_650))
    cutoff = _cutoff(retention_days, now)
    counts: dict[str, int] = {}
    asset_paths: list[Path] = []
    conn = store._connect()
    try:
        with store._LOCK:
            if not dry_run and store.db_path().is_file():
                backup = store.backup_db()
                if backup is None:
                    raise store.StoreError("retention stopped because the database backup failed")
            conn.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
            expired_revisions: list[tuple[str, int]] = []
            widgets = conn.execute(
                "SELECT DISTINCT widget_id FROM publication_revisions"
            ).fetchall()
            for widget in widgets:
                widget_id = str(widget["widget_id"])
                revisions = conn.execute(
                    "SELECT revision FROM publication_revisions WHERE widget_id = ? "
                    "ORDER BY revision DESC LIMIT -1 OFFSET ?",
                    (widget_id, keep_revisions),
                ).fetchall()
                expired_revisions.extend(
                    (widget_id, int(row["revision"])) for row in revisions
                )
            counts["publication_revisions"] = len(expired_revisions)

            expired_set = set(expired_revisions)
            all_revisions = conn.execute(
                "SELECT widget_id, revision, payload_json FROM publication_revisions"
            ).fetchall()
            referenced_assets: set[str] = set()
            for row in all_revisions:
                if (str(row["widget_id"]), int(row["revision"])) not in expired_set:
                    try:
                        payload = json.loads(row["payload_json"])
                    except (TypeError, ValueError):
                        payload = {}
                    if isinstance(payload, dict):
                        referenced_assets.update(store._publication_asset_ids(payload))
            current_rows = conn.execute("SELECT payload_json FROM publications").fetchall()
            for row in current_rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, ValueError):
                    payload = {}
                if isinstance(payload, dict):
                    referenced_assets.update(store._publication_asset_ids(payload))

            date_tables = (
                ("publication_fetches", "fetched_at"),
                ("publication_acks", "rendered_at"),
                ("publication_nudges", "attempted_at"),
                ("attention_aggregates", "updated_at"),
                ("action_audit", "created_at"),
                ("publication_render_builds", "reported_at"),
            )
            for table, timestamp_column in date_tables:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {timestamp_column} < ?", (cutoff,)
                ).fetchone()[0]
                counts[table] = int(count)

            revision_tables = (
                "publication_fetches",
                "publication_acks",
                "publication_priorities",
                "publication_nudges",
                "attention_aggregates",
                "publication_render_builds",
            )
            for table in revision_tables:
                count = 0
                for widget_id, revision in expired_revisions:
                    count += int(conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE widget_id = ? AND revision = ?",
                        (widget_id, revision),
                    ).fetchone()[0])
                counts[f"{table}_revisions"] = count

            assets = conn.execute("SELECT asset_id FROM assets").fetchall()
            known_asset_ids = {str(row["asset_id"]) for row in assets}
            assets_root = store.data_dir() / "assets"
            asset_files = (
                {
                    path.name for path in assets_root.iterdir()
                    if path.is_file() and re.fullmatch(r"asset_[0-9a-f]{24}", path.name)
                }
                if assets_root.is_dir()
                else set()
            )
            orphan_ids = sorted((known_asset_ids | asset_files) - referenced_assets)
            counts["assets"] = len(orphan_ids)

            if dry_run:
                conn.rollback()
                return {"dryRun": True, "cutoff": cutoff, "counts": counts}

            for table, timestamp_column in date_tables:
                conn.execute(f"DELETE FROM {table} WHERE {timestamp_column} < ?", (cutoff,))
            for widget_id, revision in expired_revisions:
                for table in revision_tables:
                    conn.execute(
                        f"DELETE FROM {table} WHERE widget_id = ? AND revision = ?",
                        (widget_id, revision),
                    )
                conn.execute(
                    "DELETE FROM publication_revisions WHERE widget_id = ? AND revision = ?",
                    (widget_id, revision),
                )
            for asset_id in orphan_ids:
                conn.execute("DELETE FROM assets WHERE asset_id = ?", (asset_id,))
                with contextlib.suppress(OSError, store.AssetNotFound):
                    asset_paths.append(store.asset_path(asset_id))
            conn.commit()
            for path in asset_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("could not remove orphan widget asset %s", path, exc_info=True)
            with contextlib.suppress(sqlite3.Error):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return {"dryRun": False, "cutoff": cutoff, "counts": counts}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _retention_loop() -> None:
    while True:
        try:
            result = prune()
            if any(result["counts"].values()):
                logger.info("widget retention pruned rows: %s", result["counts"])
        except Exception:
            logger.warning("widget retention pass failed", exc_info=True)
        time.sleep(RETENTION_INTERVAL_SECONDS)


def start_retention_job() -> None:
    """Start one daily retention worker in the server process."""
    global _JOB_STARTED
    with _JOB_LOCK:
        if _JOB_STARTED:
            return
        _JOB_STARTED = True
    threading.Thread(
        target=_retention_loop,
        name="hermes-widget-retention",
        daemon=True,
    ).start()
