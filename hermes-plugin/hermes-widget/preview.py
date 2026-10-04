"""Offline previews of the text, SVG, and raster publication contract."""
from __future__ import annotations

import base64
import hashlib
import io
import functools
import re
import sys
import struct
import zlib
from typing import Any, Callable

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
    source_keys = ("text", "svg", "file_path", "filePath", "presentation", "variants", "visual_variants", "visualVariants", "ticker")
    if isinstance(content, dict) and not any(key in proposed for key in source_keys):
        file_path = content.get("filePath", content.get("file_path"))
        if file_path:
            proposed = {**proposed, "filePath": file_path}
            proposed.pop("content", None)
        elif "kind" in proposed and "content" in proposed:
            return {**proposed, "widgetId": widget_id}
        else:
            raise ValueError("publication must include title, summary, and exactly one source")

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
        presentation=proposed.get("presentation"),
        visual_variants=proposed.get("visual_variants", proposed.get("visualVariants")),
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
        "presentation": prepared.presentation,
        "visualVariants": {key: {**asset.metadata(), "data": base64.b64encode(asset.data).decode("ascii")} for key, asset in (prepared.visual_variants or {}).items()},
        "ticker": proposed.get("ticker"),
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


def _fit_image(
    data: bytes, media_type: str, width: int, height: int,
    source_width: int | None = None, source_height: int | None = None,
    background: str = "#FEF7FF",
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
        canvas = Image.new("RGBA", (max(1, width), max(1, height)), background)
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
    palette: str = "light",
    font_scale: float = 1.0,
) -> list[dict[str, Any]]:
    """Render one publication to bounded, deterministic PNG previews.

    ``publication`` is the exact envelope that would be published.  No current
    state is changed.  ``asset_loader`` is supplied by the store for immutable
    publication assets; tests and offline callers may provide raw bytes instead.
    """
    if not isinstance(publication, dict):
        raise ValueError("publication must be an object")
    if palette not in ("light", "dark"):
        raise ValueError("palette must be light or dark")
    requested = _size_names(sizes, inventory)
    try:
        from .composition import render as compose
    except ImportError:
        from composition import render as compose
    output: list[dict[str, Any]] = []
    for name, width, height, already_pixels in requested:
        width_px, height_px = (width, height) if already_pixels else (width * 2, height * 2)
        if width_px * height_px > _PREVIEW_MAX_PIXELS:
            raise ValueError("preview dimensions exceed the 4-megapixel safety limit")
        dp_width, dp_height = width, height
        if already_pixels:
            base = name.split("#")[0]
            ordinal = int(name.split("#")[1]) - 1 if "#" in name else 0
            matches = [item for item in (inventory or []) if item.get("sizeClass", "custom") == base]
            source = matches[min(ordinal,len(matches)-1)] if matches else {}
            dp_width = source.get("widthDp", PUBLICATION_SIZES.get(base,(270,120))[0])
            dp_height = source.get("heightDp", PUBLICATION_SIZES.get(base,(270,120))[1])
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 1 <= v <= 4096 for v in (dp_width, dp_height)):
            raise ValueError("reported dp dimensions must be between 1 and 4096")
        data, renderer, diagnostics = compose(
            publication, dp_width, dp_height, width_px, height_px,
            asset_loader, dark=palette == "dark", font_scale=font_scale,
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
            "diagnostics": diagnostics,
        }
        if renderer == "svg-renderer-unavailable":
            item["note"] = (
                "No local SVG renderer (CairoSVG/libcairo); the connected device remains authoritative."
            )
        diagnostics["previewLimit"] = _PREVIEW_MAX_COUNT
        diagnostics["omittedWidgetInstances"] = max(0, len(inventory or []) - len(requested)) if sizes is None else 0
        output.append(item)
    return output


__all__ = [
    "PUBLICATION_SIZES", "build_preview_publication", "render_publication_previews",
]
