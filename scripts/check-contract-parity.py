#!/usr/bin/env python3
"""Check the live publication contract against the Android client.

The former v2 layout checks have been removed with that protocol. These checks cover
the contract that remains: publication limits, action and media allowlists, event
sources, preview size bands, and the shared refresh interval.
"""
from __future__ import annotations

import ast
import math
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = REPO / "hermes-plugin" / "hermes-widget"
ANDROID = REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget"

sys.path.insert(0, str(PLUGIN))
import bands  # noqa: E402
import publication  # noqa: E402

PUBLICATION_KT = (ANDROID / "net" / "Publication.kt").read_text(encoding="utf-8")
API_KT = (ANDROID / "net" / "HermesApi.kt").read_text(encoding="utf-8")
BREAKPOINTS_KT = (ANDROID / "widget" / "Breakpoints.kt").read_text(encoding="utf-8")
TYPO_KT = (ANDROID / "widget" / "Typo.kt").read_text(encoding="utf-8")
WORKER_KT = (ANDROID / "work" / "RefreshWorker.kt").read_text(encoding="utf-8")
REQUEST_EVENT_KT = (ANDROID / "net" / "RequestUpdateEvent.kt").read_text(encoding="utf-8")
SERVER_PY = (PLUGIN / "server.py").read_text(encoding="utf-8")

failures: list[str] = []


def check(name: str, expected: object, actual: object) -> None:
    if expected != actual:
        failures.append(f"{name}: expected {expected!r}, found {actual!r}")


def int_constant(source: str, name: str) -> int | None:
    match = re.search(rf"\b{name}\s*=\s*([0-9][0-9_]*(?:\s*\*\s*[0-9][0-9_]*)*)", source)
    if not match:
        return None
    return math.prod(int(part.strip().replace("_", "")) for part in match.group(1).split("*"))


def quoted_set(source: str, pattern: str, label: str) -> set[str] | None:
    match = re.search(pattern, source, re.S)
    if not match:
        failures.append(f"{label}: contract declaration was not found")
        return None
    return set(re.findall(r'"([^"\n]+)"', match.group(1)))


def python_set_literal(source: str, name: str) -> set[str] | None:
    """Read a module-level tuple/set literal from server.py without importing it."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        failures.append(f"server.py: could not parse source: {exc}")
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
                value = value.args[0] if value.args else None
            if value is None:
                break
            try:
                result = ast.literal_eval(value)
            except (ValueError, TypeError):
                break
            if isinstance(result, (tuple, list, set, frozenset)):
                return set(result)
    failures.append(f"server.py: {name} must be a literal tuple/set")
    return None


def band_budgets(source: str) -> dict[str, int] | None:
    enum = re.search(r"enum class WidgetBand\((.*?)\n\s*val showsSummary", source, re.S)
    if not enum:
        failures.append("Breakpoints.kt: WidgetBand enum was not found")
        return None
    return {
        name.lower(): int(body)
        for name, body in re.findall(r"\b(XS|S|M|L)\([^\n]*?,\s*(\d+),\s*(?:true|false)\s*\)", enum.group(1))
    }


def refresh_interval_seconds(source: str) -> int | None:
    match = re.search(
        r"PeriodicWorkRequestBuilder<RefreshWorker>\(\s*(\d+)\s*,\s*TimeUnit\.(SECONDS|MINUTES|HOURS)\s*\)",
        source,
    )
    if not match:
        failures.append("RefreshWorker.kt: periodic refresh interval was not found")
        return None
    value = int(match.group(1))
    return value * {"SECONDS": 1, "MINUTES": 60, "HOURS": 3600}[match.group(2)]


def compare_limits() -> None:
    limits = {
        "MAX_TITLE_BYTES": publication.MAX_TITLE_BYTES,
        "MAX_SUMMARY_BYTES": publication.MAX_SUMMARY_BYTES,
        "MAX_TEXT_BYTES": publication.MAX_TEXT_BYTES,
        "MAX_RASTER_BYTES": publication.MAX_ASSET_BYTES,
        "MAX_RASTER_PIXELS": publication.MAX_RASTER_PIXELS,
        "MAX_RASTER_DIMENSION": publication.MAX_RASTER_DIMENSION,
    }
    for name, expected in limits.items():
        actual = int_constant(PUBLICATION_KT, name)
        if name == "MAX_RASTER_BYTES":
            # HermesApi owns the transfer cap; Publication.kt separately validates metadata.
            api_limit = int_constant(API_KT, "MAX_ASSET_BYTES")
            check("Android HermesApi.MAX_ASSET_BYTES", expected, api_limit)
        check(f"Android Publication.kt {name}", expected, actual)
    check(
        "Android publication JSON cap",
        int_constant(API_KT, "MAX_PUBLICATION_BYTES"),
        int_constant(PUBLICATION_KT, "MAX_PUBLICATION_JSON_BYTES"),
    )
    version = re.search(r"require\(version\s*==\s*(\d+)\)", PUBLICATION_KT)
    check(
        "Android publication version",
        publication.PUBLICATION_VERSION,
        int(version.group(1)) if version else None,
    )


def compare_action_and_media_contracts() -> None:
    kinds = quoted_set(
        PUBLICATION_KT,
        r'require\(kind in setOf\((.*?)\)\s*\)\s*\{\s*"invalid publication action"',
        "Android action kinds",
    )
    check("publication action kinds", set(publication.ACTION_KINDS), kinds)
    classes = quoted_set(
        PUBLICATION_KT,
        r'require\(actionClass in setOf\((.*?)\)\s*\)\s*\{\s*"invalid publication action class"',
        "Android action classes",
    )
    check("publication action classes", set(publication.ACTION_CLASSES), classes)
    media = quoted_set(
        PUBLICATION_KT,
        r'require\(it in setOf\((.*?)\)\s*\)\s*\{\s*"unsupported image type"',
        "Android media types",
    )
    check("publication media types", set(publication.SUPPORTED_MEDIA_TYPES) | {"image/svg+xml"}, media)


def compare_event_sources() -> None:
    server_sources = python_set_literal(SERVER_PY, "EVENT_SOURCES")
    android_sources = set(re.findall(
        r"const val SOURCE_(?:WIDGET_ACTION|IN_APP_BUTTON)\s*=\s*\"([^\"]+)\"",
        REQUEST_EVENT_KT,
    ))
    check("request event sources", server_sources, android_sources)


def compare_size_bands() -> None:
    thresholds = {
        "BAND_XS_MAX_HEIGHT_DP": "XS_MAX_HEIGHT_DP",
        "BAND_S_MAX_HEIGHT_DP": "S_MAX_HEIGHT_DP",
        "BAND_M_MAX_HEIGHT_DP": "M_MAX_HEIGHT_DP",
        "SINGLE_COLUMN_MAX_WIDTH_DP": "SINGLE_COLUMN_MAX_WIDTH_DP",
    }
    for python_name, kotlin_name in thresholds.items():
        check(
            f"widget size threshold {python_name}",
            getattr(bands, python_name),
            int_constant(BREAKPOINTS_KT, kotlin_name),
        )
    kotlin_budgets = band_budgets(BREAKPOINTS_KT)
    check("widget body line budgets", bands.BAND_BODY_LINES, kotlin_budgets)


def compare_widget_surface_parity() -> None:
    """The widget's action height and hero size must match everywhere they appear.

    HermesWidget.kt (Glance), widget_preview.xml (day/night twins), widget_loading.xml
    and preview.py (host PNGs) each hard-code these numbers; the bundled SKILL.md
    documents them for the agent. A drift means the picker, the phone, the host preview
    and the agent disagree about the same surface.
    """
    res = REPO / "android" / "app" / "src" / "main" / "res"
    widget_kt = (ANDROID / "widget" / "HermesWidget.kt").read_text(encoding="utf-8")
    # HermesWidget.kt has other heights (header reservation mirrors the action); the
    # action rows themselves are the contract.
    action_block = re.findall(
        r"private fun (?:RequestActionRow|EmptyState)\([\s\S]*?\n\}", widget_kt,
    )
    action_heights = set()
    for block in action_block:
        # The action Text chains height -> background -> cornerRadius -> clickable;
        # spacers and reservations do not, so they are excluded by construction.
        action_heights.update(
            int(v) for v in re.findall(r"\.height\((\d+)\.dp\)\s*\n\s*\.background", block)
        )
    preview_py = (PLUGIN / "preview.py").read_text(encoding="utf-8")
    preview_footers = set(int(v) for v in re.findall(r"footer_h = (\d+) \* scale", preview_py))
    preview_titles = set(int(v) for v in re.findall(r"title_size = (\d+) \* scale", preview_py))
    xml_heights: set[int] = set()
    for name in ("layout/widget_preview.xml", "layout-night/widget_preview.xml"):
        text = (res / name).read_text(encoding="utf-8")
        # The footer action button: 48dp tall TextView using the action background.
        if "widget_action_background" in text:
            match = re.search(
                r"<TextView[^>]*android:layout_height=\"(\d+)dp\"[^>]*android:background=\"@drawable/widget_action_background\"",
                text,
                re.S,
            )
            if match is None:
                match = re.search(
                    r"<TextView[^>]*android:background=\"@drawable/widget_action_background\"[^>]*android:layout_height=\"(\d+)dp\"",
                    text,
                    re.S,
                )
            if match is None:
                failures.append(f"{name}: footer action button was not found")
            else:
                xml_heights.add(int(match.group(1)))
    loading = (res / "layout/widget_loading.xml").read_text(encoding="utf-8")
    if "widget_loading_button" in loading:
        match = re.search(
            r"<View[^>]*android:layout_height=\"(\d+)dp\"[^>]*android:background=\"@drawable/widget_loading_button\"",
            loading,
            re.S,
        )
        if match is None:
            failures.append("widget_loading.xml: action placeholder was not found")
        else:
            xml_heights.add(int(match.group(1)))
    heights = action_heights | preview_footers | xml_heights
    check("widget action height parity (Glance/preview XML/host preview)", 1, len(heights))
    if len(heights) == 1:
        check("widget action height is the 48dp MD3 M-size", 48, next(iter(heights)))
    # Hero size: Typo.kt title step vs host preview vs picker preview XML.
    typo_sizes = {
        name: int(size)
        for name, size in re.findall(r"\"(title|body|label|caption)\" to Spec\((\d+),", TYPO_KT)
    }
    check("Typo.kt title step is the on-scale 16sp hero", 16, typo_sizes.get("title"))
    check("host preview hero matches the Typo title step", {typo_sizes.get("title")}, preview_titles)
    for name in ("layout/widget_preview.xml", "layout-night/widget_preview.xml"):
        text = (res / name).read_text(encoding="utf-8")
        sizes = set(int(v) for v in re.findall(r"android:textSize=\"(\d+)sp\"", text))
        # Every picker size must be an MD3 step (11/12/14/16/22/...): 18sp is off-scale.
        check(f"{name}: picker sizes are all on the MD3 scale", True, 18 not in sizes)
        check(
            f"{name}: picker hero uses the Typo title step",
            True,
            f"android:textSize=\"{typo_sizes.get('title')}sp\"" in text,
        )


def compare_agent_skill() -> None:
    """The bundled agent skill must exist, parse, and match the shipped API.

    The skill is what cron-driven proactive runs reason from; an empty or stale skill
    means unattended runs improvise. This gates existence, frontmatter, removed-tool
    references and the documented type scale.
    """
    skill = PLUGIN / "skills" / "widget" / "SKILL.md"
    if not skill.is_file():
        failures.append("skills/widget/SKILL.md: file is missing")
        return
    text = skill.read_text(encoding="utf-8")
    if len(text.strip()) < 500:
        failures.append(
            f"skills/widget/SKILL.md: file is {len(text.strip())} chars; "
            "an empty skill leaves proactive runs without rules"
        )
        return
    frontmatter = text.split("---")
    check(
        "skill frontmatter names hermes-widget",
        True,
        len(frontmatter) >= 3 and "name: hermes-widget" in frontmatter[1],
    )
    for removed in ("widget_update", "widget_validate", "fixtures/golden"):
        if removed in text:
            failures.append(
                f"skills/widget/SKILL.md: references removed v2 API {removed!r}"
            )
    for tool in ("widget_publish", "widget_preview", "widget_status", "widget_watch",
                 "widget_read_intents", "widget_resolve_intent", "widget_ask"):
        if tool not in text:
            failures.append(f"skills/widget/SKILL.md: current tool {tool!r} is not mentioned")
    typo_sizes = {
        name: int(size)
        for name, size in re.findall(r"\"(title|body|label|caption)\" to Spec\((\d+),", TYPO_KT)
    }
    table_sizes = set(int(v) for v in re.findall(r"\| `(?:title|body|label|caption)` \| (\d+)sp", text))
    check(
        "skill type table matches the Typo.kt scale",
        set(typo_sizes.values()),
        table_sizes,
    )


def main() -> int:
    compare_limits()
    compare_action_and_media_contracts()
    compare_event_sources()
    compare_size_bands()
    compare_widget_surface_parity()
    compare_agent_skill()
    check("widget refresh interval seconds", publication.POLL_INTERVAL_SECONDS, refresh_interval_seconds(WORKER_KT))
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("Contract parity: publication limits, actions, media, event sources, size bands, refresh interval PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
