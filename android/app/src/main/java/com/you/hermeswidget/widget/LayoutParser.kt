package com.you.hermeswidget.widget

import org.json.JSONObject
import org.json.JSONArray

/**
 * Parses v2 Hermes agent layout JSON into [WidgetLayout].
 * Kept separate from the Glance widget so it can be unit-tested on the JVM.
 * Rejects removed types (chart/image/icon/toggle) and url/open_app actions.
 */
object LayoutParser {
    private val ALLOWED_TYPES = setOf(
        "column", "row", "box", "list",
        "text", "divider", "spacer", "badge",
        "calendar", "stat", "progress",
        "button", "list_item"
    )
    private val ALLOWED_ACTION_KINDS = setOf(
        "event", "refresh", "dismiss", "review", "approve", "snooze", "open"
    )
    private val ALLOWED_ACTION_CLASSES = setOf(
        "reversible", "read_only", "dismiss_reminder", "rerun_check", "staged_patch", "flag",
        "destructive", "external", "irreversible",
    )

    fun parse(jsonStr: String): WidgetLayout {
        val json = JSONObject(jsonStr)
        val version = json.optInt("version", 2)
        if (version != 2) throw IllegalArgumentException("Unsupported layout version $version; expected 2")
        val rootObj = json.optJSONObject("root")
            ?: throw IllegalArgumentException("Missing root")
        return WidgetLayout(
            version = version,
            widgetId = json.optString("widgetId", "hermes-brief"),
            title = json.optString("title", "").takeIf { it.isNotEmpty() },
            itemId = json.optString("itemId", "").takeIf { it.isNotEmpty() },
            ttlSeconds = json.optInt("ttlSeconds", -1).takeIf { it >= 0 },
            accentColor = json.optString("accentColor", "").takeIf { it.isNotEmpty() },
            updatedAt = json.optString("updatedAt", "").takeIf { it.isNotEmpty() },
            root = parseNode(rootObj)
        )
    }

    fun parseNode(obj: JSONObject): Node {
        val type = obj.optString("type", "")
        if (type !in ALLOWED_TYPES) throw IllegalArgumentException("Unknown or removed node type: $type")

        val action = obj.optJSONObject("action")?.let { act ->
            val kind = act.optString("kind", "event")
            if (kind !in ALLOWED_ACTION_KINDS) throw IllegalArgumentException("Removed action kind: $kind")
            if (kind == "event" && act.optString("event", "").isEmpty()) {
                throw IllegalArgumentException("event action requires non-empty event")
            }
            if (kind in setOf("dismiss", "review", "approve", "snooze", "open") &&
                act.optString("itemId", "").isEmpty()
            ) {
                throw IllegalArgumentException("$kind action requires itemId")
            }
            val actionClass = act.optString("actionClass", "").takeIf { it.isNotEmpty() }
            if (actionClass != null && actionClass !in ALLOWED_ACTION_CLASSES) {
                throw IllegalArgumentException("Unsupported action class")
            }
            Action(
                kind = kind,
                event = act.optString("event", "").takeIf { it.isNotEmpty() },
                itemId = act.optString("itemId", "").takeIf { it.isNotEmpty() },
                actionClass = actionClass,
                confirmOnDevice = act.optBoolean("confirmOnDevice", false),
                payload = act.optJSONObject("payload")?.let { p ->
                    val map = mutableMapOf<String, Any>()
                    val keys = p.keys()
                    while (keys.hasNext()) {
                        val k = keys.next()
                        map[k] = p.get(k)
                    }
                    map
                }
            )
        }

        val events: List<CalendarEvent>? = if (type == "calendar") {
            obj.optJSONArray("events")?.let { arr ->
                (0 until arr.length()).mapNotNull { i ->
                    arr.optJSONObject(i)?.let { e ->
                        CalendarEvent(
                            title = e.optString("title", ""),
                            start = e.optString("start", "").takeIf { it.isNotEmpty() },
                            end = e.optString("end", "").takeIf { it.isNotEmpty() },
                            color = e.optString("color", "").takeIf { it.isNotEmpty() }
                        )
                    }
                }
            }
        } else null

        if (type == "calendar") {
            val mode = obj.optString("mode", "agenda")
            if (mode != "agenda") throw IllegalArgumentException("calendar mode must be agenda (month removed)")
        }

        return Node(
            type = type,
            id = obj.optString("id", "").takeIf { it.isNotEmpty() },
            itemId = obj.optString("itemId", "").takeIf { it.isNotEmpty() },
            value = obj.optString("value", obj.optString("text", "")).takeIf { it.isNotEmpty() },
            label = obj.optString("label", "").takeIf { it.isNotEmpty() },
            text = obj.optString("text", "").takeIf { it.isNotEmpty() },
            title = obj.optString("title", "").takeIf { it.isNotEmpty() },
            subtitle = obj.optString("subtitle", "").takeIf { it.isNotEmpty() },
            trailingText = obj.optString("trailingText", "").takeIf { it.isNotEmpty() },
            style = obj.optString("style", "").takeIf { it.isNotEmpty() },
            color = obj.optString("color", "").takeIf { it.isNotEmpty() },
            spacing = obj.optInt("spacing", -1).takeIf { it >= 0 },
            thickness = obj.optInt("thickness", -1).takeIf { it >= 0 },
            size = obj.optInt("size", -1).takeIf { it >= 0 },
            maxItems = obj.optInt("maxItems", -1).takeIf { it >= 0 },
            maxLines = obj.optInt("maxLines", -1).takeIf { it >= 0 },
            delta = obj.optString("delta", "").takeIf { it.isNotEmpty() },
            deltaDirection = obj.optString("deltaDirection", "").takeIf { it.isNotEmpty() },
            weight = obj.optDouble("weight", Double.NaN).takeIf { !it.isNaN() },
            progressValue = if (type == "progress") obj.optDouble("value", Double.NaN).takeIf { !it.isNaN() } else null,
            showPercent = if (obj.has("showPercent")) obj.optBoolean("showPercent", false) else null,
            events = events,
            mode = obj.optString("mode", "").takeIf { it.isNotEmpty() },
            alignment = obj.optString("alignment", "").takeIf { it.isNotEmpty() },
            padding = obj.optJSONObject("padding")?.let { p ->
                Padding(
                    top = p.optInt("top", -1).takeIf { it >= 0 },
                    bottom = p.optInt("bottom", -1).takeIf { it >= 0 },
                    start = p.optInt("start", -1).takeIf { it >= 0 },
                    end = p.optInt("end", -1).takeIf { it >= 0 }
                )
            },
            action = action,
            children = obj.optJSONArray("children")?.let { arr ->
                (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i)?.let { parseNode(it) } }
            }
        )
    }
}
