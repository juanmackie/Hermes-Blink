#!/usr/bin/env python3
"""Deterministic, conservative evidence audit for the widget UX plan."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main"


def text(path: str) -> str:
    p = ANDROID / path
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def has(*needles: str, path: str) -> int:
    body = text(path)
    return int(all(needle in body for needle in needles))


def contrast(hex_a: str, hex_b: str) -> float:
    def lum(value: str) -> float:
        rgb = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        rgb = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    a, b = lum(hex_a), lum(hex_b)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def main() -> int:
    widget = text("java/com/you/hermeswidget/widget/HermesWidget.kt")
    breakpoints = text("java/com/you/hermeswidget/widget/Breakpoints.kt")
    theme = text("java/com/you/hermeswidget/widget/WidgetTheme.kt")
    widget_package = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "android/app/src/main/java/com/you/hermeswidget/widget").glob("*.kt")
    )
    dims = text("java/com/you/hermeswidget/widget/WidgetDimensions.kt")
    typo = text("java/com/you/hermeswidget/widget/Typo.kt")
    provider = text("res/xml/hermes_widget_info.xml")
    values = text("res/values/strings.xml")
    night = text("res/values-night/strings.xml")
    tests = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "android/app/src/test").rglob("*.kt"))
    plugin_tests = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "hermes-plugin/hermes-widget/tests").rglob("*.py"))
    checks = {
        "responsive_size_mode": has("SizeMode.Responsive", path="java/com/you/hermeswidget/widget/HermesWidget.kt") and has("LocalSize", path="java/com/you/hermeswidget/widget/HermesWidget.kt"),
        # The ladder lives in its own file (Breakpoints.kt) and the pinned footer is a
        # sibling of the LazyColumn, not one of its items: both are structural facts.
        # One ladder (Breakpoints.kt), consumed by the widget, with the width guard and a
        # size-class ladder that shares the same 245dp column edge.
        "band_ladder": all(token in breakpoints for token in (
            "WidgetBand", "heightDp < XS_MAX_HEIGHT_DP", "SINGLE_COLUMN_MAX_WIDTH_DP",
            "variantKey", "imageHeightDp",
        ))
        and "BandSpec" in widget
        and "WIDE_MIN_DP = 245" in dims,
        "footer_pinned": int(
            "LazyColumn" in widget
            and "FooterRow" in widget
            # The request action must appear after the LazyColumn block closes, i.e. outside it.
            and "request_update" in widget.split("LazyColumn(", 1)[-1].split("\n        }", 1)[-1]
        ),
        "header": has("Header", path="java/com/you/hermeswidget/widget/HermesWidget.kt"),
        "system_radius": int(
            ("system_app_widget_background_radius" in widget_package
             or "system_app_widget_inner_radius" in widget_package)
            and "getIdentifier" in theme
            and "widget_corner_radius" in text("res/values/dimens.xml")
        ),
        "theme_tokens": int("Theme.Material3" in text("res/values/themes.xml") or "WidgetTheme" in text("res/values/themes.xml")),
        "dark_resources": int((ROOT / "android/app/src/main/res/values-night").is_dir() or (ROOT / "android/app/src/main/res/values-night").is_dir()),
        "representative_preview": has("widget_preview", path="res/xml/hermes_widget_info.xml"),
        "loading_state": int("widget_loading" in provider),
        "contrast_gate": int("contrast" in tests.lower() and "4.5" in tests),
        "size_tests": int("WidgetBand" in tests or "Breakpoint" in tests),
        "publisher_budget": int("band" in plugin_tests.lower() and "line" in plugin_tests.lower()),
        "dead_code_clean": int("ErrorStateNode" not in text("java/com/you/hermeswidget/widget/Renderer.kt")),
    }
    # Deliberately score evidence, not intent. Contrast is a separate numeric guard.
    light_surface, dark_surface = "#FFFFFF", "#1C1C1E"
    primary = "#000000"
    secondary = "#5F5F66" if '"#5F5F66"' in typo else "#8E8E93"
    secondary_dark = "#AEAEB2"
    contrast_ok = int(
        contrast(primary, light_surface) >= 4.5
        and contrast(secondary, light_surface) >= 4.5
        and contrast(secondary_dark, dark_surface) >= 4.5
    )
    checks["contrast_values"] = contrast_ok
    weights = {
        "responsive_size_mode": 12, "band_ladder": 12, "footer_pinned": 10, "header": 7,
        "system_radius": 8, "theme_tokens": 10, "dark_resources": 7,
        "representative_preview": 7, "loading_state": 6, "contrast_gate": 7,
        "size_tests": 6, "publisher_budget": 5, "dead_code_clean": 3, "contrast_values": 0,
    }
    score = sum(weights[k] for k, v in checks.items() if v) if "contrast_values" in weights else sum(weights[k] for k, v in checks.items() if v)
    print(f"METRIC widget_quality_score={score}")
    for key, value in checks.items():
        print(f"METRIC check_{key}={value}")
    print(f"METRIC contrast_secondary_light={contrast(secondary, light_surface):.3f}")
    print(f"METRIC contrast_secondary_dark={contrast(secondary_dark, dark_surface):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
