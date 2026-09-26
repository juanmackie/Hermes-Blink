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

    def request(self, method, path, body=None, token=None, client=None):
        """`client={"version": ..., "build": ..., "sdk": ...}` sends the app's build headers."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if client is not None:
            version = client.get("version")
            build = client.get("build")
            if version is not None:
                headers["X-Hermes-App-Version"] = (
                    f"{version} ({build})" if build is not None else str(version)
                )
            if build is not None:
                headers["X-Hermes-App-Build"] = str(build)
            if client.get("sdk") is not None:
                headers["X-Hermes-Os-Sdk"] = str(client["sdk"])
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

    def test_legacy_layout_write_does_not_claim_device_publication(self):
        result = self.store.put_widget(
            "legacy-only",
            {
                "version": 2,
                "widgetId": "legacy-only",
                "updatedAt": "2026-09-16T07:30:00Z",
                "root": {"type": "column", "children": [{"type": "text", "value": "legacy"}]},
            },
        )
        self.assertEqual(result["scope"], "legacy_layout")
        self.assertFalse(result["publicationCreated"])
        self.assertEqual(result["layoutUpdatedAt"], "2026-09-16T07:30:00Z")
        self.assertNotEqual(result["storedAt"], result["layoutUpdatedAt"])
        self.assertEqual(result["warnings"][0]["code"], "legacy_layout_not_published")

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


class ClientBuildReporting(unittest.TestCase):
    """Which build the phone is running, reported on the poll and stored by the server.

    Without this, "the widget looks wrong" is unanswerable: the server sees a device, a
    revision and a render receipt, and nothing that says which APK produced them.

    These tests publish under their own widget id on purpose: `check_push_rate` keeps a
    process-wide 30-per-hour budget per widget, so adding more publishes to the default id
    would break unrelated tests in this suite rather than this one.
    """

    @classmethod
    def setUpClass(cls):
        _load_plugin()
        cls.store = importlib.import_module("hermes_plugins.hermes_widget.store")
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
        self._dir = Path(tempfile.mkdtemp(prefix="hermes-client-build-"))
        os.environ["HERMES_WIDGET_DIR"] = str(self._dir)
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        self.store.init_db()
        self.agent_token = self.store.get_agent_token()

    def request(self, method, path, body=None, token=None, client=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if client is not None:
            version, build = client.get("version"), client.get("build")
            if version is not None:
                headers["X-Hermes-App-Version"] = (
                    f"{version} ({build})" if build is not None else str(version)
                )
            if build is not None:
                headers["X-Hermes-App-Build"] = str(build)
            if client.get("sdk") is not None:
                headers["X-Hermes-Os-Sdk"] = str(client["sdk"])
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

    def pair_device(self, label="Pixel 10 Pro XL"):
        status, minted = self.request("POST", "/v1/pairing-codes", {}, self.agent_token)
        self.assertEqual(status, 200, minted)
        status, paired = self.request(
            "POST", "/v1/pair", {"code": minted["code"], "deviceLabel": label}
        )
        self.assertEqual(status, 200, paired)
        return paired

    def publish(self, **kwargs):
        return self.store.put_publication(
            "hermes-build-report",
            title=kwargs.get("title", "Status"),
            summary=kwargs.get("summary", "A short status"),
            text=kwargs.get("text", "All clear"),
        )

    def _client_row(self, device_id):
        return self.store.get_device_client_info(device_id)

    def test_the_poll_records_the_build_that_fetched_it(self):
        paired = self.pair_device()
        self.publish()
        status, fetched = self.request(
            "GET", "/v1/widgets/hermes-build-report/publication", None, paired["token"],
            client={"version": "0.2.0", "build": 200, "sdk": 35},
        )
        self.assertEqual(status, 200, fetched)

        row = self._client_row(paired["deviceId"])
        self.assertIsNotNone(row, "the poll must leave a build record")
        self.assertEqual(row["appVersion"], "0.2.0")
        self.assertEqual(row["appBuildCode"], 200)
        self.assertEqual(row["osSdk"], 35)
        self.assertIsNotNone(row["firstReportedAt"])

    def test_an_older_app_that_reports_nothing_still_works(self):
        paired = self.pair_device()
        self.publish()
        status, fetched = self.request(
            "GET", "/v1/widgets/hermes-build-report/publication", None, paired["token"],
        )
        self.assertEqual(status, 200, fetched)
        # No invented values, and nothing that breaks the fetch.
        self.assertIsNone(self._client_row(paired["deviceId"]))
        status_body = self.store.publication_status("hermes-build-report")
        delivery = status_body["delivery"][0]
        self.assertIsNone(delivery["client"])
        self.assertIsNone(delivery["renderedBy"])

    def test_a_malformed_build_never_breaks_the_poll(self):
        paired = self.pair_device()
        self.publish()
        for headers in (
            {"X-Hermes-App-Version": "a" * 200, "X-Hermes-App-Build": "99999999999999"},
            {"X-Hermes-App-Version": "0.2.0", "X-Hermes-App-Build": "not-a-number"},
            {"X-Hermes-App-Build": "-1"},
        ):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                connection.request(
                    "GET", "/v1/widgets/hermes-build-report/publication",
                    headers={"Authorization": f"Bearer {paired['token']}", **headers},
                )
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200, f"{headers} must not fail the fetch")
            finally:
                connection.close()
        row = self._client_row(paired["deviceId"])
        # Partial data is still data: the valid version is kept, the junk build is not,
        # and nothing is defaulted or guessed.
        self.assertEqual(row["appVersion"], "0.2.0")
        self.assertIsNone(row["appBuildCode"])

    def test_an_unchanged_build_does_not_rewrite_the_row(self):
        paired = self.pair_device()
        self.publish()
        self.request(
            "GET", "/v1/widgets/hermes-build-report/publication", None, paired["token"],
            client={"version": "0.2.0", "build": 200, "sdk": 35},
        )
        first = self._client_row(paired["deviceId"])["updatedAt"]
        self.request(
            "GET", "/v1/widgets/hermes-build-report/publication", None, paired["token"],
            client={"version": "0.2.0", "build": 200, "sdk": 35},
        )
        self.assertEqual(self._client_row(paired["deviceId"])["updatedAt"], first)

        # A real upgrade does move it, and keeps the original first-seen time.
        self.request(
            "GET", "/v1/widgets/hermes-build-report/publication", None, paired["token"],
            client={"version": "0.3.0", "build": 300, "sdk": 35},
        )
        upgraded = self._client_row(paired["deviceId"])
        self.assertEqual(upgraded["appVersion"], "0.3.0")
        self.assertEqual(upgraded["appBuildCode"], 300)
        self.assertEqual(upgraded["firstReportedAt"], first)

    def test_the_device_patch_accepts_a_structured_client_block(self):
        paired = self.pair_device()
        status, result = self.request(
            "PATCH", "/v1/device", {"client": {"appVersion": "0.2.0", "appBuildCode": 200, "osSdk": 35}},
            paired["token"],
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result["client"]["appVersion"], "0.2.0")
        self.assertEqual(result["client"]["appBuildCode"], 200)
        row = self._client_row(paired["deviceId"])
        self.assertEqual(row["appBuildCode"], 200)

    def test_the_render_receipt_names_the_build_that_drew_the_revision(self):
        paired = self.pair_device()
        published = self.publish()
        revision = published["revision"]
        self.request(
            "GET", "/v1/widgets/hermes-build-report/publication", None, paired["token"],
            client={"version": "0.2.0", "build": 200, "sdk": 35},
        )
        status, acked = self.request(
            "POST", "/v1/widgets/hermes-build-report/publication/ack",
            {
                "revision": revision,
                "status": "render_submitted",
                "renderedWidth": 1221,
                "renderedHeight": 1236,
                "clientVersion": "0.2.0",
                "clientBuildCode": 200,
            },
            paired["token"],
        )
        self.assertEqual(status, 200, acked)
        self.assertEqual(acked["renderedBy"]["appVersion"], "0.2.0")
        self.assertEqual(acked["renderedBy"]["appBuildCode"], 200)

        delivery = self.store.publication_status("hermes-build-report")["delivery"][0]
        self.assertEqual(delivery["renderedBy"]["appBuildCode"], 200)
        self.assertEqual(delivery["client"]["appBuildCode"], 200)
        # The render dimensions and the build travel together, which is the point.
        self.assertEqual(delivery["renderedWidth"], 1221)
        self.assertEqual(delivery["renderedHeight"], 1236)

    def test_list_devices_surfaces_the_build(self):
        paired = self.pair_device()
        self.publish()
        self.request(
            "GET", "/v1/widgets/hermes-build-report/publication", None, paired["token"],
            client={"version": "0.2.0", "build": 200, "sdk": 35},
        )
        devices = self.store.list_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["appVersion"], "0.2.0")
        self.assertEqual(devices[0]["appBuildCode"], 200)
        self.assertEqual(devices[0]["osSdk"], 35)
        self.assertIsNotNone(devices[0]["clientReportedAt"])

    def test_a_revoked_device_gets_no_client_row(self):
        paired = self.pair_device()
        self.publish()
        self.request(
            "GET", "/v1/widgets/hermes-build-report/publication", None, paired["token"],
            client={"version": "0.2.0", "build": 200, "sdk": 35},
        )
        self.assertIsNotNone(self._client_row(paired["deviceId"]))
        self.store.revoke_device(paired["deviceId"])
        self.assertIsNone(
            self.store.record_device_client(paired["deviceId"], "9.9.9", 999, 35)
        )

    def test_junk_values_are_ignored_and_never_erase_what_we_know(self):
        paired = self.pair_device()
        device_id = paired["deviceId"]
        self.assertIsNotNone(self.store.record_device_client(device_id, "0.2.0", 200, 35))
        known = self._client_row(device_id)

        # Nothing here is storable, and none of it may raise: this rides the poll.
        for junk in (
            ("x" * 64, -5, 9_999),
            (True, "1.5", None),
            (None, None, None),
            ("", "", 0),
        ):
            self.store.record_device_client(device_id, *junk)
            self.assertEqual(self._client_row(device_id), known)

        # A real upgrade still lands.
        self.store.record_device_client(device_id, "0.3.0", 300, 35)
        self.assertEqual(self._client_row(device_id)["appBuildCode"], 300)
