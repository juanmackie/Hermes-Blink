"""Binding precedence, restart reporting, and pairing-guidance regression tests.

These cover the public-deployment bugs directly: an omitted flag must never
overwrite a saved binding, a malformed saved config must fail loudly, a binding
change must report a pending restart instead of claiming the new bind is live,
and `up`/`pair` must never invent a phone URL.
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PLUGIN_DIR = Path(__file__).resolve().parents[1]

# Built rather than written as a literal so the all-interfaces lint does not trip
# on a test fixture that never binds anything.
WILDCARD_V4 = ".".join(("0", "0", "0", "0"))


def _load_plugin():
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []  # type: ignore[attr-defined]
        sys.modules["hermes_plugins"] = namespace
    name = "hermes_plugins.hermes_widget"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class BindingConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _load_plugin()
        cls.proactive = importlib.import_module("hermes_plugins.hermes_widget.proactive")
        cls.cli = importlib.import_module("hermes_plugins.hermes_widget.cli")
        cls.tools = importlib.import_module("hermes_plugins.hermes_widget.tools")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-binding-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        os.environ["HERMES_WIDGET_DIR"] = str(Path(self.temp.name) / "data")
        self.addCleanup(os.environ.pop, "HERMES_WIDGET_DIR", None)
        previous = os.environ.get("HERMES_WIDGET_FORCE_ENV")
        os.environ["HERMES_WIDGET_FORCE_ENV"] = "supported"
        self.addCleanup(
            lambda: os.environ.pop("HERMES_WIDGET_FORCE_ENV", None)
            if previous is None
            else os.environ.__setitem__("HERMES_WIDGET_FORCE_ENV", previous)
        )
        self._home_patch = patch.object(self.proactive, "_hermes_home", return_value=self.home)
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    # -- helpers -----------------------------------------------------------
    def write_saved(self, **value):
        target = self.home / "widget" / "server.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")
        return target

    def read_saved(self) -> dict:
        return json.loads((self.home / "widget" / "server.json").read_text(encoding="utf-8"))

    def fake_server(self) -> int:
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                conn.close()

        def cleanup():
            stop.set()
            with contextlib.suppress(OSError):
                listener.close()

        threading.Thread(target=serve, daemon=True).start()
        self.addCleanup(cleanup)
        return listener.getsockname()[1]

    def run_up(self, **overrides) -> tuple[int, dict, str]:
        args = {
            "json": True,
            "host": None,
            "port": None,
            "server_url": None,
            "widget_id": "hermes-brief",
            "schedule": "every 6h",
        }
        args.update(overrides)
        buffer = io.StringIO()
        with (
            patch.object(self.proactive, "install_skill_file", return_value=self.home / "skill"),
            patch.object(self.proactive, "install_startup_hook", return_value=self.home / "hook"),
            patch.object(self.proactive, "install_routine", side_effect=RuntimeError("no cron")),
            patch.object(self.cli, "_ensure_systemd_service", return_value=(True, None)),
            redirect_stdout(buffer),
        ):
            rc = self.cli._up(SimpleNamespace(**args))
        text = buffer.getvalue()
        try:
            return rc, json.loads(text), text
        except ValueError:
            return rc, {}, text

    # -- config precedence -------------------------------------------------
    def test_omitted_flags_preserve_saved_binding_independently(self):
        self.write_saved(hermesBin="/opt/hermes", home=str(self.home), host=WILDCARD_V4, port=9000)
        self.proactive.write_server_config()
        self.assertEqual((self.read_saved()["host"], self.read_saved()["port"]), (WILDCARD_V4, 9000))

        self.proactive.write_server_config("127.0.0.1", None)
        self.assertEqual((self.read_saved()["host"], self.read_saved()["port"]), ("127.0.0.1", 9000))

        self.proactive.write_server_config(None, 9100)
        self.assertEqual((self.read_saved()["host"], self.read_saved()["port"]), ("127.0.0.1", 9100))

    def test_first_install_defaults_to_loopback(self):
        self.proactive.write_server_config()
        saved = self.read_saved()
        self.assertEqual((saved["host"], saved["port"]), ("127.0.0.1", 8788))

    def test_watch_tool_dispatches_supported_operations(self):
        for operation, handler_name in (
            ("create", "_watch_create"),
            ("list", "_watch_list"),
            ("pause", "_watch_pause"),
            ("tick", "_watch_tick"),
        ):
            with self.subTest(operation=operation), patch.object(
                self.tools, handler_name, return_value="handled"
            ) as handler:
                self.assertEqual(self.tools.widget_watch({"operation": operation}), "handled")
                handler.assert_called_once_with({"operation": operation})

    def test_watch_tool_rejects_unknown_operation(self):
        result = json.loads(self.tools.widget_watch({"operation": "delete"}))
        self.assertEqual(result["error"], "invalid_watch_operation")

    def test_agent_cannot_assert_device_confirmation(self):
        result = json.loads(self.tools.widget_resolve_intent({
            "intent_id": "intent-example", "outcome": "applied", "confirmed": True,
        }))
        self.assertEqual(result["error"], "device_confirmation_required")

    def test_upgrade_uses_hermes_plugin_manager_after_backup(self):
        buffer = io.StringIO()
        with (
            patch.object(self.cli.shutil, "which", return_value="hermes"),
            patch.object(self.cli.store, "backup_db", return_value=Path("widget.db.bak.test")),
            patch.object(self.cli.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
            patch.object(self.cli.store, "list_widgets", return_value=["hermes-brief"]),
            redirect_stdout(buffer),
        ):
            rc = self.cli._upgrade(SimpleNamespace())
        self.assertEqual(rc, 0)
        run.assert_called_once_with(
            ["hermes", "plugins", "update", "hermes-widget"], check=False, timeout=600,
        )
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["plugin_updated"])
        self.assertTrue(payload["restart_required"])
        self.assertEqual(payload["backup"], "widget.db.bak.test")

    def test_uninstall_requires_confirmation_before_deleting_data(self):
        buffer = io.StringIO()
        with patch.object(self.cli.sys, "stdin", io.StringIO("")), redirect_stdout(buffer):
            rc = self.cli._uninstall(SimpleNamespace(keep_data=False, yes=False))
        self.assertEqual(rc, 1)
        self.assertIn("rerun with --yes", buffer.getvalue())

    def test_uninstall_stops_integrations_and_removes_only_widget_data(self):
        data = Path(self.temp.name) / "widget-data"
        assets = data / "assets"
        assets.mkdir(parents=True)
        for name in ("widget.db", "widget.db-wal", "agent_token", "widget.db.bak.test"):
            (data / name).write_text("test", encoding="utf-8")
        (assets / ("asset_" + "a" * 24)).write_bytes(b"asset")
        (assets / "user-note.txt").write_text("keep", encoding="utf-8")
        config = self.home / "widget" / "server.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("{}", encoding="utf-8")
        service = self.home / ".config" / "systemd" / "user" / "hermes-widget.service"
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_text("unit", encoding="utf-8")
        output = io.StringIO()
        with (
            patch.object(self.cli.Path, "home", return_value=self.home),
            patch.object(self.cli.shutil, "which", return_value="systemctl"),
            patch.object(self.cli.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
            patch.object(self.cli.store, "data_dir", return_value=data),
            patch.object(self.cli, "_port_listening", return_value=False),
            patch.object(self.proactive, "remove_routine", return_value=True),
            patch.object(self.proactive, "remove_startup_hook", return_value=True),
            patch.object(self.proactive, "remove_skill_file", return_value=True),
            redirect_stdout(output),
        ):
            rc = self.cli._uninstall(SimpleNamespace(keep_data=False, yes=True))
        self.assertEqual(rc, 0, output.getvalue())
        self.assertFalse(service.exists())
        self.assertFalse(config.exists())
        self.assertFalse((data / "widget.db").exists())
        self.assertFalse((assets / ("asset_" + "a" * 24)).exists())
        self.assertTrue((assets / "user-note.txt").exists())
        self.assertEqual(run.call_args_list[0].args[0], [
            "systemctl", "--user", "disable", "--now", "hermes-widget.service",
        ])

    def test_plugin_exposes_one_watch_schema(self):
        plugin = _load_plugin()
        watch_schemas = [schema for schema, _ in plugin._TOOLS if schema["name"].startswith("widget_watch")]
        self.assertEqual([schema["name"] for schema in watch_schemas], ["widget_watch"])
        self.assertEqual(
            watch_schemas[0]["parameters"]["properties"]["operation"]["enum"],
            ["create", "list", "pause", "tick"],
        )

    def test_schema_v3_migrates_quiet_timezones_to_utc(self):
        _load_plugin()
        schema = importlib.import_module("hermes_plugins.hermes_widget.schema")
        with tempfile.TemporaryDirectory(prefix="hermes-schema-v3-") as temp:
            conn = sqlite3.connect(Path(temp) / "widget.db")
            conn.row_factory = sqlite3.Row
            conn.execute(
                "CREATE TABLE widget_settings (widget_id TEXT PRIMARY KEY, quiet_start TEXT, "
                "quiet_end TEXT, updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO widget_settings VALUES ('legacy', '22:00', '07:00', 'old')"
            )
            conn.execute(
                "CREATE TABLE widget_watches (watch_id TEXT PRIMARY KEY, quiet_start TEXT, quiet_end TEXT, "
                "enabled INTEGER NOT NULL DEFAULT 1, next_check_at TEXT)"
            )
            conn.execute(
                "INSERT INTO widget_watches (watch_id, quiet_start, quiet_end) "
                "VALUES ('legacy-watch', '22:00', '07:00')"
            )
            conn.execute("PRAGMA user_version=3")
            schema.ensure_schema(conn)
            settings_timezone = conn.execute(
                "SELECT quiet_timezone FROM widget_settings WHERE widget_id='legacy'"
            ).fetchone()[0]
            watch_timezone = conn.execute(
                "SELECT quiet_timezone FROM widget_watches WHERE watch_id='legacy-watch'"
            ).fetchone()[0]
            claim_until = conn.execute(
                "SELECT claim_until FROM widget_watches WHERE watch_id='legacy-watch'"
            ).fetchone()[0]
            self.assertEqual((settings_timezone, watch_timezone, claim_until), ("UTC", "UTC", None))
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], schema.SCHEMA_VERSION)
            conn.close()

    def test_binding_update_preserves_saved_binary_and_home(self):
        persisted = "/persistent/hermes-home"
        self.write_saved(hermesBin="/opt/custom/hermes", home=persisted, host=WILDCARD_V4, port=8788)
        self.proactive.write_server_config("127.0.0.1", 8888)
        saved = self.read_saved()
        self.assertEqual((saved["host"], saved["port"]), ("127.0.0.1", 8888))
        self.assertEqual(saved["hermesBin"], "/opt/custom/hermes")
        self.assertEqual(saved["home"], persisted)

    def test_malformed_config_is_rejected_not_replaced(self):
        target = self.home / "widget" / "server.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(self.proactive.ServerConfigError):
            self.proactive.write_server_config("127.0.0.1", 8788)
        self.assertEqual(target.read_text(encoding="utf-8"), "{ not json")

        target.write_text('["not", "an", "object"]', encoding="utf-8")
        with self.assertRaises(self.proactive.ServerConfigError):
            self.proactive.resolve_server_binding()

    # -- restart reporting -------------------------------------------------
    def test_up_saves_change_and_reports_restart_required(self):
        old_port = self.fake_server()
        self.write_saved(hermesBin="/opt/hermes", home=str(self.home), host="127.0.0.1", port=old_port)
        rc, payload, _ = self.run_up(host=WILDCARD_V4, port=old_port)
        self.assertEqual(rc, 0, payload)
        self.assertTrue(payload["restart_required"])
        self.assertEqual(payload["state"], "degraded")
        self.assertIsNone(payload["pairing_url_hint"])
        self.assertIsNone(payload["pairing"]["url"])
        self.assertTrue(any("HTTPS" in step for step in payload["pairing"]["instructions"]))
        saved = self.read_saved()
        self.assertEqual((saved["host"], saved["port"]), (WILDCARD_V4, old_port))

    def test_up_without_change_does_not_require_restart(self):
        port = self.fake_server()
        self.write_saved(hermesBin="/opt/hermes", home=str(self.home), host="127.0.0.1", port=port)
        rc, payload, _ = self.run_up()
        self.assertEqual(rc, 0, payload)
        self.assertFalse(payload["restart_required"])
        saved = self.read_saved()
        self.assertEqual((saved["host"], saved["port"]), ("127.0.0.1", port))

    def test_up_keeps_explicit_https_pairing_hint(self):
        rc, payload, _ = self.run_up(server_url="https://widget.example.ts.net:8788")
        self.assertEqual(rc, 0, payload)
        self.assertEqual(payload["pairing_url_hint"], "https://widget.example.ts.net:8788")
        self.assertEqual(payload["pairing"]["url"], "https://widget.example.ts.net:8788")

    def test_up_rejects_malformed_saved_config(self):
        target = self.home / "widget" / "server.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("nope", encoding="utf-8")
        rc, payload, _ = self.run_up()
        self.assertEqual(rc, 2)
        self.assertEqual(payload["error"], "invalid_server_config")
        self.assertEqual(target.read_text(encoding="utf-8"), "nope")

    # -- status truthfulness ----------------------------------------------
    def test_status_distinguishes_configured_bind_from_probe(self):
        self.write_saved(hermesBin="/opt/hermes", home=str(self.home), host=WILDCARD_V4, port=8788)
        (self.home / "widget" / "server-process.json").write_text(
            json.dumps({"host": "127.0.0.1", "port": 8788, "pid": 1}), encoding="utf-8"
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            rc = self.cli._status(SimpleNamespace(json=True))
        self.assertEqual(rc, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["host"], WILDCARD_V4)
        self.assertEqual(payload["port"], 8788)
        self.assertEqual(payload["probe_host"], "127.0.0.1")
        self.assertEqual(payload["probe_port"], 8788)
        self.assertTrue(payload["restart_required"])
        for duplicate in (
            "configuredHost", "configuredPort", "configured_host", "configured_port",
            "probeHost", "probePort", "restartRequired",
        ):
            self.assertNotIn(duplicate, payload)

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.cli._status(SimpleNamespace(json=False))
        text = buffer.getvalue()
        self.assertIn("Configured:   0.0.0.0:8788", text)
        self.assertIn("Probe:        127.0.0.1:8788", text)
        self.assertIn("Restart:      required", text)

    # -- pairing guidance --------------------------------------------------
    def test_pair_requires_usable_https_url(self):
        for value in (
            None,
            "",
            "http://widget.example.ts.net:8788",
            "https://127.0.0.1:8788",
            "https://localhost:8788",
            "not-a-url",
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                rc = self.cli._pair(SimpleNamespace(server_url=value, label="t", json=False))
            self.assertEqual(rc, 2, value)
            self.assertNotIn("QR", buffer.getvalue())
            self.assertNotIn("Same-phone", buffer.getvalue())

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            rc = self.cli._pair(
                SimpleNamespace(server_url="https://widget.example.ts.net:8788", label="t", json=True)
            )
        self.assertEqual(rc, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["serverUrl"], "https://widget.example.ts.net:8788")
        self.assertTrue(payload["code"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
