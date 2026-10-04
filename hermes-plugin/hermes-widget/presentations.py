"""Bounded structured data and deterministic Pillow visuals owned by Blink."""

from __future__ import annotations

import io
import json
import math
from typing import Any

TYPES = ("metric", "progress", "comparison", "chart", "timeline")
MAX_PRESENTATION_BYTES = 32 * 1024
MAX_ITEMS = 32


def normalize(raw: Any) -> dict:
    try:
        from .publication import PublicationInputError, PublicationTooLarge
    except ImportError:
        from publication import PublicationInputError, PublicationTooLarge

    def fail(message: str) -> None:
        raise PublicationInputError(message)

    def label(value: Any, name: str, required: bool = True) -> str:
        if not isinstance(value, str) or (required and not value.strip()) or len(value) > 120:
            fail(f"{name} must be a string of at most 120 characters")
        if any(ord(c) < 32 for c in value):
            fail(f"{name} must be a single line")
        return value.strip()

    def number(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            fail(f"{name} must be a finite number")
        try:
            result = float(value)
        except (OverflowError, ValueError):
            fail(f"{name} must be a finite number")
        if not math.isfinite(result) or abs(result) > 1e15:
            fail(f"{name} must be finite and within +/- 1e15")
        return result

    if not isinstance(raw, dict):
        fail("presentation must be an object")
    try:
        encoded = json.dumps(raw, allow_nan=False).encode()
    except (ValueError, TypeError, OverflowError) as exc:
        raise PublicationInputError("presentation must contain finite JSON data") from exc
    if len(encoded) > MAX_PRESENTATION_BYTES:
        raise PublicationTooLarge("presentation exceeds the 32 KiB limit")
    kind = raw.get("type")
    if kind not in TYPES:
        fail(f"presentation.type must be one of {list(TYPES)}")
    result = {"type": kind, "unit": label(raw.get("unit", ""), "unit", False)}
    allowed = {"type", "unit"}
    if kind in {"metric", "progress"}:
        allowed |= {"label", "value", "target"}
        result.update(
            label=label(raw.get("label"), "label"), value=number(raw.get("value"), "value")
        )
        if kind == "progress" or "target" in raw:
            target = number(raw.get("target"), "target")
            if target <= 0 or result["value"] < 0:
                fail("progress requires a positive target and nonnegative value")
            if not math.isfinite(result["value"] / target):
                fail("progress ratio must be finite")
            result["target"] = target
    else:
        key = "rows" if kind == "comparison" else "events" if kind == "timeline" else "points"
        allowed.add(key)
        values = raw.get(key)
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_ITEMS:
            fail(f"{key} must contain 1..{MAX_ITEMS} items")
        items = []
        for i, value in enumerate(values):
            if not isinstance(value, dict):
                fail(f"{key}[{i}] must be an object")
            item = {"label": label(value.get("label"), f"{key}[{i}].label")}
            fields = (
                {"label", "date", "detail"} if kind == "timeline" else {"label", "value", "detail"}
            )
            if set(value) - fields:
                fail(f"unknown fields in {key}[{i}]")
            if kind == "timeline":
                item["date"] = label(value.get("date"), f"{key}[{i}].date")
            else:
                item["value"] = number(value.get("value"), f"{key}[{i}].value")
            if "detail" in value:
                item["detail"] = label(value["detail"], f"{key}[{i}].detail", False)
            items.append(item)
        result[key] = items
        if kind == "chart":
            allowed.add("style")
            style = raw.get("style", "bar")
            if not isinstance(style, str) or style not in {"bar", "line"}:
                fail("chart.style must be bar or line")
            result["style"] = style
    if set(raw) - allowed:
        fail(f"unknown presentation fields: {sorted(set(raw) - allowed)}")
    return result


def font(size: int):
    from PIL import ImageFont

    # Pillow's bundled Aileron font is independent of host-installed fonts.
    return ImageFont.load_default(size=max(8, size))


def visible_items(presentation: dict, band: str, layout: str) -> tuple[list[dict], int]:
    items = presentation.get("rows", presentation.get("events", presentation.get("points", [])))
    if band == "full":
        capacity = len(items)
    elif band == "m":
        capacity = 3 if layout == "narrow" else 2
    else:
        capacity = 4 if layout == "narrow" else 6
    return items[:capacity], max(0, len(items) - capacity)


def render(
    presentation: dict,
    width: int,
    height: int,
    *,
    band: str = "l",
    layout: str = "wide",
    palette: str = "light",
) -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        try:
            from .publication import PublicationInputError
        except ImportError:
            from publication import PublicationInputError
        raise PublicationInputError(
            "Pillow is required to render presentations; install host requirements"
        ) from exc
    dark = palette == "dark"
    bg, ink, muted, accent = (
        ("#141218", "#E6E0E9", "#CAC4D0", "#D0BCFF")
        if dark
        else ("#FEF7FF", "#1D1B20", "#49454F", "#6750A4")
    )
    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    pad = max(8, round(min(width * 0.035, height * 0.035)))
    size = max(12, min(round(width / (14 if layout == "narrow" else 28)), round(height / 5)))
    face = font(size)

    def text(x, y, value, color=ink, typeface=face, available=None):
        available = available or width - x - pad
        value = str(value)
        if draw.textlength(value, font=typeface) > available:
            while value and draw.textlength(value + "...", font=typeface) > available:
                value = value[:-1]
            value += "..."
        draw.text((x, y), value, fill=color, font=typeface)

    def value(v):
        return f"{v:g}" + (f" {presentation['unit']}" if presentation["unit"] else "")

    kind = presentation["type"]
    items, hidden = visible_items(presentation, band, layout)
    if kind in {"metric", "progress"}:
        text(pad, pad, presentation["label"], muted)
        text(pad, height * 0.25, value(presentation["value"]), accent, font(size * 2))
        if "target" in presentation:
            target = presentation["target"]
            text(pad, height * 0.6, f"of {value(target)} ({presentation['value'] / target:.0%})")
            y = round(height * 0.83)
            draw.rounded_rectangle(
                (pad, y, width - pad, y + max(4, size // 2)), radius=3, fill=muted
            )
            end = pad + (width - 2 * pad) * min(1, presentation["value"] / target)
            if end > pad:
                draw.rounded_rectangle((pad, y, end, y + max(4, size // 2)), radius=3, fill=accent)
    elif kind == "chart" and presentation["style"] == "line":
        values = [i["value"] for i in items]
        lo, hi = min(0, min(values)), max(0, max(values))
        span = hi - lo or 1
        # Full images include a labeled value table, so every plotted point is inspectable.
        plot_height = min(height, 600) if band == "full" else height
        top, bottom = pad + size * 2, plot_height - pad - size * 2
        text(pad, pad, f"{value(lo)} to {value(hi)}", muted)
        coords = [
            (
                pad + (width - 2 * pad) * i / max(1, len(items) - 1),
                bottom - (v - lo) / span * (bottom - top),
            )
            for i, v in enumerate(values)
        ]
        if len(coords) > 1:
            draw.line(coords, fill=accent, width=max(2, size // 6))
        for x, y in coords:
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=accent)
        text(pad, bottom + size, items[0]["label"], available=(width - 2 * pad) // 2)
        text(width // 2, bottom + size, items[-1]["label"])
        if band == "full":
            for index, item in enumerate(items):
                text(pad, plot_height + index * 55, f"{item['label']}  {value(item['value'])}")
                if item.get("detail"):
                    text(width // 2, plot_height + index * 55, item["detail"], muted)
        elif hidden:
            text(width // 2, pad, f"+{hidden} points in details", muted)
    else:
        row_height = (height - 2 * pad - size * (1 if hidden else 0)) / max(1, len(items))
        row_font = font(min(size, max(10, int(row_height / (3 if kind == "timeline" else 1.9)))))
        values = [i.get("value", 0) for i in items]
        lo, hi = min(0, min(values)), max(0, max(values))
        span = hi - lo or 1
        for index, item in enumerate(items):
            y = pad + index * row_height
            if kind == "timeline":
                text(pad, y, f"{item['date']}  {item['label']}", typeface=row_font)
                if item.get("detail"):
                    text(pad, y + row_height * 0.45, item["detail"], muted, typeface=row_font)
            else:
                text(pad, y, f"{item['label']}  {value(item['value'])}", typeface=row_font)
                if band == "full" and item.get("detail"):
                    text(pad, y + row_height * 0.38, item["detail"], muted, typeface=font(18))
                bar_y = y + row_height * 0.8
                zero = pad + (width - 2 * pad) * (0 - lo) / span
                end = pad + (width - 2 * pad) * (item["value"] - lo) / span
                draw.line(
                    (zero, bar_y, end, bar_y), fill=accent, width=max(2, int(row_height * 0.12))
                )
                draw.line((zero, bar_y - 4, zero, bar_y + 4), fill=muted, width=1)
        if hidden:
            text(pad, height - pad - size, f"+{hidden} more; open details", muted)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def compile_presentation(raw: Any):
    try:
        from .publication import PreparedAsset, validate_raster
    except ImportError:
        from publication import PreparedAsset, validate_raster
    import hashlib

    presentation = normalize(raw)

    def asset(width, height, **options):
        data = render(presentation, width, height, **options)
        media, w, h = validate_raster(data, ".png")
        return PreparedAsset(media, data, w, h, hashlib.sha256(data).hexdigest())

    # The primary contains all rows/events for older clients and expanded view.
    count = len(
        presentation.get("rows", presentation.get("events", presentation.get("points", [])))
    )
    primary_height = max(600, count * 80)
    if presentation.get("style") == "line":
        primary_height = 600 + count * 55 + 40
    primary = asset(1200, primary_height, band="full")
    variants = {}
    for band in ("m", "l"):
        for layout in ("narrow", "wide"):
            for palette in ("light", "dark"):
                key = f"{band}-{layout}-{palette}"
                variants[key] = asset(
                    480 if layout == "narrow" else 960,
                    (320 if layout == "narrow" else 192) if band == "m" else 480,
                    band=band,
                    layout=layout,
                    palette=palette,
                )
    return presentation, primary, variants


def select_variant(publication: dict, band: str, narrow: bool, dark: bool) -> dict:
    key = f"{band}-{'narrow' if narrow else 'wide'}-{'dark' if dark else 'light'}"
    return publication.get("visualVariants", {}).get(key, publication.get("content", {}))
