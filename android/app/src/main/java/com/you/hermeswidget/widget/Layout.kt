package com.you.hermeswidget.widget

data class WidgetLayout(
    val version: Int = 2,
    val widgetId: String = "hermes-brief",
    val title: String? = null,
    val ttlSeconds: Int? = null,
    val accentColor: String? = null,
    val updatedAt: String? = null,
    val root: Node
)

/** Per-node inset, mirroring the `padding` object in layout.schema.json. */
data class Padding(
    val top: Int? = null,
    val bottom: Int? = null,
    val start: Int? = null,
    val end: Int? = null
)

data class Node(
    val type: String,
    val id: String? = null,
    val value: String? = null,
    val label: String? = null,
    val text: String? = null,
    val title: String? = null,
    val subtitle: String? = null,
    val trailingText: String? = null,
    val style: String? = null,
    val color: String? = null,
    val children: List<Node>? = null,
    val spacing: Int? = null,
    val thickness: Int? = null,
    val size: Int? = null,
    val maxItems: Int? = null,
    val maxLines: Int? = null,
    val delta: String? = null,
    val deltaDirection: String? = null,
    val weight: Double? = null,
    val progressValue: Double? = null,
    val showPercent: Boolean? = null,
    val events: List<CalendarEvent>? = null,
    val mode: String? = null,
    val alignment: String? = null,
    val padding: Padding? = null,
    val action: Action? = null
)

data class Action(
    val kind: String,
    val event: String? = null,
    val payload: Map<String, Any>? = null,
    val itemId: String? = null
)

data class CalendarEvent(
    val title: String,
    val start: String? = null,
    val end: String? = null,
    val color: String? = null
)

/**
 * True when `updatedAt` is older than `ttlSeconds`. The widget keeps showing the last good
 * layout and flags it, rather than showing nothing (Gate 7: offline phones retain visibly
 * stale content).
 */
fun WidgetLayout.isStale(nowMillis: Long): Boolean {
    val ttl = ttlSeconds ?: return false
    if (ttl <= 0) return false
    val updated = TimeFormat.epochMillis(updatedAt) ?: return false
    return nowMillis - updated > ttl * 1000L
}

/**
 * ISO-8601 parsing kept out of the composables so it is unit-testable on the JVM.
 * Accepts an offset (`...Z`, `+02:00`) or a bare local datetime, which is what an agent
 * or an integration tends to emit.
 */
object TimeFormat {
    private val SHORT = java.time.format.DateTimeFormatter.ofPattern("HH:mm")
    private val ISO_DATE = Regex("^\\d{4}-\\d{2}-\\d{2}$")

    fun epochMillis(iso: String?): Long? {
        val value = iso?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        return runCatching { java.time.OffsetDateTime.parse(value).toInstant().toEpochMilli() }
            .recoverCatching {
                java.time.LocalDateTime.parse(value).atZone(java.time.ZoneId.systemDefault())
                    .toInstant().toEpochMilli()
            }
            .getOrNull()
    }

    /**
     * `09:30` for an ISO datetime. A date with no time (`2026-09-16`) is returned as-is —
     * inventing `00:00` would be a lie about the event.
     */
    fun shortTime(iso: String?): String? {
        val value = iso?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        if (ISO_DATE.matches(value)) return value
        return runCatching { java.time.OffsetDateTime.parse(value).format(SHORT) }
            .recoverCatching { java.time.LocalDateTime.parse(value).format(SHORT) }
            .getOrNull()
            ?: value
    }
}
