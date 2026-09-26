"""Delivery truthfulness, render surface, and device-identity regressions.

Covers field feedback: superseded revisions must leave a trace, status must
separate "waiting" from "lost", maxAgeSeconds must drop stale content instead of
rendering it late, capabilities must expose the render surface and SVG allowlist,
and the layout endpoint must point at the publication store.
"""
from __future__ import annotations

import http.client
import importlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


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


class DeliveryTruthfulness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _load_plugin()
        cls.store = importlib.import_module("hermes_plugins.hermes_widget.store")
        cls.publication = importlib.import_module("hermes_plugins.hermes_widget.publication")
        cls.tools = importlib.import_module("hermes_plugins.hermes_widget.tools")
        cls.server_module = importlib.import_module("hermes_plugins.hermes_widget.server")
        cls.server = cls.server_module.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        os.environ.pop("HERMES_WIDGET_DIR", None)

    def setUp(self):
        # A fresh store per test keeps revision numbers deterministic.
        self._dir = Path(tempfile.mkdtemp(prefix="hermes-delivery-"))
        os.environ["HERMES_WIDGET_DIR"] = str(self._dir)
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        self.agent_token = self.store.get_agent_token()
        self.assertTrue(self.agent_token)

    def request(self, method, path, body=None, token=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read().decode("utf-8") or "{}"
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, {"raw": raw}
        finally:
            connection.close()

    def pair_device(self, label="Pixel 9"):
        minted = self.store.mint_pairing_code()
        device = self.store.register_device(minted["code"], label)
        assert device is not None
        return device

    def test_superseded_revision_leaves_a_trace_and_skipped_state(self):
        device = self.pair_device()
        first = self.store.put_publication("hermes-brief", title="One", summary="first", text="a")
        second = self.store.put_publication("hermes-brief", title="Two", summary="second", text="b")
        self.assertEqual(first["revision"], 1)
        self.assertEqual(second["revision"], 2)

        status = self.store.publication_status("hermes-brief")
        revisions = {item["revision"]: item for item in status["revisions"]}
        self.assertTrue(revisions[1]["superseded"])
        self.assertEqual(revisions[1]["supersededReason"], "superseded_by_revision_2")
        self.assertFalse(revisions[2]["superseded"])

        delivery = {item["deviceId"]: item for item in status["delivery"]}
        info = delivery[device["deviceId"]]
        self.assertIsNone(info["lastFetchedRevision"])
        self.assertEqual(info["skippedRevisions"], [1])
        self.assertEqual(status["pollIntervalSeconds"], 900)

    def test_status_surfaces_a_revision_history_gap(self):
        self.store.put_publication("hermes-brief", title="One", summary="first", text="a")
        self.store.put_publication("hermes-brief", title="Two", summary="second", text="b")
        with sqlite3.connect(str(self.store.db_path())) as conn:
            conn.execute("DELETE FROM publication_revisions WHERE widget_id = ?", ("hermes-brief",))
            conn.commit()
        status = self.store.publication_status("hermes-brief")
        self.assertEqual(status["revisionHistory"]["currentRevision"], 2)
        self.assertEqual(status["revisionHistory"]["maxRecordedRevision"], 0)
        self.assertEqual(status["revisionHistory"]["missingRevisionCount"], 2)
        self.assertEqual(status["revisionHistory"]["missingRevisions"], [1, 2])
        self.assertTrue(status["revisionHistory"]["gap"])
        self.assertEqual(status["warnings"][0]["code"], "revision_history_gap")
        self.assertIn("cannot be treated as complete", status["warnings"][0]["detail"])

    def test_fetching_the_current_revision_clears_the_skipped_state(self):
        device = self.pair_device()
        self.store.put_publication("hermes-brief", title="One", summary="first", text="a")
        self.store.put_publication("hermes-brief", title="Two", summary="second", text="b")
        self.store.record_publication_fetch("hermes-brief", device["deviceId"], 2, downloaded=True)
        status = self.store.publication_status("hermes-brief")
        info = status["delivery"][0]
        self.assertEqual(info["lastFetchedRevision"], 2)
        self.assertEqual(info["skippedRevisions"], [1])
        self.assertEqual(info["state"], "downloaded")

    def test_max_age_seconds_drops_a_stale_publication(self):
        self.store.put_publication(
            "hermes-brief", title="Chart", summary="market", text="do not render late",
            max_age_seconds=60,
        )
        with sqlite3.connect(str(self.store.db_path())) as conn:
            row = conn.execute(
                "SELECT payload_json FROM publications WHERE widget_id = ?", ("hermes-brief",)
            ).fetchone()
            payload = json.loads(row[0])
            payload["publishedAt"] = "2000-01-01T00:00:00Z"
            conn.execute(
                "UPDATE publications SET payload_json = ? WHERE widget_id = ?",
                (json.dumps(payload), "hermes-brief"),
            )
        self.assertTrue(self.store.get_publication("hermes-brief")["stale"])
        status_code, body = self.request(
            "GET", "/v1/widgets/hermes-brief/publication", token=self.agent_token
        )
        self.assertEqual(status_code, 410, body)
        self.assertEqual(body["error"], "publication_stale")
        status = self.store.publication_status("hermes-brief")
        self.assertTrue(status["stale"])
        self.assertEqual(status["state"], "stale")

    def test_capabilities_expose_svg_allowlist_and_render_surface(self):
        caps = self.store.publication_capabilities()
        self.assertIn("path", caps["svg"]["allowedElements"])
        self.assertIn("d", caps["svg"]["allowedAttributes"])
        self.assertEqual(caps["svg"]["ignoredAttributes"], [])
        self.assertEqual(caps["render"]["fit"], "contain")
        self.assertEqual(caps["layoutMaxTtlSeconds"], 86400)
        self.assertEqual(caps["publicationMaxTtlSeconds"], 31536000)
        self.assertIn("refresh", caps["events"]["vocabulary"])

        device = self.pair_device()
        self.store.put_publication("hermes-brief", title="T", summary="S", text="x")
        self.store.record_publication_fetch("hermes-brief", device["deviceId"], 1, downloaded=True)
        self.store.acknowledge_publication_render("hermes-brief", device["deviceId"], 1, 966, 387)
        caps = self.store.publication_capabilities()
        last = caps["render"]["lastRendered"]
        self.assertEqual((last["width"], last["height"]), (966, 387))
        self.assertEqual(caps["render"]["recommendedAspectRatio"], round(966 / 387, 4))

    def test_layout_endpoint_points_at_the_publication_store(self):
        layout = {
            "version": 2,
            "widgetId": "hermes-brief",
            "root": {"type": "column", "children": [{"type": "text", "value": "hi"}]},
        }
        self.store.put_widget("hermes-brief", layout)
        self.store.put_publication("hermes-brief", title="T", summary="S", text="x")
        status_code, body = self.request("GET", "/v1/widgets/hermes-brief", token=self.agent_token)
        self.assertEqual(status_code, 200, body)
        pointer = body.get("publication")
        assert isinstance(pointer, dict)
        self.assertEqual(pointer["revision"], 1)
        self.assertEqual(pointer["state"], "published")

    def test_device_label_round_trip_and_rename_authorization(self):
        device = self.pair_device(label="Pixel 9 Pro")
        listing = {item["deviceId"]: item for item in self.store.list_devices()}
        self.assertEqual(listing[device["deviceId"]]["label"], "Pixel 9 Pro")

        status_code, body = self.request(
            "PATCH", "/v1/device", {"label": "Kitchen tablet"}, token=device["token"]
        )
        self.assertEqual(status_code, 200, body)
        self.assertEqual(body["label"], "Kitchen tablet")

        status_code, _ = self.request(
            "PATCH", "/v1/device", {"label": "nope"}, token=self.agent_token
        )
        self.assertEqual(status_code, 403)
        status_code, _ = self.request("PATCH", "/v1/device", {"label": "  "}, token=device["token"])
        self.assertEqual(status_code, 400)

    def test_mint_pairing_code_returns_a_copy_paste_line(self):
        result = json.loads(
            self.tools.widget_mint_pairing_code(
                {"server_url": "https://widget.example.ts.net:8443"}
            )
        )
        self.assertEqual(result["serverUrl"], "https://widget.example.ts.net:8443")
        self.assertEqual(
            result["pairingLine"],
            f"https://widget.example.ts.net:8443  code={result['code']}",
        )

    def test_layout_ttl_ceiling_matches_the_shared_constant(self):
        ceiling = self.publication.LAYOUT_MAX_TTL_SECONDS
        ok = {
            "version": 2,
            "widgetId": "ttl-widget",
            "ttlSeconds": ceiling,
            "root": {"type": "column", "children": [{"type": "text", "value": "hi"}]},
        }
        self.store.put_widget("ttl-widget", ok)
        too_big = {**ok, "ttlSeconds": ceiling + 1}
        with self.assertRaises(self.store.StoreError):
            self.store.put_widget("ttl-widget", too_big)


if __name__ == "__main__":
    unittest.main(verbosity=2)
