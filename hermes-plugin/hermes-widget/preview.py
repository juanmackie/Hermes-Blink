"""Offline previews of the text, SVG, and raster publication contract."""
from __future__ import annotations

import base64
import hashlib
import io
import pathlib
import functools
import re
import sys
import struct
import textwrap
import zlib
from typing import Any, Callable
from xml.sax.saxutils import escape as xml_escape

try:  # normal path: imported as part of the hermes-widget plugin package
    from .bands import SINGLE_COLUMN_MAX_WIDTH_DP, size_band
except ImportError:  # pragma: no cover - direct import from scripts/tests
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from bands import SINGLE_COLUMN_MAX_WIDTH_DP, size_band  # type: ignore

# The publication surface tokens, mirroring android/app/src/main/res/values/colors.xml and
# values-night/colors.xml. scripts/check-contract-parity.py fails if these drift from the
# device resources, so the preview cannot flatter a palette the phone would reject.
WIDGET_SURFACE: dict[str, dict[str, str]] = {
    # The Material 3 baseline roles, same values as android/app/src/main/res/values*/colors.xml.
    "day": {
        "surface": "#FEF7FF",          # neutral 98
        "on_surface": "#1D1B20",       # neutral 10
        "secondary": "#49454F",        # neutral-variant 30
        "accent": "#6750A4",           # primary 40
        "on_accent": "#FFFFFF",        # primary 100
        "status_fresh": "#1E7D3C",
    },
    "night": {
        "surface": "#141218",          # neutral 6
        "on_surface": "#E6E0E9",       # neutral 90
        "secondary": "#CAC4D0",        # neutral-variant 80
        "accent": "#D0BCFF",           # primary 80
        "on_accent": "#381E72",        # primary 20
        "status_fresh": "#7BD88F",
    },
}

# What the device renders per band (android/.../widget/Breakpoints.kt). The preview draws
# the same first screen, including the pinned footer, so a screenshot of this PNG is a
# screenshot of what a user sees before scrolling.
BAND_PLAN: dict[str, dict[str, Any]] = {
    "xs": {"hero_lines": 1, "summary": False, "body_lines": 0, "footer": False, "ticker": False},
    "s": {"hero_lines": 2, "summary": True, "body_lines": 0, "footer": False, "ticker": False},
    "m": {"hero_lines": 2, "summary": True, "body_lines": 3, "footer": True, "ticker": False},
    "l": {"hero_lines": 2, "summary": True, "body_lines": 8, "footer": True, "ticker": True},
}
PREVIEW_SCALE = 2  # dp -> px, so a preview PNG is retina-sized

ELLIPSIS = "\u2026"
ELLIPSIS_STUB = "..."

PUBLICATION_SIZES: dict[str, tuple[int, int]] = {
    "2x2": (120, 120),
    "4x2": (270, 120),
    "2x4": (120, 270),
    "4x4": (270, 270),
}
_PREVIEW_MAX_BYTES = 2 * 1024 * 1024
_PREVIEW_MAX_COUNT = 16
_PREVIEW_MAX_PIXELS = 4_000_000


def build_preview_publication(raw: Any, widget_id: str) -> dict[str, Any]:
    """Normalize a proposed publication into the preview endpoint's wire envelope."""
    if not isinstance(raw, dict):
        raise ValueError("publication must be a JSON object")

    proposed = raw
    content = proposed.get("content")
    source_keys = ("text", "svg", "file_path", "filePath")
    if isinstance(content, dict) and not any(key in proposed for key in source_keys):
        file_path = content.get("filePath", content.get("file_path"))
        if file_path:
            proposed = {**proposed, "filePath": file_path}
            proposed.pop("content", None)
        else:
            return {**proposed, "widgetId": widget_id}

    try:
        from .publication import prepare_publication
        from .timeutil import _now
    except ImportError:  # direct import from scripts/tests
        from publication import prepare_publication  # type: ignore
        from timeutil import _now  # type: ignore

    prepared = prepare_publication(
        title=proposed.get("title"),
        summary=proposed.get("summary"),
        text=proposed.get("text"),
        svg=proposed.get("svg"),
        file_path=proposed.get("file_path", proposed.get("filePath")),
        expires_at=proposed.get("expires_at", proposed.get("expiresAt")),
        ttl_seconds=proposed.get("ttl_seconds", proposed.get("ttlSeconds")),
        max_age_seconds=proposed.get("max_age_seconds", proposed.get("maxAgeSeconds")),
        priority=proposed.get("priority", "normal"),
        item_id=proposed.get("item_id", proposed.get("itemId")),
        actions=proposed.get("actions"),
        provenance=proposed.get("provenance"),
        dark_palette=proposed.get("dark_palette", proposed.get("darkPalette", False)),
        variants=proposed.get("variants"),
    )
    if prepared.kind == "text":
        body: dict[str, Any] = {
            "type": "text",
            "mediaType": "text/plain; charset=utf-8",
            "text": prepared.text,
        }
    else:
        assert prepared.asset is not None
        body = {
            "type": "image",
            "mediaType": prepared.asset.media_type,
            "width": prepared.asset.width,
            "height": prepared.asset.height,
            "bytes": len(prepared.asset.data),
            "sha256": prepared.asset.sha256,
            "data": base64.b64encode(prepared.asset.data).decode("ascii"),
        }
    try:
        from .publication import PUBLICATION_VERSION
    except ImportError:  # direct import from scripts/tests
        from publication import PUBLICATION_VERSION  # type: ignore
    return {
        "version": PUBLICATION_VERSION,
        "widgetId": widget_id,
        "publicationId": "preview",
        "revision": 0,
        "kind": prepared.kind,
        "title": prepared.title,
        "summary": prepared.summary,
        "publishedAt": _now(),
        "expiresAt": prepared.expires_at,
        "priority": prepared.priority,
        "itemId": prepared.item_id,
        "actions": list(prepared.actions),
        "provenance": prepared.provenance,
        "darkPalette": prepared.dark_palette,
        "variants": prepared.variants or {},
        "content": body,
    }


@functools.lru_cache(maxsize=32)
def _fallback_png(width: int, height: int, *, label: bytes = b"Hermes") -> bytes:
    """Make a valid deterministic PNG when an optional raster backend is absent.

    CairoSVG/Pillow are preferred for fidelity.  This fallback keeps the API
    useful on a minimal host (and makes failures explicit in ``renderer``), while
    never pretending that a placeholder is an exact device render.
    """
    width = max(1, min(4096, int(width)))
    height = max(1, min(4096, int(height)))
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter: none
        for x in range(width):
            # A quiet scrim with a small accent marker, independent of input.
            edge = x < 3 or y < 3 or x >= width - 3 or y >= height - 3
            if edge:
                rows.extend((124, 58, 237))
            else:
                value = 245 - (y * 8 // max(1, height))
                rows.extend((value, value, min(255, value + 3)))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def _band_plan(width_px: int, height_px: int) -> tuple[str, dict[str, Any], bool]:
    """The device's band for this preview plus the width guard, in one place."""
    width_dp = width_px / PREVIEW_SCALE
    height_dp = height_px / PREVIEW_SCALE
    band = size_band(width_dp, height_dp) or "m"
    plan = BAND_PLAN[band]
    single_column = width_dp < SINGLE_COLUMN_MAX_WIDTH_DP
    if single_column:
        plan = {**plan, "ticker": False, "hero_lines": 1}
    return band, plan, single_column


LABEL_ADVANCE = 0.62   # bold label average advance, as a fraction of the font size
TITLE_ADVANCE = 0.62  # bold 18sp hero, same figure as the action label


@functools.lru_cache(maxsize=1)
def _load_cairosvg_module() -> Any:
    try:
        import cairosvg  # type: ignore
        return cairosvg
    except Exception:
        return None


def _cairosvg_module() -> Any:
    # Respect an explicit missing-module marker used by minimal-host callers/tests,
    # while caching the normal import result for repeated renders.
    if sys.modules.get("cairosvg", object()) is None:
        return None
    return _load_cairosvg_module()


@functools.lru_cache(maxsize=128)
def _pillow_font(size: int, bold: bool) -> Any:
    from PIL import ImageFont  # type: ignore
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_width(draw: Any, text: str, typeface: Any) -> int:
    """Real text metrics when Pillow offers them; the estimate otherwise."""
    try:
        return int(draw.textlength(text, font=typeface))
    except Exception:
        return int(len(text) * 10)


def _wrap(value: str, width_px: int, size_px: int, advance: float = 0.55) -> list[str]:
    """Approximate the device's line breaking.

    0.55em is the average advance of the regular UI font and 0.62em of the bold one; using
    the bold figure for the hero is what keeps a long headline from pretending to fit on
    one line the way the phone would not.
    """
    per_line = max(8, int(width_px / max(1, size_px * advance)))
    return textwrap.wrap(value, width=per_line) or [""]


def _publication_text(publication: dict) -> tuple[str, str, str, str, str]:
    """(title, summary, body, ticker, status) exactly as the widget composes them."""
    title = str(publication.get("title") or "Hermes")
    summary = str(publication.get("summary") or "")
    body = str((publication.get("content") or {}).get("text") or "")
    provenance = str(publication.get("provenance") or "")
    prefix = {"verified": "\u2713 ", "from_price": "~price ", "estimate": "est. "}.get(provenance, "")
    regions = publication.get("regions")
    ticker_obj = regions.get("ticker") if isinstance(regions, dict) else None
    ticker = ""
    if isinstance(ticker_obj, dict) and not ticker_obj.get("decayed"):
        ticker = f"{ticker_obj.get('title', '')} \u00b7 {ticker_obj.get('summary', '')}".strip(" \u00b7")
    status = "Fresh \u00b7 updated moments ago"
    return prefix + title, summary, body, ticker, status


def _svg_text_png(publication: dict, width: int, height: int) -> tuple[bytes, str]:
    """Render the shipped composition: header, hero, summary, body, ticker, pinned footer."""
    width_px, height_px = max(1, width), max(1, height)
    band, plan, _ = _band_plan(width_px, height_px)
    tokens = WIDGET_SURFACE["night"] if publication.get("darkPalette") else WIDGET_SURFACE["day"]
    title, summary, body, ticker, status = _publication_text(publication)

    scale = PREVIEW_SCALE
    pad = 12 * scale
    mark = 16 * scale
    dot = 6 * scale
    title_size = 18 * scale
    body_size = 14 * scale
    caption_size = 11 * scale
    label_size = 12 * scale
    footer_h = 48 * scale if plan["footer"] else 0
    usable = max(40, width_px - pad * 2)

    chunks = [
        f'<rect width="100%" height="100%" rx="{8 * scale}" ry="{8 * scale}" fill="{tokens["surface"]}"/>'
    ]

    # Header (always): mark, wordmark when the cell has width, freshness dot.
    y = pad + mark
    chunks.append(
        f'<circle cx="{pad + dot}" cy="{pad + dot}" r="{dot}" fill="{tokens["accent"]}"/>'
        f'<text x="{pad + dot}" y="{pad + dot + label_size * 0.36}" font-family="sans-serif" '
        f'font-size="{label_size}" font-weight="700" fill="{tokens["on_accent"]}" '
        f'text-anchor="middle">H</text>'
    )
    header_x = pad + mark + 6 * scale
    chunks.append(
        f'<circle cx="{header_x + dot}" cy="{pad + dot}" r="{dot}" fill="{tokens["status_fresh"]}"/>'
    )
    if plan["hero_lines"] > 1 or band != "xs":
        chunks.append(
            f'<text x="{header_x + dot * 3}" y="{y}" font-family="sans-serif" '
            f'font-size="{label_size}" font-weight="500" fill="{tokens["on_surface"]}">Hermes</text>'
        )
    y += 4 * scale

    # Hero (capped by band; Glance has no ellipsis, so the preview clips like the device).
    title_lines = _wrap(title, usable, title_size, TITLE_ADVANCE)[: plan["hero_lines"]]
    for line in title_lines:
        chunks.append(
            f'<text x="{pad}" y="{y + title_size * 0.8}" font-family="sans-serif" '
            f'font-size="{title_size}" font-weight="700" fill="{tokens["on_surface"]}">'
            f'{xml_escape(line)}</text>'
        )
        y += int(title_size * 1.2)
    if plan["summary"] and summary:
        chunks.append(
            f'<text x="{pad}" y="{y + caption_size}" font-family="sans-serif" '
            f'font-size="{caption_size}" fill="{tokens["secondary"]}">'
            f'{xml_escape(textwrap.shorten(summary, width=max(12, usable // (caption_size // 2)), placeholder=ELLIPSIS))}</text>'
        )
        y += caption_size + 4 * scale

    # Body: the first screen only. The device scrolls the rest (a pinned footer means the
    # action is never below the fold), so the preview shows what is visible without it.
    if plan["body_lines"]:
        y += 4 * scale
        body_lines = _wrap(body, usable, body_size)[: plan["body_lines"]]
        for line in body_lines:
            chunks.append(
                f'<text x="{pad}" y="{y + body_size * 0.8}" font-family="sans-serif" '
                f'font-size="{body_size}" fill="{tokens["on_surface"]}">{xml_escape(line)}</text>'
            )
            y += int(body_size * 1.25)

    if plan["ticker"] and ticker:
        chunks.append(
            f'<text x="{pad}" y="{y + caption_size}" font-family="sans-serif" '
            f'font-size="{caption_size}" fill="{tokens["secondary"]}">'
            f'{xml_escape(textwrap.shorten(ticker, width=max(12, usable // (caption_size // 2)), placeholder=ELLIPSIS))}</text>'
        )

    # Pinned footer: status line plus the 48dp request action.
    if plan["footer"]:
        footer_y = height_px - pad - footer_h
        label = "Request update"
        label_w = int(len(label) * label_size * LABEL_ADVANCE) + 24 * scale
        button_x = width_px - pad - label_w
        # The status line gets whatever is left of the row, so it never runs under the action.
        status_room = max(8, (button_x - pad - 8 * scale) // max(6, int(caption_size * 0.55)))
        status_line = textwrap.shorten(status, width=status_room, placeholder=ELLIPSIS)
        chunks.append(
            f'<text x="{pad}" y="{footer_y + footer_h * 0.68}" font-family="sans-serif" '
            f'font-size="{caption_size}" fill="{tokens["secondary"]}">{xml_escape(status_line)}</text>'
            f'<rect x="{button_x}" y="{footer_y}" width="{label_w}" height="{footer_h}" '
            f'rx="{8 * scale}" ry="{8 * scale}" fill="{tokens["accent"]}"/>'
            f'<text x="{button_x + label_w / 2}" y="{footer_y + footer_h * 0.66}" '
            f'font-family="sans-serif" font-size="{label_size}" font-weight="500" '
            f'fill="{tokens["on_accent"]}" text-anchor="middle">{xml_escape(label)}</text>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" height="{height_px}" '
        f'viewBox="0 0 {width_px} {height_px}">' + "".join(chunks) + "</svg>"
    ).encode("utf-8")
    try:
        cairosvg = _cairosvg_module()
        if cairosvg is None:
            raise ImportError("CairoSVG is unavailable")
        return cairosvg.svg2png(bytestring=svg, output_width=width_px, output_height=height_px), "cairosvg"
    except Exception:
        # Text does not need libcairo.  Keep a useful built-in path on minimal
        # hosts instead of returning the same placeholder as a failed raster.
        return _pillow_text_png(publication, width_px, height_px)


def _pillow_text_png(publication: dict, width: int, height: int) -> tuple[bytes, str]:
    """Render the same composition with Pillow when CairoSVG is absent."""
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:
        return _fallback_png(width, height, label=b"text"), "fallback"
    try:
        width_px, height_px = max(1, width), max(1, height)
        band, plan, _ = _band_plan(width_px, height_px)
        tokens = WIDGET_SURFACE["night"] if publication.get("darkPalette") else WIDGET_SURFACE["day"]
        title, summary, body, ticker, status = _publication_text(publication)

        scale = PREVIEW_SCALE
        pad = 12 * scale
        mark = 16 * scale
        dot = 6 * scale
        title_size = 18 * scale
        body_size = 14 * scale
        caption_size = 11 * scale
        label_size = 12 * scale
        footer_h = 48 * scale if plan["footer"] else 0
        usable = max(40, width_px - pad * 2)

        def font(size: int, bold: bool = False):
            return _pillow_font(size, bold)

        def rgb(value: str) -> tuple[int, int, int]:
            value = value.lstrip("#")
            return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

        image = Image.new("RGB", (width_px, height_px), rgb(tokens["surface"]))
        draw = ImageDraw.Draw(image)

        # Header: accent mark with an H monogram, status dot, wordmark when there is width.
        draw.ellipse(
            [pad, pad, pad + dot * 2, pad + dot * 2], fill=rgb(tokens["accent"])
        )
        draw.text(
            (pad + dot * 0.5, pad + dot * 0.15), "H",
            font=font(int(label_size * 0.9), True), fill=rgb(tokens["on_accent"]),
        )
        header_x = pad + mark + 6 * scale
        draw.ellipse(
            [header_x, pad + dot, header_x + dot * 2, pad + dot * 3], fill=rgb(tokens["status_fresh"])
        )
        if band != "xs":
            draw.text(
                (header_x + dot * 3, pad + 2 * scale), "Hermes",
                font=font(label_size, True), fill=rgb(tokens["on_surface"]),
            )
        y = pad + mark + 4 * scale

        for line in _wrap(title, usable, title_size, TITLE_ADVANCE)[: plan["hero_lines"]]:
            draw.text((pad, y), line, font=font(title_size, True), fill=rgb(tokens["on_surface"]))
            y += int(title_size * 1.2)
        if plan["summary"] and summary:
            line = textwrap.shorten(
                summary, width=max(12, usable // max(6, caption_size // 2)), placeholder=ELLIPSIS
            )
            draw.text((pad, y), line, font=font(caption_size), fill=rgb(tokens["secondary"]))
            y += caption_size + 4 * scale
        if plan["body_lines"]:
            y += 4 * scale
            for line in _wrap(body, usable, body_size)[: plan["body_lines"]]:
                draw.text((pad, y), line, font=font(body_size), fill=rgb(tokens["on_surface"]))
                y += int(body_size * 1.25)
        if plan["ticker"] and ticker:
            line = textwrap.shorten(
                ticker, width=max(12, usable // max(6, caption_size // 2)), placeholder=ELLIPSIS
            )
            draw.text((pad, y), line, font=font(caption_size), fill=rgb(tokens["secondary"]))

        if plan["footer"]:
            footer_y = height_px - pad - footer_h
            label = "Request update"
            label_font = font(label_size, True)
            # Real metrics, not a guess: the button must fit its own label.
            label_w = int(_text_width(draw, label, label_font)) + 24 * scale
            button_x = width_px - pad - label_w
            status_room = max(8, (button_x - pad - 8 * scale) // max(6, int(caption_size * 0.55)))
            draw.text(
                (pad, footer_y + footer_h * 0.35),
                textwrap.shorten(status, width=status_room, placeholder=ELLIPSIS),
                font=font(caption_size), fill=rgb(tokens["secondary"]),
            )
            draw.rounded_rectangle(
                [button_x, footer_y, width_px - pad, footer_y + footer_h],
                radius=8 * scale, fill=rgb(tokens["accent"]),
            )
            draw.text(
                (button_x + 12 * scale, footer_y + footer_h * 0.32), label,
                font=label_font, fill=rgb(tokens["on_accent"]),
            )

        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue(), "pillow-text"
    except Exception:
        return _fallback_png(width, height, label=b"text"), "fallback"


def _fit_image(
    data: bytes, media_type: str, width: int, height: int,
    source_width: int | None = None, source_height: int | None = None,
) -> tuple[bytes, str]:
    # Pillow is sufficient for raster publications.  CairoSVG is only needed for
    # the SVG branch; importing both together made a perfectly valid PNG fall back
    # to the placeholder on hosts that do not have libcairo installed.
    try:
        from PIL import Image, ImageOps  # type: ignore
    except Exception:
        return _fallback_png(width, height, label=b"image"), "fallback"
    try:
        if media_type == "image/svg+xml":
            cairosvg = _cairosvg_module()
            if cairosvg is None:
                return _fallback_png(width, height, label=b"image"), "svg-renderer-unavailable"
            svg_width = source_width if isinstance(source_width, int) and source_width > 0 else width
            svg_height = source_height if isinstance(source_height, int) and source_height > 0 else height
            scale = min(width / svg_width, height / svg_height)
            output_width = max(1, min(width, int(svg_width * scale)))
            output_height = max(1, min(height, int(svg_height * scale)))
            source = cairosvg.svg2png(
                bytestring=data, output_width=output_width, output_height=output_height
            )
            image = Image.open(io.BytesIO(source)).convert("RGBA")
        else:
            image = Image.open(io.BytesIO(data))
            if image.width * image.height > 16_000_000:
                raise ValueError("source image exceeds the 16-megapixel preview limit")
            image = image.convert("RGBA")
        image = ImageOps.contain(image, (max(1, width), max(1, height)), method=Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (max(1, width), max(1, height)), (245, 245, 247, 255))
        canvas.alpha_composite(image, ((canvas.width - image.width) // 2, (canvas.height - image.height) // 2))
        output = io.BytesIO()
        canvas.convert("RGB").save(output, format="PNG", optimize=True)
        return output.getvalue(), "pillow"
    except Exception:
        return _fallback_png(width, height, label=b"image"), "fallback"


def _size_names(sizes: Any, inventory: list[dict] | None = None) -> list[tuple[str, int, int, bool]]:
    if sizes is None:
        if inventory:
            result: list[tuple[str, int, int, bool]] = []
            used: dict[str, int] = {}
            for item in inventory:
                base_name = str(item.get("sizeClass") or "custom")
                used[base_name] = used.get(base_name, 0) + 1
                name = base_name if used[base_name] == 1 else f"{base_name}#{used[base_name]}"
                width_px = item.get("widthPx")
                height_px = item.get("heightPx")
                if isinstance(width_px, int) and isinstance(height_px, int):
                    result.append((name, width_px, height_px, True))
                else:
                    result.append((
                        name,
                        int(item.get("widthDp") or PUBLICATION_SIZES.get(base_name, (270, 120))[0]),
                        int(item.get("heightDp") or PUBLICATION_SIZES.get(base_name, (270, 120))[1]),
                        False,
                    ))
            return result[:_PREVIEW_MAX_COUNT]
        return [(name, width, height, False) for name, (width, height) in PUBLICATION_SIZES.items()]
    if isinstance(sizes, str):
        sizes = [part.strip() for part in sizes.split(",") if part.strip()]
    if not isinstance(sizes, list) or not sizes or len(sizes) > _PREVIEW_MAX_COUNT:
        raise ValueError(f"sizes must contain at most {_PREVIEW_MAX_COUNT} size names")
    inventory_dims: dict[str, tuple[int, int]] = {}
    for item in inventory or []:
        name = str(item.get("sizeClass") or "custom")
        if isinstance(item.get("widthPx"), int) and isinstance(item.get("heightPx"), int):
            inventory_dims.setdefault(name, (item["widthPx"], item["heightPx"]))
    result: list[tuple[str, int, int, bool]] = []
    seen: set[str] = set()
    for raw in sizes:
        if not isinstance(raw, str):
            raise ValueError("each preview size must be a string")
        name = raw.strip()
        if name in seen:
            raise ValueError(f"duplicate preview size {name!r}")
        seen.add(name)
        if name in PUBLICATION_SIZES:
            if name in inventory_dims:
                result.append((name, *inventory_dims[name], True))
            else:
                result.append((name, *PUBLICATION_SIZES[name], False))
            continue
        # One digit is allowed because the range check below admits 1dp; a widget can be
        # dragged smaller than any named cell, and "unknown preview size 9x9" was a refusal
        # the ladder never needed to make.
        match = re.fullmatch(r"(\d{1,5})x(\d{1,5})", name)
        if not match:
            raise ValueError(f"unknown preview size {name!r}")
        width, height = int(match.group(1)), int(match.group(2))
        if not 1 <= width <= 4096 or not 1 <= height <= 4096:
            raise ValueError("preview dimensions are out of range")
        result.append((name, width, height, False))
    return result


def render_publication_previews(
    publication: dict,
    *,
    sizes: Any = None,
    inventory: list[dict] | None = None,
    asset_loader: Callable[[str], bytes] | None = None,
) -> list[dict[str, Any]]:
    """Render one publication to bounded, deterministic PNG previews.

    ``publication`` is the exact envelope that would be published.  No current
    state is changed.  ``asset_loader`` is supplied by the store for immutable
    publication assets; tests and offline callers may provide raw bytes instead.
    """
    if not isinstance(publication, dict):
        raise ValueError("publication must be an object")
    requested = _size_names(sizes, inventory)
    content = publication.get("content") or {}
    kind = publication.get("kind")
    asset_data: bytes | None = None
    if kind == "image":
        asset_id = content.get("assetId")
        if asset_loader is not None and isinstance(asset_id, str):
            asset_data = asset_loader(asset_id)
        elif isinstance(content.get("data"), str):
            try:
                asset_data = base64.b64decode(content["data"], validate=True)
            except (ValueError, TypeError):
                asset_data = None
    if kind == "image" and not asset_data:
        raise ValueError("image asset bytes are unavailable; provide a local file, inline data, or a stored assetId")
    output: list[dict[str, Any]] = []
    for name, width, height, already_pixels in requested:
        width_px, height_px = (width, height) if already_pixels else (width * 2, height * 2)
        if width_px * height_px > _PREVIEW_MAX_PIXELS:
            raise ValueError("preview dimensions exceed the 4-megapixel safety limit")
        if kind == "text":
            variant = (publication.get("variants") or {}).get(name) if isinstance(publication.get("variants"), dict) else None
            render_publication = publication
            if isinstance(variant, dict) and variant.get("text") is not None:
                render_publication = {**publication, "title": variant.get("title", publication.get("title")), "summary": variant.get("summary", publication.get("summary")), "content": {"type": "text", "mediaType": "text/plain; charset=utf-8", "text": variant["text"]}}
            data, renderer = _svg_text_png(render_publication, width_px, height_px)
        else:
            media_type = str(content.get("mediaType") or "image/png")
            data, renderer = _fit_image(
                asset_data or b"", media_type, width_px, height_px,
                content.get("width"), content.get("height"),
            )
        if len(data) > _PREVIEW_MAX_BYTES:
            raise ValueError("rendered preview exceeds the 2 MiB limit")
        item = {
            "size": name,
            "width": width_px if already_pixels else width,
            "height": height_px if already_pixels else height,
            "pixelWidth": width_px,
            "pixelHeight": height_px,
            "mediaType": "image/png",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": base64.b64encode(data).decode("ascii"),
            "renderer": renderer,
        }
        if renderer == "svg-renderer-unavailable":
            item["note"] = (
                "No local SVG renderer (CairoSVG/libcairo); the connected device remains authoritative."
            )
        output.append(item)
    return output


__all__ = [
    "PUBLICATION_SIZES", "build_preview_publication", "render_publication_previews",
]
