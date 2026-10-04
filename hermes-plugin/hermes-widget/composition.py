"""Advisory host composition with Android's dp bands and pinned controls."""

from __future__ import annotations

import base64
import io
import math

try:
    from .bands import size_band, is_single_column
    from .presentations import font, select_variant, visible_items
    from .errors import StoreError
except ImportError:
    from bands import size_band, is_single_column
    from presentations import font, select_variant, visible_items
    from errors import StoreError

ACTION_HEIGHT_DP = 48
TITLE_SIZE_SP = 16


def geometry(width_dp, height_dp, font_scale=1.0, *, question=False, ticker=False):
    band = size_band(width_dp, height_dp) or "xs"
    narrow = is_single_column(width_dp)
    action = band in {"m", "l"}
    header = 52 if action else 20
    footer = 18 if action else 0
    hero_lines = 1 if narrow or band == "xs" else 2
    summary = band != "xs"
    hero = (hero_lines * 22 + (17 if summary else 0)) * font_scale
    show_ticker = ticker and band == "l" and not narrow
    show_question = question and band != "xs" and not narrow
    scroll = max(0, min(4096, math.floor(height_dp - 24 - header - footer)))
    # Mirrors BandSpec.imageHeightDp: all optional slots are reserved conservatively.
    # Floor without a +0.5 bias so a fractional hero (font_scale 1.5) cannot push
    # visual end 0.5dp past heightDp - pad - footer (210x276, 210x412 overflow).
    image_height = (
        max(
            0,
            math.floor(
                scroll
                - hero
                - 8
                - (19 * font_scale if band == "l" and not narrow else 0)
                - (19 * font_scale if band != "xs" and not narrow else 0)
            ),
        )
        if action
        else 0
    )
    return dict(
        band=band,
        narrow=narrow,
        header=header,
        footer=footer,
        heroLines=hero_lines,
        summary=summary,
        scrollHeight=scroll,
        imageHeight=image_height,
        heroHeight=hero,
        action=action,
        ticker=show_ticker,
        question=show_question,
    )


def render(
    publication,
    width_dp,
    height_dp,
    width_px,
    height_px,
    asset_loader=None,
    *,
    dark=False,
    font_scale=1.0,
):
    from PIL import Image, ImageDraw, ImageOps

    try:
        from .preview import WIDGET_SURFACE, _fit_image
    except ImportError:
        from preview import WIDGET_SURFACE, _fit_image
    if (
        not isinstance(font_scale, (int, float))
        or isinstance(font_scale, bool)
        or not math.isfinite(font_scale)
        or not 0.5 <= font_scale <= 3
    ):
        raise ValueError("font_scale must be between 0.5 and 3")
    dark = dark or bool(publication.get("darkPalette"))
    tokens = WIDGET_SURFACE["night" if dark else "day"]
    plan = geometry(
        width_dp,
        height_dp,
        font_scale,
        question=bool(publication.get("question"))
        and publication["question"].get("status", "open") == "open",
        ticker=bool(publication.get("ticker")) and not publication["ticker"].get("decayed", False),
    )
    sx, sy = width_px / width_dp, height_px / height_dp
    image = Image.new("RGB", (width_px, height_px), tokens["surface"])
    draw = ImageDraw.Draw(image)
    pad = 12
    usable = max(1, width_dp - 24)
    hidden = []
    rectangles = {}

    def rect(key, x, y, w, h):
        rectangles[key] = [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]

    def line(text, x, y, size, color, available=usable):
        face = font(round(size * sx * font_scale))
        text = str(text)
        max_px = max(1, available * sx)
        clipped = False
        if draw.textlength(text, font=face) > max_px:
            clipped = True
            while text and draw.textlength(text + "...", font=face) > max_px:
                text = text[:-1]
            text += "..."
        draw.text((x * sx, y * sy), text, font=face, fill=color)
        return clipped

    header_y = pad + (16 if plan["action"] else 0)
    line("H", pad, header_y, 12, tokens["accent"], 20)
    dot_x = pad + 22
    if not plan["narrow"] and plan["band"] != "xs":
        line("Hermes", pad + 22, header_y, 12, tokens["on_surface"], 70)
        dot_x += draw.textlength("Hermes", font=font(round(12 * font_scale))) + 6
    draw.ellipse(
        (dot_x * sx, (header_y + 5) * sy, (dot_x + 6) * sx, (header_y + 11) * sy),
        fill=tokens["accent"],
    )
    if plan["action"]:
        label = "Update" if plan["narrow"] else "Request update"
        face = font(round(12 * font_scale))
        button_width = min(usable, draw.textlength(label, font=face) + 24)
        bx = width_dp - pad - button_width
        draw.rounded_rectangle(
            (bx * sx, pad * sy, (width_dp - pad) * sx, (pad + ACTION_HEIGHT_DP) * sy),
            radius=24 * sy,
            fill=tokens["accent"],
        )
        line(label, bx + 12, pad + 12, 12, tokens["on_accent"], max(1, button_width - 24))
        rect("requestUpdate", bx, pad, button_width, ACTION_HEIGHT_DP)
    y = pad + plan["header"]
    variant_keys = {
        "xs": ["2x2", "4x2"],
        "s": ["2x2", "4x2"],
        "m": ["4x2", "4x4", "2x2"],
        "l": ["4x4", "4x2", "2x2"],
    }
    variant = next(
        (
            publication.get("variants", {}).get(k)
            for k in variant_keys[plan["band"]]
            if k in publication.get("variants", {})
        ),
        {},
    )
    title = variant.get("title", publication.get("title", ""))
    summary = variant.get("summary", publication.get("summary", ""))
    # Reserve the same capped hero slot as the device. Text line metrics differ by host.
    words = title.split()
    rows = []
    current = ""
    face = font(round(TITLE_SIZE_SP * sx * font_scale))
    for word in words:
        proposed = (current + " " + word).strip()
        if current and draw.textlength(proposed, font=face) > usable * sx:
            rows.append(current)
            current = word
        else:
            current = proposed
    if current:
        rows.append(current)
    for i, text in enumerate(rows[: plan["heroLines"]]):
        line(text, pad, y + i * 22 * font_scale, TITLE_SIZE_SP, tokens["on_surface"])
    if len(rows) > plan["heroLines"]:
        hidden.append("takeaway truncated")
    if plan["summary"]:
        if line(summary, pad, y + plan["heroLines"] * 22 * font_scale + 2, 11, tokens["secondary"]):
            hidden.append("summary truncated")
    elif summary:
        hidden.append("summary hidden in xs band")
    rect("hero", pad, y, usable, plan["heroHeight"])
    y += plan["heroHeight"] + 8
    renderer = "pillow-composition"
    if publication.get("kind") == "image":
        if plan["imageHeight"] >= 1:
            descriptor = select_variant(publication, plan["band"], plan["narrow"], dark)

            def load(d):
                if d.get("assetId") and asset_loader:
                    return asset_loader(d["assetId"])
                return base64.b64decode(d.get("data", ""), validate=True)

            try:
                data = load(descriptor)
            except (ValueError, OSError, StoreError):
                descriptor = publication.get("content", {})
                data = load(descriptor)
                hidden.append("variant unavailable; primary fallback")
            if not data:
                raise ValueError("image asset bytes are unavailable")
            w, h = max(1, round(usable * sx)), max(1, round(plan["imageHeight"] * sy))
            png, source_renderer = _fit_image(
                data,
                descriptor.get("mediaType", "image/png"),
                w,
                h,
                descriptor.get("width"),
                descriptor.get("height"),
                background=tokens["surface"],
            )
            visual = Image.open(io.BytesIO(png)).convert("RGB")
            visual = ImageOps.contain(visual, (w, h))
            image.paste(visual, (round(pad * sx), round(y * sy)))
            rect("visual", pad, y, usable, plan["imageHeight"])
            y += plan["imageHeight"]
            if plan["imageHeight"] < 80:
                hidden.append("limited visual height; open full image for readable detail")
            if source_renderer == "svg-renderer-unavailable":
                renderer = source_renderer
            if source_renderer not in {"pillow"}:
                hidden.append(f"visual renderer: {source_renderer}; validate on Android")
        else:
            hidden.append("visual hidden; tap takeaway for full image")
        if publication.get("presentation"):
            _, count = visible_items(
                publication["presentation"], plan["band"], "narrow" if plan["narrow"] else "wide"
            )
            if count:
                hidden.append(f"{count} presentation items in expanded image only")
            if plan["band"] != "full":
                hidden.append("long visual labels may be shortened; normalized data is retained")
            if any(
                item.get("detail")
                for item in publication["presentation"].get(
                    "rows", publication["presentation"].get("points", [])
                )
            ):
                hidden.append("row details are in the expanded image")
    elif plan["band"] in {"m", "l"}:
        body = variant.get("text", publication.get("content", {}).get("text", ""))
        available = max(
            0,
            (
                height_dp
                - pad
                - plan["footer"]
                - y
                - (19 * font_scale if plan["question"] else 0)
                - (19 * font_scale if plan["ticker"] else 0)
            )
            / (18 * font_scale),
        )
        body_rows = []
        body_face = font(round(14 * sx * font_scale))
        for paragraph in body.splitlines():
            row = ""
            for word in paragraph.split():
                proposed = (row + " " + word).strip()
                if row and draw.textlength(proposed, font=body_face) > usable * sx:
                    body_rows.append(row)
                    row = word
                else:
                    row = proposed
            body_rows.append(row)
        for i, text in enumerate(body_rows[: int(available)]):
            line(text, pad, y + i * 18 * font_scale, 14, tokens["on_surface"])
        y += min(len(body_rows), int(available)) * 18 * font_scale
        if len(body_rows) > int(available):
            hidden.append("body continues in scroll region or expanded view")
    else:
        hidden.append("body hidden in compact band")
    if plan["question"]:
        line(
            "Tap to answer: " + publication["question"]["prompt"], pad, y + 4, 11, tokens["accent"]
        )
        y += 19 * font_scale
    if plan["ticker"]:
        ticker = publication["ticker"]
        rect("ticker", pad, y + 4, usable, 15 * font_scale)
        line(
            f"{ticker.get('title', '')} · {ticker.get('summary', '')}",
            pad,
            y + 4,
            11,
            tokens["secondary"],
        )
    elif publication.get("ticker"):
        hidden.append("ticker hidden by band/width")
    if plan["footer"]:
        line(
            "Updated · advisory preview",
            pad,
            height_dp - pad - plan["footer"] + 4,
            11,
            tokens["secondary"],
        )
    out = io.BytesIO()
    image.save(out, format="PNG")
    return (
        out.getvalue(),
        renderer,
        {
            "band": plan["band"],
            "widthDp": width_dp,
            "heightDp": height_dp,
            "palette": "dark" if dark else "light",
            "fontScale": font_scale,
            "controls": rectangles,
            "hiddenDetails": hidden,
            "advisory": True,
        },
    )
