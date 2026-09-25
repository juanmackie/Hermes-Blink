"""Cross-language contract test: Python validator + shared fixtures (v2).

Every fixture in fixtures/ is validated here; the same files are read by
Android's LayoutContractTest.kt. A failure here or there breaks the shared contract.
Checks: typed values, action payloads, item IDs, timestamps, expiry, 64KB/100-node caps.
"""
from __future__ import annotations

import json
import os
import pathlib
import unittest
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[3]
PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"

try:
    from ..validate import validate_layout, ValidationError, LAYOUT_VERSION
    from ..validate import inspect_layout
    from .. import store
    from .. import tools
    from .. import schemas
    from .. import preview
except ImportError:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from validate import validate_layout, ValidationError, LAYOUT_VERSION  # type: ignore
    from validate import inspect_layout  # type: ignore
    import store  # type: ignore
    import tools  # type: ignore
    import schemas  # type: ignore
    import preview  # type: ignore

VALID_FIXTURES = ["brief-v2.json", "brief-v2-minimal.json"]
GOLDEN_FIXTURES = [
    "small-hero-stat.json",
    "medium-split-strip.json",
    "medium-agenda.json",
    "large-brief.json",
]
GOLDEN_DIR = FIXTURES / "golden"
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


class GoldenLayouts(unittest.TestCase):
    """The reference layouts the design skill points at.

    A golden that stops validating is the skill teaching a lie, so this is the guard on
    skills/widget/SKILL.md.
    """

    def test_every_golden_is_present_and_valid_v2(self):
        for name in GOLDEN_FIXTURES:
            path = GOLDEN_DIR / name
            self.assertTrue(path.is_file(), f"missing golden fixture {name}")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("version"), 2, f"{name}: version must be 2")
            try:
                validate_layout(data)
            except ValidationError as exc:
                self.fail(f"{name} should be a valid v2 layout, got: {exc}")

    def test_goldens_use_no_removed_types(self):
        for name in GOLDEN_FIXTURES:
            raw = (GOLDEN_DIR / name).read_text(encoding="utf-8")
            for bad in ('"chart"', '"image"', '"icon"', '"toggle"', '"open_app"'):
                self.assertNotIn(bad, raw, f"{name} must not contain removed token {bad}")

    def test_goldens_are_clean_enough_to_teach(self):
        # A golden with design warnings would be teaching the anti-pattern.
        for name in GOLDEN_FIXTURES:
            data = json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))
            report = inspect_layout(data)
            self.assertEqual(
                [], report["warnings"],
                f"{name} carries design warnings: {report['warnings']}",
            )


class HexColourRule(unittest.TestCase):
    """Colours are 3- or 6-digit hex, the same rule as accentColor.

    The device ignores an 8-digit alpha hex instead of failing to paint, so without this
    check a push using one would succeed and render the wrong colour.
    """

    def _layout(self, node):
        return {"version": 2, "widgetId": "hermes-brief",
                "root": {"type": "column", "children": [node]}}

    def test_three_and_six_digit_hex_are_accepted(self):
        for colour in ("#7C3AED", "#abc", "#10b981"):
            for node in (
                {"type": "text", "value": "x", "color": colour},
                {"type": "divider", "color": colour},
                {"type": "badge", "text": "x", "color": colour},
                {"type": "stat", "label": "l", "value": "v", "color": colour},
            ):
                validate_layout(self._layout(node))

    def test_eight_digit_alpha_hex_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_layout(self._layout({"type": "text", "value": "x", "color": "#FF000080"}))

    def test_named_and_malformed_colours_are_rejected(self):
        for colour in ("red", "#12345", "", "rgb(1,2,3)", "#GGGGGG"):
            with self.assertRaises(ValidationError):
                validate_layout(self._layout({"type": "text", "value": "x", "color": colour}))

    def test_calendar_event_colour_is_checked(self):
        with self.assertRaises(ValidationError):
            validate_layout({
                "version": 2, "widgetId": "hermes-brief",
                "root": {"type": "column", "children": [{
                    "type": "calendar",
                    "events": [{"title": "x", "start": "2026-09-16T09:30:00Z", "color": "#FF000080"}],
                }]},
            })


class DryRunValidate(unittest.TestCase):
    """widget_validate: the same verdicts as widget_update, with nothing stored."""

    def setUp(self):
        # A private data dir under the already-ignored _testdata/: this class must not touch
        # the real widget DB or another test class's fixture store.
        self.workdir = PLUGIN_DIR / "_testdata" / "dryrun"
        self.workdir.mkdir(parents=True, exist_ok=True)
        os.environ["HERMES_WIDGET_DIR"] = str(self.workdir)
        os.environ["HERMES_WIDGET_FORCE_ENV"] = "supported"
        store.init_db()

    def _brief(self):
        return json.loads((FIXTURES / "brief-v2.json").read_text(encoding="utf-8"))

    def test_report_shape(self):
        report = json.loads(tools.widget_validate({"widget_id": "hermes-brief", "layout": self._brief()}))
        self.assertTrue(report["ok"], report)
        self.assertEqual(19, report["nodeCount"])
        self.assertLess(report["bytes"], report["maxBytes"])
        self.assertEqual(["caption", "label", "title"], report["textStyles"])
        self.assertEqual("hermes-brief", report["widgetId"])

    def test_nothing_is_stored(self):
        before = tools.widget_list()
        tools.widget_validate({"widget_id": "hermes-brief", "layout": self._brief()})
        self.assertEqual(before, tools.widget_list())

    def test_the_dry_run_does_not_spend_a_push(self):
        # 30 pushes per window: a dry run must not consume one.
        layout = self._brief()
        for _ in range(40):
            tools.widget_validate({"widget_id": "hermes-brief", "layout": layout})
        pushed = json.loads(tools.widget_update({"widget_id": "hermes-brief", "layout": layout}))
        self.assertTrue(pushed.get("ok"), pushed)

    def test_an_invalid_layout_reports_the_same_error_code_as_a_push(self):
        bad = self._brief()
        bad["root"]["children"][0]["color"] = "#FF000080"
        dry = json.loads(tools.widget_validate({"widget_id": "hermes-brief", "layout": bad}))
        live = json.loads(tools.widget_update({"widget_id": "hermes-brief", "layout": bad}))
        self.assertEqual("invalid_layout", dry["error"])
        self.assertEqual(live["error"], dry["error"])
        self.assertEqual(live["detail"], dry["detail"])

    def test_design_warnings_are_advisory(self):
        thin = self._brief()
        thin["root"]["padding"] = {"top": 4, "bottom": 4, "start": 4, "end": 4}
        report = json.loads(tools.widget_validate({"layout": thin}))
        self.assertTrue(report["ok"])
        self.assertIn("ROOT_PADDING_LOW", [w["code"] for w in report["warnings"]])

    def test_layout_accepts_a_json_string_too(self):
        report = json.loads(tools.widget_validate({"layout": json.dumps(self._brief())}))
        self.assertTrue(report["ok"])

    def test_widget_validate_is_registered_and_has_no_extra_requirements(self):
        self.assertEqual("widget_validate", schemas.WIDGET_VALIDATE["name"])
        self.assertEqual(["layout"], schemas.WIDGET_VALIDATE["parameters"]["required"])


class PreviewRender(unittest.TestCase):
    """The offline preview must not flatter the layout: it reads the registry, and it shows
    the same stale banner and the same clipping the device would."""

    def _layout(self, name):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def test_every_golden_renders_every_shape(self):
        for name in GOLDEN_FIXTURES:
            html = preview.render_html(self._layout("golden/" + name))
            for label, _, _ in preview.SHAPES:
                self.assertIn(f"<figcaption>{label.split()[0]}", html,
                              f"{name}: missing the {label} panel")
            self.assertIn("<!doctype html>", html)

    def test_every_node_type_survives_the_renderer(self):
        # One layout containing all 13 types, so a new type cannot be added to the schema
        # without the preview (and this test) failing.
        layout = {
            "version": 2, "widgetId": "hermes-brief", "accentColor": "#7C3AED",
            "updatedAt": "2026-09-16T07:30:00Z", "ttlSeconds": 1800,
            "root": {"type": "column", "spacing": 8,
                     "padding": {"top": 12, "bottom": 12, "start": 12, "end": 12},
                     "children": [
                         {"type": "text", "value": "hero", "style": "title"},
                         {"type": "divider", "thickness": 2, "color": "#E5E5EA"},
                         {"type": "spacer", "size": 4},
                         {"type": "badge", "text": "New", "color": "#10b981"},
                         {"type": "stat", "label": "L", "value": "V", "delta": "+1",
                          "deltaDirection": "up"},
                         {"type": "progress", "value": 0.5, "label": "P", "showPercent": True},
                         {"type": "calendar", "mode": "agenda", "maxItems": 2,
                          "events": [{"title": "E", "start": "2026-09-16T09:30:00Z", "color": "#7C3AED"}]},
                         {"type": "button", "label": "B", "style": "filled",
                          "action": {"kind": "refresh"}},
                         {"type": "list", "maxItems": 2, "children": [
                             {"type": "list_item", "id": "i1", "title": "T", "subtitle": "S",
                              "trailingText": "2h",
                              "action": {"kind": "review", "itemId": "i1"}}]},
                         {"type": "row", "spacing": 4, "children": [
                             {"type": "text", "value": "r", "style": "body"},
                             {"type": "box", "children": [
                                 {"type": "text", "value": "b", "style": "label"}]}]},
                     ]},
        }
        validate_layout(layout)
        html = preview.render_html(layout)
        for token in ("hero", "New", "+1", "09:30", "B", "T", "2h", "b"):
            self.assertIn(token, html, f"preview dropped {token!r}")
        # ISO datetimes become clock times, never the raw string.
        self.assertNotIn("2026-09-16T09:30:00Z", html)

    def test_typography_and_palette_come_from_the_registry(self):
        schema = json.loads((PLUGIN_DIR / "layout.schema.json").read_text(encoding="utf-8"))
        typo = schema["definitions"]["typography"]["properties"]
        palette = schema["definitions"]["palette"]["properties"]
        # brief-v2 has a progress bar and a button, so the accent is painted somewhere.
        html = preview.render_html(self._layout("brief-v2.json"))
        self.assertIn(f"font-size:{typo['title']['sizeSp']}px", html)
        self.assertIn(f"font-size:{typo['caption']['sizeSp']}px", html)
        self.assertIn(palette["secondary"]["const"], html)
        self.assertIn(palette["accent"]["const"], html)
        self.assertIn(palette["hairline"]["const"], html)

    def test_colour_override_beats_the_scale_colour(self):
        layout = self._layout("golden/large-brief.json")
        layout["root"]["children"][0]["color"] = "#FF0000"
        self.assertIn("#FF0000", preview.render_html(layout))

    def test_stale_rule_matches_the_device(self):
        fresh = {"ttlSeconds": 1800, "updatedAt": "2026-09-16T07:30:00Z"}
        self.assertFalse(preview.is_stale(fresh, now=datetime(2026, 9, 16, 7, 45, tzinfo=timezone.utc)))
        self.assertTrue(preview.is_stale(fresh, now=datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)))
        # No ttl, no timestamp, or an unparseable one: never stale (never a false alarm).
        self.assertFalse(preview.is_stale({"updatedAt": "2026-09-16T07:30:00Z"},
                                          now=datetime(2030, 1, 1, tzinfo=timezone.utc)))
        self.assertFalse(preview.is_stale({"ttlSeconds": 1800},
                                          now=datetime(2030, 1, 1, tzinfo=timezone.utc)))
        self.assertFalse(preview.is_stale({"ttlSeconds": 1800, "updatedAt": "not a date"},
                                          now=datetime(2030, 1, 1, tzinfo=timezone.utc)))

    def test_the_stale_banner_is_shown_exactly_when_stale(self):
        # large-brief has updatedAt 2026-09-16 and ttlSeconds, so it is stale by now.
        self.assertIn("Stale — reconnecting", preview.render_html(self._layout("golden/large-brief.json")))
        fresh = self._layout("golden/large-brief.json")
        fresh["updatedAt"] = datetime.now(timezone.utc).isoformat()
        self.assertNotIn("Stale — reconnecting", preview.render_html(fresh))

    def test_an_invalid_layout_is_refused_rather_than_drawn(self):
        bad = self._layout("brief-v2.json")
        bad["root"]["children"][0]["color"] = "#FF000080"
        with self.assertRaises(ValidationError):
            preview.render_html(bad)
        with self.assertRaises(ValidationError):
            preview.render_html(self._layout("invalid-chart.json"))

    def test_preview_file_defaults_to_sitting_next_to_the_input(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "layout.json"
            src.write_text(json.dumps(self._layout("golden/small-hero-stat.json")), encoding="utf-8")
            out = preview.preview_file(src)
            self.assertEqual(src.with_suffix(".preview.html"), out)
            self.assertTrue(out.is_file())
