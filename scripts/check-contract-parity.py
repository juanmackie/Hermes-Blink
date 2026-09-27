#!/usr/bin/env python3
"""Fail when the layout contract and the things that promise it drift apart.

The v2 bug this exists to prevent: layout.schema.json declared `style`, `color`,
`spacing`, `thickness`, `alignment`, `padding` and `weight`; validate.py accepted them;
LayoutParser.kt parsed them; and Renderer.kt applied none of them, so a layout author
silently got a bare black Text. Six copies of the same contract, no check that they agreed.

One registry (layout.schema.json), everything else mirrors it, and this compares them:

  * node types    - schema == validate.py == LayoutParser.kt == SKILL.md
  * action kinds  - schema == validate.py == LayoutParser.kt
  * text styles   - schema == validate.py == Typo.kt == SKILL.md
  * node fields   - schema(type-specific) == the SKILL.md "only these fields render" table
  * envelope      - every schema root property is either rendered or listed as metadata

Exit 0 when everything agrees. Exit 1 with a per-check diff otherwise.

    python scripts/check-contract-parity.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = REPO / "hermes-plugin" / "hermes-widget"
SCHEMA_PATH = PLUGIN / "layout.schema.json"
VALIDATE_PATH = PLUGIN / "validate.py"
PARSER_PATH = REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget" / "widget" / "LayoutParser.kt"
TYPO_PATH = REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget" / "widget" / "Typo.kt"
SKILL_PATH = PLUGIN / "skills" / "widget" / "SKILL.md"
SCHEMA_DOC_PATH = REPO / "docs" / "SCHEMA.md"
WIDGET_KT = REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget" / "widget" / "HermesWidget.kt"
PREVIEW_PY = PLUGIN / "preview.py"
RES = REPO / "android" / "app" / "src" / "main" / "res"
WIDGET_INFO = RES / "xml" / "hermes_widget_info.xml"
PREVIEW_LAYOUT = RES / "layout" / "widget_preview.xml"
PREVIEW_LAYOUT_NIGHT = RES / "layout-night" / "widget_preview.xml"
LOADING_LAYOUT = RES / "layout" / "widget_loading.xml"
STRINGS_XML = RES / "values" / "strings.xml"
DIMENS_XML = RES / "values" / "dimens.xml"

# Fields that live in the envelope, not on a node, and are not rendered per-node.
ENVELOPE_METADATA = {"version", "widgetId", "itemId", "title", "ttlSeconds", "accentColor", "updatedAt", "root"}
# Common node fields checked once as a group rather than inside the per-type table.
NODE_BASE_FIELDS = {"type", "id", "itemId", "weight", "padding", "alignment"}

failures: list[str] = []


def fail(check: str, detail: str) -> None:
    failures.append(f"[{check}] {detail}")


def source_of(path: pathlib.Path | str) -> str:
    """File contents with comments stripped.

    A gate that greps for a forbidden token must not match the comment explaining *why*
    the token is forbidden. That mistake shipped three times in this file, each time
    turning a good comment into a false positive, so the stripping is centralised here.
    """
    text = pathlib.Path(path).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)   # C-style block
    text = "\n".join(                                     # Python and shell line comments
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    return re.sub(r"//.*", "", text)


def mentions(text: str, name: str) -> bool:
    """A field documented as a `code` token or as a "quoted" JSON key."""
    return f"`{name}`" in text or f'"{name}"' in text


def section(text: str, heading: str) -> str:
    """The body of `## heading` up to the next `## ` heading."""
    match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    if not match:
        fail("skill-parse", f"SKILL.md has no '## {heading}' section")
        return ""
    rest = text[match.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def schema_node_types(schema: dict) -> set[str]:
    return set(schema["definitions"]["node"]["properties"]["type"]["enum"])


def schema_type_fields(schema: dict) -> dict[str, set[str]]:
    """Per node type, the fields declared in `nodeSpecifics`."""
    out: dict[str, set[str]] = {}
    for branch in schema["definitions"]["nodeSpecifics"]["oneOf"]:
        const = branch["if"]["properties"]["type"]["const"]
        fields = set(branch.get("then", {}).get("properties", {}))
        out[const] = fields
    return out


def kotlin_set(path: pathlib.Path, name: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"private val {name} = setOf\((.*?)\)", text, re.S)
    if not match:
        fail("kotlin-parse", f"{path.name}: no `{name}` setOf(...) found")
        return set()
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def skill_node_types(text: str) -> set[str]:
    body = section(text, "The v2 layout contract (transport stays /v1/)")
    found: set[str] = set()
    for line in body.splitlines():
        if re.match(r"^(Containers|Content|Data|Interactive):", line):
            found |= set(re.findall(r"`([a-z_]+)`", line))
    return found


def skill_fields(text: str) -> dict[str, set[str]]:
    """The `| node | fields |` table under 'Only these fields render'."""
    body = section(text, "Only these fields render")
    out: dict[str, set[str]] = {}
    for line in body.splitlines():
        match = re.match(r"^\|\s*`([a-z_]+)`\s*\|\s*(.*?)\s*\|\s*$", line)
        if not match:
            continue
        node, cells = match.group(1), match.group(2)
        # Drop the parenthesised value lists: `alignment` (`start`/`center`/...) is one field.
        cells = re.sub(r"\([^)]*\)", "", cells)
        out[node] = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", cells))
    return out


def skill_text_styles(text: str) -> dict[str, tuple[int, str, str]]:
    """The SKILL.md typography table as {name: (sizeSp, weight, colour)}."""
    body = section(text, "Typography — four steps, pick at most three")
    found: dict[str, tuple[int, str, str]] = {}
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        name = re.fullmatch(r"`([a-z_]+)`", cells[0])
        size = re.fullmatch(r"(\d+)sp", cells[1])
        colour = re.fullmatch(r"`(#[0-9A-Fa-f]{6})`", cells[3])
        if name and size and colour:
            found[name.group(1)] = (int(size.group(1)), cells[2], colour.group(1))
    return found


def typo_scale_keys(path: pathlib.Path) -> dict[str, tuple[int, str, str]]:
    """Typo.kt's SCALE as {name: (sizeSp, weight, colorHex)}."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"val SCALE: Map<String, Spec> = mapOf\((.*?)\n    \)", text, re.S)
    if not match:
        fail("kotlin-parse", "Typo.kt: SCALE map not found")
        return {}
    out: dict[str, tuple[int, str, str]] = {}
    for name, size, weight, colour in re.findall(
        r'"([a-z_]+)" to Spec\((\d+), "([a-z]+)", ([A-Z_]+)\)', match.group(1)
    ):
        # The colour is a Typo constant; resolve it from the constants above the map.
        const = re.search(rf'const val {colour} = "(#[0-9A-Fa-f]{{6}})"', text)
        out[name] = (int(size), weight, const.group(1) if const else "?")
    return out


def kotlin_constants(path: pathlib.Path) -> dict[str, str]:
    return dict(re.findall(r'const val ([A-Z_]+) = "(#[0-9A-Fa-f]{3,6})"', path.read_text(encoding="utf-8")))


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    skill = SKILL_PATH.read_text(encoding="utf-8")
    schema_doc = SCHEMA_DOC_PATH.read_text(encoding="utf-8")

    sys.path.insert(0, str(PLUGIN))
    import validate  # noqa: PLC0415 - after sys.path setup, and minimal deps by design

    # --- node types -------------------------------------------------------
    types_schema = schema_node_types(schema)
    types_validate = set(validate.NODE_TYPES)
    types_parser = kotlin_set(PARSER_PATH, "ALLOWED_TYPES")
    types_skill = skill_node_types(skill)

    for name, other in (
        ("validate.py NODE_TYPES", types_validate),
        ("LayoutParser.kt ALLOWED_TYPES", types_parser),
        ("SKILL.md node list", types_skill),
    ):
        if other != types_schema:
            fail("node-types", f"schema vs {name}: "
                               f"only-in-schema={sorted(types_schema - other)} "
                               f"only-in-{name}={sorted(other - types_schema)}")

    # --- action kinds -----------------------------------------------------
    kinds_schema = set(schema["definitions"]["action"]["properties"]["kind"]["enum"])
    kinds_validate = set(validate.ACTION_KINDS)
    kinds_parser = kotlin_set(PARSER_PATH, "ALLOWED_ACTION_KINDS")
    if kinds_validate != kinds_schema:
        fail("action-kinds", f"validate.py={sorted(kinds_validate)} schema={sorted(kinds_schema)}")
    if kinds_parser != kinds_schema:
        fail("action-kinds", f"LayoutParser.kt={sorted(kinds_parser)} schema={sorted(kinds_schema)}")

    # --- text styles ------------------------------------------------------
    styles_schema: set[str] = set()
    for branch in schema["definitions"]["nodeSpecifics"]["oneOf"]:
        if branch["if"]["properties"]["type"]["const"] == "text":
            styles_schema = set(branch["then"]["properties"]["style"]["enum"])
    # Names AND numbers: comparing name -> (size, weight, colour) against every mirror
    # subsumes a separate name-set check, so this is the only text-style comparison needed.
    scale_schema = schema["definitions"]["typography"]["properties"]
    if set(scale_schema) != styles_schema:
        fail("text-styles",
             f"schema text.style enum {sorted(styles_schema)} != schema typography {sorted(scale_schema)}")
    if set(validate._TEXT_STYLES) != styles_schema:
        fail("text-styles",
             f"validate.py _TEXT_STYLES={sorted(validate._TEXT_STYLES)} schema={sorted(styles_schema)}")
    typo_scale = typo_scale_keys(TYPO_PATH)
    skill_styles = skill_text_styles(skill)
    for name in sorted(styles_schema):
        want = scale_schema.get(name)
        if want is None:
            fail("typography", f"schema typography has no entry for {name!r}")
            continue
        expect = (want["sizeSp"], want["weight"], want["color"])
        for mirror, got in (("Typo.kt", typo_scale.get(name)),
                            ("SKILL.md", skill_styles.get(name))):
            if got != expect:
                fail("typography", f"{name}: schema {expect} but {mirror} {got}")

    # --- per-type fields vs the documented list ---------------------------
    fields_schema = schema_type_fields(schema)
    fields_skill = skill_fields(skill)
    for node_type in sorted(types_schema):
        declared = fields_schema.get(node_type, set())
        documented = fields_skill.get(node_type)
        if documented is None:
            fail("node-fields", f"{node_type}: no row in the SKILL.md field table")
            continue
        undocumented = declared - documented
        invented = documented - declared
        if undocumented:
            fail("node-fields", f"{node_type}: schema declares {sorted(undocumented)} "
                                 f"but SKILL.md does not document them")
        if invented:
            fail("node-fields", f"{node_type}: SKILL.md documents {sorted(invented)} "
                                 f"which the schema does not declare")
    for extra in sorted(set(fields_skill) - types_schema):
        fail("node-fields", f"SKILL.md documents unknown node type {extra!r}")

    # --- nodeBase fields must at least be documented somewhere ------------
    for field in sorted(NODE_BASE_FIELDS - {"type"}):
        if not mentions(skill, field):
            fail("node-base-fields", f"SKILL.md never mentions the common field `{field}`")

    # --- envelope ---------------------------------------------------------
    envelope = set(schema["properties"])
    unknown = envelope - ENVELOPE_METADATA
    if unknown:
        fail("envelope", f"schema declares envelope fields with no documented handling: {sorted(unknown)}")
    for field in ("version", "widgetId", "ttlSeconds", "accentColor"):
        if not mentions(skill, field):
            fail("envelope", f"SKILL.md does not mention the envelope field `{field}`")

    # --- palette and spacing constants ------------------------------------
    typo_src = TYPO_PATH.read_text(encoding="utf-8")
    palette = schema["definitions"]["palette"]["properties"]
    consts = kotlin_constants(TYPO_PATH)
    for name, const in (("primary", "PRIMARY"), ("secondary", "SECONDARY"),
                        ("success", "SUCCESS"), ("danger", "DANGER"),
                        ("hairline", "HAIRLINE")):
        want = palette[name]["const"]
        if consts.get(const) != want:
            fail("palette", f"{name}: schema {want} but Typo.kt {const}={consts.get(const)}")
    renderer = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                "hermeswidget" / "widget" / "Renderer.kt").read_text(encoding="utf-8")
    accent = re.search(r'DEFAULT_ACCENT = "(#[0-9A-Fa-f]{6})"', renderer)
    if not accent or accent.group(1) != palette["accent"]["const"]:
        fail("palette",
             f"accent: schema {palette['accent']['const']} but Renderer.kt "
             f"{accent.group(1) if accent else 'not found'}")
    for field, const in (("containerSpacing", "CONTAINER_SPACING"),
                         ("rootPadding", "ROOT_PADDING")):
        want = schema["definitions"]["spacing"]["properties"][field]["const"]
        found = re.search(rf"const val {const} = (\d+)", typo_src)
        if not found or int(found.group(1)) != want:
            fail("spacing",
                 f"{field}: schema {want} but Typo.kt LayoutDefaults.{const}="
                 f"{found.group(1) if found else 'not found'}")

    # --- the widget surface itself (Tasks 3-10) ----------------------------
    widget = WIDGET_KT.read_text(encoding="utf-8")
    for retired, why in (
        ("WIDGET_SCRIM", "the 65%-alpha scrim was replaced by theme tokens (WC-1)"),
        ("cornerRadius(24.dp)", "the system corner radius is resolved instead (WS-2)"),
        ("height(200.dp)", "image height is band driven (D15)"),
        ("compact", "the single compact boolean was replaced by the band ladder (D2)"),
    ):
        if retired in widget:
            fail("widget-surface", f"HermesWidget.kt still has {retired!r}: {why}")
    if "SizeMode.Responsive" not in widget:
        fail("widget-surface", "HermesWidget.kt must compose responsively (SizeMode.Responsive)")
    if "LocalSize" not in widget:
        fail("widget-surface", "HermesWidget.kt must read LocalSize for per-instance geometry")
    if "LazyColumn" not in widget:
        fail("widget-surface", "the publication body must stay scrollable (LazyColumn)")
    # The status/action footer must be composed outside the LazyColumn: everything the
    # LazyColumn holds between its braces may not mention the request action.
    lazy_body = re.search(r"LazyColumn\((.*?)\n        \}", widget, re.S)
    if lazy_body and "request_update" in lazy_body.group(1):
        fail("widget-surface",
             "the request action is inside the scroll region again; the footer must be pinned (WT-4)")

    # --- preview tokens vs the device's color resources -------------------
    preview_src = PREVIEW_PY.read_text(encoding="utf-8")
    device_colors = {
        "day": dict(re.findall(r'<color name="([a-z_]+)">(#[0-9A-Fa-f]{6})</color>',
                               (RES / "values" / "colors.xml").read_text(encoding="utf-8"))),
        "night": dict(re.findall(r'<color name="([a-z_]+)">(#[0-9A-Fa-f]{6})</color>',
                                (RES / "values-night" / "colors.xml").read_text(encoding="utf-8"))),
    }
    token_block = re.search(r"WIDGET_SURFACE: dict\[str, dict\[str, str\]\] = \{(.*?)\n\}",
                            preview_src, re.S)
    if not token_block:
        fail("preview-surface", "preview.py has no WIDGET_SURFACE token block")
    else:
        for mode, mapping in re.findall(r'"(day|night)": \{(.*?)\}', token_block.group(1), re.S):
            pairs = re.findall(r'"([a-z_]+)": "(#[0-9A-Fa-f]{6})"', mapping)
            if not pairs:
                fail("preview-surface", f"preview.py WIDGET_SURFACE[{mode}] has no colours")
            for name, value in pairs:
                token = "widget_" + name
                want = device_colors[mode].get(token)
                if want is None:
                    fail("preview-surface",
                         f"preview.py {mode} token {token} has no device resource")
                elif want.lower() != value.lower():
                    fail("preview-surface",
                         f"preview.py {mode} {token}={value} but colors.xml says {want}")

    # --- the picker preview mirrors the shipped composition ----------------
    for name, path in (("day", PREVIEW_LAYOUT), ("night", PREVIEW_LAYOUT_NIGHT)):
        if not path.is_file():
            fail("preview-layout", f"missing {path.relative_to(REPO)}")
    if PREVIEW_LAYOUT.is_file() and PREVIEW_LAYOUT_NIGHT.is_file():
        day_xml = PREVIEW_LAYOUT.read_text(encoding="utf-8")
        night_xml = PREVIEW_LAYOUT_NIGHT.read_text(encoding="utf-8")
        if day_xml != night_xml:
            # Only colors may differ: the structure has to stay the same shape. Comments
            # are stripped because each file explains itself in its own words.
            def shape(xml: str) -> str:
                return re.sub(r'@color/[a-z_]+', "@color/x", re.sub(r"<!--.*?-->", "", xml, flags=re.S))
            if shape(day_xml) != shape(night_xml):
                fail("preview-layout",
                     "layout-night/widget_preview.xml is not structurally identical to the day one")
        strings = {
            m.group(1): m.group(2)
            for m in re.finditer(r'<string name="([a-z_]+)">(.*?)</string>',
                                 STRINGS_XML.read_text(encoding="utf-8"))
        }
        for key in ("widget_header_title", "widget_request_update"):
            want = strings.get(key)
            if want is None:
                fail("preview-layout", f"strings.xml has no {key}")
            elif f'android:text="{want}"' not in day_xml:
                fail("preview-layout",
                     f"the picker preview does not show {key} ({want!r}); it must mirror the widget")
        for needed in ("ic_hermes_mark", "widget_status_dot", "widget_action_background",
                       "widget_preview_background"):
            if needed not in day_xml:
                fail("preview-layout", f"the picker preview does not use {needed}")
        radius_drawable = (RES / "drawable" / "widget_preview_background.xml").read_text(encoding="utf-8")
        if "@dimen/widget_corner_radius" not in radius_drawable:
            fail("preview-layout",
                 "the preview background must round with @dimen/widget_corner_radius (WS-2)")
        if re.search(r'android:radius="[0-9]+dp"', radius_drawable):
            fail("preview-layout", "the preview background hard-codes a radius literal (WS-2)")

    # --- loading state mirrors the shipped hierarchy -----------------------
    info = WIDGET_INFO.read_text(encoding="utf-8")
    if 'android:initialLayout="@layout/widget_loading"' not in info:
        fail("loading-state", "initialLayout must be the shipped-shape wireframe (WS-3)")
    elif LOADING_LAYOUT.is_file():
        loading = LOADING_LAYOUT.read_text(encoding="utf-8")
        for needed in ("widget_loading_bar", "widget_loading_button"):
            if needed not in loading:
                fail("loading-state", f"widget_loading.xml has no {needed} placeholder")
    else:
        fail("loading-state", "res/layout/widget_loading.xml is missing")
    if "@layout/widget_preview" not in info:
        fail("loading-state", "previewLayout must stay @layout/widget_preview (WD-1)")

    # --- the companion surfaces must not regress to literals ----------------
    app_res = REPO / "android" / "app" / "src" / "main" / "res"
    screens = [
        "layout/activity_main.xml",
        "layout/activity_pairing.xml",
        "layout/activity_settings.xml",
        "layout/activity_diagnostics.xml",
    ]
    for screen in screens:
        path = app_res / screen
        if not path.is_file():
            fail("app-surface", f"{screen} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        for literal in sorted(set(re.findall(r"#[0-9A-Fa-f]{6,8}\b", text))):
            fail("app-surface",
                 f"{screen} hard-codes {literal}; a light-only literal is what made the "
                 "pairing screen unreadable in dark mode (docs/APP_SURFACE.md)")
    for mode in ("values", "values-night"):
        colors_file = app_res / mode / "app_colors.xml"
        if not colors_file.is_file():
            fail("app-surface", f"{mode}/app_colors.xml is missing")
            continue
        tokens = set(re.findall(r'<color name="(app_[a-z_]+)"', colors_file.read_text(encoding="utf-8")))
        for required in ("app_surface", "app_surface_container", "app_surface_container_high",
                         "app_on_surface", "app_on_surface_variant", "app_primary", "app_on_primary"):
            if required not in tokens:
                fail("app-surface", f"{mode}/app_colors.xml has no {required}")
    body = source_of(REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                     "hermeswidget" / "PublicationActivity.kt")
    for literal in sorted(set(re.findall(r"Color\.(?:WHITE|BLACK)|Color\.rgb\(", body))):
        fail("app-surface",
             f"PublicationActivity.kt paints {literal}; the zoom view follows the device "
             "theme now, so white text would be invisible in light mode")
    if not (REPO / "docs" / "APP_SURFACE.md").is_file():
        fail("app-surface", "docs/APP_SURFACE.md must record what was adopted and what was not")
    # A button with no state layer gives no press feedback, and a tap then reads as a dead
    # control. That is the whole of "the buttons do nothing".
    for name in ("bg_button_filled", "bg_button_tonal", "bg_button_outlined"):
        drawable = app_res / "drawable" / f"{name}.xml"
        if not drawable.is_file():
            fail("app-surface", f"{name}.xml is missing")
            continue
        if "<ripple" not in drawable.read_text(encoding="utf-8"):
            fail("app-surface",
                 f"{name}.xml has no <ripple>; a button with no press state gives no "
                 "feedback and reads as broken")
    for mode in ("values", "values-night"):
        if "app_state_layer" not in (app_res / mode / "app_colors.xml").read_text(encoding="utf-8"):
            fail("app-surface", f"{mode}/app_colors.xml has no app_state_layer")
    # An action that can fail silently must not be able to.
    pinning = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
               "hermeswidget" / "WidgetPinning.kt").read_text(encoding="utf-8")
    diagnostics = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                   "hermeswidget" / "DiagnosticsActivity.kt").read_text(encoding="utf-8")
    if "offerNow" not in diagnostics or "offerNow" not in pinning:
        fail("app-action-feedback",
             "the Add widget button must call the manual offer path, which always reports")
    diagnostics_code = source_of(REPO / "android" / "app" / "src" / "main" / "java" / "com" /
                              "you" / "hermeswidget" / "DiagnosticsActivity.kt")
    if "ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS" in diagnostics_code:
        fail("app-action-feedback",
             "ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS opens nothing on modern Android; "
             "use the settings list and say so")
    settings = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                "hermeswidget" / "SettingsActivity.kt").read_text(encoding="utf-8")
    for needed in ("STATUS_POLL_MS = 2_000L", "onPause", "PairingStatus.read"):
        if needed not in settings:
            fail("app-action-feedback", f"SettingsActivity is missing {needed!r}")

    # --- the widget action must be reachable and observable ------------------
    widget_kt = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                 "hermeswidget" / "widget" / "HermesWidget.kt").read_text(encoding="utf-8")
    breakpoints = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                   "hermeswidget" / "widget" / "Breakpoints.kt").read_text(encoding="utf-8")
    # Only the LazyColumn matters: a width weight inside the footer Row is fine, so the
    # check is scoped to the scroll region's own modifier block, comments excluded.
    widget_code = source_of(REPO / "android" / "app" / "src" / "main" / "java" / "com" /
                           "you" / "hermeswidget" / "widget" / "HermesWidget.kt")
    lazy = re.search(r"LazyColumn\((.*?)\n        \)", widget_code, re.S)
    if lazy and "defaultWeight()" in lazy.group(1):
        fail("widget-action",
             "the scroll region must use an explicit height, not defaultWeight(): a "
             "weight-constrained lazy list can be measured past the cell, which clips the "
             "pinned action off the bottom (round 6)")
    if "spec.scrollHeightDp" not in widget_kt:
        fail("widget-action", "the LazyColumn must be bounded by BandSpec.scrollHeightDp")
    if "scrollHeightDp" not in breakpoints or "chromeHeightDp" not in breakpoints:
        fail("widget-action", "Breakpoints.kt must own the chrome/scroll arithmetic")
    if "setLastComposition" not in widget_kt:
        fail("widget-action",
             "the composition must record whether it drew an action, or 'no button' and "
             "'the tap went elsewhere' stay indistinguishable")
    if "getActionFires" not in diagnostics or "renderComposition" not in diagnostics:
        fail("widget-action",
             "DiagnosticsActivity must render the action trail; a press that produces no "
             "record must never again be unanswerable")
    receiver = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                "hermeswidget" / "widget" / "HermesWidgetReceiver.kt").read_text(encoding="utf-8")
    if "ActionCallbackBroadcastReceiver:callbackClass" not in receiver:
        fail("widget-action",
             "the receiver must count action broadcasts before Glance dispatches them")

    _SERVER_SRC = (PLUGIN / "server.py").read_text(encoding="utf-8")
    _STORE_SRC = (PLUGIN / "store.py").read_text(encoding="utf-8")

    # --- the geometry a composition is laid out for (round 8, P0) --------------
    size_gate = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                 "hermeswidget" / "widget" / "WidgetTheme.kt").read_text(encoding="utf-8")
    if "INSTANCE_INVENTORY" not in size_gate or "RESPONSIVE_SAMPLE" not in size_gate:
        fail("widget-geometry",
             "SizeGate must prefer the instance's reported geometry over the responsive "
             "sample: LocalSize is the sample Glance composed for, not the cell (round 8)")
    widget_code_for_geometry = source_of(REPO / "android" / "app" / "src" / "main" / "java" /
                                        "com" / "you" / "hermeswidget" / "widget" / "HermesWidget.kt")
    if "specForInstance(context, appWidgetId" not in widget_code_for_geometry:
        fail("widget-geometry",
             "provideGlance must resolve the geometry through SizeGate.specForInstance")
    if "idOf(id)" not in widget_kt:
        fail("widget-geometry",
             "provideGlance must resolve the instance id, or it cannot ask the launcher "
             "how big this cell is")
    for token in ("instanceDp", "composedHeightDp", "cellHeightDp"):
        if token not in size_gate and token not in widget_kt:
            fail("widget-geometry", f"the geometry trail is missing {token}")

    # --- attention reports must survive the route (round 8, P1) ---------------
    if "_ATTENTION_ROUTING_FIELDS" not in _SERVER_SRC:
        fail("attention-route",
             "the attention route must filter routing fields before calling the store; "
             "passing the whole body rejected every report with a 400")
    attention_tests = (PLUGIN / "tests" / "test_delivery.py").read_text(encoding="utf-8")
    if "AttentionRouteRoundTrip" not in attention_tests:
        fail("attention-route",
             "no test sends a realistic attention body through the route; the store-level "
             "tests cannot catch a route that rejects its own payload")

    # --- CI must be able to run at all (round 8, P1) -------------------------
    workflow = REPO / ".github" / "workflows" / "ci.yml"
    if not workflow.is_file():
        fail("ci-workflow", ".github/workflows/ci.yml is missing")
    else:
        if not (REPO / "scripts" / "check-workflow-yaml.py").is_file():
            fail("ci-workflow", "scripts/check-workflow-yaml.py is missing")
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = re.match(r"^\s*-\s+[A-Za-z_][\w-]*:\s*(.+)$", line)
            if not match:
                continue
            value = match.group(1)
            if value[:1] in "\"'" or value.rstrip().endswith(("|", ">", "-")):
                continue
            if ": " in value:
                fail("ci-workflow",
                     f"ci.yml:{number}: unquoted ': ' in {line.strip()!r} makes the value a "
                     "mapping; the whole workflow stops parsing and GitHub schedules nothing")

    # --- one press may mean exactly one thing (round 11) ------------------------
    if "HEADER_WITH_ACTION_DP" not in source_of(
        REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget"
        / "widget" / "Breakpoints.kt"
    ):
        fail("widget-action",
             "the chrome arithmetic must know the header carries the action (round 11)")
    # Two click targets, never nested. The widget root must not be a click target, and the
    # action must be composed in the header, which a scroll region cannot push out of the
    # cell. A press on the action reaching the surface instead is what the host saw as a
    # lone `review` event with no `request_update` (round 11).
    #
    # Both checks fail when they cannot find the region they are about, rather than
    # skipping: a gate that quietly stops looking is the disease this file keeps meeting.
    surface_start = widget_code.find("private fun PublicationSurface(")
    header_start = widget_code.find("private fun HeaderRow(")
    footer_start = widget_code.find("private fun FooterRow(")
    if surface_start < 0 or header_start < 0 or footer_start < 0:
        fail("widget-action",
             f"could not find PublicationSurface/HeaderRow/FooterRow in HermesWidget.kt "
             f"(offsets {surface_start}/{header_start}/{footer_start}); this check is "
             f"stale and must be updated rather than skipped")
    else:
        # Only the root Column's own modifier chain, not the whole function span: the
        # hero legitimately carries the drill-down and is defined inside that span.
        root_at = widget_code.find("Column(", surface_start, header_start)
        root_end = widget_code.find(") {", root_at) if root_at >= 0 else -1
        if root_at < 0 or root_end < 0:
            fail("widget-action",
                 "could not find the root Column of PublicationSurface; this check is stale "
                 "and must be updated rather than skipped")
        surface_head = widget_code[root_at:root_end] if root_at >= 0 and root_end > root_at else ""
        if "clickable(" in surface_head:
            fail("widget-action",
                 "the widget root must not be a click target while the action lives inside "
                 "it: a press on the action then reaches the surface and silently opens the "
                 "app (round 11)")
        header_block = widget_code[header_start:footer_start]
        # Both the guard and the action: a dead `if (false)` around the action would still
        # contain the call, and the button would be gone while the gate stayed green.
        if "requestUpdateAction()" not in header_block or "showsRequestAction" not in header_block:
            fail("widget-action",
                 "the request action must be composed in the header row: a Glance lazy "
                 "collection can measure past its height and push anything below it out of "
                 "the cell, which is how the button became unreachable (round 11)")
        # Below the header and before the body helper: the footer, and nothing else. The
        # action's own definition lives further down and must not count.
        body_start = widget_code.find("private fun PublicationBody(", footer_start)
        if "requestUpdateAction()" in widget_code[footer_start:body_start if body_start > 0 else len(widget_code)]:
            fail("widget-action",
                 "the action must not also be composed below the scroll region")

    # --- the client-side action trail must survive a Glance rename (round 11) ----
    receiver_src = source_of(
        REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget"
        / "widget" / "HermesWidgetReceiver.kt"
    )
    if "actionCallbackClass()" not in receiver_src or "endsWith(\":callbackClass\")" not in receiver_src:
        fail("widget-action",
             "the action-fire detector must match Glance's internal callback extra by "
             "suffix; matching one exact name means a Glance upgrade silences the trail "
             "with no signal, which is how it hid twice (round 11)")
    if "recordActionFired(context, \"unnamed(" not in receiver_src:
        fail("widget-action",
             "an action broadcast whose callback extra cannot be named must still be "
             "counted, or the trail goes quiet when the library changes")
    if "EXTRA_PARAMETERS" not in receiver_src:
        fail("widget-action",
             "the fallback detector needs the parameters extra to recognise an action "
             "broadcast at all")
    config_kt = source_of(
        REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget"
        / "net" / "Config.kt"
    )
    for needed in ("fun recordComposition", "fun compositionHistory",
                   "COMPOSITION_HISTORY_LIMIT"):
        if needed not in config_kt:
            fail("widget-action",
                 f"Config.{needed} is missing: the client trail must record every "
                 "composition, not only the last, or two presses cannot be compared")
    diagnostics_kt = source_of(
        REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget"
        / "DiagnosticsActivity.kt"
    )
    if "widgetTrailReport" not in diagnostics_kt or "copy_widget_trail" not in source_of(
        REPO / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_diagnostics.xml"
    ):
        fail("widget-action",
             "Diagnostics must offer the whole trail in one paste; asking for a screenshot "
             "of a phone is how two rounds were lost to transcription (round 11)")

    # --- the access log must actually emit (round 8, P2) -----------------------
    if "def configure_access_log" not in _SERVER_SRC:
        fail("access-log",
             "the server logger has no handler or level, so the access log is dropped at "
             "the default WARNING threshold")
    if "configure_access_log()" not in _SERVER_SRC:
        fail("access-log", "make_server must configure the access log")
    # The round-9 defect: guarding on the *root* logger's handlers. That is true in a bare
    # test process and false wherever the host has configured logging, which is the only
    # place the log matters — so the line was green in tests and dead in production.
    if "not logging.getLogger().handlers" in source_of(PLUGIN / "server.py"):
        fail("access-log",
             "the access log must not depend on whether the *root* logger has handlers: "
             "the host configures logging, so that guard disabled the line in production "
             "while every test passed (round 9)")
    if "propagate = False" not in _SERVER_SRC:
        fail("access-log",
             "our own handler must stop propagation, or a verbose host prints every line twice")
    if "HERMES_WIDGET_LOG" not in _SERVER_SRC:
        fail("access-log", "an access log on a busy server needs a documented opt-out")

    # --- tap observability: the trail exists on both sides and is documented ---
    for token, blob, name in (
        ("record_rejected_event", _SERVER_SRC, "server.py"),
        ("rejection_summary", _STORE_SRC, "store.py"),
        ('"rejections"', _STORE_SRC, "store.py"),
        ("instanceId", _SERVER_SRC, "server.py"),
    ):
        if token not in blob:
            fail("tap-observability", f"{name} has no {token}")
    for doc_name, doc_text in (("docs/SCHEMA.md", schema_doc),):
        if "event_rejections" not in doc_text:
            fail("tap-observability", f"{doc_name} does not document event_rejections")
    api_kt = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
              "hermeswidget" / "PublicationActivity.kt").read_text(encoding="utf-8")
    if "Outcome" not in api_kt or "recordActionOutcome" not in api_kt:
        fail("tap-observability",
             "the in-app tap must report a real outcome instead of one generic toast")
    callbacks_path = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                      "hermeswidget" / "widget" / "ActionCallbacks.kt")
    callbacks = callbacks_path.read_text(encoding="utf-8")
    if "instanceId" not in callbacks or "recordActionOutcome" not in callbacks:
        fail("tap-observability",
             "the widget's own action must send instanceId and record the outcome")
    # The credential exits are the ones that matter: a missing URL or token used to
    # `?: return` silently, which is exactly the round-5 failure mode.
    credential_exits = re.findall(
        r"SecureStore\.(?:baseUrl|token)\(context\) \?: Config\.getBackendUrl\(context\) \?: return",
        callbacks,
    )
    if credential_exits:
        fail("tap-observability", "the tap path still returns silently on a missing credential")
    for script, token in (("check-version-bump.py", "versionCode"),
                          ("release-evidence.py", "release-evidence")):
        if not (REPO / "scripts" / script).is_file():
            fail("release-gate", f"scripts/{script} is missing")
    if not (REPO / "docs" / "APK_RELEASE.md").is_file() or "Release evidence" not in (
        REPO / "docs" / "APK_RELEASE.md"
    ).read_text(encoding="utf-8"):
        fail("release-gate", "docs/APK_RELEASE.md has no generated release-evidence table")

    # --- client build reporting: docs, headers and the store agree ---------
    api = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
           "hermeswidget" / "net" / "HermesApi.kt").read_text(encoding="utf-8")
    store_src = (PLUGIN / "store.py").read_text(encoding="utf-8")
    server_src = (PLUGIN / "server.py").read_text(encoding="utf-8")
    for header in ("X-Hermes-App-Version", "X-Hermes-App-Build", "X-Hermes-Os-Sdk",
                   "X-Hermes-App-Sha"):
        for name, blob in (("HermesApi.kt", api), ("server.py", server_src),
                           ("docs/SCHEMA.md", schema_doc)):
            if header not in blob:
                fail("client-build", f"{name} never mentions {header}")
    for token in ("device_client_info", "publication_render_builds",
                  "record_device_client", "record_render_build"):
        if token not in store_src:
            fail("client-build", f"store.py has no {token}")
    if "renderedBy" not in store_src:
        fail("client-build", "publication_status must name the build that rendered a revision")
    if "ClientBuildReporting" not in (PLUGIN / "tests" / "test_delivery.py").read_text(encoding="utf-8"):
        fail("client-build", "no host test covers client build reporting")

    # --- the radius fallback lives in resources, not in Kotlin -------------
    if not DIMENS_XML.is_file() or "widget_corner_radius" not in DIMENS_XML.read_text(encoding="utf-8"):
        fail("corner-radius", "values/dimens.xml must hold the widget_corner_radius fallback (WS-2)")

    # --- docs/SCHEMA.md must not promise removed things -------------------
    # Naming a removed field is fine as long as the line says it is gone; what this
    # forbids is a doc that reads as if the field exists.
    for dead in ("visibleIf",):
        for line in (line for line in schema_doc.splitlines() if dead in line):
            if not re.search(r"removed|never|not implemented|no longer|gone", line, re.I):
                fail("schema-doc",
                     f"docs/SCHEMA.md mentions `{dead}` without saying it is gone: {line.strip()!r}")

    if failures:
        print(f"contract parity FAILED ({len(failures)} check(s)):\n")
        for line in failures:
            print(f"  {line}")
        print("\nOne registry (layout.schema.json); update the mirrors it names.")
        return 1

    print(
        "contract parity OK: "
        f"{len(types_schema)} node types, {len(kinds_schema)} action kinds, "
        f"{len(styles_schema)} text styles, "
        f"{sum(len(v) for v in fields_schema.values())} per-type fields, "
        f"{len(envelope)} envelope fields, "
        "widget surface (bands, pinned footer, theme tokens, preview, loading state)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
