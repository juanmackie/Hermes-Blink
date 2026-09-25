"""Validation and bounded media preparation for widget publications.

The publication API is intentionally separate from the v2 layout tree.  It accepts
one text payload or one immutable visual asset and returns a small, JSON-safe
description that :mod:`store` can atomically attach to a publication revision.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from defusedxml import ElementTree as SafeET
except ImportError:  # pragma: no cover - a missing security dependency is reported below
    SafeET = None  # type: ignore[assignment]

PUBLICATION_VERSION = 1
MAX_TITLE_BYTES = 512
MAX_SUMMARY_BYTES = 4 * 1024
MAX_TEXT_BYTES = 32 * 1024
MAX_SVG_BYTES = 256 * 1024
MAX_ASSET_BYTES = 5 * 1024 * 1024
MAX_RASTER_PIXELS = 16_000_000
MAX_RASTER_DIMENSION = 16_384
MAX_SVG_DIMENSION = 8_192
MAX_SVG_ELEMENTS = 2_000
MAX_SVG_DEPTH = 32
MAX_TTL_SECONDS = 365 * 24 * 60 * 60
# The v2 layout channel uses ttlSeconds only as a device-side "stale" banner; the
# publication channel uses it as a server-side expiry. They are deliberately
# different windows, so each gets an explicit name rather than one shared number.
LAYOUT_MAX_TTL_SECONDS = 24 * 60 * 60
POLL_INTERVAL_SECONDS = 15 * 60
PRIORITIES = ("normal", "high")
PRIORITY_HIGH_MAX_PER_HOUR = 6
PRIORITY_HIGH_MAX_PER_DAY = 30
MAX_ACTIONS = 10
MAX_ACTION_PAYLOAD_BYTES = 4 * 1024
ACTION_KINDS = ("approve", "snooze", "open")
ACTION_CLASSES = (
    "reversible", "read_only", "dismiss_reminder", "rerun_check", "staged_patch", "flag",
    "destructive", "external", "irreversible",
)
SENSITIVE_ACTION_CLASSES = frozenset({"destructive", "external", "irreversible"})
EVENT_VOCABULARY = ("refresh", "dismiss", "review", "event") + ACTION_KINDS
EVENT_EMISSION_POINTS = {
    "refresh": "publication tap fetches now; v2 button action kind=refresh",
    "dismiss": "v2 button action kind=dismiss",
    "review": "opening the publication zoom view",
    "event": "v2 button action kind=event with a caller-supplied name in payload",
    "approve": "queue a validated, allowlisted approval intent",
    "snooze": "queue a validated, allowlisted snooze intent",
    "open": "queue a validated, allowlisted open intent",
}

SUPPORTED_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")
SUPPORTED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

_SVG_NS = "http://www.w3.org/2000/svg"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_NUMBER_RE = re.compile(r"^[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_UNSAFE_VALUE_RE = re.compile(
    r"(?:javascript\s*:|data\s*:|file\s*:|expression\s*\(|@import)",
    re.IGNORECASE,
)
_UNSAFE_SVG_VALUE_RE = re.compile(
    r"(?:https?|ftp)\s*:", re.IGNORECASE
)
_LOCAL_URL_RE = re.compile(r"url\(\s*#([A-Za-z_][A-Za-z0-9_.:-]{0,127})\s*\)", re.IGNORECASE)

SVG_ALLOWED_ELEMENTS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "defs", "linearGradient", "radialGradient",
    "stop", "clipPath", "title", "desc",
}
SVG_ALLOWED_ATTRIBUTES = {
    "id", "class", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r",
    "rx", "ry", "width", "height", "d", "points", "transform", "fill",
    "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset", "stroke-opacity",
    "opacity", "color", "font-family", "font-size", "font-weight", "font-style",
    "text-anchor", "dominant-baseline", "alignment-baseline", "letter-spacing",
    "word-spacing", "viewBox", "preserveAspectRatio", "version", "baseProfile",
    "gradientUnits", "gradientTransform", "spreadMethod", "offset", "stop-color",
    "stop-opacity", "clip-path", "clipPathUnits", "textLength", "lengthAdjust",
    "rotate", "dx", "dy", "vector-effect", "paint-order", "role", "aria-label", "style",
}
# Elements absent from SVG_ALLOWED_ELEMENTS (script, foreignObject, image, use,
# animate, style, ...) are rejected with an error naming the element, never silently
# dropped. The same is true for attributes absent from SVG_ALLOWED_ATTRIBUTES.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PublicationInputError(ValueError):
    """The proposed publication is invalid or exceeds a hard limit."""

    def __init__(self, message: str, *, too_large: bool = False) -> None:
        super().__init__(message)
        self.too_large = too_large


class PublicationTooLarge(PublicationInputError):
    """A publication or decoded image exceeds a configured hard limit."""

    def __init__(self, message: str) -> None:
        super().__init__(message, too_large=True)


@dataclass(frozen=True)
class PreparedAsset:
    media_type: str
    data: bytes
    width: int
    height: int
    sha256: str

    def metadata(self) -> dict[str, Any]:
        return {
            "mediaType": self.media_type,
            "bytes": len(self.data),
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class PreparedPublication:
    title: str
    summary: str
    kind: str
    text: str | None
    asset: PreparedAsset | None
    expires_at: str | None
    max_age_seconds: int | None = None
    priority: str = "normal"
    item_id: str | None = None
    actions: tuple[dict[str, Any], ...] = ()


def capabilities() -> dict[str, Any]:
    """Machine-readable limits and formats exposed to agents and phones."""
    return {
        "publicationVersion": PUBLICATION_VERSION,
        "kinds": ["text", "image"],
        "text": {"mimeType": "text/plain; charset=utf-8", "maxBytes": MAX_TEXT_BYTES},
        "image": {
            "mediaTypes": list(SUPPORTED_MEDIA_TYPES),
            "maxBytes": MAX_ASSET_BYTES,
            "maxPixels": MAX_RASTER_PIXELS,
            "maxDimension": MAX_RASTER_DIMENSION,
        },
        "svg": {
            "mediaType": "image/svg+xml",
            "maxBytes": MAX_SVG_BYTES,
            "maxElements": MAX_SVG_ELEMENTS,
            "maxDimension": MAX_SVG_DIMENSION,
            "staticSubset": True,
            "allowedElements": sorted(SVG_ALLOWED_ELEMENTS),
            "allowedAttributes": sorted(SVG_ALLOWED_ATTRIBUTES),
            "ignoredAttributes": [],
            "rejectedConstructs": [
                "script", "foreignObject", "image", "use", "animate", "style",
                "DOCTYPE/ENTITY", "http(s) or ftp URLs", "event handler attributes",
                "attributes absent from allowedAttributes",
            ],
            "textAllowed": True,
            "safeFontFamilies": [
                "sans-serif", "serif", "monospace",
            ],
            "fontNote": (
                "Only generic families are guaranteed on-device; named families may be "
                "substituted by the Android renderer."
            ),
        },
        "titleMaxBytes": MAX_TITLE_BYTES,
        "summaryRequired": True,
        "summaryMaxBytes": MAX_SUMMARY_BYTES,
        "maxTtlSeconds": MAX_TTL_SECONDS,
        "publicationMaxTtlSeconds": MAX_TTL_SECONDS,
        "layoutMaxTtlSeconds": LAYOUT_MAX_TTL_SECONDS,
        "maxAgeSeconds": MAX_TTL_SECONDS,
        "pollIntervalSeconds": POLL_INTERVAL_SECONDS,
        "priority": {
            "values": list(PRIORITIES),
            "highMaxPerHour": PRIORITY_HIGH_MAX_PER_HOUR,
            "highMaxPerDay": PRIORITY_HIGH_MAX_PER_DAY,
            "overLimit": "degrade_to_normal_and_record",
            "quietHours": "per-widget UTC HH:MM setting",
        },
        "push": {
            "transport": "UnifiedPush",
            "payload": "fetch",
            "contentInPayload": False,
            "endpointRegistration": "PATCH /v1/device",
            "distributor": "self-hosted ntfy or another UnifiedPush distributor",
        },
        "receipts": ["nudge_sent", "fetched", "downloaded", "render_submitted", "rendered"],
        "inventory": {
            "endpoint": "PUT /v1/device/instances",
            "sizeClasses": ["2x2", "4x2", "2x4", "4x4", "custom"],
        },
        "actions": {
            "kinds": list(ACTION_KINDS),
            "classes": list(ACTION_CLASSES),
            "sensitiveClasses": sorted(SENSITIVE_ACTION_CLASSES),
            "queueNotAuthorize": True,
            "endpoint": "POST /v1/widgets/{id}/events",
        },
        "render": {
            "fit": "contain",
            "fitMode": "ContentScale.Fit",
            "note": (
                "Images and SVG are letterboxed inside the widget bounds; they are never "
                "cropped or stretched. lastRendered and recommendedAspectRatio are added "
                "by the server from the most recent render acknowledgement."
            ),
        },
        "events": {
            "vocabulary": list(EVENT_VOCABULARY),
            "emissionPoints": EVENT_EMISSION_POINTS,
        },
    }


def _normalize_priority(priority: Any) -> str:
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise PublicationInputError("priority must be 'normal' or 'high'")
    return priority


def _normalize_item_id(item_id: Any, name: str = "item_id") -> str | None:
    if item_id is None:
        return None
    if not isinstance(item_id, str) or not _ID_RE.fullmatch(item_id):
        raise PublicationInputError(f"{name} must be a stable identifier of at most 128 characters")
    return item_id


def _normalize_actions(
    actions: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    publication_item_id: str | None,
) -> tuple[dict[str, Any], ...]:
    if actions is None:
        return ()
    if not isinstance(actions, (list, tuple)) or len(actions) > MAX_ACTIONS:
        raise PublicationInputError(f"actions must be an array of at most {MAX_ACTIONS} items")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(actions):
        if not isinstance(raw, dict):
            raise PublicationInputError(f"actions[{index}] must be an object")
        kind = raw.get("kind", raw.get("action"))
        if kind not in ACTION_KINDS:
            raise PublicationInputError(f"actions[{index}].kind must be one of {list(ACTION_KINDS)}")
        item_id = _normalize_item_id(
            raw.get("itemId", raw.get("item_id", publication_item_id)),
            f"actions[{index}].itemId",
        )
        if not item_id:
            raise PublicationInputError(f"actions[{index}] requires itemId")
        action_key = (str(kind), item_id)
        if action_key in seen:
            raise PublicationInputError(f"actions contains duplicate {kind} itemId {item_id!r}")
        seen.add(action_key)
        action_class = raw.get("actionClass", raw.get("action_class", "reversible"))
        if action_class not in ACTION_CLASSES:
            raise PublicationInputError(
                f"actions[{index}].actionClass must be one of {list(ACTION_CLASSES)}"
            )
        payload = raw.get("payload", {})
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise PublicationInputError(f"actions[{index}].payload must be an object")
        try:
            payload_size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise PublicationInputError(f"actions[{index}].payload is not JSON-safe") from exc
        if payload_size > MAX_ACTION_PAYLOAD_BYTES:
            raise PublicationTooLarge(f"actions[{index}].payload exceeds the {MAX_ACTION_PAYLOAD_BYTES}-byte limit")
        label = raw.get("label", kind.capitalize())
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise PublicationInputError(f"actions[{index}].label must be a short string")
        confirm = raw.get("confirmOnDevice", raw.get("confirm_on_device", False))
        if not isinstance(confirm, bool):
            raise PublicationInputError(f"actions[{index}].confirmOnDevice must be boolean")
        result.append({
            "kind": kind,
            "itemId": item_id,
            "label": label.strip(),
            "actionClass": action_class,
            "confirmOnDevice": confirm,
            "payload": payload,
        })
    return tuple(result)


def _normalize_max_age(max_age_seconds: int | None) -> int | None:
    if max_age_seconds is None:
        return None
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int):
        raise PublicationInputError("max_age_seconds must be an integer")
    if not 1 <= max_age_seconds <= MAX_TTL_SECONDS:
        raise PublicationInputError(
            f"max_age_seconds must be between 1 and {MAX_TTL_SECONDS}"
        )
    return max_age_seconds


def prepare_publication(
    *,
    title: Any,
    summary: Any,
    text: str | None = None,
    svg: str | None = None,
    file_path: str | os.PathLike[str] | None = None,
    expires_at: str | datetime | None = None,
    ttl_seconds: int | None = None,
    max_age_seconds: int | None = None,
    priority: str = "normal",
    item_id: str | None = None,
    actions: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    now: datetime | None = None,
) -> PreparedPublication:
    """Validate exactly one content source and return bounded publication data."""
    title_value = _bounded_text(title, "title", MAX_TITLE_BYTES, required=True, single_line=True)
    summary_value = _bounded_text(summary, "summary", MAX_SUMMARY_BYTES, required=True)
    max_age = _normalize_max_age(max_age_seconds)
    priority_value = _normalize_priority(priority)
    item_id_value = _normalize_item_id(item_id, "item_id")
    action_values = _normalize_actions(actions, item_id_value)

    sources = [("text", text), ("svg", svg), ("file", file_path)]
    supplied = [(kind, value) for kind, value in sources if value is not None]
    if len(supplied) != 1:
        raise PublicationInputError("provide exactly one of text, svg, or file_path")
    kind, value = supplied[0]

    expiry = _normalize_expiry(expires_at, ttl_seconds, now or datetime.now(timezone.utc))
    if kind == "text":
        text_value = _bounded_text(value, "text", MAX_TEXT_BYTES, required=True)
        return PreparedPublication(
            title_value, summary_value, "text", text_value, None, expiry, max_age,
            priority_value, item_id_value, action_values,
        )

    if kind == "svg":
        if not isinstance(value, str):
            raise PublicationInputError("svg must be inline SVG text")
        raw = value.encode("utf-8")
        width, height = validate_svg(raw)
        return PreparedPublication(
            title_value,
            summary_value,
            "image",
            None,
            PreparedAsset("image/svg+xml", raw, width, height, hashlib.sha256(raw).hexdigest()),
            expiry,
            max_age,
            priority_value,
            item_id_value,
            action_values,
        )

    if not isinstance(value, (str, os.PathLike)):
        raise PublicationInputError("file_path must be a local filesystem path")
    path_text = os.fspath(value)
    if not path_text.strip() or "\x00" in path_text:
        raise PublicationInputError("file_path must be a local filesystem path")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", path_text):
        raise PublicationInputError("file_path must be local; URLs are not accepted")
    path = Path(path_text).expanduser()
    data = _read_bounded_file(path)
    media_type, width, height = validate_raster(data, path.suffix)
    return PreparedPublication(
        title_value,
        summary_value,
        "image",
        None,
        PreparedAsset(media_type, data, width, height, hashlib.sha256(data).hexdigest()),
        expiry,
        max_age,
        priority_value,
        item_id_value,
        action_values,
    )


def validate_svg(raw: bytes) -> tuple[int, int]:
    """Validate the documented static SVG subset and return intrinsic dimensions."""
    if len(raw) > MAX_SVG_BYTES:
        raise PublicationTooLarge(
            f"SVG exceeds the {MAX_SVG_BYTES}-byte limit ({len(raw)} bytes)"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PublicationInputError("SVG must be UTF-8") from exc
    without_decl = re.sub(r"^\s*<\?xml[^?]*\?>", "", text, count=1, flags=re.IGNORECASE)
    if "<?" in without_decl:
        raise PublicationInputError("SVG processing instructions are not supported")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise PublicationInputError("SVG DOCTYPE and XML entity declarations are forbidden")
    if re.search(r"xmlns\s*:\s*[^=]", text, re.IGNORECASE):
        raise PublicationInputError("SVG namespace prefixes are not supported")
    if _UNSAFE_VALUE_RE.search(text):
        raise PublicationInputError("SVG contains a script or external-resource URI")
    if SafeET is None:
        raise PublicationInputError(
            "SVG publication requires the defusedxml security package"
        )
    try:
        root = SafeET.fromstring(raw)
    except Exception as exc:  # defusedxml raises ParseError plus safety subclasses
        raise PublicationInputError(f"SVG is not well-formed XML: {exc}") from exc

    root_namespace, root_local = _expanded_name(root.tag)
    if root_local != "svg" or root_namespace not in ("", _SVG_NS):
        raise PublicationInputError("SVG root must be a supported <svg> element")

    elements = 0
    stack: list[tuple[Any, int]] = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        elements += 1
        if elements > MAX_SVG_ELEMENTS:
            raise PublicationTooLarge(f"SVG exceeds the {MAX_SVG_ELEMENTS}-element limit")
        if depth > MAX_SVG_DEPTH:
            raise PublicationInputError(f"SVG nesting exceeds the {MAX_SVG_DEPTH}-level limit")
        namespace, local = _expanded_name(element.tag)
        if namespace != root_namespace or local not in SVG_ALLOWED_ELEMENTS:
            raise PublicationInputError(f"unsupported SVG element <{local or element.tag}>")
        for raw_name, attr_value in element.attrib.items():
            attr_ns, attr_local = _expanded_name(raw_name)
            if attr_ns not in ("", _SVG_NS, _XML_NS) or attr_local in {"href", "src"}:
                raise PublicationInputError(f"unsupported or external SVG attribute {raw_name!r}")
            if attr_local not in SVG_ALLOWED_ATTRIBUTES:
                raise PublicationInputError(f"unsupported SVG attribute {attr_local!r}")
            _validate_svg_value(attr_local, attr_value)
        stack.extend((child, depth + 1) for child in element)

    width = _svg_length(root.get("width"))
    height = _svg_length(root.get("height"))
    if width is None or height is None:
        view_box = _parse_view_box(root.get("viewBox"))
        if view_box is not None:
            width = width if width is not None else view_box[2]
            height = height if height is not None else view_box[3]
    if width is None or height is None:
        raise PublicationInputError("SVG needs positive width/height or a viewBox")
    for name, number in (("width", width), ("height", height)):
        if not math.isfinite(number) or number <= 0 or number > MAX_SVG_DIMENSION:
            raise PublicationInputError(
                f"SVG {name} must be between 1 and {MAX_SVG_DIMENSION}"
            )
    return _rounded_dimension(width), _rounded_dimension(height)


def validate_raster(data: bytes, suffix: str = "") -> tuple[str, int, int]:
    """Identify a supported raster from its magic and return (media type, width, height)."""
    if len(data) > MAX_ASSET_BYTES:
        raise PublicationTooLarge(
            f"image exceeds the {MAX_ASSET_BYTES}-byte limit ({len(data)} bytes)"
        )
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type, dimensions = "image/png", _png_dimensions(data)
    elif data.startswith(b"\xff\xd8"):
        media_type, dimensions = "image/jpeg", _jpeg_dimensions(data)
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        media_type, dimensions = "image/webp", _webp_dimensions(data)
    else:
        raise PublicationInputError("file must contain a PNG, JPEG, or WebP image")
    expected = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix.lower())
    if expected and expected != media_type:
        raise PublicationInputError(
            f"file extension {suffix.lower()!r} does not match detected {media_type}"
        )
    width, height = dimensions
    _validate_raster_dimensions(width, height)
    return media_type, width, height


def _read_bounded_file(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            if not os.path.isfile(path) or stat.st_size > MAX_ASSET_BYTES:
                raise PublicationTooLarge(
                    f"image exceeds the {MAX_ASSET_BYTES}-byte limit ({stat.st_size} bytes)"
                )
            data = handle.read(MAX_ASSET_BYTES + 1)
    except FileNotFoundError as exc:
        raise PublicationInputError("file_path does not exist") from exc
    except IsADirectoryError as exc:
        raise PublicationInputError("file_path must identify a regular file") from exc
    if len(data) > MAX_ASSET_BYTES:
        raise PublicationTooLarge(f"image exceeds the {MAX_ASSET_BYTES}-byte limit")
    return data


def _bounded_text(value: Any, name: str, limit: int, *, required: bool, single_line: bool = False) -> str:
    if not isinstance(value, str):
        raise PublicationInputError(f"{name} must be a string")
    text = value.strip()
    if required and not text:
        raise PublicationInputError(f"{name} is required")
    if _CONTROL_RE.search(text):
        raise PublicationInputError(f"{name} contains unsupported control characters")
    if single_line and ("\n" in text or "\r" in text):
        raise PublicationInputError(f"{name} must be a single line")
    size = len(text.encode("utf-8"))
    if size > limit:
        raise PublicationTooLarge(f"{name} exceeds the {limit}-byte limit ({size} bytes)")
    return text


def _normalize_expiry(
    expires_at: str | datetime | None,
    ttl_seconds: int | None,
    now: datetime,
) -> str | None:
    if expires_at is not None and ttl_seconds is not None:
        raise PublicationInputError("provide expires_at or ttl_seconds, not both")
    if ttl_seconds is not None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise PublicationInputError("ttl_seconds must be an integer")
        if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise PublicationInputError(
                f"ttl_seconds must be between 1 and {MAX_TTL_SECONDS}"
            )
        return _utc_iso(now + timedelta(seconds=ttl_seconds))
    if expires_at is None:
        return None
    if isinstance(expires_at, datetime):
        parsed = expires_at
    elif isinstance(expires_at, str):
        try:
            parsed = datetime.fromisoformat(expires_at.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise PublicationInputError("expires_at must be an ISO-8601 timestamp") from exc
    else:
        raise PublicationInputError("expires_at must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        raise PublicationInputError("expires_at must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    if parsed <= now.astimezone(timezone.utc):
        raise PublicationInputError("expires_at must be in the future")
    return _utc_iso(parsed)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _expanded_name(name: str) -> tuple[str, str]:
    if name.startswith("{"):
        namespace, local = name[1:].split("}", 1)
        return namespace, local
    return "", name


def _validate_svg_value(name: str, value: str) -> None:
    if _UNSAFE_VALUE_RE.search(value) or _UNSAFE_SVG_VALUE_RE.search(value):
        raise PublicationInputError("SVG contains a script or external-resource URI")
    if name == "style" and ("\\" in value or "@" in value):
        raise PublicationInputError("SVG style values cannot use escapes or imports")
    if "url(" in value.lower():
        leftovers = _LOCAL_URL_RE.sub("", value)
        if "url(" in leftovers.lower():
            raise PublicationInputError("SVG may reference only local fragment URLs")
    if name == "id" and not _ID_RE.fullmatch(value):
        raise PublicationInputError("SVG id contains unsupported characters")


def _svg_length(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if text.lower().endswith("px"):
        text = text[:-2].strip()
    if not _NUMBER_RE.fullmatch(text):
        raise PublicationInputError("SVG width/height must be positive pixel numbers")
    try:
        return float(text)
    except (OverflowError, ValueError) as exc:
        raise PublicationInputError("SVG width/height must be finite") from exc


def _rounded_dimension(value: float) -> int:
    try:
        return int(round(value))
    except (OverflowError, ValueError) as exc:  # defensive; range is checked first
        raise PublicationInputError("SVG dimensions must be finite") from exc


def _parse_view_box(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    parts = re.split(r"[\s,]+", value.strip())
    if len(parts) != 4 or any(not _NUMBER_RE.fullmatch(part) for part in parts):
        raise PublicationInputError("SVG viewBox must contain four numbers")
    try:
        result = tuple(float(part) for part in parts)
    except (OverflowError, ValueError) as exc:
        raise PublicationInputError("SVG viewBox values must be finite") from exc
    if not all(math.isfinite(value) for value in result):
        raise PublicationInputError("SVG viewBox values must be finite")
    if result[2] <= 0 or result[3] <= 0:
        raise PublicationInputError("SVG viewBox width and height must be positive")
    return result  # type: ignore[return-value]


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PublicationInputError("PNG has an invalid signature")
    offset = 8
    first_chunk = True
    saw_iend = False
    dimensions: tuple[int, int] | None = None
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        chunk_end = offset + 12 + length
        if length > len(data) or chunk_end > len(data):
            raise PublicationInputError("PNG contains a truncated chunk")
        chunk_type = data[offset + 4:offset + 8]
        chunk_data = data[offset + 8:offset + 8 + length]
        expected_crc = int.from_bytes(data[offset + 8 + length:chunk_end], "big")
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise PublicationInputError("PNG contains an invalid chunk checksum")
        if first_chunk:
            if chunk_type != b"IHDR" or length != 13:
                raise PublicationInputError("PNG has an invalid or missing IHDR header")
            dimensions = struct.unpack(">II", chunk_data[:8])
            first_chunk = False
        if chunk_type == b"IEND":
            saw_iend = True
            if length != 0 or chunk_end != len(data):
                raise PublicationInputError("PNG has trailing or invalid IEND data")
            break
        offset = chunk_end
    if first_chunk or dimensions is None or not saw_iend:
        raise PublicationInputError("PNG is incomplete or has no IHDR/IEND chunks")
    return dimensions


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    # Only a leading SOI and a readable frame header are required. Trailing bytes are
    # allowed on purpose: Pixel cameras save Motion Photos, which append an MP4 after
    # the JPEG's EOI, so requiring the file to end with FFD9 rejected real photos.
    if not data.startswith(b"\xff\xd8"):
        raise PublicationInputError("JPEG is missing its start-of-image marker")
    index = 2
    sof_markers = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
    while index < len(data):
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        length = int.from_bytes(data[index:index + 2], "big")
        if length < 2 or index + length > len(data):
            raise PublicationInputError("JPEG contains a truncated segment")
        if marker in sof_markers and length >= 7:
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            return width, height
        index += length
    raise PublicationInputError("JPEG has no valid frame header")


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 16:
        raise PublicationInputError("WebP header is truncated")
    declared = int.from_bytes(data[4:8], "little") + 8
    if declared != len(data):
        raise PublicationInputError("WebP RIFF length does not match the file size")
    index = 12
    while index + 8 <= len(data):
        chunk_type = data[index:index + 4]
        chunk_size = int.from_bytes(data[index + 4:index + 8], "little")
        payload_start = index + 8
        payload_end = payload_start + chunk_size
        if payload_end > len(data):
            raise PublicationInputError("WebP contains a truncated chunk")
        payload = data[payload_start:payload_end]
        if chunk_type == b"VP8X" and len(payload) >= 10:
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
            return width, height
        if chunk_type == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(payload[6:8], "little") & 0x3FFF
            height = int.from_bytes(payload[8:10], "little") & 0x3FFF
            return width, height
        if chunk_type == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
            bits = int.from_bytes(payload[1:5], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        index = payload_end + (payload_end & 1)
    raise PublicationInputError("WebP has no supported image chunk")


def _validate_raster_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise PublicationInputError("image dimensions must be positive")
    if width > MAX_RASTER_DIMENSION or height > MAX_RASTER_DIMENSION:
        raise PublicationInputError(
            f"image dimensions cannot exceed {MAX_RASTER_DIMENSION} pixels"
        )
    if width * height > MAX_RASTER_PIXELS:
        raise PublicationInputError(
            f"image exceeds the {MAX_RASTER_PIXELS}-pixel decoded-size limit"
        )
