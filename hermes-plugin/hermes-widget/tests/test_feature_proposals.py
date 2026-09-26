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
from datetime import datetime, timedelta, timezone
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
        cls.tools = importlib.import_module("hermes_plugins.hermes_widget.tools")
        cls.preview = importlib.import_module("hermes_plugins.hermes_widget.preview")
        cls.watches = importlib.import_module("hermes_plugins.hermes_widget.watches")
        cls.cli = importlib.import_module("hermes_plugins.hermes_widget.cli")
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

    def test_cli_resolves_repository_fixture_from_any_cwd(self):
        with tempfile.TemporaryDirectory(prefix="hermes-cwd-") as temp:
            with patch("pathlib.Path.cwd", return_value=Path(temp)):
                resolved = self.cli._resolve_input_file("fixtures/brief-v2.json", label="layout file")
        self.assertTrue(resolved.is_file())
        self.assertEqual(resolved.name, "brief-v2.json")

    def test_push_state_and_wake_test_are_visible_without_a_publication(self):
        state = self.store.set_device_push_state(
            self.device["deviceId"], "failed", distributor_present=True, failure_reason="AUTH_FAILED"
        )
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["failureReason"], "AUTH_FAILED")
        self.store.put_widget(
            "feature",
            {"version": 2, "widgetId": "feature", "root": {"type": "column", "children": [{"type": "text", "value": "x"}]}},
        )
        self.store.set_device_push_endpoint(self.device["deviceId"], "https://ntfy.example/up/wake")
        with patch.object(self.store._push, "wake") as wake:
            result = self.store.wake_test("feature")
        wake.assert_called_once_with("https://ntfy.example/up/wake")
        self.assertTrue(result["contentFree"])
        self.assertEqual(result["receiptChain"][0]["state"], "nudge_sent")
        status = self.store.publication_status("feature")
        self.assertTrue(status["wake"]["devices"][0]["registered"])
        self.assertEqual(status["wake"]["registeredCount"], 1)

    def test_ticker_update_keeps_hero_and_gets_independent_expiry(self):
        self.store.put_publication("regions", title="Hero", summary="Hero summary", text="hero body")
        result = self.store.put_ticker(
            "regions", title="Ticker", summary="countdown", text="12:00", max_age_seconds=60
        )
        self.assertEqual(result["content"]["text"], "hero body")
        self.assertEqual(result["regions"]["ticker"]["summary"], "countdown")
        self.assertEqual(result["ticker"]["summary"], "countdown")
        self.assertEqual(result["regions"]["ticker"]["maxAgeSeconds"], 60)

    def test_publication_carries_provenance_dark_and_variants(self):
        result = self.store.put_publication(
            "polish", title="Estimate", summary="S", text="base",
            provenance="estimate", dark_palette=True,
            variants={"2x2": {"title": "Small", "summary": "S", "text": "compact"}},
        )
        self.assertEqual(result["provenance"], "estimate")
        self.assertTrue(result["darkPalette"])
        self.assertEqual(result["variants"]["2x2"]["text"], "compact")
        rendered = self.preview.render_publication_previews(result, sizes=["2x2"])
        self.assertEqual(rendered[0]["renderer"], "pillow-text")

    def test_bounded_question_is_answered_through_authenticated_path(self):
        self.store.put_publication("questions", title="Q", summary="S", text="body")
        question = self.store.ask_question("questions", "Which option?")
        self.assertEqual(self.store.get_publication("questions")["question"]["prompt"], "Which option?")
        answer = self.store.answer_question(self.device["deviceId"], question["questionId"], "first")
        self.assertEqual(answer["status"], "answered")
        self.assertIsNone(self.store.get_publication("questions").get("question"))

    def test_attention_report_is_aggregate_only_and_scorecard_visible(self):
        self.store.put_publication("attention", title="A", summary="S", text="body")
        self.store.report_attention(self.device["deviceId"], "attention", {
            "revision": 1, "rendered": 1, "dwellLt5": 1, "taps": 2,
        })
        summary = self.store.publication_status("attention")["attention"]
        self.assertEqual(summary["rendered"], 1)
        self.assertEqual(summary["dwell_lt5"], 1)
        self.assertEqual(summary["taps"], 2)
        with self.assertRaises(self.store.StoreError):
            self.store.report_attention(self.device["deviceId"], "attention", {
                "revision": 1, "rendered": 1, "content": "secret",
            })

    def test_watch_publishes_only_on_transition_and_clears_when_resolved(self):
        self.store.put_publication("watch-widget", title="Hero", summary="hero", text="hero")
        watch = self.watches.create_watch(
            "watch-widget", name="receipt",
            condition={"type": "source_equals", "source": "receipt", "equals": True},
            payload={"title": "Receipt", "summary": "ready", "text": "done"},
            cadence_seconds=60,
        )
        base = datetime.now(timezone.utc)
        first = self.watches.tick_watches(sources={"receipt": True}, now=base)
        self.assertEqual(first[0]["state"], "published")
        second = self.watches.tick_watches(sources={"receipt": True}, now=base + timedelta(seconds=61))
        self.assertEqual(second, [])
        cleared = self.watches.tick_watches(sources={"receipt": False}, now=base + timedelta(seconds=122))
        self.assertEqual(cleared[0]["state"], "cleared")
        self.assertFalse(self.watches.get_watch(watch["watchId"])["enabled"])

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

    def test_preview_accepts_a_local_file_in_a_publication_envelope(self):
        from PIL import Image
        path = Path(self._dir) / "source.png"
        Image.new("RGB", (40, 20), (10, 120, 200)).save(path)
        result = json.loads(self.tools.widget_preview({
            "widget_id": "file-preview",
            "publication": {
                "title": "File", "summary": "Local file",
                "content": {"filePath": str(path)},
            },
            "sizes": ["2x2"],
        }))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["previews"][0]["renderer"], "pillow")

    def test_svg_preview_reports_missing_local_renderer(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
        publication = {
            "kind": "image", "title": "Chart", "summary": "Chart",
            "content": {"type": "image", "mediaType": "image/svg+xml", "data": __import__("base64").b64encode(svg).decode()},
        }
        with patch.dict(sys.modules, {"cairosvg": None}):
            rendered = self.preview.render_publication_previews(publication, sizes=["2x2"])
        self.assertEqual(rendered[0]["renderer"], "svg-renderer-unavailable")
        self.assertIn("No local SVG renderer", rendered[0]["note"])

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
