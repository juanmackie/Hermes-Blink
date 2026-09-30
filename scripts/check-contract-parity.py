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


def main() -> int:
    compare_limits()
    compare_action_and_media_contracts()
    compare_event_sources()
    compare_size_bands()
    check("widget refresh interval seconds", publication.POLL_INTERVAL_SECONDS, refresh_interval_seconds(WORKER_KT))
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("Contract parity: publication limits, actions, media, event sources, size bands, refresh interval PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
