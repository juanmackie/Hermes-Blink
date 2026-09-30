"""Retention keeps current publications and bounds historical widget data."""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _load_plugin():
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []
        sys.modules["hermes_plugins"] = namespace
    name = "hermes_plugins.hermes_widget"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Retention(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _load_plugin()
        cls.store = importlib.import_module("hermes_plugins.hermes_widget.store")
        cls.retention = importlib.import_module("hermes_plugins.hermes_widget.retention")

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="hermes-retention-"))
        os.environ["HERMES_WIDGET_DIR"] = str(self._dir)
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        self.addCleanup(os.environ.pop, "HERMES_WIDGET_DIR", None)

    def test_dry_run_then_prune_history_telemetry_and_orphan_assets(self):
        for index in range(3):
            self.store.put_publication(
                "retention", title=f"Revision {index}", summary="S", text=f"body {index}"
            )
        old = "2025-01-01T00:00:00Z"
        conn = self.store._connect()
        try:
            conn.execute(
                "INSERT INTO publication_fetches VALUES (?, ?, ?, ?, ?)",
                ("retention", "device-old", 3, old, old),
            )
            conn.execute(
                "INSERT INTO publication_acks "
                "(widget_id, device_id, revision, rendered_at, width, height) VALUES (?, ?, ?, ?, ?, ?)",
                ("retention", "device-old", 3, old, 200, 100),
            )
            conn.execute(
                "INSERT INTO publication_nudges "
                "(widget_id, revision, device_id, status, attempted_at, sent_at, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("retention", 3, "device-old", "sent", old, old, None),
            )
            conn.execute(
                "INSERT INTO attention_aggregates "
                "(device_id, widget_id, revision, updated_at) VALUES (?, ?, ?, ?)",
                ("device-old", "retention", 3, old),
            )
            conn.execute(
                "INSERT INTO action_audit "
                "(widget_id, device_id, item_id, action_class, revision, event, outcome, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("retention", "device-old", "item", "open", 3, "open", "ok", old),
            )
            conn.execute(
                "INSERT INTO publication_render_builds "
                "(widget_id, device_id, revision, reported_at) VALUES (?, ?, ?, ?)",
                ("retention", "device-old", 3, old),
            )
            asset_id = "asset_" + "a" * 24
            asset_data = b"orphan"
            asset_path = self.store.assets_dir() / asset_id
            asset_path.write_bytes(asset_data)
            conn.execute(
                "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)",
                (asset_id, "a" * 64, "image/png", len(asset_data), 1, 1, old),
            )
            conn.commit()
        finally:
            conn.close()

        now = datetime(2025, 4, 2, tzinfo=timezone.utc)
        preview = self.retention.prune(keep_revisions=2, retention_days=90, now=now, dry_run=True)
        self.assertTrue(preview["dryRun"])
        self.assertEqual(preview["counts"]["publication_revisions"], 1)
        self.assertEqual(preview["counts"]["assets"], 1)
        self.assertTrue(asset_path.is_file())
        self.assertEqual(list(self._dir.glob("widget.db.bak.*")), [])

        result = self.retention.prune(keep_revisions=2, retention_days=90, now=now)
        self.assertFalse(result["dryRun"])
        self.assertEqual(result["counts"]["action_audit"], 1)
        self.assertFalse(asset_path.exists())
        self.assertEqual(len(list(self._dir.glob("widget.db.bak.*"))), 1)
        status = self.store.publication_status("retention")
        self.assertEqual([row["revision"] for row in status["revisions"]], [3, 2])
        conn = self.store._connect()
        try:
            for table in (
                "publication_fetches", "publication_acks", "publication_nudges",
                "attention_aggregates", "action_audit",
                "publication_render_builds",
            ):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='delivery_receipts'"
            ).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)
        finally:
            conn.close()
