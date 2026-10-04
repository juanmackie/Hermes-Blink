"""OpenAI-style tool schemas exposed by the hermes-widget plugin."""

WIDGET_LIST = {
    "name": "widget_list",
    "description": (
        "List the widget ids that currently have a publication or are configured. "
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
WIDGET_PUBLISH = {
    "name": "widget_publish",
    "description": (
        "Publish one accessible update to the personal Hermes widget. Provide a short title "
        "and a required text summary, then exactly one source: plain text, a supported static "
        "inline SVG subset, a local PNG/JPEG/WebP file, or structured presentation data. Publishing stores a revision; the "
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
                "description": "Plain accessible text; mutually exclusive with svg, file_path, and presentation.",
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
                "description": "Optional server-side expiry lifetime; mutually exclusive with expires_at.",
            },
            "max_age_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 31536000,
                "description": (
                    "Optional freshness window since publishedAt. A revision older than "
                    "this when the phone fetches is dropped (410 publication_stale) and "
                    "reported as stale instead of rendered late."
                ),
            },
            "dark_palette": {
                "type": "boolean",
                "default": False,
                "description": "Use the explicit dark palette variant for this publication.",
            },
            "variants": {
                "type": "object",
                "description": "Optional text variants keyed by registered size class (2x2, 4x2, 2x4, 4x4).",
            },
            "presentation": {
                "type": "object",
                "description": "Exactly one source. Blink renders typography/layout. metric/progress: label,value,unit,target (required for progress); comparison: rows of label,value,detail; chart: style=line/bar, points of label,value; timeline: events of date,label,detail. At most 32 items, finite numbers; units optional.",
                "properties": {
                    "type": {"type": "string", "enum": ["metric", "progress", "comparison", "chart", "timeline"]},
                    "label": {"type": "string", "maxLength": 120},
                    "value": {"type": "number"},
                    "unit": {"type": "string", "maxLength": 120},
                    "target": {"type": "number", "exclusiveMinimum": 0},
                    "style": {"type": "string", "enum": ["line", "bar"]},
                    "rows": {"type": "array", "maxItems": 32, "items": {"type": "object"}},
                    "points": {"type": "array", "maxItems": 32, "items": {"type": "object"}},
                    "events": {"type": "array", "maxItems": 32, "items": {"type": "object"}},
                },
                "required": ["type"],
                "additionalProperties": False,
            },
            "visual_variants": {"type": "object", "description": "Optional custom image sources keyed m/l-narrow/wide-light/dark, e.g. m-narrow-dark. Each has exactly one svg or file_path. Requires a primary SVG/raster; structured presentations generate these automatically."},
            "work_context": {"type": "object", "properties": {"sources": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 512}}, "session": {"type": "string", "maxLength": 256}, "recheck": {"type": "string", "maxLength": 2000}}, "additionalProperties": False},
            "refresh_id": {"type": "string", "description": "Lease returned by widget_status when refreshing. Completes requests atomically after publishing or deduplicating."},
            "provenance": {
                "type": "string",
                "enum": ["verified", "from_price", "estimate"],
                "description": "Evidence class shown honestly on the widget surface.",
            },
            "priority": {
                "type": "string",
                "enum": ["normal", "high"],
                "description": (
                    "High asks the phone to wake through its content-free UnifiedPush "
                    "endpoint; it is rate-limited and may degrade to normal visibly."
                ),
            },
            "item_id": {
                "type": "string",
                "maxLength": 128,
                "description": "Stable item identity for an optional action round-trip.",
            },
            "actions": {
                "type": "array",
                "maxItems": 10,
                "description": "Allowlisted, queue-not-authorise actions attached to stable itemIds.",
                "items": {"type": "object"},
            },
            "ticker": {
                "type": "object",
                "description": "Optional independent ticker update; when supplied without a hero source, the current hero is retained.",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "text": {"type": "string"},
                    "priority": {"type": "string", "enum": ["normal", "high"]},
                    "max_age_seconds": {"type": "integer", "minimum": 1, "maximum": 31536000},
                    "item_id": {"type": "string", "maxLength": 128},
                    "provenance": {"type": "string", "enum": ["verified", "from_price", "estimate"]},
                },
                "required": ["title", "summary"],
            },
        },
        "required": ["title", "summary"],
    },
}


WIDGET_PREVIEW = {
    "name": "widget_preview",
    "description": (
        "Render the exact publication or a proposed publication to bounded PNG previews "
        "at registered widget sizes without publishing it. This is a design aid, not a "
        "claim that the phone rendered the content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {"type": "string", "description": "Widget id; defaults to hermes-brief."},
            "publication": {
                "type": "object",
                "description": "Optional proposed publication envelope; omit to preview the current revision.",
            },
            "sizes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional size classes; omit to use the device inventory.",
            },
            "palette": {"type": "string", "enum": ["light", "dark"]},
            "font_scale": {"type": "number", "minimum": 0.5, "maximum": 3},
        },
        "required": [],
    },
}

WIDGET_FINISH_REFRESH = {
    "name": "widget_finish_refresh",
    "description": "Complete an acknowledged refresh without publishing. Report unchanged only after rechecking sources; report failed with a reason if they cannot be rechecked. Old data remains dated honestly.",
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {"type": "string"},
            "refresh_id": {"type": "string"},
            "outcome": {"type": "string", "enum": ["unchanged", "failed"]},
            "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
        },
        "required": ["refresh_id", "outcome", "reason"],
    },
}


WIDGET_READ_INTENTS = {
    "name": "widget_read_intents",
    "description": (
        "Read durable, allowlisted widget action intents and their audit trail. Intents are "
        "queued taps, not executions; resolve them explicitly after the agent has validated the work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {"type": "string"},
            "status": {"type": "string", "enum": ["queued", "awaiting_confirmation", "applied", "declined", "held", "expired"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "required": [],
    },
}


WIDGET_RESOLVE_INTENT = {
    "name": "widget_resolve_intent",
    "description": (
        "Record the terminal outcome of a widget intent after the agent has handled it. "
        "This records a decision; it never executes the queued operation. Sensitive "
        "actions must first be confirmed through the paired device."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intent_id": {"type": "string", "maxLength": 160},
            "outcome": {"type": "string", "enum": ["applied", "declined", "held", "expired"]},
            "result": {"type": "string", "maxLength": 2000},
        },
        "required": ["intent_id", "outcome"],
    },
}


WIDGET_ASK = {
    "name": "widget_ask",
    "description": "Open one bounded free-text question on the widget for the user to answer; the answer is queued, never executed automatically.",
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {"type": "string"},
            "prompt": {"type": "string", "minLength": 1, "maxLength": 500},
            "item_id": {"type": "string", "maxLength": 128},
        },
        "required": ["prompt"],
    },
}

WIDGET_READ_QUESTIONS = {
    "name": "widget_read_questions",
    "description": "Read bounded widget questions and their answers for chat mirroring or follow-up.",
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {"type": "string"},
            "status": {"type": "string", "enum": ["open", "answered"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": [],
    },
}

WIDGET_WATCH = {
    "name": "widget_watch",
    "description": "Manage standing widget watches: create, list, pause or resume, and evaluate due watches with a bounded source snapshot.",
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["create", "list", "pause", "tick"]},
            "widget_id": {"type": "string"},
            "name": {"type": "string", "maxLength": 120},
            "condition": {"type": "object"},
            "payload": {"type": "object"},
            "cadence_seconds": {"type": "integer", "minimum": 60, "maximum": 2592000},
            "quiet_hours": {
                "type": ["object", "null"],
                "properties": {
                    "start": {"type": "string", "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$"},
                    "end": {"type": "string", "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$"},
                    "timezone": {"type": "string", "description": "IANA timezone name (defaults to UTC)."},
                },
                "required": ["start", "end"],
                "additionalProperties": False,
            },
            "max_per_day": {"type": "integer", "minimum": 1, "maximum": 50},
            "expires_at": {"type": ["string", "null"]},
            "enabled": {"type": "boolean"},
            "watch_id": {"type": "string"},
            "paused": {"type": "boolean", "default": True},
            "sources": {"type": "object"},
        },
        "required": ["operation"],
    },
}

WIDGET_WAKE_TEST = {
    "name": "widget_wake_test",
    "description": "Send one content-free UnifiedPush fetch wake to registered device endpoints and print the receipt chain. It does not create or claim a publication revision.",
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {"type": "string", "description": "Widget id; defaults to hermes-brief."},
        },
        "required": [],
    },
}


WIDGET_SET_QUIET_HOURS = {
    "name": "widget_set_quiet_hours",
    "description": "Set or clear a widget's quiet-hours window in an IANA timezone; high-priority wakes degrade visibly to normal during it.",
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {"type": "string"},
            "quiet_hours": {
                "type": ["object", "null"],
                "properties": {
                    "start": {"type": "string", "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$"},
                    "end": {"type": "string", "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$"},
                    "timezone": {"type": "string", "description": "IANA timezone name (defaults to UTC)."},
                },
                "required": ["start", "end"],
                "additionalProperties": False,
            },
        },
        "required": ["widget_id", "quiet_hours"],
    },
}


WIDGET_STATUS = {
    "name": "widget_status",
    "description": (
        "Report Hermes widget host status: publication revision/regions and summary, ordered "
        "nudge/fetch/download/render receipts, wake/distributor state, registered widget "
        "instances, queued action intents/questions, aggregate attention, history-gap warnings, "
        "connection freshness, routine, devices, and counts. None of these states claims the "
        "user saw content. Use before pairing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "widget_id": {
                "type": "string",
                "description": "Widget to inspect; defaults to hermes-brief.",
            },
            "consume_update_requests": {
                "type": "boolean",
                "description": "Acknowledge update requests and claim a durable refreshLease. Read workContext before rechecking. Consumed means read; pass refresh_id to widget_publish or widget_finish_refresh to complete. A busy lease belongs to another run.",
            },
            "summary": {
                "type": "boolean",
                "description": "Return a compact recent window (default true); set false for a larger bounded window.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of records per status list, capped at 10 in summary mode.",
            },
        },
        "required": [],
    },
}
