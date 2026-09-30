"""SQLite schema creation and additive migrations for the widget store."""

from __future__ import annotations

import logging
import sqlite3
import threading

try:
    from .errors import StoreError
except ImportError:  # direct import from scripts/tests
    from errors import StoreError  # type: ignore

SCHEMA_VERSION = 7
_SCHEMA_LOCK = threading.RLock()
logger = logging.getLogger(__name__)

def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the store schema without relying on process lifetime."""
    with _SCHEMA_LOCK:
        schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if schema_version == SCHEMA_VERSION:
            return
        if schema_version > SCHEMA_VERSION:
            raise StoreError(
                f"database schema {schema_version} is newer than supported schema {SCHEMA_VERSION}"
            )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS devices ("
            "device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, label TEXT, "
            "created_at TEXT, last_seen_at TEXT, revoked INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, widget_id TEXT NOT NULL, "
            "device_id TEXT NOT NULL, event TEXT NOT NULL, payload TEXT, "
            "client_event_id TEXT, created_at TEXT)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS devices_token_hash_idx ON devices(token_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS events_widget_created_idx ON events(widget_id, id)")
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
            "render_confirmed_at TEXT, "
            "PRIMARY KEY (widget_id, device_id, revision))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS publication_revisions ("
            "widget_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "published_at TEXT NOT NULL, expires_at TEXT, max_age_seconds INTEGER, "
            "kind TEXT, title TEXT, summary TEXT, payload_json TEXT NOT NULL, watch_id TEXT, "
            "superseded_at TEXT, superseded_reason TEXT, "
            "PRIMARY KEY (widget_id, revision))"
        )
        # Additive tables keep upgrades safe for existing widget.db files.  Priority
        # is intentionally separate from the legacy revision table: old rows have no
        # priority and must remain readable.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS widget_settings ("
            "widget_id TEXT PRIMARY KEY, quiet_start TEXT, quiet_end TEXT, "
            "quiet_timezone TEXT NOT NULL DEFAULT 'UTC', updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS push_rate_limits ("
            "widget_id TEXT NOT NULL, occurred_at INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS push_rate_limits_window_idx "
            "ON push_rate_limits(widget_id, occurred_at)"
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
            "attempt_count INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, claim_until TEXT, "
            "PRIMARY KEY (widget_id, revision, device_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_push_endpoints ("
            "device_id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, endpoint_hash TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_push_state ("
            "device_id TEXT PRIMARY KEY, state TEXT NOT NULL, distributor_present INTEGER, "
            "failure_reason TEXT, updated_at TEXT NOT NULL)"
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
            "CREATE TABLE IF NOT EXISTS widget_update_requests ("
            "request_id TEXT PRIMARY KEY, widget_id TEXT NOT NULL, device_id TEXT, "
            "client_event_id TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, "
            "triggered_at TEXT, trigger_error TEXT, consumed_at TEXT, "
            "UNIQUE (widget_id, client_event_id))"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS widget_update_requests_status_idx "
            "ON widget_update_requests(widget_id, status, created_at)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS attention_aggregates ("
            "device_id TEXT NOT NULL, widget_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "rendered INTEGER NOT NULL DEFAULT 0, dwell_lt5 INTEGER NOT NULL DEFAULT 0, "
            "dwell_5_60 INTEGER NOT NULL DEFAULT 0, dwell_gt60 INTEGER NOT NULL DEFAULT 0, "
            "taps INTEGER NOT NULL DEFAULT 0, superseded_before_fetch INTEGER NOT NULL DEFAULT 0, "
            "updated_at TEXT NOT NULL, PRIMARY KEY (device_id, widget_id, revision))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS widget_questions ("
            "question_id TEXT PRIMARY KEY, widget_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "item_id TEXT, prompt TEXT NOT NULL, status TEXT NOT NULL, answer TEXT, "
            "created_at TEXT NOT NULL, answered_at TEXT, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS widget_questions_open_idx "
            "ON widget_questions(widget_id, status, created_at)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS widget_watches ("
            "watch_id TEXT PRIMARY KEY, widget_id TEXT NOT NULL, name TEXT NOT NULL, "
            "condition_json TEXT NOT NULL, payload_json TEXT NOT NULL, cadence_seconds INTEGER NOT NULL, "
            "quiet_start TEXT, quiet_end TEXT, quiet_timezone TEXT NOT NULL DEFAULT 'UTC', "
            "max_per_day INTEGER NOT NULL, enabled INTEGER NOT NULL, "
            "last_state INTEGER, last_fired_at TEXT, next_check_at TEXT, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, expires_at TEXT, claim_until TEXT)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS widget_watches_due_idx "
            "ON widget_watches(enabled, next_check_at)"
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS action_audit_device_created_idx "
            "ON action_audit(device_id, created_at)"
        )
        # Which build the phone is running. A device that renders the wrong thing is
        # usually a build problem, and the only way to answer "which build rendered
        # revision N" is to have recorded it at the time. Additive tables, like
        # device_push_state, so an existing widget.db upgrades without a migration.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_client_info ("
            "device_id TEXT PRIMARY KEY, app_version TEXT, app_build_code INTEGER, "
            "os_sdk INTEGER, app_build_sha TEXT, first_reported_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS publication_render_builds ("
            "widget_id TEXT NOT NULL, device_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "app_version TEXT, app_build_code INTEGER, reported_at TEXT NOT NULL, "
            "PRIMARY KEY (widget_id, device_id, revision))"
        )
        # A device request that was refused. Before this table, "no row in events" could
        # mean an unauthenticated request, a non-device token, a malformed body, a
        # rate limit, a server error, or a tap that never left the phone. Now each of
        # those leaves a row of its own (see server._access_log).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS event_rejections ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, widget_id TEXT, device_id TEXT, "
            "event TEXT, method TEXT, path TEXT, status INTEGER, code TEXT, detail TEXT, "
            "request_id TEXT, occurred_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS event_rejections_device_idx "
            "ON event_rejections(device_id, occurred_at)"
        )
        conn.commit()
        _add_column_if_missing(conn, "events", "instance_id", "TEXT")
        _add_column_if_missing(conn, "events", "client_event_id", "TEXT")
        _add_column_if_missing(conn, "publication_revisions", "watch_id", "TEXT")
        _add_column_if_missing(conn, "widget_update_requests", "instance_id", "TEXT")
        _add_column_if_missing(conn, "widget_update_requests", "consumed_at", "TEXT")
        _add_column_if_missing(conn, "publication_render_builds", "instance_id", "TEXT")
        _add_column_if_missing(conn, "device_client_info", "app_build_sha", "TEXT")
        _add_column_if_missing(conn, "publication_render_builds", "app_build_sha", "TEXT")
        _add_column_if_missing(conn, "widget_settings", "quiet_timezone", "TEXT NOT NULL DEFAULT 'UTC'")
        _add_column_if_missing(conn, "widget_watches", "quiet_timezone", "TEXT NOT NULL DEFAULT 'UTC'")
        _add_column_if_missing(conn, "widget_watches", "claim_until", "TEXT")
        _add_column_if_missing(conn, "publication_nudges", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "publication_nudges", "next_attempt_at", "TEXT")
        _add_column_if_missing(conn, "publication_nudges", "claim_until", "TEXT")
        _add_column_if_missing(conn, "publication_acks", "render_confirmed_at", "TEXT")
        # Delivery receipts repeated timestamps and details already owned by the
        # fetch, ack, and nudge tables. Preserve the only signal that was not stored
        # elsewhere (a positive render confirmation) before removing that table.
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='delivery_receipts'"
        ).fetchone():
            conn.execute(
                "UPDATE publication_acks SET render_confirmed_at = ("
                "SELECT occurred_at FROM delivery_receipts r WHERE "
                "r.widget_id=publication_acks.widget_id AND r.device_id=publication_acks.device_id "
                "AND r.revision=publication_acks.revision AND r.state='rendered') "
                "WHERE EXISTS (SELECT 1 FROM delivery_receipts r WHERE "
                "r.widget_id=publication_acks.widget_id AND r.device_id=publication_acks.device_id "
                "AND r.revision=publication_acks.revision AND r.state='rendered')"
            )
            conn.execute("DROP TABLE delivery_receipts")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS publication_revisions_watch_idx "
            "ON publication_revisions(widget_id, watch_id, published_at)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS events_client_event_idx "
            "ON events(widget_id, device_id, client_event_id) WHERE client_event_id IS NOT NULL"
        )
        conn.commit()
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, declaration: str
) -> None:
    """Add a column to an existing table, once.

    The schema is normally created fresh with CREATE TABLE IF NOT EXISTS, which does
    not add columns to a database that already exists. Every one of these additions is
    nullable, so an old row keeps working and reads as "not reported" rather than as a
    default we invented.
    """
    try:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return
    if not existing or column in existing:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        conn.commit()
    except sqlite3.Error as exc:  # pragma: no cover - concurrent migration
        logger.debug("could not add %s.%s: %s", table, column, exc)
