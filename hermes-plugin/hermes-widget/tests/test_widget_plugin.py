"""End-to-end verification for the hermes-widget plugin.

Starts the real stdlib HTTP server against a workspace-local SQLite store and
exercises the full device lifecycle: pair -> push (agent tool) -> fetch ->
interaction event -> read events. Run with the Hermes venv python:

    python -m unittest discover -s hermes-plugin/hermes-widget/tests -v
"""
from __future__ import annotations

import http.client
import importlib
import importlib.util
import json
import os
import shutil
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
TEST_DATA = PLUGIN_DIR / "_testdata"

def _load_plugin():
    """Load the plugin the way Hermes' PluginManager does (as a package)."""
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []  # type: ignore[attr-defined]
        sys.modules["hermes_plugins"] = ns
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


class WidgetPluginEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(TEST_DATA, ignore_errors=True)
        TEST_DATA.mkdir(parents=True, exist_ok=True)
        os.environ["HERMES_WIDGET_DIR"] = str(TEST_DATA)
        os.environ["HERMES_HOME"] = str(TEST_DATA / "home")
        cls.plugin = _load_plugin()
        cls.store = importlib.import_module("hermes_plugins.hermes_widget.store")
        cls.tools = importlib.import_module("hermes_plugins.hermes_widget.tools")
        server_mod = importlib.import_module("hermes_plugins.hermes_widget.server")
        cls.server = server_mod.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        os.environ.pop("HERMES_WIDGET_DIR", None)

    # -- helpers -----------------------------------------------------------
    def request(self, method, path, body=None, token=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode() or "{}"
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"raw": raw}

    def setUp(self):
        self.agent_token = self.store.get_agent_token()
        assert self.agent_token, "agent token should be created on demand"

    # -- tests -------------------------------------------------------------
    def test_health_is_public(self):
        status, body = self.request("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIn("widgets", body)

    def test_unknown_route_is_404(self):
        status, body = self.request("GET", "/v1/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_removed_v2_layout_route_is_404(self):
        status, body = self.request("GET", "/v1/widgets/hermes-brief")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")
        status, body = self.request("PUT", "/v1/widgets/hermes-brief", {})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_sensitive_intent_confirmation_requires_owning_device_token(self):
        status, minted = self.request("POST", "/v1/pairing-codes", {}, self.agent_token)
        self.assertEqual(status, 200, minted)
        status, paired = self.request("POST", "/v1/pair", {"code": minted["code"]})
        self.assertEqual(status, 200, paired)
        device_token = paired["token"]
        self.store.put_publication(
            "confirm-route", title="Confirm", summary="S", text="Review",
            actions=[{
                "kind": "approve", "itemId": "task-1", "actionClass": "destructive",
                "confirmOnDevice": True,
            }],
        )
        status, queued = self.request(
            "POST", "/v1/widgets/confirm-route/events",
            {"event": "approve", "itemId": "task-1", "actionClass": "destructive", "revision": 1},
            device_token,
        )
        self.assertEqual(status, 200, queued)
        intent_id = queued["intent"]["intentId"]
        status, rejected = self.request(
            "POST", f"/v1/device/action-intents/{intent_id}/confirm", {}, self.agent_token,
        )
        self.assertEqual(status, 403, rejected)
        status, rejected = self.request(
            "POST", "/v1/intents",
            {"intentId": intent_id, "outcome": "applied", "confirmed": True},
            self.agent_token,
        )
        self.assertEqual(status, 400, rejected)
        status, confirmed = self.request(
            "POST", f"/v1/device/action-intents/{intent_id}/confirm", {}, device_token,
        )
        self.assertEqual(status, 200, confirmed)
        self.assertEqual(confirmed["intent"]["status"], "queued")
        status, applied = self.request(
            "POST", "/v1/intents",
            {"intentId": intent_id, "outcome": "applied", "result": "approved"},
            self.agent_token,
        )
        self.assertEqual(status, 200, applied)
        self.assertEqual(applied["intent"]["status"], "applied")

    def test_full_lifecycle(self):
        # 1. agent mints a pairing code over HTTP
        status, minted = self.request("POST", "/v1/pairing-codes", {}, self.agent_token)
        self.assertEqual(status, 200, minted)
        code = minted["code"]

        # 2. device redeems it
        status, paired = self.request("POST", "/v1/pair", {"code": code, "deviceLabel": "Pixel"})
        self.assertEqual(status, 200, paired)
        device_token = paired["token"]
        self.assertTrue(device_token.startswith("dvc_"))
        self.assertNotIn(device_token, json.dumps(self.store.list_devices()))

        # 3. agent publishes user-visible content through the current tool.
        published = json.loads(self.tools.widget_publish({
            "widget_id": "hermes-brief",
            "title": "Today",
            "summary": "Good morning",
            "text": "Good morning",
        }))
        self.assertTrue(published.get("ok"), published)

        # 4. device fetches the current publication over HTTP.
        status, publication = self.request(
            "GET", "/v1/widgets/hermes-brief/publication", token=device_token
        )
        self.assertEqual(status, 200, publication)
        self.assertEqual(publication["widgetId"], "hermes-brief")
        self.assertEqual(publication["content"]["text"], "Good morning")

        # 5. device posts an interaction event
        status, event = self.request(
            "POST",
            "/v1/widgets/hermes-brief/events",
            {"event": "refresh", "payload": {}},
            device_token,
        )
        self.assertEqual(status, 200, event)
        self.assertTrue(event["id"] > 0)

        # 6. agent reads the event back through the tool
        read = json.loads(self.tools.widget_read_events({"widget_id": "hermes-brief"}))
        self.assertTrue(any(e["event"] == "refresh" for e in read["events"]), read)

    def test_auth_boundaries(self):
        # Protected publication routes require authentication, even on loopback.
        # Only /v1/health and /v1/pair are public; everything else needs a bearer.
        status, body = self.request("GET", "/v1/widgets")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")
        # A presented-but-invalid bearer must be rejected.
        status, body = self.request("GET", "/v1/widgets", token="not-a-real-token")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")
        # Authenticated operator path still works.
        status, body = self.request("GET", "/v1/widgets", token=self.agent_token)
        self.assertEqual(status, 200)
        # Health stays public; pairing-code mint needs agent auth.
        status, _ = self.request("GET", "/v1/health")
        self.assertEqual(status, 200)
        status, _ = self.request("POST", "/v1/pairing-codes", {}, token=None)
        self.assertEqual(status, 401)

    def test_keepalive_survives_an_error_with_a_body(self):
        # Regression: Android reuses connections. An error response to a
        # request that carried a body must not leave those bytes to be parsed
        # as the next request line on the same HTTP/1.1 connection.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(
                "GET",
                "/v1/widgets",
                body=json.dumps({"ignored": True}),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer not-a-real-token",
                },
            )
            first = conn.getresponse()
            first.read()
            self.assertEqual(first.status, 401)
            conn.request("GET", "/v1/health")
            second = conn.getresponse()
            payload = json.loads(second.read().decode())
            self.assertEqual(second.status, 200)
            self.assertEqual(payload["status"], "ok")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
