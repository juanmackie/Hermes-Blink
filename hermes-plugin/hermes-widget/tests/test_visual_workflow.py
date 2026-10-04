"""Publishing, recovery, and capacity contracts for structured visuals."""

from __future__ import annotations

import base64
import importlib
import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_feature_proposals import _load_plugin

EXAMPLES = [
    {"type": "metric", "label": "Checks passing", "value": 87, "unit": "%"},
    {"type": "progress", "label": "Migration", "value": 7, "target": 10, "unit": "modules"},
    {
        "type": "comparison",
        "unit": "seconds",
        "rows": [{"label": "Incremental", "value": 12}, {"label": "Clean", "value": 50}],
    },
    {
        "type": "chart",
        "style": "line",
        "points": [{"label": "Monday", "value": -2}, {"label": "Tuesday", "value": 3}],
    },
    {
        "type": "chart",
        "style": "bar",
        "points": [{"label": "Before", "value": 8}, {"label": "After", "value": 5}],
    },
    {
        "type": "timeline",
        "events": [
            {"date": "2026-10-01", "label": "Host ready"},
            {"date": "2026-10-03", "label": "Phone validation", "detail": "Manual pass pending"},
        ],
    },
]


class VisualWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _load_plugin()
        for name in (
            "store",
            "tools",
            "preview",
            "publication",
            "presentations",
            "refresh",
            "normal_wakes",
            "composition",
            "retention",
        ):
            setattr(cls, name, importlib.import_module("hermes_plugins.hermes_widget." + name))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blink-visual-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"HERMES_WIDGET_DIR": self.temp.name}).start()
        self.wake = patch.object(self.store._push, "wake").start()

    def publish(self, **extra):
        return self.store.put_publication(
            "visual",
            title="A useful finding",
            summary="Measured outcomes",
            presentation=EXAMPLES[2],
            **extra,
        )

    def device(self):
        code = self.store.mint_pairing_code()["code"]
        device = self.store.register_device(code, "Test phone")
        self.store.set_device_push_endpoint(device["deviceId"], "https://push.example/wake")
        return device["deviceId"]

    def test_all_presentations_render_complete_compositions_across_geometry(self):
        from PIL import Image

        sizes = ["110x56", "200x160", "210x276", "407x276", "210x412", "407x412", "900x650"]
        for example in EXAMPLES:
            publication = self.preview.build_preview_publication(
                {
                    "title": "Useful finding",
                    "summary": "Evidence summary",
                    "presentation": example,
                    "ticker": {"title": "Next", "summary": "Validate on phone"},
                },
                "visual",
            )
            self.assertEqual(len(publication["visualVariants"]), 8)
            for palette in ("light", "dark"):
                for font_scale in (1, 1.5, 2):
                    rendered = self.preview.render_publication_previews(
                        publication, sizes=sizes, palette=palette, font_scale=font_scale
                    )
                    for item in rendered:
                        with self.subTest(
                            example=example["type"],
                            size=item["size"],
                            palette=palette,
                            font_scale=font_scale,
                        ):
                            image = Image.open(io.BytesIO(base64.b64decode(item["data"])))
                            self.assertEqual(image.size, (item["pixelWidth"], item["pixelHeight"]))
                            plan = item["diagnostics"]
                            self.assertTrue(plan["advisory"])
                            controls = plan["controls"]
                            if plan["band"] in {"m", "l"}:
                                action = controls["requestUpdate"]
                                self.assertEqual(action[3], 48)
                                self.assertLessEqual(action[1] + action[3], plan["heightDp"])
                                if "visual" in controls:
                                    visual = controls["visual"]
                                    self.assertGreaterEqual(visual[1], action[1] + action[3])
                                    self.assertLessEqual(
                                        visual[1] + visual[3], plan["heightDp"] - 12 - 18
                                    )
                            else:
                                self.assertNotIn("visual", controls)
                                self.assertTrue(
                                    any("visual hidden" in s for s in plan["hiddenDetails"])
                                )

    def test_reported_dp_controls_band_independently_of_pixel_density(self):
        pub = self.preview.build_preview_publication(
            {"title": "Title", "summary": "Summary", "presentation": EXAMPLES[0]}, "visual"
        )
        results = []
        for density in (1, 2, 3):
            results.append(
                self.preview.render_publication_previews(
                    pub,
                    inventory=[
                        {
                            "sizeClass": "custom",
                            "widthDp": 210,
                            "heightDp": 276,
                            "widthPx": 210 * density,
                            "heightPx": 276 * density,
                        }
                    ],
                )[0]
            )
        self.assertEqual({r["diagnostics"]["band"] for r in results}, {"m"})
        self.assertEqual(
            results[0]["diagnostics"]["controls"], results[-1]["diagnostics"]["controls"]
        )

    def test_data_and_assets_are_retained_and_duplicate_has_no_revision_or_wake(self):
        self.device()
        first = self.publish(
            work_context={
                "sources": ["report.json"],
                "session": "session-1",
                "recheck": "Repeat measured build commands",
            }
        )
        self.assertEqual(first["kind"], "image")
        ids = self.store._publication_asset_ids(first)
        self.assertEqual(len(ids), 9)
        self.assertEqual(self.refresh.read_context("visual")["presentation"], first["presentation"])
        for asset_id in ids:
            self.assertTrue(self.store.read_asset(asset_id)[1].startswith(b"\x89PNG"))
        second = self.publish()
        self.assertTrue(second["unchanged"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(self.wake.call_count, 1)
        self.retention.prune(keep_revisions=1)
        for asset_id in ids:
            self.assertTrue(self.store.asset_path(asset_id).is_file())

    def test_variant_only_change_creates_revision_and_old_assets_retained_by_history(self):
        first = self.publish()
        example = {
            **EXAMPLES[2],
            "rows": [{"label": "Incremental", "value": 10}, {"label": "Clean", "value": 50}],
        }
        second = self.store.put_publication(
            "visual", title="A useful finding", summary="Measured outcomes", presentation=example
        )
        self.assertEqual(second["revision"], 2)
        self.assertNotEqual(first["visualVariants"], second["visualVariants"])
        self.retention.prune(keep_revisions=1)
        orphan_ids = self.store._publication_asset_ids(first) - self.store._publication_asset_ids(
            second
        )
        self.assertTrue(all(not self.store.asset_path(i).exists() for i in orphan_ids))

    def test_ticker_preserves_visual_data_assets_and_work_handoff(self):
        first = self.publish(ttl_seconds=3600, work_context={"sources": ["timings.json"], "recheck": "Repeat checks"})
        updated = self.store.put_ticker("visual", title="Next", summary="Validate", text="Phone pass")
        self.assertEqual(updated["presentation"], first["presentation"])
        self.assertEqual(updated["visualVariants"], first["visualVariants"])
        self.assertEqual(updated["content"], first["content"])
        self.assertEqual(updated["expiresAt"], first["expiresAt"])
        self.assertEqual(updated["ttlSeconds"], 3600)
        self.assertEqual(self.refresh.read_context("visual")["sources"], ["timings.json"])
        self.assertEqual(self.refresh.read_context("visual")["revision"], 2)

    def test_duplicate_attempts_do_not_spend_a_publication_or_wake_budget(self):
        self.device()
        first = self.store.put_publication("visual", title="Same", summary="Checked", text="data", ttl_seconds=3600)
        for _ in range(35):
            duplicate = self.store.put_publication("visual", title="Same", summary="Checked", text="data", ttl_seconds=3600)
            self.assertTrue(duplicate["unchanged"])
            self.assertEqual(duplicate["revision"], first["revision"])
        self.assertEqual(self.wake.call_count, 1)

    def test_dispatch_exception_retains_durable_wake_for_retry(self):
        publications = importlib.import_module("hermes_plugins.hermes_widget.publications")
        with patch.object(publications, "_dispatch_priority_nudges", side_effect=RuntimeError("connection lost")):
            self.publish()
        conn = self.store._connect()
        try:
            pending = conn.execute("SELECT * FROM widget_normal_wakes").fetchone()
            self.assertIsNotNone(pending)
            self.assertIsNone(pending["claim_until"])
        finally:
            conn.close()

    def test_missing_variant_preview_falls_back_to_primary_and_reports_capacity(self):
        first = self.publish()
        missing = first["visualVariants"]["m-wide-dark"]["assetId"]
        def loader(asset_id):
            if asset_id == missing:
                raise self.store.AssetUnavailable("offline")
            return self.store.read_asset(asset_id)[1]
        preview = self.preview.render_publication_previews(first, sizes=["407x276"], palette="dark", asset_loader=loader)[0]
        self.assertIn("variant unavailable; primary fallback", preview["diagnostics"]["hiddenDetails"])

    def test_invalid_data_and_missing_renderer_preserve_current_revision(self):
        self.publish()
        invalid = [
            {},
            {"type": "progress", "label": "Done", "value": 1, "target": 0},
            {"type": "progress", "label": "Done", "value": 1e15, "target": 1e-300},
            {"type": "metric", "label": "Invalid", "value": float("nan")},
            {"type": "comparison", "rows": []},
            {"type": "chart", "points": [{"label": "Bad", "value": True}]},
            {"type": "chart", "style": [], "points": [{"label": "Bad", "value": 1}]},
            {"type": "timeline", "events": [{"date": "Today", "label": "A", "unexpected": 1}]},
        ]
        for raw in invalid:
            with self.assertRaises(self.store.PublicationError):
                self.store.put_publication(
                    "visual", title="Title", summary="Summary", presentation=raw
                )
        with self.assertRaises(self.store.PublicationError):
            self.store.put_publication(
                "visual",
                title="Title",
                summary="Summary",
                presentation=EXAMPLES[0],
                text="Conflicting source",
            )
        with patch.dict(sys.modules, {"PIL": None}):
            with self.assertRaisesRegex(self.store.PublicationError, "Pillow is required"):
                self.store.put_publication(
                    "visual", title="Title", summary="Summary", presentation=EXAMPLES[0]
                )
        self.assertEqual(self.store.get_publication("visual")["revision"], 1)

    def test_full_primary_retains_more_items_than_medium_variant(self):
        raw = {"type": "comparison", "rows": [{"label": str(i), "value": i} for i in range(32)]}
        normalized, primary, variants = self.presentations.compile_presentation(raw)
        self.assertEqual(self.presentations.visible_items(normalized, "full", "wide")[1], 0)
        self.assertEqual(self.presentations.visible_items(normalized, "m", "narrow")[1], 29)
        self.assertGreater(primary.height, variants["m-narrow-light"].height)

    def test_refresh_ack_is_not_completion_and_publication_completes_atomically(self):
        device = self.device()
        self.publish()
        request = self.store.request_update("visual", device, "tap-1")
        self.store.mark_update_request_triggered(request["requestId"])
        status = json.loads(
            self.tools.widget_status({"widget_id": "visual", "consume_update_requests": True})
        )
        self.assertEqual(status["updateRequests"][0]["status"], "consumed")
        self.assertIsNone(status["updateRequests"][0]["completedAt"])
        lease = status["refreshLease"]["refreshId"]
        self.assertTrue(self.publish(refresh_id=lease)["unchanged"])
        outcome = self.store.list_update_requests("visual")[0]
        self.assertEqual(outcome["outcome"], "unchanged")
        self.assertEqual(outcome["resultRevision"], 1)
        self.assertIsNotNone(outcome["completedAt"])
        with self.assertRaises(self.store.StoreError):
            self.store.put_publication(
                "visual",
                title="Changed",
                summary="Source checked",
                text="new",
                refresh_id="invalid-lease",
            )
        self.assertEqual(self.store.get_publication("visual")["revision"], 1)

    def test_interrupted_refresh_is_recovered_and_unavailable_source_is_explicit_failed(self):
        device = self.device()
        request = self.store.request_update("visual", device, "tap-2")
        self.store.mark_update_request_triggered(request["requestId"])
        first = self.refresh.claim("visual")
        self.assertTrue(self.refresh.claim("visual")["busy"])
        conn = self.store._connect()
        try:
            conn.execute("UPDATE widget_refresh_runs SET lease_until='2000-01-01T00:00:00Z'")
            conn.commit()
        finally:
            conn.close()
        recovered = self.refresh.claim("visual")
        self.assertNotEqual(first["refreshId"], recovered["refreshId"])
        self.assertIn(request["requestId"], recovered["requests"])
        finished = json.loads(
            self.tools.widget_finish_refresh(
                {
                    "widget_id": "visual",
                    "refresh_id": recovered["refreshId"],
                    "outcome": "failed",
                    "reason": "Source is offline; previous measurements remain current",
                }
            )
        )
        self.assertTrue(finished["ok"])
        self.assertEqual(self.store.list_update_requests("visual")[0]["outcome"], "failed")
        self.assertIsNone(self.store.get_publication("visual"))

    def test_final_user_result_attaches_only_to_exact_publishing_turn(self):
        publication = self.publish()
        self.refresh.final_result(
            session_id="s1", turn_id="t1", assistant_response="Unrelated response"
        )
        self.assertNotIn("finalResult", self.refresh.read_context("visual"))
        self.refresh.published_turn(
            tool_name="widget_publish",
            result=json.dumps({"ok": True, **publication}),
            session_id="s1",
            turn_id="t1",
        )
        self.refresh.final_result(
            session_id="other", turn_id="t1", assistant_response="Other session"
        )
        self.assertNotIn("finalResult", self.refresh.read_context("visual"))
        self.refresh.final_result(
            session_id="s1", turn_id="t1", assistant_response="Finding: " + "x" * 4000
        )
        self.assertEqual(len(self.refresh.read_context("visual")["finalResult"]), 3000)

    def test_normal_wakes_coalesce_latest_and_survive_process_reload(self):
        self.device()
        epoch = time.time()
        with patch.object(self.normal_wakes.time, "time", return_value=epoch):
            self.publish()
        for value in (2, 3):
            with patch.object(self.normal_wakes.time, "time", return_value=epoch + value):
                self.store.put_publication(
                    "visual", title=f"Changed {value}", summary="New evidence", text=str(value)
                )
        self.assertEqual(self.wake.call_count, 1)
        module = importlib.reload(self.normal_wakes)
        module.flush(now=epoch + 60)
        self.assertEqual(self.wake.call_count, 2)
        self.assertEqual(
            self.store.publication_status("visual")["delivery"][0]["nudgeStatus"], "sent"
        )

    def test_normal_quiet_hours_cap_and_missing_push_use_polling(self):
        self.device()
        self.store.set_widget_settings(
            "visual", {"start": "00:00", "end": "23:59", "timezone": "UTC"}
        )
        self.publish()
        self.wake.assert_not_called()
        self.store.set_widget_settings("visual", None)
        epoch = time.time()
        conn = self.store._connect()
        try:
            conn.executemany(
                "INSERT INTO widget_normal_wake_attempts VALUES (?,?)",
                [("visual", self.store._iso_from_epoch(epoch - 120 - i)) for i in range(30)],
            )
            conn.execute(
                "UPDATE widget_normal_wakes SET due_at=?", (self.store._iso_from_epoch(epoch),)
            )
            conn.commit()
        finally:
            conn.close()
        self.normal_wakes.flush(now=epoch)
        self.wake.assert_not_called()
        self.normal_wakes.flush(now=epoch + 3601)
        self.assertEqual(self.wake.call_count, 1)
        self.store.put_publication(
            "no-push", title="Polling", summary="No endpoint", text="fallback"
        )
        self.assertEqual(
            self.store.publication_status("no-push")["delivery"][0]["nudgeStatus"], "sent"
        )
        # Revoke the endpoint to exercise polling fallback, without discarding content.
        device = self.store.list_devices()[0]["deviceId"]
        self.store.set_device_push_endpoint(device, None)
        self.store.put_publication(
            "missing-push", title="Polling", summary="No endpoint", text="fallback"
        )
        self.assertEqual(
            self.store.publication_status("missing-push")["delivery"][0]["nudgeStatus"],
            "not_subscribed",
        )

    def test_preview_tool_returns_paths_or_supported_multimodal_envelope(self):
        args = {
            "publication": {"title": "Finding", "summary": "Evidence", "presentation": EXAMPLES[0]},
            "sizes": ["407x412"],
        }
        vision = types.ModuleType("tools.vision_tools")
        vision._should_use_native_vision_fast_path = lambda: False
        with patch.dict(sys.modules, {"tools.vision_tools": vision}):
            result = json.loads(self.tools.widget_preview(args))
        self.assertNotIn("data", result["previews"][0])
        self.assertTrue(Path(result["previews"][0]["path"]).is_file())
        vision._should_use_native_vision_fast_path = lambda: True
        with patch.dict(sys.modules, {"tools.vision_tools": vision}):
            result = self.tools.widget_preview(args)
        self.assertTrue(result["_multimodal"])
        self.assertEqual(result["content"][1]["type"], "image_url")
