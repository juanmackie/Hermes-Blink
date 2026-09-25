"""Publication, asset, and authenticated delivery contract tests."""
from __future__ import annotations

import http.client
import importlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

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


class PublicationContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _load_plugin()
        cls.store = importlib.import_module("hermes_plugins.hermes_widget.store")
        cls.publication = importlib.import_module("hermes_plugins.hermes_widget.publication")
        cls.tools = importlib.import_module("hermes_plugins.hermes_widget.tools")
        cls.schemas = importlib.import_module("hermes_plugins.hermes_widget.schemas")
        cls.proactive = importlib.import_module("hermes_plugins.hermes_widget.proactive")
        cls.gateway_hook = importlib.import_module("hermes_plugins.hermes_widget.gateway_hook")
        cls.server_module = importlib.import_module("hermes_plugins.hermes_widget.server")
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="hermes-publication-test-"))
        os.environ["HERMES_WIDGET_DIR"] = str(cls.temp_dir)
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
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        self.agent_token = self.store.get_agent_token()
        self.assertTrue(self.agent_token)

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], Any, bytes]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=data, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            content_type = response.getheader("Content-Type", "")
            payload = json.loads(raw.decode("utf-8")) if "json" in content_type and raw else None
            return response.status, dict(response.getheaders()), payload, raw
        finally:
            connection.close()

    def pair_device(self, label="Pixel"):
        status, _, minted, _ = self.request("POST", "/v1/pairing-codes", {}, self.agent_token)
        self.assertEqual(status, 200, minted)
        status, _, paired, _ = self.request(
            "POST", "/v1/pair", {"code": minted["code"], "deviceLabel": label}
        )
        self.assertEqual(status, 200, paired)
        return paired["token"]

    def test_publish_tool_schema_and_handler_are_registered(self):
        self.assertEqual(self.schemas.WIDGET_PUBLISH["name"], "widget_publish")
        self.assertEqual(
            self.schemas.WIDGET_PUBLISH["parameters"]["required"],
            ["title", "summary"],
        )
        self.assertIn(
            "widget_publish",
            [schema["name"] for schema, _handler in _load_plugin()._TOOLS],
        )
        reminder = _load_plugin()._availability_reminder(user_message="ordinary task")
        self.assertIn("widget_publish", reminder["context"])
        self.assertIn("does not prove", reminder["context"])
        self.assertTrue(callable(self.tools.widget_publish))

    def test_windows_gateway_hook_uses_a_short_lived_launcher(self):
        calls = []

        class FakePopen:
            def __init__(self, argv, **kwargs):
                self.argv = argv
                self.kwargs = kwargs
                self.pid = 4321
                calls.append(self)
                if "-c" in argv:
                    pid_path = Path(argv[3])
                    pid_path.parent.mkdir(parents=True, exist_ok=True)
                    pid_path.write_text("1234", encoding="ascii")

            def poll(self):
                return 0

        with tempfile.TemporaryDirectory(prefix="hermes-launcher-test-") as temp, patch.object(
            self.gateway_hook.os, "name", "nt"
        ), patch.object(self.gateway_hook.subprocess, "Popen", FakePopen):
            process, server_pid = self.gateway_hook._spawn_server(
                "fake-hermes", "127.0.0.1", 8788, Path(temp), io.BytesIO()
            )

        self.assertEqual(process.pid, 4321)
        self.assertEqual(server_pid, 1234)
        self.assertEqual(len(calls), 1)
        self.assertIn("-c", calls[0].argv)
        self.assertIn("widget", calls[0].argv)
        self.assertIn("serve", calls[0].argv)
        self.assertIn("creationflags", calls[0].kwargs)

    def test_startup_hook_and_server_config_are_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="hermes-install-test-") as temp:
            home = Path(temp)
            with patch.object(self.proactive, "_hermes_home", return_value=home), patch.dict(
                os.environ, {"HERMES_BIN": "fake-hermes"}, clear=False
            ):
                first_hook = self.proactive.install_startup_hook()
                first_config = self.proactive.write_server_config("127.0.0.1", 8788)
                first_bytes = {
                    path: path.read_bytes()
                    for path in (first_hook / "HOOK.yaml", first_hook / "handler.py", first_config)
                }
                second_hook = self.proactive.install_startup_hook()
                second_config = self.proactive.write_server_config("127.0.0.1", 8788)
                self.assertEqual(first_hook, second_hook)
                self.assertEqual(first_config, second_config)
                self.assertEqual(
                    first_bytes,
                    {
                        path: path.read_bytes()
                        for path in (second_hook / "HOOK.yaml", second_hook / "handler.py", second_config)
                    },
                )
                self.assertEqual(
                    json.loads(second_config.read_text(encoding="utf-8")),
                    {
                        "hermesBin": "fake-hermes",
                        "home": str(home),
                        "host": "127.0.0.1",
                        "port": 8788,
                    },
                )
                self.assertEqual(list(home.rglob("*.tmp")), [])

    def test_routine_prompt_uses_publish_and_can_noop(self):
        prompt = self.proactive.routine_prompt("hermes-brief")
        self.assertIn("widget_publish", prompt)
        self.assertIn("do nothing when nothing useful changed", prompt)
        self.assertNotIn("widget_update", prompt)
        self.assertIn("not that the phone rendered", prompt)

    def test_routine_update_removes_duplicate_jobs(self):
        class FakeJobs:
            def __init__(self):
                self.records = [
                    {"id": "first", "name": "hermes-widget-refresh", "prompt": "old", "schedule": "every 1h"},
                    {"id": "duplicate", "name": "hermes-widget-refresh", "prompt": "duplicate"},
                ]
                self.created = []

            def list_jobs(self):
                return [dict(record) for record in self.records]

            def update_job(self, job_id, updates):
                for record in self.records:
                    if record["id"] == job_id:
                        record.update(updates)
                        return dict(record)
                return None

            def create_job(self, **kwargs):
                self.created.append(kwargs)
                return {"id": "created", **kwargs}

            def remove_job(self, job_id):
                before = len(self.records)
                self.records = [record for record in self.records if record["id"] != job_id]
                return before != len(self.records)

        fake = FakeJobs()
        with (
            tempfile.TemporaryDirectory(prefix="hermes-routine-test-") as temp,
            patch.object(self.proactive, "_hermes_home", return_value=Path(temp)),
            patch.object(self.proactive, "_cron_jobs", return_value=fake),
        ):
            result = self.proactive.install_routine()
        self.assertEqual(result["prompt"], self.proactive.routine_prompt())
        self.assertEqual([record["id"] for record in fake.records], ["first"])
        self.assertFalse(fake.created)

    def test_text_publish_is_idempotent_and_status_is_honest(self):
        first = json.loads(
            self.tools.widget_publish(
                {"title": "Status", "summary": "A short status", "text": "All clear"}
            )
        )
        self.assertTrue(first["ok"])
        self.assertGreaterEqual(first["revision"], 1)
        self.assertEqual(first["visibility"], "not_claimed")

        second = json.loads(
            self.tools.widget_publish(
                {"title": "Status", "summary": "A short status", "text": "All clear"}
            )
        )
        self.assertTrue(second["unchanged"])
        self.assertEqual(second["revision"], first["revision"])

        status = json.loads(self.tools.widget_status())
        self.assertEqual(status["publicationState"], "published")
        self.assertEqual(status["deliveryState"], "not_downloaded")
        self.assertNotIn("userSeen", status)
        self.assertNotIn("visibility", status)

    def test_svg_subset_and_rejections(self):
        valid = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120">'
            '<title>Chart</title><path d="M0 100 L240 20" stroke="#000" fill="none"/>'
            "</svg>"
        )
        result = self.store.put_publication(
            "hermes-brief", title="Chart", summary="A rising line", svg=valid
        )
        self.assertEqual(result["content"]["mediaType"], "image/svg+xml")
        self.assertEqual(result["content"]["width"], 240)

        bad_svgs = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>alert(1)</script></svg>',
            '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">&xxe;</svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><image href="https://example.invalid/a.png"/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><foreignObject><b>x</b></foreignObject></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><animate attributeName="x"/></svg>',
        ]
        for bad in bad_svgs:
            with self.subTest(bad=bad[:60]), self.assertRaises(self.store.PublicationError):
                self.store.put_publication(
                    "hermes-brief", title="Bad", summary="Unsafe", svg=bad
                )

    def test_raster_validation_uses_magic_dimensions_and_local_file(self):
        def chunk(kind: bytes, data: bytes) -> bytes:
            checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
            return len(data).to_bytes(4, "big") + kind + data + checksum.to_bytes(4, "big")

        png = b"\x89PNG\r\n\x1a\n" + chunk(
            b"IHDR",
            (320).to_bytes(4, "big")
            + (180).to_bytes(4, "big")
            + b"\x08\x06\x00\x00\x00",
        ) + chunk(b"IEND", b"")
        path = self.temp_dir / "photo.png"
        path.write_bytes(png)
        result = self.store.put_publication(
            "hermes-brief", title="Photo", summary="A small raster", file_path=str(path)
        )
        self.assertEqual(result["content"]["mediaType"], "image/png")
        self.assertEqual(result["content"]["width"], 320)
        self.assertEqual(result["content"]["height"], 180)
        jpeg = (
            b"\xff\xd8\xff\xc0"
            + (17).to_bytes(2, "big")
            + b"\x08"
            + (16).to_bytes(2, "big")
            + (32).to_bytes(2, "big")
            + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
            + b"\xff\xd9"
        )
        jpeg_result = self.publication.validate_raster(jpeg, ".jpg")
        self.assertEqual(jpeg_result, ("image/jpeg", 32, 16))
        # Pixel cameras append an MP4 to the JPEG (Motion Photo), so the file does not
        # end with EOI. Requiring that rejected valid photos as "incomplete".
        motion_photo = jpeg + b"\x00\x00\x00\x18ftypmp42" + bytes(64)
        self.assertEqual(
            self.publication.validate_raster(motion_photo, ".jpg"),
            ("image/jpeg", 32, 16),
        )
        with self.assertRaises(self.publication.PublicationInputError):
            self.publication.validate_raster(b"\xff\xd8not-a-jpeg", ".jpg")
        webp_payload = bytearray(10)
        webp_payload[4:7] = (31).to_bytes(3, "little")
        webp_payload[7:10] = (15).to_bytes(3, "little")
        webp_chunk = b"VP8X" + (10).to_bytes(4, "little") + bytes(webp_payload)
        webp = b"RIFF" + (4 + len(webp_chunk)).to_bytes(4, "little") + b"WEBP" + webp_chunk
        self.assertEqual(
            self.publication.validate_raster(webp, ".webp"),
            ("image/webp", 32, 16),
        )
        with self.assertRaises(self.publication.PublicationInputError):
            self.publication.validate_raster(png[:-1], ".png")
        with self.assertRaises(self.store.PublicationError):
            self.store.put_publication(
                "hermes-brief", title="Remote", summary="Not local", file_path="https://example.invalid/a.png"
            )
        with self.assertRaises(self.store.PublicationError):
            self.store.put_publication(
                "hermes-brief", title="Bad", summary="Wrong type", file_path=str(path), text="also text"
            )

    def test_authenticated_conditional_fetch_asset_and_render_ack(self):
        device = self.pair_device()
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="blue"/></svg>'
        status, _, published, _ = self.request(
            "POST",
            "/v1/widgets/hermes-brief/publication",
            {"title": "Blue", "summary": "A blue rectangle", "svg": svg},
            self.agent_token,
        )
        self.assertEqual(status, 200, published)
        asset_id = published["content"]["assetId"]

        status, headers, fetched, _ = self.request(
            "GET", "/v1/widgets/hermes-brief/publication", token=device
        )
        self.assertEqual(status, 200, fetched)
        etag = headers["ETag"]
        self.assertEqual(
            self.request(
                "GET",
                "/v1/widgets/hermes-brief/publication",
                token=device,
                headers={"If-None-Match": etag},
            )[0],
            304,
        )

        asset_status, asset_headers, _, asset_bytes = self.request(
            "GET", f"/v1/assets/{asset_id}", token=device
        )
        self.assertEqual(asset_status, 200)
        self.assertEqual(asset_headers["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", asset_bytes)
        self.assertEqual(
            self.request(
                "GET",
                f"/v1/assets/{asset_id}",
                token=device,
                headers={"If-None-Match": asset_headers["ETag"]},
            )[0],
            304,
        )

        ack_status, _, ack, _ = self.request(
            "POST",
            "/v1/widgets/hermes-brief/publication/ack",
            {
                "revision": published["revision"],
                "status": "render_submitted",
                "renderedWidth": 100,
                "renderedHeight": 50,
            },
            device,
        )
        self.assertEqual(ack_status, 200, ack)
        self.assertEqual(ack["status"], "render_submitted")

        status_payload = json.loads(self.tools.widget_status())
        delivery = status_payload["delivery"]
        self.assertEqual(status_payload["deliveryState"], "render_submitted")
        self.assertTrue(any(item["downloaded"] for item in delivery))
        self.assertTrue(any(item["renderSubmitted"] for item in delivery))
        self.assertNotIn("userSeen", status_payload)
        self.assertNotIn("visibility", status_payload)

    def test_auth_and_ack_boundaries(self):
        self.assertEqual(
            self.request("GET", "/v1/widgets/hermes-brief/publication")[0], 401
        )
        invalid_token = "not-a-real-" + "credential"
        self.assertEqual(
            self.request(
                "GET", "/v1/widgets/hermes-brief/publication", token=invalid_token
            )[0],
            401,
        )
        device = self.pair_device()
        published = self.store.put_publication(
            "hermes-brief", title="Text", summary="Plain text", text="hello"
        )
        self.assertEqual(
            self.request(
                "POST",
                "/v1/widgets/hermes-brief/publication/ack",
                {
                    "revision": published["revision"],
                    "status": "render_submitted",
                    "renderedWidth": 10,
                    "renderedHeight": 10,
                },
                device,
            )[0],
            409,
        )
        self.assertEqual(
            self.request(
                "POST",
                "/v1/widgets/hermes-brief/publication/ack",
                {
                    "revision": published["revision"],
                    "status": "render_submitted",
                    "renderedWidth": 10,
                    "renderedHeight": 10,
                },
                self.agent_token,
            )[0],
            403,
        )

    def test_oversized_body_and_missing_asset_are_bounded(self):
        oversized = {"title": "x", "summary": "x", "text": "x" * (400 * 1024)}
        self.assertEqual(
            self.request(
                "POST",
                "/v1/widgets/hermes-brief/publication",
                oversized,
                self.agent_token,
            )[0],
            413,
        )
        self.assertEqual(
            self.request("GET", "/v1/assets/asset_000000000000000000000000", token=self.pair_device())[0],
            404,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
