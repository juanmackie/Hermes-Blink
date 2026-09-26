"""Dependency-free validation for the Hermes widget layout contract (v2).

Mirrors layout.schema.json. Hand-written on purpose: the plugin must run inside
the Hermes venv without pulling in jsonschema, fastapi, or pydantic.
v2 retains: column,row,box,list,text,divider,spacer,badge,calendar(agenda),
stat,progress,button,list_item. Removed: chart,image,icon,toggle,calendar-month,
url/open_app actions. Layout version is 2; transport stays /v1/.
"""
from __future__ import annotations

import json
import re
from typing import Any

MAX_LAYOUT_BYTES = 64 * 1024
MAX_NODES = 100
LAYOUT_VERSION = 2

_HEX = re.compile(r"^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")

NODE_TYPES = frozenset({
    "column", "row", "box", "list",
    "text", "divider", "spacer", "badge",
    "calendar", "stat", "progress",
    "button", "list_item",
})
CONTAINER_TYPES = frozenset({"column", "row", "box", "list"})
ACTION_KINDS = frozenset({"event", "refresh", "dismiss", "review", "approve", "snooze", "open"})
ACTION_CLASSES = frozenset({
    "reversible", "read_only", "dismiss_reminder", "rerun_check", "staged_patch", "flag",
    "destructive", "external", "irreversible",
})
_TEXT_STYLES = frozenset({"title", "body", "label", "caption"})
_ALIGNMENTS = frozenset({"start", "center", "end", "fill"})
_BUTTON_STYLES = frozenset({"filled", "tonal", "outlined"})
_DELTA_DIRECTIONS = frozenset({"up", "down", "flat"})

_CONTAINER_CAP = {"column": 100, "row": 20, "box": 100, "list": 100}



class ValidationError(ValueError):
    """Raised when a layout cannot be represented by the v2 contract."""


def _fail(message: str) -> None:
    raise ValidationError(message)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_str(node: dict, key: str, *, max_len: int, min_len: int = 0,
               where: str, required: bool = False) -> None:
    if key not in node:
        if required:
            _fail(f"{where}: missing required field '{key}'")
        return
    value = node[key]
    if not isinstance(value, str):
        _fail(f"{where}: '{key}' must be a string")
    if len(value) < min_len:
        _fail(f"{where}: '{key}' must be at least {min_len} characters")
    if len(value) > max_len:
        _fail(f"{where}: '{key}' must be at most {max_len} characters")


def _check_int(node: dict, key: str, *, lo: int, hi: int, where: str,
               required: bool = False) -> None:
    if key not in node:
        if required:
            _fail(f"{where}: missing required field '{key}'")
        return
    value = node[key]
    if not _is_int(value):
        _fail(f"{where}: '{key}' must be an integer")
    if value < lo or value > hi:
        _fail(f"{where}: '{key}' must be between {lo} and {hi}")


def _check_hex(node: dict, key: str, *, where: str) -> None:
    """Colour fields take a 3- or 6-digit hex only, same rule as 'accentColor'.

    The device ignores an 8-digit alpha hex rather than failing to paint, but shipping one
    is a design mistake the author should hear about at push time.
    """
    if key not in node:
        return
    value = node[key]
    if not isinstance(value, str) or not _HEX.match(value):
        _fail(f"{where}: '{key}' must be a hex colour like #7C3AED (3 or 6 digits)")


def _check_action(action: Any, where: str) -> None:
    if not isinstance(action, dict):
        _fail(f"{where}: 'action' must be an object")
    kind = action.get("kind")
    if kind not in ACTION_KINDS:
        _fail(f"{where}: 'action.kind' must be one of {sorted(ACTION_KINDS)}")
    if kind == "event":
        event = action.get("event")
        if not isinstance(event, str) or not event:
            _fail(f"{where}: 'event' action requires a non-empty 'event'")
        if len(event) > 200:
            _fail(f"{where}: 'event' must be at most 200 characters")
        if "payload" in action and not isinstance(action["payload"], dict):
            _fail(f"{where}: 'action.payload' must be an object")
        if "itemId" in action:
            _check_str(action, "itemId", max_len=128, min_len=1, where=where)
    elif kind == "refresh":
        pass  # no extra fields required
    elif kind in ("dismiss", "review", "approve", "snooze", "open"):
        _check_str(action, "itemId", max_len=128, min_len=1, where=where, required=True)
        if "payload" in action and not isinstance(action["payload"], dict):
            _fail(f"{where}: 'action.payload' must be an object")
        if "actionClass" in action and action["actionClass"] not in ACTION_CLASSES:
            _fail(f"{where}: 'action.actionClass' is not allowlisted")
        if "confirmOnDevice" in action and not isinstance(action["confirmOnDevice"], bool):
            _fail(f"{where}: 'action.confirmOnDevice' must be boolean")


def _check_node(node: Any, where: str, counter: list) -> None:
    counter[0] += 1
    if counter[0] > MAX_NODES:
        _fail(f"layout exceeds the {MAX_NODES}-node cap")
    if not isinstance(node, dict):
        _fail(f"{where}: node must be an object")
    node_type = node.get("type")
    if node_type not in NODE_TYPES:
        _fail(f"{where}: unknown node type {node_type!r}")
    label = f"{where}<{node_type}>"

    _check_str(node, "id", max_len=128, where=label)
    _check_str(node, "itemId", max_len=128, where=label)
    if "weight" in node and not _is_number(node["weight"]):
        _fail(f"{label}: 'weight' must be a number")
    if "weight" in node and node["weight"] < 0:
        _fail(f"{label}: 'weight' must be non-negative")
    if "alignment" in node and node["alignment"] not in _ALIGNMENTS:
        _fail(f"{label}: 'alignment' must be one of {sorted(_ALIGNMENTS)}")
    if "padding" in node:
        padding = node["padding"]
        if not isinstance(padding, dict):
            _fail(f"{label}: 'padding' must be an object")
        for edge in ("top", "bottom", "start", "end"):
            if edge in padding and (not _is_int(padding[edge]) or padding[edge] < 0):
                _fail(f"{label}: 'padding.{edge}' must be a non-negative integer")

    if node_type in CONTAINER_TYPES:
        children = node.get("children")
        if not isinstance(children, list):
            _fail(f"{label}: 'children' must be an array")
        if node_type != "list" and not children:
            _fail(f"{label}: 'children' must not be empty")
        cap = _CONTAINER_CAP[node_type]
        if len(children) > cap:
            _fail(f"{label}: 'children' exceeds the {cap}-item cap")
        if "spacing" in node:
            _check_int(node, "spacing", lo=0, hi=64, where=label)
        if node_type == "list" and "maxItems" in node:
            _check_int(node, "maxItems", lo=1, hi=50, where=label)
        for index, child in enumerate(children):
            _check_node(child, f"{label}.children[{index}]", counter)
        return

    if node_type == "text":
        _check_str(node, "value", max_len=500, min_len=1, where=label, required=True)
        if "style" in node and node["style"] not in _TEXT_STYLES:
            _fail(f"{label}: 'style' must be one of {sorted(_TEXT_STYLES)}")
        if "maxLines" in node:
            _check_int(node, "maxLines", lo=1, hi=50, where=label)
        _check_hex(node, "color", where=label)
    elif node_type == "divider":
        if "thickness" in node:
            _check_int(node, "thickness", lo=1, hi=16, where=label)
        _check_hex(node, "color", where=label)
    elif node_type == "spacer":
        _check_int(node, "size", lo=1, hi=256, where=label, required=True)
    elif node_type == "badge":
        _check_str(node, "text", max_len=50, min_len=1, where=label, required=True)
        _check_hex(node, "color", where=label)
    elif node_type == "calendar":
        events = node.get("events")
        if not isinstance(events, list):
            _fail(f"{label}: 'events' must be an array")
        if len(events) > 50:
            _fail(f"{label}: 'events' exceeds the 50-item cap")
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                _fail(f"{label}.events[{index}]: must be an object")
            _check_str(event, "title", max_len=200, min_len=1,
                       where=f"{label}.events[{index}]", required=True)
            _check_hex(event, "color", where=f"{label}.events[{index}]")
        if "mode" in node and node["mode"] != "agenda":
            _fail(f"{label}: 'mode' must be 'agenda' (month removed in v2)")
        if "maxItems" in node:
            _check_int(node, "maxItems", lo=1, hi=50, where=label)
    elif node_type == "stat":
        _check_str(node, "label", max_len=50, where=label, required=True)
        _check_str(node, "value", max_len=50, min_len=1, where=label, required=True)
        _check_str(node, "delta", max_len=20, where=label)
        if "deltaDirection" in node and node["deltaDirection"] not in _DELTA_DIRECTIONS:
            _fail(f"{label}: 'deltaDirection' must be one of {sorted(_DELTA_DIRECTIONS)}")
        _check_hex(node, "color", where=label)
    elif node_type == "progress":
        value = node.get("value")
        if not _is_number(value) or value < 0 or value > 1:
            _fail(f"{label}: 'value' must be a number between 0 and 1")
        _check_str(node, "label", max_len=50, where=label)
        if "showPercent" in node and not isinstance(node["showPercent"], bool):
            _fail(f"{label}: 'showPercent' must be a boolean")
    elif node_type == "button":
        _check_str(node, "label", max_len=200, min_len=1, where=label, required=True)
        if "style" in node and node["style"] not in _BUTTON_STYLES:
            _fail(f"{label}: 'style' must be one of {sorted(_BUTTON_STYLES)}")
        if "action" in node:
            _check_action(node["action"], label)
    elif node_type == "list_item":
        _check_str(node, "title", max_len=200, min_len=1, where=label, required=True)
        _check_str(node, "subtitle", max_len=200, where=label)
        _check_str(node, "trailingText", max_len=50, where=label)
        if "action" in node:
            _check_action(node["action"], label)


def validate_layout(layout: Any) -> None:
    """Validate a full v2 layout envelope. Raises ValidationError."""
    if not isinstance(layout, dict):
        _fail("layout must be a JSON object")
    version = layout.get("version")
    if not _is_int(version) or version != LAYOUT_VERSION:
        _fail(f"'version' must be the integer {LAYOUT_VERSION}")
    _check_str(layout, "widgetId", max_len=128, min_len=1, where="layout", required=True)
    _check_str(layout, "itemId", max_len=128, where="layout")
    if "title" in layout:
        _check_str(layout, "title", max_len=200, where="layout")
    if "ttlSeconds" in layout:
        _check_int(layout, "ttlSeconds", lo=1, hi=86400, where="layout")
    if "accentColor" in layout:
        color = layout["accentColor"]
        if not isinstance(color, str) or not _HEX.match(color):
            _fail("'accentColor' must be a hex colour like #7C3AED")
    if "updatedAt" in layout and not isinstance(layout["updatedAt"], str):
        _fail("'updatedAt' must be an ISO-8601 string")
    if "root" not in layout:
        _fail("layout is missing the required 'root' node")
    _check_node(layout["root"], "root", [0])

    try:
        size = len(json.dumps(layout, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        _fail(f"layout is not JSON-serialisable: {exc}")
    if size > MAX_LAYOUT_BYTES:
        _fail(f"layout payload is {size} bytes, over the {MAX_LAYOUT_BYTES}-byte cap")


# ---------------------------------------------------------------------------
# Dry run: validate and describe a layout without storing it
# ---------------------------------------------------------------------------

MIN_ROOT_PADDING = 12
_TEXT_STYLE_WARN_COUNT = 3
_NODE_WARN_COUNT = 80

# --- the device's breakpoint ladder ------------------------------------------------
# A mirror of android/.../widget/Breakpoints.kt, so a publisher warning and the rendered
# widget agree about what fits. Height drives the band; width is the single-column guard.
BAND_XS_MAX_HEIGHT_DP = 130
BAND_S_MAX_HEIGHT_DP = 185
BAND_M_MAX_HEIGHT_DP = 300
SINGLE_COLUMN_MAX_WIDTH_DP = 245

# Body lines the widget renders before the reader has to scroll, per band. XS and S do not
# render the body at all (a 130dp cell cannot hold a scroll region and a footer).
BAND_BODY_LINES = {"xs": 0, "s": 0, "m": 3, "l": 8}
# Lines of chrome (header, hero, summary, footer) the band spends before the body.
BAND_CHROME_LINES = {"xs": 3, "s": 4, "m": 6, "l": 6}
# Glance 1.1.0 has no ellipsizing, so long strings clip mid-glyph rather than ending in "…".
CHARS_PER_LINE_AT_245DP = 34


def size_band(width_dp: Any, height_dp: Any) -> str | None:
    """The widget band for a registered instance, or None for unusable geometry.

    Accepts ints and floats: the device reports ints, the offline preview derives dp from
    pixels and therefore gets floats.
    """
    for value in (width_dp, height_dp):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
    if width_dp <= 0 or height_dp <= 0:
        return None
    if height_dp < BAND_XS_MAX_HEIGHT_DP:
        return "xs"
    if height_dp < BAND_S_MAX_HEIGHT_DP:
        return "s"
    if height_dp < BAND_M_MAX_HEIGHT_DP:
        return "m"
    return "l"


def is_single_column(width_dp: Any) -> bool:
    if isinstance(width_dp, bool) or not isinstance(width_dp, (int, float)):
        return True
    return width_dp < SINGLE_COLUMN_MAX_WIDTH_DP


def chars_per_line(width_dp: Any) -> int:
    """Approximate body characters per line: ~34 at 245dp, linear in the usable width."""
    if isinstance(width_dp, bool) or not isinstance(width_dp, (int, float)) or width_dp <= 0:
        return CHARS_PER_LINE_AT_245DP
    return max(8, int(CHARS_PER_LINE_AT_245DP * width_dp / 245))


def _smallest_instance(inventory: Any) -> dict[str, Any] | None:
    if not isinstance(inventory, list) or not inventory:
        return None
    valid = [
        item for item in inventory
        if isinstance(item, dict)
        and not isinstance(item.get("widthDp"), bool)
        and not isinstance(item.get("heightDp"), bool)
        and isinstance(item.get("widthDp"), (int, float))
        and isinstance(item.get("heightDp"), (int, float))
    ]
    if not valid:
        return None
    return min(valid, key=lambda item: (item["heightDp"], item["widthDp"]))


def _band_warnings(stats: dict, inventory: Any) -> list[dict[str, str]]:
    """Per-band fit advice, keyed on the smallest instance a device actually registered.

    The device bands the same way (Breakpoints.kt), so the warning names the band the user
    would see rather than a nominal canvas. Two different failure modes are reported:

    * the band has no body region at all (`xs`/`s`), so nodes that need one are lost;
    * the longest string is longer than the band's visible body, so the reader has to
      scroll. The line count is an estimate and says so.
    """
    smallest = _smallest_instance(inventory)
    if smallest is None:
        return []
    band = size_band(smallest["widthDp"], smallest["heightDp"])
    if band is None:
        return []
    width_dp = smallest["widthDp"]
    size = f'{width_dp}x{smallest["heightDp"]}dp'
    warnings: list[dict[str, str]] = []
    body_lines = BAND_BODY_LINES[band]
    per_line = chars_per_line(width_dp)
    longest = int(stats.get("longestText") or 0)
    if body_lines == 0 and stats["nodes"] > 2:
        warnings.append({
            "code": "LAYOUT_TOO_TALL_FOR_BAND",
            "detail": (
                f"{stats['nodes']} nodes cannot render in the {band} band ({size}); "
                f"only the header, one title line"
                f"{' and the summary' if band == 's' else ''} render there"
            ),
        })
    if body_lines and longest > body_lines * per_line:
        warnings.append({
            "code": f"LAYOUT_MAY_SCROLL_{band.upper()}",
            "detail": (
                f"longest string is {longest} characters, about "
                f"{max(1, -(-longest // per_line))} body lines "
                f"(est. {per_line} chars/line at {width_dp}dp) against a {band} budget of "
                f"{body_lines}; the rest is reachable by scrolling the widget"
            ),
        })
    if band in {"xs", "s"} and stats["textNodes"] > 3:
        warnings.append({
            "code": f"TEXT_MAY_CLIP_{band.upper()}",
            "detail": f"{stats['textNodes']} text nodes may not fit in the {band} band ({size})",
        })
    return warnings


def _capacity_warnings(stats: dict, inventory: Any) -> list[dict[str, str]]:
    return _band_warnings(stats, inventory)


def _inspect_node(node: Any, stats: dict) -> None:
    if not isinstance(node, dict):
        return
    stats["nodes"] += 1
    if node.get("type") == "text":
        # The effective step, not the declared one: an omitted style renders as body.
        stats["styles"].add(node.get("style") or "body")
        stats["textNodes"] += 1
        value = node.get("value")
        if isinstance(value, str):
            stats["longestText"] = max(int(stats.get("longestText") or 0), len(value))
    for child in node.get("children") or []:
        _inspect_node(child, stats)


def inspect_layout(layout: Any, inventory: Any = None) -> dict:
    """Validate a layout and describe it without storing anything.

    Raises ValidationError for exactly the reasons widget_update would, so an agent can
    check a layout before spending one of its pushes. The warnings are advisory: the
    renderer copes, the design does not.
    """
    validate_layout(layout)

    stats: dict = {"nodes": 0, "styles": set(), "textNodes": 0, "longestText": 0}
    _inspect_node(layout["root"], stats)
    payload = json.dumps(layout, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    warnings: list[dict[str, str]] = []
    padding = layout["root"].get("padding")
    if isinstance(padding, dict):
        thin = {edge: padding.get(edge, 0) for edge in ("top", "bottom", "start", "end")
                if padding.get(edge, 0) < MIN_ROOT_PADDING}
        if thin:
            warnings.append({
                "code": "ROOT_PADDING_LOW",
                "detail": (
                    f"root padding is thin on {thin} - {MIN_ROOT_PADDING} on every edge keeps "
                    "content clear of the widget border"
                ),
            })
    if len(stats["styles"]) > _TEXT_STYLE_WARN_COUNT:
        warnings.append({
            "code": "TOO_MANY_TEXT_STYLES",
            "detail": (
                f"{len(stats['styles'])} text styles used {sorted(stats['styles'])} - "
                f"pick at most {_TEXT_STYLE_WARN_COUNT}"
            ),
        })
    if stats["nodes"] > _NODE_WARN_COUNT:
        warnings.append({
            "code": "NEAR_NODE_CAP",
            "detail": f"{stats['nodes']} nodes of the {MAX_NODES} allowed - trim before adding more",
        })

    capacity = _capacity_warnings(stats, inventory)
    warnings.extend(capacity)
    return {
        "ok": True,
        "nodeCount": stats["nodes"],
        "bytes": len(payload),
        "maxBytes": MAX_LAYOUT_BYTES,
        "textStyles": sorted(stats["styles"]),
        "warnings": warnings,
        "capacityWarnings": capacity,
    }
