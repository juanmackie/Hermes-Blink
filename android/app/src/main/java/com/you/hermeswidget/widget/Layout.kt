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

data class Node(
    val type: String,
    val id: String? = null,
    val value: String? = null,
    val label: String? = null,
    val text: String? = null,
    val title: String? = null,
    val subtitle: String? = null,
    val leadingIcon: String? = null,
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
