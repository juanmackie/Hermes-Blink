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
ACTION_KINDS = frozenset({"event", "refresh", "dismiss", "review"})
_TEXT_STYLES = frozenset({"title", "body", "label", "caption"})
_ALIGNMENTS = frozenset({"start", "center", "end", "fill"})
_BUTTON_STYLES = frozenset({"filled", "tonal", "outlined"})
_DELTA_DIRECTIONS = frozenset({"up", "down", "flat"})

_CONTAINER_CAP = {"column": 100, "row": 20, "box": 100, "list": 100}

# v1 types/fields that v2 drops — used by migration to strip/replace.
_REMOVED_NODE_TYPES = frozenset({"chart", "image", "icon", "toggle"})
_REMOVED_ACTION_KINDS = frozenset({"url", "open_app"})


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
    elif kind in ("dismiss", "review"):
        _check_str(action, "itemId", max_len=128, min_len=1, where=where, required=True)
        if "payload" in action and not isinstance(action["payload"], dict):
            _fail(f"{where}: 'action.payload' must be an object")


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
    elif node_type == "divider":
        if "thickness" in node:
            _check_int(node, "thickness", lo=1, hi=16, where=label)
    elif node_type == "spacer":
        _check_int(node, "size", lo=1, hi=256, where=label, required=True)
    elif node_type == "badge":
        _check_str(node, "text", max_len=50, min_len=1, where=label, required=True)
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
        _check_str(node, "leadingIcon", max_len=50, where=label)
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
# Migration: v1 -> v2
# ---------------------------------------------------------------------------

def _needs_refresh_node(reason: str = "unsupported content removed") -> dict:
    return {
        "type": "text",
        "value": "Content updated — pull to refresh for the latest brief.",
        "style": "caption",
    }


def _migrate_node(node: Any) -> dict | None:
    """Convert a single v1 node to v2. Returns None if the node should be dropped."""
    if not isinstance(node, dict):
        return None
    t = node.get("type")
    # Drop removed types entirely; caller will insert refresh-needed placeholder if needed
    if t in _REMOVED_NODE_TYPES:
        return None
    # Calendar: only agenda mode survives
    if t == "calendar" and node.get("mode") == "month":
        return None
    # Containers: recurse into children, drop unsupported children
    if t in CONTAINER_TYPES:
        children = node.get("children", [])
        if not isinstance(children, list):
            return None
        kept: list[dict] = []
        dropped = 0
        for child in children:
            migrated = _migrate_node(child)
            if migrated is not None:
                kept.append(migrated)
            else:
                dropped += 1
        if not kept:
            # If all children were dropped and container was non-empty, keep a refresh placeholder
            if dropped > 0:
                return _needs_refresh_node()
            return None
        out = {k: v for k, v in node.items() if k not in ("children",)}
        # Strip calendar month field if present
        if out.get("mode") == "month":
            out.pop("mode", None)
        # Strip removed action kinds from container-level action if any
        if "action" in out and isinstance(out["action"], dict):
            if out["action"].get("kind") in _REMOVED_ACTION_KINDS:
                out.pop("action")
        out["children"] = kept
        if dropped > 0 and t != "list":
            # Surface that something was removed
            pass
        return out
    # Leaf: strip url/open_app actions
    if "action" in node and isinstance(node["action"], dict):
        if node["action"].get("kind") in _REMOVED_ACTION_KINDS:
            out = dict(node)
            out.pop("action")
            return out
    # Strip removed fields that are harmless but no longer in schema
    out = {k: v for k, v in node.items() if k not in ("chartType", "series", "labels", "stateKey", "height", "showAxis", "url", "contentDescription", "cornerRadius", "name", "tint", "value")}
    # Re-add fields that are valid for retained types
    for keep in ("id", "value", "label", "text", "title", "subtitle", "leadingIcon", "trailingText",
                 "style", "maxLines", "color", "thickness", "size", "events", "mode", "maxItems",
                 "delta", "deltaDirection", "weight", "spacing", "alignment", "padding", "action",
                 "showPercent", "accentColor"):
        if keep in node and keep not in out:
            out[keep] = node[keep]
    # Fix calendar mode
    if out.get("type") == "calendar" and out.get("mode") == "month":
        out["mode"] = "agenda"
    # Ensure required fields for retained types
    if t == "text" and "value" not in out:
        # image/icon/chart had no value — replace with placeholder
        return _needs_refresh_node()
    if t == "badge" and "text" not in out:
        return None
    return out


def migrate_layout(layout: dict) -> tuple[dict, bool]:
    """Convert a v1 envelope to v2. Returns (migrated_layout, had_removed_content).

    Supported legacy content (text, stat, calendar-agenda, etc.) is kept.
    Unsupported sections (chart/image/icon/toggle/month/deeplink) are replaced
    with an explicit refresh-needed state. Caller should back up before calling
    and regenerate the brief if had_removed_content is True.
    """
    if not isinstance(layout, dict):
        raise ValidationError("layout must be a JSON object")
    version = layout.get("version")
    if version == LAYOUT_VERSION:
        return layout, False
    if version != 1:
        raise ValidationError(f"cannot migrate layout version {version!r} to {LAYOUT_VERSION}")

    had_removed = False
    # Check top-level for removed content before migration
    raw = json.dumps(layout)
    for marker in ('"chart"', '"image"', '"icon"', '"toggle"', '"month"', '"open_app"', '"deeplink"', '"chartType"'):
        if marker in raw:
            had_removed = True
            break

    migrated: dict[str, Any] = {}
    for k, v in layout.items():
        if k == "version":
            migrated["version"] = LAYOUT_VERSION
        elif k == "root":
            new_root = _migrate_node(v)
            if new_root is None:
                migrated["root"] = {
                    "type": "column",
                    "children": [_needs_refresh_node()],
                }
                had_removed = True
            else:
                migrated["root"] = new_root
                # If any child was dropped, mark had_removed
                if json.dumps(new_root) != json.dumps(v):
                    # conservative: if shape changed, assume removed content
                    had_removed = True
        else:
            migrated[k] = v

    # Validate the result; if it fails, fall back to explicit refresh-needed state
    try:
        validate_layout(migrated)
    except ValidationError:
        migrated = {
            "version": LAYOUT_VERSION,
            "widgetId": layout.get("widgetId", "hermes-brief"),
            "title": layout.get("title", "Brief"),
            "root": {
                "type": "column",
                "children": [_needs_refresh_node()],
            },
        }
        if "updatedAt" in layout:
            migrated["updatedAt"] = layout["updatedAt"]
        had_removed = True

    return migrated, had_removed
