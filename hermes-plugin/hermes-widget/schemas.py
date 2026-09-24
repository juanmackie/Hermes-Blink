"""OpenAI-style tool schemas exposed by the hermes-widget plugin.

The agent sees these descriptions verbatim, so they carry the parts of the v1
layout contract the model must respect (node/size caps and the https-only rule)
rather than relying on the bundled skill being loaded.
"""

_LAYOUT_PROPERTY = {
    "type": "object",
    "description": (
        "Full v2 layout envelope: {version: 2, widgetId, root, title?, ttlSeconds?, "
        "accentColor?, updatedAt?}. A JSON string containing the same object is also "
        "accepted. version must be 2; there is no v1 migration."
    ),
}

WIDGET_UPDATE = {
    "name": "widget_update",
    "description": (
        "Push a complete v2 widget layout envelope to the user's home-screen "
        "widget. Keep it under 100 nodes and 64 KB. Nodes: column,row,box,list,"
        "text,divider,spacer,badge,calendar(agenda),stat,progress,button,"
        "list_item. Actions: event|refresh|dismiss|review only. For ordinary visual "
        "updates prefer widget_publish; this tool is the legacy structured layout path."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {
                "type": "string",
                "description": (
                    "Widget to replace, e.g. 'hermes-brief'. Defaults to the "
                    "standard brief widget when omitted."
                ),
            },
            "layout": _LAYOUT_PROPERTY,
        },
        "required": ["widget_id", "layout"],
    },
}

WIDGET_VALIDATE = {
    "name": "widget_validate",
    "description": (
        "Dry-run a v2 layout without publishing it. Returns the node count, byte "
        "size, the text styles used, and any design warnings, or the same error "
        "widget_update would return. Use this while iterating: a rejected push "
        "wastes a rate-limit slot, and a bad accepted one replaces a working widget. "
        "Warnings do not block a push."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {
                "type": "string",
                "description": (
                    "Widget the layout is intended for, e.g. 'hermes-brief'. Defaults "
                    "to the standard brief widget when omitted."
                ),
            },
            "layout": _LAYOUT_PROPERTY,
        },
        "required": ["layout"],
    },
}

WIDGET_LIST = {
    "name": "widget_list",
    "description": (
        "List the widget ids that currently have a stored publication or legacy layout. "
        "Use this before pushing to discover whether a widget already exists."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

WIDGET_READ_EVENTS = {
    "name": "widget_read_events",
    "description": (
        "Read interaction events the user's widget sent back (button taps: "
        "refresh/dismiss/review/event). Newest first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since": {
                "type": "string",
                "description": "ISO-8601 timestamp; only return events created after it.",
            },
            "widget_id": {
                "type": "string",
                "description": "Restrict events to a single widget id.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "description": "Maximum number of events to return. Defaults to 200.",
            },
        },
        "required": [],
    },
}

WIDGET_SETUP = {
    "name": "widget_setup",
    "description": (
        "Deterministic, resumable, idempotent setup for the Hermes widget host: "
        "detects the current Hermes host and persistent home, installs the "
        "plugin/skill/gateway startup hook, prepares the widget, and reports "
        "capability-based progress with state needs_user_action|starting|"
        "awaiting_pairing|ready|degraded. Use the bootstrap command for a full "
        "container recreation installation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {"type": "string", "description": "Widget id, default hermes-brief."},
            "schedule": {"type": "string", "description": "Cron schedule, default every 6h."},
        },
        "required": [],
    },
}

WIDGET_PUBLISH = {
    "name": "widget_publish",
    "description": (
        "Publish one accessible update to the personal Hermes widget. Provide a short title "
        "and a required text summary, then exactly one source: plain text, a supported static "
        "inline SVG subset, or a local PNG/JPEG/WebP file. Publishing stores a revision; the "
        "phone fetches it periodically, so success does not mean the user has seen it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {
                "type": "string",
                "description": "Existing widget id; defaults to hermes-brief.",
            },
            "title": {
                "type": "string",
                "description": "Short visible title (required, at most 512 UTF-8 bytes).",
            },
            "summary": {
                "type": "string",
                "description": "Required accessible text summary for the publication.",
            },
            "text": {
                "type": "string",
                "description": "Plain accessible text; mutually exclusive with svg and file_path.",
            },
            "svg": {
                "type": "string",
                "description": (
                    "Inline static SVG only. Scripts, external resources, XML entities, "
                    "foreignObject, animation, filters, and unsupported elements are rejected."
                ),
            },
            "file_path": {
                "type": "string",
                "description": "Local path to a bounded PNG, JPEG, or WebP file; URLs are rejected.",
            },
            "expires_at": {
                "type": "string",
                "description": "Optional ISO-8601 timestamp with timezone.",
            },
            "ttl_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 31536000,
                "description": "Optional expiry lifetime; mutually exclusive with expires_at.",
            },
        },
        "required": ["title", "summary"],
    },
}


WIDGET_STATUS = {
    "name": "widget_status",
    "description": (
        "Report Hermes widget host status: publication revision and summary, separate "
        "per-device downloaded/render_submitted states, connection freshness, routine, "
        "devices, and counts. None of these states claims the user saw content. Use before pairing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {
                "type": "string",
                "description": "Widget to inspect; defaults to hermes-brief.",
            },
        },
        "required": [],
    },
}

WIDGET_MINT_PAIRING_CODE = {
    "name": "widget_mint_pairing_code",
    "description": (
        "Create a short-lived single-use pairing code and show the user the code and its "
        "expiry. Pass server_url as well when the device should scan a QR code instead of "
        "typing: the result then also carries qrPayload and samePhoneLink."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "device_label": {
                "type": "string",
                "description": "Human label for the device, e.g. 'Pixel 9'. Defaults to 'unknown'.",
            },
            "ttl_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 120,
                "description": "Minutes the code stays valid. Defaults to 10.",
            },
            "server_url": {
                "type": "string",
                "description": (
                    "The private URL the phone will reach, e.g. an HTTPS Tailscale Serve "
                    "URL. Include it only when pairing by QR scan; "
                    "omitting it returns just the typed code."
                ),
            },
        },
        "required": [],
    },
}
