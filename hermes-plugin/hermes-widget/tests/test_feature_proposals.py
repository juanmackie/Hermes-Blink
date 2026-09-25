"""Feature-proposal contract tests: priority wake, previews, inventory, and intents."""
from __future__ import annotations

import http.client
import importlib
import io
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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


class FeatureProposals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _load_plugin()
        cls.store = importlib.import_module("hermes_plugins.hermes_widget.store")
        cls.preview = importlib.import_module("hermes_plugins.hermes_widget.preview")
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
        self._dir = Path(tempfile.mkdtemp(prefix="hermes-features-"))
        os.environ["HERMES_WIDGET_DIR"] = str(self._dir)
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        self.agent = self.store.get_agent_token()
        self.minted = self.store.mint_pairing_code()
        self.device = self.store.register_device(self.minted["code"], "Pixel")
        assert self.device

    def request(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, data, headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw.decode()) if raw else {}
        finally:
            conn.close()

    def test_priority_wake_is_content_free_and_receipted(self):
        self.store.set_device_push_endpoint(self.device["deviceId"], "https://ntfy.example/up/device")
        with patch.object(self.store._push, "wake") as wake:
            publication = self.store.put_publication(
                "feature", title="Alert", summary="A private summary", text="secret body", priority="high"
            )
        wake.assert_called_once_with("https://ntfy.example/up/device")
        self.assertEqual(publication["priority"], "high")
        status = self.store.publication_status("feature")
        delivery = status["delivery"][0]
        self.assertEqual(delivery["nudgeStatus"], "sent")
        self.assertEqual(delivery["receipts"][0]["state"], "nudge_sent")
        self.store.record_publication_fetch("feature", self.device["deviceId"], 1, downloaded=True)
        self.store.acknowledge_publication_render("feature", self.device["deviceId"], 1, 240, 240, status="rendered")
        delivery = self.store.publication_status("feature")["delivery"][0]
        self.assertEqual([r["state"] for r in delivery["receipts"]], [
            "nudge_sent", "fetched", "downloaded", "render_submitted", "rendered"
        ])

    def test_priority_over_limit_degrades_visibly(self):
        with patch.object(self.store, "HIGH_PRIORITY_MAX_PER_HOUR", 1):
            first = self.store.put_publication("limit", title="1", summary="s", text="a", priority="high")
            second = self.store.put_publication("limit", title="2", summary="s", text="b", priority="high")
        self.assertEqual(first["priority"], "high")
        self.assertEqual(second["priority"], "normal")
        self.assertEqual(second["requestedPriority"], "high")
        self.assertEqual(second["priorityDegradedReason"], "high_priority_hour_limit")

    def test_inventory_and_preview_endpoint(self):
        self.store.report_widget_instances(
            self.device["deviceId"], "preview-widget",
            [{"instanceId": "7", "sizeClass": "2x2", "widthDp": 120, "heightDp": 120, "widthPx": 240, "heightPx": 240}],
        )
        self.store.put_publication("preview-widget", title="T", summary="S", text="hello")
        status, body = self.request("POST", "/v1/widgets/preview-widget/preview", {}, self.agent)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["sizes"], ["2x2"])
        preview = body["previews"][0]
        self.assertEqual((preview["width"], preview["height"]), (240, 240))
        self.assertEqual((preview["pixelWidth"], preview["pixelHeight"]), (240, 240))
        self.assertEqual(preview["mediaType"], "image/png")
        self.assertTrue(preview["data"])

    def test_raster_preview_uses_pillow_without_cairo(self):
        from PIL import Image

        source = Image.new("RGB", (1200, 480), (12, 140, 220))
        source.putpixel((0, 0), (255, 0, 0))
        encoded = io.BytesIO()
        source.save(encoded, format="PNG")
        with patch.dict(sys.modules, {"cairosvg": None}):
            data, renderer = self.preview._fit_image(encoded.getvalue(), "image/png", 540, 240)
        self.assertEqual(renderer, "pillow")
        self.assertNotEqual(renderer, "fallback")
        self.assertGreater(len(data), 700)
        with Image.open(io.BytesIO(data)) as preview:
            self.assertEqual(preview.size, (540, 240))

    def test_text_preview_keeps_a_usable_path_without_cairo(self):
        with patch.dict(sys.modules, {"cairosvg": None}):
            data, renderer = self.preview._svg_text_png(
                {"title": "Title", "summary": "Summary", "content": {"text": "Body"}}, 240, 240
            )
        self.assertEqual(renderer, "pillow-text")
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_action_is_idempotent_queued_and_resolution_is_audited(self):
        self.store.put_publication(
            "actions", title="Waiting", summary="S", text="three waiting", item_id="task-1",
            actions=[{"kind": "approve", "itemId": "task-1", "actionClass": "reversible"}],
        )
        first = self.store.post_action_event(
            "actions", self.device["deviceId"], "approve",
            {"clientEventId": "tap-1", "itemId": "task-1"}, revision=1,
        )
        second = self.store.post_action_event(
            "actions", self.device["deviceId"], "approve",
            {"clientEventId": "tap-1", "itemId": "task-1"}, revision=1,
        )
        self.assertEqual(first["intent"]["intentId"], second["intent"]["intentId"])
        self.assertTrue(second["duplicate"])
        intent_id = first["intent"]["intentId"]
        resolved = self.store.resolve_intent(intent_id, "applied", result="validated", confirmed=True)
        self.assertEqual(resolved["status"], "applied")
        self.assertEqual(len(self.store.get_events(widget_id="actions")), 1)

    def test_revoked_device_cannot_report_or_act(self):
        self.store.revoke_device(self.device["deviceId"])
        with self.assertRaises(self.store.StoreError):
            self.store.report_widget_instances(self.device["deviceId"], "x", [])
        with self.assertRaises(self.store.ActionIntentError):
            self.store.post_action_event("x", self.device["deviceId"], "open", {"itemId": "x"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
