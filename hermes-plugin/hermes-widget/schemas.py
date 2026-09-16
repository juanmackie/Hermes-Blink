"""OpenAI-style tool schemas exposed by the hermes-widget plugin.

The agent sees these descriptions verbatim, so they carry the parts of the v1
layout contract the model must respect (node/size caps and the https-only rule)
rather than relying on the bundled skill being loaded.
"""

WIDGET_UPDATE = {
    "name": "widget_update",
    "description": (
        "Push a complete v2 widget layout envelope to the user's home-screen "
        "widget. Keep it under 100 nodes and 64 KB. Nodes: column,row,box,list,"
        "text,divider,spacer,badge,calendar(agenda),stat,progress,button,"
        "list_item. Actions: event|refresh|dismiss|review only."
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
            "layout": {
                "type": "object",
                "description": (
                    "Full v2 layout envelope: {version: 2, widgetId, root, "
                    "title?, ttlSeconds?, accentColor?, updatedAt?}. A JSON "
                    "string containing the same object is also accepted. "
                    "v1 layouts are auto-migrated."
                ),
            },
        },
        "required": ["widget_id", "layout"],
    },
}

WIDGET_LIST = {
    "name": "widget_list",
    "description": (
        "List the widget ids that currently have a stored layout. Use this "
        "before pushing to discover whether a widget already exists."
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

WIDGET_MINT_PAIRING_CODE = {
    "name": "widget_mint_pairing_code",
    "description": (
        "Create a short-lived pairing code the user types into the Android "
        "widget to link a new device. Show the code and its expiry to the user."
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
        },
        "required": [],
    },
}
