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
import time
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
        cls.push = importlib.import_module("hermes_plugins.hermes_widget.push")
        cls.push_state = importlib.import_module("hermes_plugins.hermes_widget.push_state")
        cls.publications = importlib.import_module("hermes_plugins.hermes_widget.publications")
        cls.watches = importlib.import_module("hermes_plugins.hermes_widget.watches")
        cls.proactive = importlib.import_module("hermes_plugins.hermes_widget.proactive")
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
                resolved = self.cli._resolve_input_file("README.md", label="documentation file")
        self.assertTrue(resolved.is_file())
        self.assertEqual(resolved.name, "README.md")

    def test_push_state_and_wake_test_are_visible_without_a_publication(self):
        state = self.store.set_device_push_state(
            self.device["deviceId"], "failed", distributor_present=True, failure_reason="AUTH_FAILED"
        )
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["failureReason"], "AUTH_FAILED")
        self.store.ensure_widget("feature")
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
        history = self.store.publication_history("regions")
        self.assertEqual(len(history), 2, "ticker publication must create one revision")
        hero_update = self.store.put_publication(
            "regions", title="Hero v2", summary="Hero summary", text="updated hero"
        )
        self.assertEqual(hero_update["regions"]["ticker"]["summary"], "countdown")

    def test_ticker_change_invalidates_conditional_publication_fetch(self):
        self.store.put_publication("ticker-etag", title="Hero", summary="Main", text="body")

        def fetch(etag=None):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            headers = {"Authorization": f"Bearer {self.device['token']}"}
            if etag:
                headers["If-None-Match"] = etag
            try:
                connection.request("GET", "/v1/widgets/ticker-etag/publication", headers=headers)
                response = connection.getresponse()
                body = response.read()
                return response.status, dict(response.getheaders()), body
            finally:
                connection.close()

        first_status, first_headers, _ = fetch()
        self.assertEqual(first_status, 200)
        first_etag = first_headers["ETag"]

        self.store.put_ticker(
            "ticker-etag", title="Countdown", summary="Two minutes", text="02:00"
        )
        changed_status, changed_headers, changed_body = fetch(first_etag)
        self.assertEqual(changed_status, 200)
        self.assertNotEqual(changed_headers["ETag"], first_etag)
        changed = json.loads(changed_body.decode("utf-8"))
        self.assertEqual(changed["ticker"]["summary"], "Two minutes")

        unchanged_status, _, _ = fetch(changed_headers["ETag"])
        self.assertEqual(unchanged_status, 304)

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
        # Which text backend runs depends on whether CairoSVG is importable (the full-suite
        # CI job installs the optional preview dependencies; a minimal host does not). The contract is "a real text
        # renderer", not a specific one â€” see the suppressed tests below for the
        # deterministic path.
        self.assertIn(rendered[0]["renderer"], {"pillow-composition"})

    def test_request_update_pokes_the_existing_refresh_routine(self):
        self.store.put_publication("poke", title="A", summary="S", text="body")
        with patch.object(self.server_module, "proactive") as host:
            host.trigger_refresh.return_value = {"triggered": True, "pid": 123}
            status, body = self.request(
                "POST", "/v1/widgets/poke/events",
                {"event": "request_update", "clientEventId": "poke-1"},
                self.device["token"],
            )
        self.assertEqual(status, 200, body)
        host.trigger_refresh.assert_called_once_with()
        self.assertEqual(body["request"]["status"], "triggered")
        self.assertEqual(self.store.publication_status("poke")["updateRequests"][0]["status"], "triggered")

    def test_request_update_is_single_flight_and_rate_limited_per_device(self):
        first = self.store.request_update("single-flight", self.device["deviceId"], "first")
        again = self.store.request_update("single-flight", self.device["deviceId"], "second")
        self.assertTrue(first["_shouldTrigger"])
        self.assertFalse(again["_shouldTrigger"])
        self.assertEqual(again["requestId"], first["requestId"])

        for index in range(self.store.UPDATE_REQUEST_MAX_PER_DEVICE - 1):
            request = self.store.request_update(
                "rate-limit", self.device["deviceId"], f"unique-{index}"
            )
            self.store.mark_update_request_triggered(request["requestId"])
        with self.assertRaises(self.store.RateLimitError):
            self.store.request_update(
                "rate-limit", self.device["deviceId"], "one-too-many"
            )

    def test_status_consumes_only_triggered_update_requests_on_explicit_request(self):
        self.store.put_publication("consume", title="A", summary="S", text="body")
        request = self.store.request_update("consume", self.device["deviceId"], "consume-1")
        self.store.mark_update_request_triggered(request["requestId"])
        before = json.loads(self.tools.widget_status({"widget_id": "consume"}))
        self.assertEqual(before["newlyConsumedUpdateRequests"], [])
        self.assertEqual(before["updateRequests"][0]["status"], "triggered")

        consumed = json.loads(self.tools.widget_status({
            "widget_id": "consume", "consume_update_requests": True,
        }))
        self.assertEqual(consumed["newlyConsumedUpdateRequests"][0]["requestId"], request["requestId"])
        self.assertEqual(consumed["updateRequests"][0]["status"], "consumed")

    def test_status_limits_recent_records_and_reports_truncation(self):
        for index in range(4):
            self.store.put_publication(
                "bounded-status", title=f"Revision {index}", summary="S", text=f"body {index}"
            )
        full = self.store.publication_status("bounded-status", limit=2)
        self.assertEqual([row["revision"] for row in full["revisions"]], [4, 3])
        self.assertEqual(full["resultLimits"]["totals"]["revisions"], 4)
        self.assertTrue(full["resultLimits"]["truncated"]["revisions"])

        compact = json.loads(self.tools.widget_status({
            "widget_id": "bounded-status", "summary": True, "limit": 100,
        }))
        self.assertEqual(compact["resultLimits"]["limit"], 10)

    def test_refresh_process_single_flight_reuses_running_cron(self):
        # Isolate the process fixture from previous HTTP trigger fixtures.
        self.proactive._REFRESH_PROCESS = None
        release = threading.Event()
        reaped = threading.Event()

        class FakeProcess:
            pid = 4242

            def poll(self):
                return None if not release.is_set() else 0

            def wait(self):
                release.wait()
                reaped.set()
                return 0

        try:
            with (
                patch.dict(os.environ, {"HERMES_BIN": "hermes-test"}),
                patch.object(self.proactive.subprocess, "Popen", return_value=FakeProcess()) as popen,
                patch.object(self.proactive.store, "has_unconsumed_update_requests", return_value=False),
            ):
                first = self.proactive.trigger_refresh()
                second = self.proactive.trigger_refresh()
                self.assertTrue(first["triggered"])
                self.assertTrue(second["duplicate"])
                self.assertEqual(second["pid"], 4242)
                popen.assert_called_once()
        finally:
            release.set()
            self.assertTrue(reaped.wait(timeout=2))
            deadline = time.monotonic() + 2
            while self.proactive._REFRESH_PROCESS is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(self.proactive._REFRESH_PROCESS)

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

    def test_quiet_hours_use_timezone_for_watches_and_settings(self):
        from zoneinfo import ZoneInfo

        timeutil = importlib.import_module("hermes_plugins.hermes_widget.timeutil")
        now = datetime(2026, 9, 30, 10, 30, tzinfo=timezone.utc)
        self.assertTrue(timeutil._in_quiet_window("20:00", "21:00", "Australia/Brisbane", now))
        self.assertFalse(timeutil._in_quiet_window("20:00", "21:00", "UTC", now))
        self.assertTrue(timeutil._in_quiet_window("20:00", "06:00", "Australia/Brisbane", now))
        with self.assertRaises(self.store.StoreError):
            self.store.set_widget_settings("quiet-invalid", {
                "start": "20:00", "end": "21:00", "timezone": "Mars/Olympus",
            })

        configured = self.store.set_widget_settings("quiet-settings", {
            "start": "20:00", "end": "21:00", "timezone": "Australia/Brisbane",
        })
        self.assertEqual(configured["quietHours"]["timezone"], "Australia/Brisbane")
        local_now = datetime.now(timezone.utc).astimezone(ZoneInfo("Australia/Brisbane"))
        priority_window = self.store.set_widget_settings("priority-timezone", {
            "start": local_now.strftime("%H:%M"),
            "end": (local_now + timedelta(minutes=3)).strftime("%H:%M"),
            "timezone": "Australia/Brisbane",
        })
        self.assertEqual(priority_window["quietHours"]["timezone"], "Australia/Brisbane")
        publication = self.store.put_publication(
            "priority-timezone", title="Quiet", summary="Quiet", text="body", priority="high",
        )
        self.assertEqual(publication["priorityDegradedReason"], "quiet_hours")

        check_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        local_minute = check_at.astimezone(ZoneInfo("Australia/Brisbane")).strftime("%H:%M")
        local_end = (check_at.astimezone(ZoneInfo("Australia/Brisbane")) + timedelta(minutes=1)).strftime("%H:%M")
        watch = self.watches.create_watch(
            "watch-timezone", name="quiet test", condition={"type": "always"},
            payload={"title": "Quiet", "summary": "Quiet", "text": "body"},
            cadence_seconds=60,
            quiet_hours={"start": local_minute, "end": local_end, "timezone": "Australia/Brisbane"},
        )
        self.assertEqual(watch["quietHours"]["timezone"], "Australia/Brisbane")
        self.assertEqual(
            self.watches.tick_watches(now=check_at),
            [{"watchId": watch["watchId"], "state": "deferred", "reason": "quiet_hours"}],
        )

    def test_server_ticker_fires_date_watches_and_leaves_source_watches_to_agent(self):
        server_module = importlib.import_module("hermes_plugins.hermes_widget.server")
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        date_watch = self.watches.create_watch(
            "server-date-watch", name="deadline", condition={
                "type": "date_reached", "at": (now - timedelta(seconds=1)).isoformat(),
            },
            payload={"title": "Deadline", "summary": "Reached", "text": "ready"},
            cadence_seconds=3600,
        )
        source_watch = self.watches.create_watch(
            "agent-source-watch", name="source", condition={
                "type": "source_equals", "source": "ready", "equals": True,
            },
            payload={"title": "Source", "summary": "Ready", "text": "ready"},
            cadence_seconds=3600,
        )
        results = self.watches.tick_watches(condition_types={"date_reached"}, now=now)
        self.assertEqual([row["watchId"] for row in results], [date_watch["watchId"]])
        self.assertEqual(self.watches.get_watch(date_watch["watchId"])["lastState"], True)
        self.assertIsNone(self.watches.get_watch(source_watch["watchId"])["lastState"])

        concurrent_watch = self.watches.create_watch(
            "concurrent-date-watch", name="single fire", condition={
                "type": "date_reached", "at": (now - timedelta(seconds=1)).isoformat(),
            },
            payload={"title": "Once", "summary": "Once", "text": "body"},
            cadence_seconds=3600,
        )
        start = threading.Barrier(2)
        concurrent_results = []

        def tick_concurrently():
            start.wait()
            concurrent_results.append(
                self.watches.tick_watches(condition_types={"date_reached"}, now=now)
            )

        with patch.object(self.store, "put_publication", wraps=self.store.put_publication) as publish:
            workers = [threading.Thread(target=tick_concurrently) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=5)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(
            [item["watchId"] for result in concurrent_results for item in result],
            [concurrent_watch["watchId"]],
        )

        stop_event = threading.Event()
        with patch.object(server_module, "WATCH_TICK_INTERVAL_SECONDS", 0):
            with patch.object(self.watches, "tick_watches", side_effect=lambda **_kwargs: stop_event.set() or [] ) as tick:
                server_module._date_watch_loop(stop_event)
        tick.assert_called_once_with(condition_types={"date_reached"})

        class StoppedServer:
            server_address = ("127.0.0.1", 8788)
            closed = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        stopped_server = StoppedServer()
        ticker_started = threading.Event()

        def run_ticker(stop):
            ticker_started.set()
            stop.wait()

        with patch.object(server_module.retention, "start_retention_job"):
            with patch.object(server_module, "make_server", return_value=stopped_server):
                with patch.object(server_module, "_date_watch_loop", side_effect=run_ticker):
                    server_module.run_server("127.0.0.1", 8788, quiet=True)
        self.assertTrue(ticker_started.is_set())
        self.assertTrue(stopped_server.closed)

    def test_watch_publish_failure_does_not_consume_transition_or_daily_budget(self):
        watch = self.watches.create_watch(
            "watch-retry", name="retry",
            condition={"type": "source_equals", "source": "ready", "equals": True},
            payload={"title": "Retry", "summary": "Ready", "text": "ready"},
            cadence_seconds=60,
        )
        base = datetime.now(timezone.utc)
        with patch.object(self.store, "put_publication", side_effect=self.store.PublicationError("offline")):
            failed = self.watches.tick_watches(sources={"ready": True}, now=base)
        self.assertEqual(failed[0]["state"], "error")
        state = self.watches.get_watch(watch["watchId"])
        self.assertIsNone(state["lastFiredAt"])
        self.assertIsNone(state["lastState"])

        retried = self.watches.tick_watches(
            sources={"ready": True}, now=base + timedelta(seconds=61)
        )
        self.assertEqual(retried[0]["state"], "published")

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

    def test_transient_priority_wake_failure_retries_with_backoff(self):
        self.store.set_device_push_endpoint(self.device["deviceId"], "https://ntfy.example/up/device")
        with patch.object(
            self.store._push, "wake", side_effect=[self.store._push.PushError("offline"), None]
        ) as wake:
            publication = self.store.put_publication(
                "wake-retry", title="Retry", summary="Retry", text="body", priority="high",
            )
            conn = self.store._connect()
            try:
                pending = conn.execute(
                    "SELECT status, attempt_count, next_attempt_at FROM publication_nudges "
                    "WHERE widget_id=? AND revision=? AND device_id=?",
                    ("wake-retry", publication["revision"], self.device["deviceId"]),
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(pending["status"], "failed")
            self.assertEqual(pending["attempt_count"], 1)
            self.assertIsNotNone(pending["next_attempt_at"])
            self.assertEqual(
                self.store.retry_failed_nudges(now=datetime.now(timezone.utc) + timedelta(minutes=2)),
                1,
            )
        self.assertEqual(wake.call_count, 2)
        delivery = self.store.publication_status("wake-retry")["delivery"][0]
        self.assertEqual(delivery["nudgeStatus"], "sent")
        conn = self.store._connect()
        try:
            final = conn.execute(
                "SELECT attempt_count, next_attempt_at, claim_until FROM publication_nudges "
                "WHERE widget_id=? AND revision=? AND device_id=?",
                ("wake-retry", publication["revision"], self.device["deviceId"]),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(final["attempt_count"], 2)
        self.assertIsNone(final["next_attempt_at"])
        self.assertIsNone(final["claim_until"])

    def test_push_wake_rejects_private_dns_results(self):
        private_address = (2, 1, 6, "", ("127.0.0.1", 443))
        with patch.object(self.push.socket, "getaddrinfo", return_value=[private_address]):
            with self.assertRaises(self.push.PushError):
                self.push.wake("https://push.example/device")

    def test_priority_over_limit_degrades_visibly(self):
        with patch.object(self.publications, "HIGH_PRIORITY_MAX_PER_HOUR", 1):
            first = self.store.put_publication("limit", title="1", summary="s", text="a", priority="high")
            second = self.store.put_publication("limit", title="2", summary="s", text="b", priority="high")
        self.assertEqual(first["priority"], "high")
        self.assertEqual(second["priority"], "normal")
        self.assertEqual(second["requestedPriority"], "high")
        self.assertEqual(second["priorityDegradedReason"], "high_priority_hour_limit")

    def test_push_rate_limit_is_persisted_and_shared_by_database_connections(self):
        with patch.object(self.push_state, "PUSH_MAX_PER_WINDOW", 2):
            with patch.object(self.push_state, "_epoch_now", side_effect=[10_000, 10_001, 10_002]):
                self.store.check_push_rate("persisted-rate")
                self.store.check_push_rate("persisted-rate")
                with self.assertRaises(self.store.RateLimitError):
                    self.store.check_push_rate("persisted-rate")
        conn = self.store._connect()
        try:
            rows = conn.execute(
                "SELECT occurred_at FROM push_rate_limits WHERE widget_id = ? ORDER BY occurred_at",
                ("persisted-rate",),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([row["occurred_at"] for row in rows], [10_000, 10_001])

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
        self.assertEqual(result["previews"][0]["renderer"], "pillow-composition")

    def test_svg_preview_reports_missing_local_renderer(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
        publication = {
            "kind": "image", "title": "Chart", "summary": "Chart",
            "content": {"type": "image", "mediaType": "image/svg+xml", "data": __import__("base64").b64encode(svg).decode()},
        }
        with patch.dict(sys.modules, {"cairosvg": None}):
            rendered = self.preview.render_publication_previews(publication, sizes=["400x400"])
        self.assertEqual(rendered[0]["renderer"], "svg-renderer-unavailable")
        self.assertIn("No local SVG renderer", rendered[0]["note"])

    def test_text_preview_keeps_a_usable_path_without_cairo(self):
        with patch.dict(sys.modules, {"cairosvg": None}):
            previews = self.preview.render_publication_previews(
                {"kind": "text", "title": "Title", "summary": "Summary", "content": {"text": "Body"}}, sizes=["400x400"]
            )
            data = __import__("base64").b64decode(previews[0]["data"])
        self.assertEqual(previews[0]["renderer"], "pillow-composition")
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
        resolved = self.store.resolve_intent(intent_id, "applied", result="validated")
        self.assertEqual(resolved["status"], "applied")
        self.assertEqual(len(self.store.get_events(widget_id="actions")), 1)

    def test_sensitive_action_requires_paired_device_confirmation(self):
        self.store.put_publication(
            "sensitive-actions", title="Sensitive", summary="S", text="confirm first",
            actions=[{
                "kind": "approve", "itemId": "pay-1", "actionClass": "destructive",
                "confirmOnDevice": True,
            }],
        )
        created = self.store.post_action_event(
            "sensitive-actions", self.device["deviceId"], "approve",
            {"itemId": "pay-1"}, revision=1,
        )
        intent_id = created["intent"]["intentId"]
        self.assertEqual(created["intent"]["status"], "awaiting_confirmation")
        with self.assertRaisesRegex(self.store.ActionIntentError, "actionClass does not match"):
            self.store.post_action_event(
                "sensitive-actions", self.device["deviceId"], "approve",
                {"itemId": "pay-1"}, revision=1, action_class="reversible",
            )
        with self.assertRaisesRegex(self.store.ActionIntentError, "paired device"):
            self.store.resolve_intent(intent_id, "applied")
        with self.assertRaisesRegex(self.store.ActionIntentError, "different device"):
            self.store.confirm_action_intent(intent_id, "other-device")
        confirmed = self.store.confirm_action_intent(intent_id, self.device["deviceId"])
        self.assertEqual(confirmed["status"], "queued")
        self.assertTrue(confirmed["payload"]["deviceConfirmed"])
        resolved = self.store.resolve_intent(intent_id, "applied", result="validated")
        self.assertEqual(resolved["status"], "applied")

    def test_revoked_device_cannot_report_or_act(self):
        self.store.revoke_device(self.device["deviceId"])
        with self.assertRaises(self.store.StoreError):
            self.store.report_widget_instances(self.device["deviceId"], "x", [])
        with self.assertRaises(self.store.ActionIntentError):
            self.store.post_action_event("x", self.device["deviceId"], "open", {"itemId": "x"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
