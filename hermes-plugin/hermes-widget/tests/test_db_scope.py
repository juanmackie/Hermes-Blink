"""Request scopes reuse a thread-local connection and always release it."""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
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


class DatabaseConnectionScope(unittest.TestCase):
    def setUp(self):
        _load_plugin()
        self.db = importlib.import_module("hermes_plugins.hermes_widget.db")
        self.directory = Path(tempfile.mkdtemp(prefix="hermes-db-scope-"))
        os.environ["HERMES_WIDGET_DIR"] = str(self.directory)
        self.addCleanup(os.environ.pop, "HERMES_WIDGET_DIR", None)
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def test_nested_store_connections_borrow_the_outer_connection(self):
        with self.db.db() as outer:
            borrowed = self.db._connect()
            borrowed.close()  # legacy callers release their borrow, not the scope
            borrowed_again = self.db._connect()
            self.assertIs(outer, borrowed)
            self.assertIs(outer, borrowed_again)
            self.assertEqual(borrowed_again.execute("PRAGMA user_version").fetchone()[0], 8)

        with self.assertRaisesRegex(Exception, "closed"):
            outer.execute("SELECT 1")

    def test_scope_exit_rolls_back_uncommitted_work_and_closes(self):
        setup = self.db._connect()
        try:
            setup.execute("CREATE TABLE scoped_write (value INTEGER)")
            setup.commit()
        finally:
            setup.close()
        with self.assertRaisesRegex(RuntimeError, "abort request"):
            with self.db.db() as conn:
                conn.execute("INSERT INTO scoped_write VALUES (1)")
                raise RuntimeError("abort request")

        conn = self.db._connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scoped_write").fetchone()[0], 0)
        finally:
            conn.close()

    def test_a_real_writer_lock_raises_after_the_configured_busy_timeout(self):
        setup = self.db._connect()
        try:
            setup.execute("CREATE TABLE locked_write (value INTEGER)")
            setup.commit()
        finally:
            setup.close()

        locker = sqlite3.connect(str(self.db.db_path()), timeout=0.05)
        writer = None
        try:
            locker.execute("BEGIN IMMEDIATE")
            writer = self.db._connect(timeout=0.05)
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                writer.execute("INSERT INTO locked_write VALUES (1)")
        finally:
            if writer is not None:
                writer.close()
            locker.rollback()
            locker.close()

        check = self.db._connect()
        try:
            self.assertEqual(check.execute("SELECT COUNT(*) FROM locked_write").fetchone()[0], 0)
        finally:
            check.close()


if __name__ == "__main__":
    unittest.main()
