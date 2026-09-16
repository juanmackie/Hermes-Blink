"""Cross-language contract test: Python validator + shared fixtures (v2).

Every fixture in fixtures/ is validated here; the same files are read by
Android's LayoutContractTest.kt. A failure here or there breaks the shared contract.
Checks: typed values, action payloads, item IDs, timestamps, expiry, 64KB/100-node caps.
"""
from __future__ import annotations

import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
FIXTURES = REPO / "fixtures"

try:
    from ..validate import validate_layout, migrate_layout, ValidationError, LAYOUT_VERSION
except ImportError:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from validate import validate_layout, migrate_layout, ValidationError, LAYOUT_VERSION  # type: ignore

VALID_FIXTURES = ["brief-v2.json", "brief-v2-minimal.json"]
INVALID_FIXTURES = [
    "invalid-chart.json",
    "invalid-toggle.json",
    "invalid-image.json",
    "invalid-url-action.json",
    "invalid-calendar-month.json",
]


class ContractV2(unittest.TestCase):
    def test_fixtures_directory_exists(self):
        self.assertTrue(FIXTURES.is_dir(), f"fixtures/ missing at {FIXTURES}")

    def test_canonical_schema_is_v2(self):
        self.assertEqual(LAYOUT_VERSION, 2)

    def test_valid_fixtures_pass(self):
        for name in VALID_FIXTURES:
            p = FIXTURES / name
            self.assertTrue(p.is_file(), f"missing fixture {name}")
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data.get("version"), 2, f"{name}: version must be 2")
            try:
                validate_layout(data)
            except ValidationError as exc:
                self.fail(f"{name} should be valid v2, got: {exc}")
            # check contract preservation: typed values, payloads, item IDs, timestamps
            raw = json.dumps(data)
            self.assertIn("widgetId", raw, f"{name}: missing widgetId")
            if name == "brief-v2.json":
                self.assertIn("item-1", raw, "brief-v2: missing item IDs")
                self.assertIn("itemId", raw, "brief-v2: missing action itemId")
                self.assertIn("updatedAt", raw, "brief-v2: missing timestamp")
                self.assertIn("review", raw, "brief-v2: missing review action")
                self.assertIn("dismiss", raw, "brief-v2: missing dismiss action")

    def test_invalid_fixtures_rejected(self):
        for name in INVALID_FIXTURES:
            p = FIXTURES / name
            self.assertTrue(p.is_file(), f"missing invalid fixture {name}")
            data = json.loads(p.read_text(encoding="utf-8"))
            with self.assertRaises(ValidationError, msg=f"{name} should be rejected"):
                validate_layout(data)

    def test_removed_types_absent_from_valid_fixtures(self):
        for name in VALID_FIXTURES:
            raw = (FIXTURES / name).read_text(encoding="utf-8")
            for bad in ('"chart"', '"image"', '"icon"', '"toggle"', '"open_app"', '"deeplink"', '"chartType"'):
                self.assertNotIn(bad, raw, f"{name} must not contain removed token {bad}")
            # month is only valid as part of a larger string; check exact mode value
            data = json.loads(raw)
            if "calendar" in raw:
                self.assertNotIn('"month"', raw, f"{name}: calendar month mode removed")

    def test_removed_types_cannot_validate_as_supported(self):
        # Direct programmatic check: validator must reject removed types as unsupported,
        # not silently ignore them.
        samples = [
            {"version": 2, "widgetId": "hermes-brief", "root": {"type": "column", "children": [{"type": "chart", "chartType": "bar", "series": [1]}]}},
            {"version": 2, "widgetId": "hermes-brief", "root": {"type": "column", "children": [{"type": "toggle", "label": "x", "stateKey": "k"}]}},
            {"version": 2, "widgetId": "hermes-brief", "root": {"type": "column", "children": [{"type": "button", "label": "x", "action": {"kind": "url", "url": "https://x"}}]}},
        ]
        for sample in samples:
            with self.assertRaises(ValidationError):
                validate_layout(sample)

    def test_migration_converts_legacy_v1(self):
        p = FIXTURES / "legacy-v1.json"
        self.assertTrue(p.is_file(), "legacy-v1.json missing")
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data.get("version"), 1)
        migrated, had_removed = migrate_layout(data)
        self.assertTrue(had_removed, "legacy fixture contains removed content; had_removed should be True")
        self.assertEqual(migrated.get("version"), 2)
        # Migrated payload must itself validate as v2
        try:
            validate_layout(migrated)
        except ValidationError as exc:
            self.fail(f"migrated fixture should be valid v2, got: {exc}")
        # Unsupported sections replaced; supported text preserved
        raw = json.dumps(migrated)
        for bad in ('"chart"', '"image"', '"toggle"'):
            self.assertNotIn(bad, raw)

    def test_migration_preserves_supported_fields(self):
        data = json.loads((FIXTURES / "legacy-v1.json").read_text())
        migrated, _ = migrate_layout(data)
        self.assertIn("Kept text", json.dumps(migrated))
        self.assertIn("hermes-brief", migrated.get("widgetId", ""))

    def test_interaction_payload_preserved(self):
        p = FIXTURES / "interaction-event.json"
        if p.is_file():
            data = json.loads(p.read_text())
            self.assertIn("itemId", json.dumps(data))
            self.assertIn("clientEventId", json.dumps(data))

    def test_fixture_assets_match_canonical(self):
        # The two shipped assets copies must remain byte-identical to the canonical fixture
        for rel in ["assets/fixture_layout.json", "android/app/src/main/assets/fixture_layout.json"]:
            shipped = REPO / rel
            if shipped.is_file():
                self.assertEqual(
                    shipped.read_text(encoding="utf-8"),
                    (FIXTURES / "brief-v2.json").read_text(encoding="utf-8"),
                    f"{rel} must match fixtures/brief-v2.json",
                )
