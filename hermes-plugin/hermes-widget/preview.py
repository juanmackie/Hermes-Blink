"""Offline HTML preview of a v2 widget layout.

The device is authoritative, but it is a phone: iterating on a design should not require a
build, an install, and a screenshot. This renders the same tree the device would, at the
dp sizes Android gives each widget shape, so a layout can be looked at before it is pushed.

Every number and colour comes from layout.schema.json (typography, palette, spacing), and
scripts/check-contract-parity.py keeps that registry, Typo.kt, the renderer and the skill in
agreement — so a preview that looks right and a device that looks wrong is a bug, not a
tolerance. It is an approximation of Compose, not a simulator: it approximates RemoteViews
sizing and cannot show real font metrics.
"""
from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import pathlib
import re
import struct
import textwrap
import zlib
from datetime import datetime, timezone
from typing import Any, Callable
from xml.sax.saxutils import escape as xml_escape

try:  # normal path: imported as part of the hermes-widget plugin package
    from .validate import ValidationError, inspect_layout
except ImportError:  # pragma: no cover - direct import from scripts/tests
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from validate import ValidationError, inspect_layout  # type: ignore

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent
SCHEMA_PATH = PLUGIN_DIR / "layout.schema.json"

# Android sizes a widget cell in dp; the widget is resizable, so preview every shape.
# The same approximations the design skill documents.
SHAPES: tuple[tuple[str, int, int], ...] = (
    ("2x2", 120, 120),
    ("4x2 (default)", 270, 120),
    ("2x4", 120, 270),
    ("4x4", 270, 270),
)

_WEIGHT_CSS = {"normal": "400", "medium": "500", "bold": "700"}
_ALIGN_ITEMS = {"start": "flex-start", "center": "center", "end": "flex-end", "fill": "stretch"}
_TEXT_ALIGN = {"start": "left", "center": "center", "end": "right", "fill": "left"}


def _registry() -> dict[str, Any]:
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read widget schema: {exc}") from exc


def _tokens() -> tuple[dict[str, dict], dict[str, str], dict[str, int]]:
    schema = _registry()
    typo = schema["definitions"]["typography"]["properties"]
    palette = {k: v["const"] for k, v in schema["definitions"]["palette"]["properties"].items()}
    spacing = {k: v["const"] for k, v in schema["definitions"]["spacing"]["properties"].items()}
    return typo, palette, spacing


def _hex(value: Any, fallback: str | None) -> str | None:
    if isinstance(value, str) and value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            return "#" + "".join(c * 2 for c in value[1:])
        return value
    return fallback


def is_stale(layout: dict, now: datetime | None = None) -> bool:
    """Mirrors WidgetLayout.isStale in Layout.kt: past ttlSeconds the device flags the
    content rather than hiding it. The preview must show that banner too, or it would
    render more flattering than the device."""
    ttl = layout.get("ttlSeconds")
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0:
        return False
    updated = layout.get("updatedAt")
    if not isinstance(updated, str) or not updated:
        return False
    try:
        stamp = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - stamp).total_seconds() > ttl


def _ink_on(background: str) -> str:
    r, g, b = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    return "#FFFFFF" if (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 < 0.6 else "#000000"


class _Renderer:
    def __init__(self, typo: dict, palette: dict, spacing: dict, accent: str):
        self.typo = typo
        self.palette = palette
        self.spacing = spacing
        self.accent = accent

    # -- helpers ----------------------------------------------------------
    def _style(self, name: str, colour: str | None = None, align: str | None = None,
               max_lines: int | None = None) -> str:
        spec = self.typo.get(name, self.typo["body"])
        chunks = [
            f"font-size:{spec['sizeSp']}px",
            f"font-weight:{_WEIGHT_CSS.get(spec['weight'], '400')}",
            f"color:{colour or spec['color']}",
            f"text-align:{_TEXT_ALIGN.get(align or 'start', 'left')}",
        ]
        if max_lines:
            chunks += [
                f"max-height:{max_lines * (spec['sizeSp'] + 5)}px",
                "overflow:hidden",
                f"-webkit-line-clamp:{max_lines}",
                "-webkit-box-orient:vertical",
                "display:-webkit-box",
            ]
        return ";".join(chunks)

    def _spacing(self, node: dict) -> int:
        value = node.get("spacing")
        return value if isinstance(value, int) else self.spacing["containerSpacing"]

    def _padding(self, node: dict, fallback: int | None = None) -> str:
        pad = node.get("padding")

        def edge(key: str, default: int) -> int:
            return pad.get(key, 0) if isinstance(pad, dict) else default
        top = edge("top", fallback if fallback is not None else 0)
        bottom = edge("bottom", fallback if fallback is not None else 0)
        start = edge("start", fallback if fallback is not None else 0)
        end = edge("end", fallback if fallback is not None else 0)
        return f"padding:{top}px {end}px {bottom}px {start}px"

    def _weight(self, node: dict) -> str:
        weight = node.get("weight")
        return f"flex:{weight} 1 0%" if isinstance(weight, (int, float)) and weight > 0 else ""

    # -- nodes ------------------------------------------------------------
    def node(self, node: dict) -> str:
        kind = node.get("type")
        handler = getattr(self, f"_{kind}", None)
        return handler(node) if handler else ""

    def _children(self, node: dict) -> str:
        kids = node.get("children") or []
        return "".join(self.node(child) for child in kids)

    def _column(self, node: dict) -> str:
        css = (
            f"display:flex;flex-direction:column;gap:{self._spacing(node)}px;"
            f"align-items:{_ALIGN_ITEMS.get(node.get('alignment') or 'fill', 'stretch')};"
            f"{self._padding(node)};width:100%"
        )
        return f'<div style="{css}">{self._children(node)}</div>'

    def _row(self, node: dict) -> str:
        css = (
            f"display:flex;flex-direction:row;gap:{self._spacing(node)}px;"
            f"align-items:{_ALIGN_ITEMS.get(node.get('alignment') or 'start', 'flex-start')};"
            f"{self._padding(node)};width:100%"
        )
        return f'<div style="{css}">{self._children(node)}</div>'

    def _box(self, node: dict) -> str:
        css = (
            "display:flex;flex-direction:column;align-items:flex-start;"
            f"{self._padding(node)};width:100%"
        )
        return f'<div style="{css}">{self._children(node)}</div>'

    def _list(self, node: dict) -> str:
        # A list is a column with a cap: no `spacing` field, so the container default applies.
        items = (node.get("children") or [])[: node.get("maxItems") or 100]
        css = (
            f"display:flex;flex-direction:column;gap:{self._spacing(node)}px;"
            f"{self._padding(node)};width:100%"
        )
        return f'<div style="{css}">{"".join(self.node(i) for i in items)}</div>'

    def _text(self, node: dict) -> str:
        text = html.escape(str(node.get("value") or node.get("text") or ""))
        css = self._style(node.get("style") or "body", _hex(node.get("color"), None) if node.get("color") else None,
                          node.get("alignment"), node.get("maxLines"))
        return f'<div style="{css}">{text}</div>'

    def _stat(self, node: dict) -> str:
        out = [f'<div style="{self._style("caption")}">{html.escape(str(node.get("label") or ""))}</div>']
        out.append(
            f'<div style="{self._style("title", _hex(node.get("color"), None) if node.get("color") else None, node.get("alignment"), 1)}">'
            f'{html.escape(str(node.get("value") or ""))}</div>'
        )
        if node.get("delta"):
            direction = node.get("deltaDirection")
            if not isinstance(direction, str):
                direction = ""
            shade = {"up": self.palette["success"], "down": self.palette["danger"]}.get(
                direction, self.palette["secondary"])
            glyph = {"up": "▲", "down": "▼"}.get(direction, "•")
            out.append(f'<div style="{self._style("caption", shade, None, 1)}">{glyph} {html.escape(str(node["delta"]))}</div>')
        return f'<div style="display:flex;flex-direction:column;{self._weight(node)}">{ "".join(out) }</div>'

    def _badge(self, node: dict) -> str:
        fill = _hex(node.get("color"), self.palette["hairline"])
        css = (f"display:inline-block;border-radius:8px;padding:3px 8px;background:{fill};"
               f"{self._style('caption', _ink_on(fill), None, 1)}")
        return f'<div style="display:flex"><div style="{css}">{html.escape(str(node.get("text") or ""))}</div></div>'

    def _divider(self, node: dict) -> str:
        thickness = node.get("thickness") or 1
        colour = _hex(node.get("color"), self.palette["hairline"])
        return f'<div style="height:{thickness}px;background:{colour};width:100%"></div>'

    def _spacer(self, node: dict) -> str:
        return f'<div style="height:{node.get("size") or 8}px"></div>'

    def _progress(self, node: dict) -> str:
        try:
            value = max(0.0, min(1.0, float(node.get("value") or 0)))
        except (TypeError, ValueError):
            value = 0.0
        head = f'<div style="{self._style("caption")}">{html.escape(str(node.get("label") or "Progress"))}</div>'
        percent = (f'<div style="{self._style("caption")}">{value * 100:.0f}%</div>'
                   if node.get("showPercent") else "")
        bar = (
            f'<div style="display:flex;align-items:center;gap:8px">{head}{percent}</div>'
            f'<div style="height:6px;border-radius:3px;background:{self.palette["hairline"]};width:100%">'
            f'<div style="height:6px;border-radius:3px;width:{value * 100:.1f}%;background:{self.accent}"></div></div>'
        )
        return f'<div style="display:flex;flex-direction:column;gap:4px;width:100%">{bar}</div>'

    def _calendar(self, node: dict) -> str:
        events = (node.get("events") or [])[: node.get("maxItems") or 50]
        if not events:
            return f'<div style="{self._style("caption")}">No events</div>'
        rows = []
        for event in events:
            start = event.get("start")
            # A clock time, never the raw ISO string — the same rule the renderer follows.
            time = ""
            if isinstance(start, str) and "T" in start:
                time = start.split("T", 1)[1][:5]
            elif isinstance(start, str):
                time = start
            cells = ""
            if time:
                cells += f'<div style="width:48px;{self._style("caption")}">{html.escape(time)}</div>'
            cells += f'<div style="{self._style("label", _hex(event.get("color"), None) if event.get("color") else None, None, 1)}">{html.escape(str(event.get("title") or ""))}</div>'
            rows.append(f'<div style="display:flex;align-items:center;padding:2px 0">{cells}</div>')
        return f'<div style="display:flex;flex-direction:column;width:100%">{"".join(rows)}</div>'

    def _list_item(self, node: dict) -> str:
        left = f'<div style="flex:1 1 0%"><div style="{self._style("body", None, None, 1)}">{html.escape(str(node.get("title") or ""))}</div>'
        if node.get("subtitle"):
            left += f'<div style="{self._style("caption", None, None, 1)}">{html.escape(str(node["subtitle"]))}</div>'
        left += "</div>"
        trailing = ""
        if node.get("trailingText"):
            trailing = f'<div style="{self._style("caption")}">{html.escape(str(node["trailingText"]))}</div>'
        return f'<div style="display:flex;align-items:center;padding:8px;width:100%">{left}{trailing}</div>'

    def _button(self, node: dict) -> str:
        style = node.get("style")
        if style == "filled":
            fill, ink = self.accent, "#FFFFFF"
        elif style == "outlined":
            fill, ink = "#FFFFFF", self.palette["primary"]
        else:
            fill, ink = self.palette["hairline"], self.palette["primary"]
        spec = self.typo["body"]
        css = (f"display:inline-block;border-radius:8px;padding:8px 12px;background:{fill};"
               f"font-size:{spec['sizeSp']}px;font-weight:{_WEIGHT_CSS['medium']};color:{ink};"
               "text-align:center")
        return f'<div style="display:flex"><div style="{css}">{html.escape(str(node.get("label") or "Button"))}</div></div>'


def render_html(layout: dict, *, title: str = "Hermes widget preview") -> str:
    """Render a v2 layout envelope to a standalone HTML page."""
    typo, palette, spacing = _tokens()
    accent = _hex(layout.get("accentColor"), palette["accent"])
    renderer = _Renderer(typo, palette, spacing, accent)

    report = inspect_layout(layout)
    stale = is_stale(layout)
    banners = "".join(
        f'<div class="warn"><b>{html.escape(w["code"])}</b> — {html.escape(w["detail"])}</div>'
        for w in report["warnings"]
    ) or '<div class="ok">No design warnings.</div>'

    panels = []
    stale_banner = (
        f'<div style="padding-bottom:4px;{renderer._style("caption", palette["danger"], None, 1)}">'
        f'Stale — reconnecting…</div>'
    ) if stale else ""
    for label, width, height in SHAPES:
        panels.append(
            f'<figure><figcaption>{label} · ~{width}×{height}dp</figcaption>'
            f'<div class="widget" style="width:{width}px;height:{height}px;overflow:hidden">'
            f'{stale_banner}{renderer.node(layout["root"])}</div></figure>'
        )

    stale_note = ""
    ttl = layout.get("ttlSeconds")
    if ttl:
        state = "STALE — the device is showing this with a reconnecting banner" if stale \
            else "fresh"
        stale_note = f'<div class="meta">ttlSeconds {ttl} · {state}</div>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  body {{ margin:0; padding:24px; background:#f2f2f7; color:#111;
         font:13px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }}
  h1 {{ font-size:16px; margin:0 0 4px; }}
  .meta {{ color:#6b6b70; margin-bottom:12px; }}
  .warn {{ background:#fff4e5; border-left:3px solid #ff9500; padding:6px 10px; margin:4px 0; }}
  .ok {{ background:#e8f6ec; border-left:3px solid #34c759; padding:6px 10px; margin:4px 0; }}
  .shapes {{ display:flex; flex-wrap:wrap; gap:24px; align-items:flex-start; margin-top:20px; }}
  figure {{ margin:0; }}
  figcaption {{ font-size:11px; color:#6b6b70; margin-bottom:6px; }}
  .widget {{ border-radius:16px; background:rgba(255,255,255,0.65); box-shadow:0 1px 4px rgba(0,0,0,.12);
             /* The outline is the point: it shows exactly where the device would clip. */
             outline:1px dashed rgba(0,0,0,.3);
             display:flex; flex-direction:column; overflow:hidden; }}
  /* RemoteViews clip an oversized layout; they never shrink the content to fit. Without
     flex:0 0 auto here the flexbox would compress children instead of overflowing. */
  .widget > * {{ flex:0 0 auto; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<div class="meta">widgetId {html.escape(str(layout.get('widgetId', '?')))} · {report['nodeCount']} nodes ·
  {report['bytes']} / {report['maxBytes']} bytes · styles: {", ".join(report['textStyles']) or "none"}</div>
{stale_note}{banners}
<div class="shapes">{''.join(panels)}</div>
<p class="meta">Approximation of Compose on Android — dp sizes and the registry's typography, palette and
spacing. The device is authoritative.</p>
</body></html>
"""


def preview_file(path: str | pathlib.Path, out: str | pathlib.Path | None = None) -> pathlib.Path:
    """Render the layout at `path` and write the HTML next to it (or to `out`)."""
    source = pathlib.Path(path)
    try:
        layout = json.loads(source.read_text(encoding="utf-8"))
        target = pathlib.Path(out) if out else source.with_suffix(".preview.html")
        target.write_text(render_html(layout, title=source.name), encoding="utf-8")
        return target
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot render preview: {exc}") from exc


# Nominal publication previews use the same dp classes as the Android widget.
# The values are intentionally explicit: a caller can ask for a registered
# custom size and the server will use its reported pixel dimensions instead.
PUBLICATION_SIZES: dict[str, tuple[int, int]] = {
    "2x2": (120, 120),
    "4x2": (270, 120),
    "2x4": (120, 270),
    "4x4": (270, 270),
}
_PREVIEW_MAX_BYTES = 2 * 1024 * 1024
_PREVIEW_MAX_COUNT = 16
_PREVIEW_MAX_PIXELS = 4_000_000


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


def _svg_text_png(publication: dict, width: int, height: int) -> tuple[bytes, str]:
    content = publication.get("content") or {}
    text = str(content.get("text") or "")
    title = str(publication.get("title") or "Hermes")
    summary = str(publication.get("summary") or "")
    width_px, height_px = max(1, width), max(1, height)
    pad = max(8, min(18, width_px // 12))
    title_size = max(12, min(22, width_px // 12))
    body_size = max(10, min(16, width_px // 15))
    summary_size = max(9, min(12, width_px // 18))
    usable = max(40, width_px - pad * 2)
    title_line = textwrap.shorten(title, width=max(8, usable // max(7, title_size // 2)), placeholder="…")
    summary_line = textwrap.shorten(summary, width=max(12, usable // max(6, summary_size // 2)), placeholder="…")
    body_lines = textwrap.wrap(text, width=max(12, usable // max(6, body_size // 2))) or [""]
    # A 2x2 preview intentionally shows the same clipping boundary as a glanceable
    # surface: no server-side reflow or crop is hidden from the publisher.
    max_lines = max(1, (height_px - pad * 2 - title_size - summary_size - 12) // (body_size + 5))
    body_lines = body_lines[:max_lines]
    chunks = [
        '<rect width="100%" height="100%" fill="#F5F5F7"/>',
        f'<text x="{pad}" y="{pad + title_size}" font-family="sans-serif" font-size="{title_size}" font-weight="700" fill="#111">{xml_escape(title_line)}</text>',
        f'<text x="{pad}" y="{pad + title_size + summary_size + 4}" font-family="sans-serif" font-size="{summary_size}" fill="#6E6E73">{xml_escape(summary_line)}</text>',
    ]
    y = pad + title_size + summary_size + 12 + body_size
    for line in body_lines:
        chunks.append(f'<text x="{pad}" y="{y}" font-family="sans-serif" font-size="{body_size}" fill="#111">{xml_escape(line)}</text>')
        y += body_size + 5
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" height="{height_px}" '
        f'viewBox="0 0 {width_px} {height_px}">' + "".join(chunks) + "</svg>"
    ).encode("utf-8")
    try:
        import cairosvg  # type: ignore
        return cairosvg.svg2png(bytestring=svg), "cairosvg"
    except Exception:
        # Text does not need libcairo.  Keep a useful built-in path on minimal
        # hosts instead of returning the same placeholder as a failed raster.
        return _pillow_text_png(publication, width_px, height_px)


def _pillow_text_png(publication: dict, width: int, height: int) -> tuple[bytes, str]:
    """Render the bounded text envelope with Pillow when CairoSVG is absent."""
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception:
        return _fallback_png(width, height, label=b"text"), "fallback"
    try:
        width_px, height_px = max(1, width), max(1, height)
        pad = max(8, min(18, width_px // 12))
        title_size = max(12, min(22, width_px // 12))
        body_size = max(10, min(16, width_px // 15))
        summary_size = max(9, min(12, width_px // 18))
        usable = max(40, width_px - pad * 2)

        def font(size: int, bold: bool = False):
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

        content = publication.get("content") or {}
        title = str(publication.get("title") or "Hermes")
        summary = str(publication.get("summary") or "")
        text = str(content.get("text") or "")
        title_line = textwrap.shorten(title, width=max(8, usable // max(7, title_size // 2)), placeholder="…")
        summary_line = textwrap.shorten(summary, width=max(12, usable // max(6, summary_size // 2)), placeholder="…")
        body_lines = textwrap.wrap(text, width=max(12, usable // max(6, body_size // 2))) or [""]
        max_lines = max(1, (height_px - pad * 2 - title_size - summary_size - 12) // (body_size + 5))
        body_lines = body_lines[:max_lines]
        image = Image.new("RGB", (width_px, height_px), (245, 245, 247))
        draw = ImageDraw.Draw(image)
        y = pad
        draw.text((pad, y), title_line, font=font(title_size, True), fill=(17, 17, 17))
        y += title_size + 4
        draw.text((pad, y), summary_line, font=font(summary_size), fill=(110, 110, 115))
        y += summary_size + 8
        for line in body_lines:
            draw.text((pad, y), line, font=font(body_size), fill=(17, 17, 17))
            y += body_size + 5
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue(), "pillow-text"
    except Exception:
        return _fallback_png(width, height, label=b"text"), "fallback"


def _fit_image(data: bytes, media_type: str, width: int, height: int) -> tuple[bytes, str]:
    # Pillow is sufficient for raster publications.  CairoSVG is only needed for
    # the SVG branch; importing both together made a perfectly valid PNG fall back
    # to the placeholder on hosts that do not have libcairo installed.
    try:
        from PIL import Image, ImageOps  # type: ignore
    except Exception:
        return _fallback_png(width, height, label=b"image"), "fallback"
    try:
        if media_type == "image/svg+xml":
            try:
                import cairosvg  # type: ignore
            except Exception:
                return _fallback_png(width, height, label=b"image"), "fallback"
            source = cairosvg.svg2png(bytestring=data)
            image = Image.open(io.BytesIO(source)).convert("RGBA")
        else:
            image = Image.open(io.BytesIO(data)).convert("RGBA")
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
            result.append((name, *PUBLICATION_SIZES[name], False))
            continue
        match = re.fullmatch(r"(\d{2,5})x(\d{2,5})", name)
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
    output: list[dict[str, Any]] = []
    for name, width, height, already_pixels in requested:
        width_px, height_px = (width, height) if already_pixels else (width * 2, height * 2)
        if width_px * height_px > _PREVIEW_MAX_PIXELS:
            raise ValueError("preview dimensions exceed the 4-megapixel safety limit")
        if kind == "text":
            data, renderer = _svg_text_png(publication, width_px, height_px)
        else:
            media_type = str(content.get("mediaType") or "image/png")
            data, renderer = _fit_image(asset_data or b"", media_type, width_px, height_px)
        if len(data) > _PREVIEW_MAX_BYTES:
            raise ValueError("rendered preview exceeds the 2 MiB limit")
        output.append({
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
        })
    return output


__all__ = [
    "render_html", "preview_file", "is_stale", "SHAPES", "PUBLICATION_SIZES",
    "render_publication_previews", "ValidationError",
]
