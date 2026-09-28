"""Cross-language contract test: Python validator + shared fixtures (v2).

Every fixture in fixtures/ is validated here; the same files are read by
Android's LayoutContractTest.kt. A failure here or there breaks the shared contract.
Checks: typed values, action payloads, item IDs, timestamps, expiry, 64KB/100-node caps.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
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
    from .. import validate
except ImportError:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from validate import validate_layout, ValidationError, LAYOUT_VERSION  # type: ignore
    from validate import inspect_layout  # type: ignore
    import validate  # type: ignore
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


class BandBudgets(unittest.TestCase):
    """Task 12: publisher warnings follow the device's band ladder, not a nominal canvas.

    The phone groups instances by height (Breakpoints.kt); the same thresholds live in
    validate.size_band, so a warning names the band the user would actually see.
    """

    # 472 characters: under the contract's 500-char text cap and far over the 3-line
    # budget of a 4x2 at 407dp (est. 56 chars/line -> 168).
    WALL = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet " * 8)[:472]

    def _layout(self, body_text="short", extra_lines=0):
        children = [
            {"type": "text", "id": "h", "value": "Overnight batch", "style": "title"},
            {"type": "text", "id": "s", "value": "3 need review", "style": "caption"},
            {"type": "text", "id": "b", "value": body_text, "style": "body"},
        ]
        for index in range(extra_lines):
            children.append({
                "type": "text",
                "id": f"x{index}",
                "value": f"detail line {index}",
                "style": "label",
            })
        return {
            "version": 2,
            "widgetId": "hermes-brief",
            "title": "Overnight batch",
            "root": {"type": "column", "children": children},
        }

    def test_band_edges_match_the_device(self):
        cases = {
            (109, 56): "xs", (624, 129): "xs",
            (624, 130): "s", (306, 184): "s",
            (624, 185): "m", (306, 299): "m",
            (624, 300): "l", (407, 412): "l",
        }
        for (width, height), band in cases.items():
            self.assertEqual(band, validate.size_band(width, height), f"{width}x{height}")

    def test_a_wide_short_instance_is_xs_and_has_no_body_region(self):
        # 4x1: 245-624 x 56-130. The body cannot render there at all.
        report = inspect_layout(self._layout(self.WALL), [{"widthDp": 624, "heightDp": 120}])
        codes = [w["code"] for w in report["warnings"]]
        self.assertIn("LAYOUT_TOO_TALL_FOR_BAND", codes)
        detail = next(w["detail"] for w in report["warnings"]
                      if w["code"] == "LAYOUT_TOO_TALL_FOR_BAND")
        self.assertIn("xs band", detail)
        self.assertIn("624x120dp", detail)

    def test_too_many_text_nodes_names_the_small_band(self):
        report = inspect_layout(self._layout("All clear.", extra_lines=2),
                                [{"widthDp": 624, "heightDp": 120}])
        codes = {w["code"] for w in report["warnings"]}
        self.assertIn("TEXT_MAY_CLIP_XS", codes)

    def test_a_text_wall_on_a_2x2_names_the_band_and_its_geometry(self):
        # 2x2 max height is 276dp; 150dp is the S band: title + summary, no body region.
        report = inspect_layout(self._layout(self.WALL), [{"widthDp": 300, "heightDp": 150}])
        codes = {w["code"] for w in report["warnings"]}
        self.assertIn("LAYOUT_TOO_TALL_FOR_BAND", codes)
        detail = next(w["detail"] for w in report["warnings"]
                      if w["code"] == "LAYOUT_TOO_TALL_FOR_BAND")
        self.assertIn("s band", detail)
        self.assertIn("300x150dp", detail)
        self.assertIn("summary", detail)

    def test_a_4x4_reports_scroll_not_clipping(self):
        report = inspect_layout(self._layout(self.WALL), [{"widthDp": 407, "heightDp": 412}])
        codes = {w["code"] for w in report["warnings"]}
        self.assertIn("LAYOUT_MAY_SCROLL_L", codes)
        self.assertNotIn("TEXT_MAY_CLIP_L", codes)

    def test_a_4x2_flags_a_body_that_needs_scrolling(self):
        report = inspect_layout(self._layout(self.WALL), [{"widthDp": 407, "heightDp": 250}])
        self.assertIn("LAYOUT_MAY_SCROLL_M", {w["code"] for w in report["warnings"]})

    def test_a_tight_layout_on_a_4x2_warns_nothing(self):
        report = inspect_layout(self._layout("All clear."), [{"widthDp": 407, "heightDp": 250}])
        codes = {w["code"] for w in report["warnings"]}
        self.assertNotIn("LAYOUT_MAY_SCROLL_M", codes)
        self.assertNotIn("LAYOUT_TOO_TALL_FOR_BAND", codes)

    def test_without_inventory_there_is_nothing_to_warn_about(self):
        report = inspect_layout(self._layout(self.WALL), [])
        codes = {w["code"] for w in report["warnings"]}
        self.assertNotIn("LAYOUT_TOO_TALL_FOR_BAND", codes)
        self.assertNotIn("LAYOUT_MAY_SCROLL_L", codes)

    def test_the_estimate_is_stated_as_an_estimate(self):
        # 34 chars/line at 245dp, linear in width: the message must not present it as exact.
        self.assertEqual(34, validate.chars_per_line(245))
        self.assertEqual(16, validate.chars_per_line(120))
        report = inspect_layout(self._layout(self.WALL), [{"widthDp": 407, "heightDp": 412}])
        detail = next(w["detail"] for w in report["warnings"]
                      if w["code"] == "LAYOUT_MAY_SCROLL_L")
        self.assertIn("est.", detail)
        self.assertIn("chars/line", detail)

    def test_size_class_normalization_matches_the_device(self):
        cases = {
            (110, 110): "2x2", (200, 150): "2x2",
            (245, 130): "4x2", (307, 60): "4x2", (624, 276): "4x2",
            (180, 400): "2x4",
            (407, 412): "4x4", (624, 422): "4x4",
            (700, 300): "custom", (100, 50): "custom",
        }
        for (width, height), expected in cases.items():
            self.assertEqual(expected, store._size_class(width, height), f"{width}x{height}")
        # A declared value from the wire contract is always honored verbatim.
        for declared in ("2x2", "4x2", "2x4", "4x4", "custom"):
            self.assertEqual(declared, store._size_class(407, 412, declared))


class GeometryMirror(unittest.TestCase):
    """Hold each pair of geometry implementations to the other.

    Breakpoints.kt and WidgetDimensions.kt decide what the widget draws; validate.py and
    store._size_class decide what a publisher is warned about and which content key is
    read. Both Python files describe themselves as mirrors of the Kotlin, and nothing held
    them to it. If an edge moves on one side only, a layout is budgeted against a band the
    widget never draws and every warning is confidently wrong, with no test failing.

    The assertions are behavioural rather than literal: a refactor that keeps the behaviour
    does not have to touch this test, and a drifting edge cannot hide behind a rename.
    """

    KOTLIN_LADDER = REPO / "android/app/src/main/java/com/you/hermeswidget/widget/Breakpoints.kt"
    KOTLIN_DIMS = REPO / "android/app/src/main/java/com/you/hermeswidget/widget/WidgetDimensions.kt"

    def _const(self, path: pathlib.Path, name: str) -> int:
        match = re.search(rf"const val {name} = (\d+)", path.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, f"{name} is not declared in {path.name}")
        return int(match.group(1))

    def test_band_edges_match_the_validator(self):
        if not self.KOTLIN_LADDER.is_file():
            self.skipTest("Android sources are not part of this distribution")
        xs = self._const(self.KOTLIN_LADDER, "XS_MAX_HEIGHT_DP")
        small = self._const(self.KOTLIN_LADDER, "S_MAX_HEIGHT_DP")
        medium = self._const(self.KOTLIN_LADDER, "M_MAX_HEIGHT_DP")
        wide = self._const(self.KOTLIN_LADDER, "SINGLE_COLUMN_MAX_WIDTH_DP")
        self.assertLess(xs, small, "the height ladder must be monotonic")
        self.assertLess(small, medium, "the height ladder must be monotonic")
        for height in range(56, 423):
            expected = "xs" if height < xs else "s" if height < small else "m" if height < medium else "l"
            with self.subTest(height=height):
                self.assertEqual(expected, validate.size_band(300, height))
        for width in (109, wide - 1, wide, wide + 1, 624):
            with self.subTest(width=width):
                self.assertEqual(width < wide, validate.is_single_column(width))

    def test_size_class_boundaries_match_the_store(self):
        if not self.KOTLIN_DIMS.is_file():
            self.skipTest("Android sources are not part of this distribution")
        min_w = self._const(self.KOTLIN_DIMS, "MIN_WIDTH_DP")
        max_w = self._const(self.KOTLIN_DIMS, "MAX_WIDTH_DP")
        min_h = self._const(self.KOTLIN_DIMS, "MIN_HEIGHT_DP")
        max_h = self._const(self.KOTLIN_DIMS, "MAX_HEIGHT_DP")
        wide = self._const(self.KOTLIN_DIMS, "WIDE_MIN_DP")
        tall = self._const(self.KOTLIN_DIMS, "TALL_MIN_DP")

        def expected(width: int, height: int) -> str:
            if not (min_w <= width <= max_w and min_h <= height <= max_h):
                return "custom"
            if width >= wide and height >= tall:
                return "4x4"
            if width >= wide:
                return "4x2"
            if height >= tall:
                return "2x4"
            return "2x2"

        widths = (min_w - 1, min_w, wide - 1, wide, wide + 1, max_w, max_w + 1)
        heights = (min_h - 1, min_h, tall - 1, tall, tall + 1, max_h, max_h + 1)
        for width in widths:
            for height in heights:
                with self.subTest(size=f"{width}x{height}"):
                    self.assertEqual(expected(width, height), store._size_class(width, height))

    def test_any_size_is_classified_rather_than_refused(self):
        """A resizable widget gets dragged to sizes nobody enumerated; none may be refused.

        Every geometry below has to produce a band, one of the contract's five size
        classes, and a preview, because the ladders are step functions over a range and
        not lookup tables over a fixed set of cells.
        """
        for width, height in ((180, 110), (300, 180), (624, 422), (110, 180), (700, 300), (100, 50)):
            with self.subTest(size=f"{width}x{height}"):
                self.assertIsNotNone(validate.size_band(width, height))
                self.assertIn(
                    store._size_class(width, height),
                    {"2x2", "4x2", "2x4", "4x4", "custom"},
                )
                self.assertEqual((width, height), preview._size_names(f"{width}x{height}")[0][1:3])

    def test_single_digit_preview_sizes_are_accepted(self):
        # preview.py range-checks 1..4096, so a one-digit size has to parse too.
        for size in ("9x9", "1x1", "12x7"):
            with self.subTest(size=size):
                self.assertEqual(
                    (int(size.split("x")[0]), int(size.split("x")[1])),
                    preview._size_names(size)[0][1:3],
                )


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
