"""Delivery-state migration keeps the render signal without duplicate receipts."""
from __future__ import annotations

import importlib
import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _load_plugin():
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []
        sys.modules["hermes_plugins"] = namespace
    name = "hermes_plugins.hermes_widget"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)


class DeliveryReceiptMigration(unittest.TestCase):
    def test_schema_seven_migrates_render_confirmation_and_drops_duplicate_table(self):
        _load_plugin()
        schema = importlib.import_module("hermes_plugins.hermes_widget.schema")
        with tempfile.TemporaryDirectory(prefix="hermes-receipts-migration-") as directory:
            conn = sqlite3.connect(str(Path(directory) / "widget.db"))
            conn.row_factory = sqlite3.Row
            try:
                conn.executescript(
                    "CREATE TABLE publication_acks ("
                    "widget_id TEXT NOT NULL, device_id TEXT NOT NULL, revision INTEGER NOT NULL, "
                    "rendered_at TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, "
                    "PRIMARY KEY(widget_id, device_id, revision));"
                    "CREATE TABLE delivery_receipts ("
                    "widget_id TEXT NOT NULL, device_id TEXT NOT NULL, revision INTEGER NOT NULL, "
                    "state TEXT NOT NULL, occurred_at TEXT NOT NULL, detail TEXT, "
                    "PRIMARY KEY(widget_id, device_id, revision, state));"
                    "INSERT INTO publication_acks VALUES ('brief', 'device', 4, 'submitted-at', 240, 180);"
                    "INSERT INTO delivery_receipts VALUES ('brief', 'device', 4, 'rendered', 'confirmed-at', NULL);"
                    "PRAGMA user_version=6;"
                )

                schema.ensure_schema(conn)

                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 8)
                row = conn.execute(
                    "SELECT rendered_at, render_confirmed_at FROM publication_acks"
                ).fetchone()
                self.assertEqual(row["rendered_at"], "submitted-at")
                self.assertEqual(row["render_confirmed_at"], "confirmed-at")
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='delivery_receipts'"
                ).fetchone())
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
