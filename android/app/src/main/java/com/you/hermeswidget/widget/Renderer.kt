package com.you.hermeswidget.widget

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.glance.GlanceModifier
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.LinearProgressIndicator
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.cornerRadius
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.ColumnScope
import androidx.glance.layout.Row
import androidx.glance.layout.RowScope
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider

/** Used when the envelope carries no usable `accentColor`. */private const val DEFAULT_ACCENT = "#7C3AED"

private val CONTAINER_TYPES = setOf("column", "row", "box", "list")

/**
 * Renders the v2 layout contract. Every style field the schema declares is applied here or
 * it is not declared at all — see docs/SCHEMA.md and Typo.kt. Unknown node types and
 * unknown fields degrade to a safe default rather than blanking the widget.
 */
@Composable
fun WidgetSurface(layout: WidgetLayout) {
    val renderer = WidgetRenderer(Typo.colorOr(layout.accentColor, DEFAULT_ACCENT))
    Column(
        modifier = GlanceModifier.fillMaxWidth()
            .padding(layout.root.padding, LayoutDefaults.ROOT_PADDING),
    ) {
        if (layout.isStale(System.currentTimeMillis())) StaleIndicatorNode()
        renderer.renderRoot(layout.root)
    }
}

private class WidgetRenderer(private val accent: Color) {

    /** The root is a container in practice; anything else still renders instead of vanishing. */
    @Composable
    fun renderRoot(root: Node) {
        if (root.type in CONTAINER_TYPES) renderChildren(root, stacked = true, weighted = GlanceModifier)
        else renderChild(root)
    }

    @Composable
    private fun ColumnScope.renderChildren(node: Node) {
        // defaultWeight() only means something inside a Row/Column, so the scope resolves it here.
        renderChildren(node, stacked = true, weighted = GlanceModifier.defaultWeight())
    }

    @Composable
    private fun RowScope.renderChildren(node: Node) {
        renderChildren(node, stacked = false, weighted = GlanceModifier.defaultWeight())
    }

    @Composable
    fun renderChild(node: Node) {
        when (node.type) {
            "column" -> ColumnNode(node)
            "row" -> RowNode(node)
            "box" -> BoxNode(node)
            "list" -> ListNode(node)
            "text" -> TextNode(node)
            "button" -> ButtonNode(node)
            "stat" -> StatNode(node)
            "divider" -> DividerNode(node)
            "spacer" -> SpacerNode(node)
            "badge" -> BadgeNode(node)
            "calendar" -> CalendarNode(node)
            "progress" -> ProgressNode(node)
            "list_item" -> ListItemNode(node)
            else -> Spacer(GlanceModifier.size(0.dp))
        }
    }

    @Composable
    private fun renderChildren(
        node: Node,
        stacked: Boolean,
        weighted: GlanceModifier,
        limit: Int? = null,
    ) {
        val spacing = (node.spacing ?: LayoutDefaults.CONTAINER_SPACING).dp
        val children = safeChildren(node).let { if (limit == null) it else it.take(limit) }
        children.forEachIndexed { index, child ->
            if (index > 0 && spacing.value > 0f) {
                if (stacked) Spacer(GlanceModifier.height(spacing))
                else Spacer(GlanceModifier.width(spacing))
            }
            if ((child.weight ?: 0.0) > 0.0) Box(modifier = weighted) { renderChild(child) }
            else renderChild(child)
        }
    }

    @Composable
    fun ColumnNode(node: Node) {
        Column(
            modifier = GlanceModifier.fillMaxWidth().padding(node.padding),
            horizontalAlignment = node.alignment.columnAlignment(),
        ) {
            renderChildren(node)
        }
    }

    @Composable
    fun RowNode(node: Node) {
        Row(
            modifier = GlanceModifier.fillMaxWidth().padding(node.padding),
            verticalAlignment = node.alignment.rowAlignment(),
        ) {
            renderChildren(node)
        }
    }

    @Composable
    fun BoxNode(node: Node) {
        Box(
            modifier = GlanceModifier.fillMaxWidth().padding(node.padding),
            contentAlignment = node.alignment.boxAlignment(),
        ) {
            renderChildren(node, stacked = true, weighted = GlanceModifier)
        }
    }

    @Composable
    fun ListNode(node: Node) {
        // A list is a column with a cap: it has no `spacing` field, so the container default applies.
        Column(modifier = GlanceModifier.fillMaxWidth().padding(node.padding)) {
            renderChildren(node, stacked = true, weighted = GlanceModifier, limit = node.maxItems)
        }
    }

    @Composable
    fun TextNode(node: Node) {
        Text(
            text = node.value ?: node.text ?: "",
            style = Typo.textStyle(node.style, node.color, node.alignment),
            maxLines = node.maxLines ?: Int.MAX_VALUE,
        )
    }

    @Composable
    fun ButtonNode(node: Node) {
        val click = node.action.clickModifier() ?: GlanceModifier
        val (fill, ink) = when (node.style) {
            "filled" -> accent to Color.White
            "outlined" -> Typo.colorOr(null, "#FFFFFF") to Typo.colorOr(null, Typo.PRIMARY)
            else -> Typo.colorOr(null, Typo.HAIRLINE) to Typo.colorOr(null, Typo.PRIMARY)
        }
        Text(
            text = node.label ?: "Button",
            modifier = click
                .background(fill)
                .cornerRadius(LayoutDefaults.BUTTON_CORNER.dp)
                .padding(LayoutDefaults.BUTTON_PADDING_H.dp, LayoutDefaults.BUTTON_PADDING_V.dp),
            style = TextStyle(
                color = ColorProvider(ink),
                fontSize = Typo.fontSize("body"),
                fontWeight = Typo.fontWeight("medium"),
                textAlign = Typo.textAlign("center"),
            ),
        )
    }

    @Composable
    fun ListItemNode(node: Node) {
        val click = node.action.clickModifier() ?: GlanceModifier
        Row(
            modifier = click.fillMaxWidth().padding(LayoutDefaults.CARD_PADDING.dp),
            verticalAlignment = Alignment.Vertical.CenterVertically,
        ) {
            Column(modifier = GlanceModifier.defaultWeight()) {
                Text(text = node.title ?: "", style = Typo.textStyle("body"), maxLines = 1)
                if (!node.subtitle.isNullOrEmpty()) {
                    Text(text = node.subtitle, style = Typo.textStyle("caption"), maxLines = 1)
                }
            }
            if (!node.trailingText.isNullOrEmpty()) {
                Text(text = node.trailingText, style = Typo.textStyle("caption"))
            }
        }
    }

    @Composable
    fun StatNode(node: Node) {
        Column(modifier = GlanceModifier.padding(node.padding)) {
            Text(text = node.label ?: "", style = Typo.textStyle("caption"), maxLines = 1)
            Text(
                text = node.value ?: "",
                style = Typo.textStyle("title", node.color, node.alignment),
                maxLines = node.maxLines ?: 1,
            )
            if (!node.delta.isNullOrEmpty()) {
                val glyph = when (node.deltaDirection) {
                    "up" -> "▲"
                    "down" -> "▼"
                    else -> "•"
                }
                val shade = when (node.deltaDirection) {
                    "up" -> Typo.SUCCESS
                    "down" -> Typo.DANGER
                    else -> Typo.SECONDARY
                }
                Text(text = "$glyph ${node.delta}", style = Typo.textStyle("caption", shade), maxLines = 1)
            }
        }
    }

    @Composable
    fun BadgeNode(node: Node) {
        val fill = Typo.colorOr(node.color, Typo.HAIRLINE)
        // Ink flips on a saturated fill; a light badge keeps primary text.
        val ink = if (fill.luminance() < 0.6f) Color.White else Typo.colorOr(null, Typo.PRIMARY)
        Text(
            text = node.text ?: "",
            modifier = GlanceModifier
                .background(fill)
                .cornerRadius(LayoutDefaults.BADGE_CORNER.dp)
                .padding(LayoutDefaults.BADGE_PADDING_H.dp, LayoutDefaults.BADGE_PADDING_V.dp),
            style = TextStyle(
                color = ColorProvider(ink),
                fontSize = Typo.fontSize("caption"),
                fontWeight = Typo.fontWeight("medium"),
            ),
            maxLines = 1,
        )
    }

    @Composable
    fun DividerNode(node: Node) {
        val thickness = (node.thickness ?: LayoutDefaults.HAIRLINE_THICKNESS).dp
        Spacer(
            modifier = GlanceModifier
                .fillMaxWidth()
                .height(thickness)
                .background(Typo.colorOr(node.color, Typo.HAIRLINE)),
        )
    }

    @Composable
    fun SpacerNode(node: Node) {
        Spacer(GlanceModifier.fillMaxWidth().height((node.size ?: 8).dp))
    }

    @Composable
    fun CalendarNode(node: Node) {
        val events = node.events ?: emptyList()
        val max = node.maxItems ?: events.size
        Column(modifier = GlanceModifier.fillMaxWidth().padding(node.padding)) {
            if (events.isEmpty()) Text(text = "No events", style = Typo.textStyle("caption"))
            events.take(max).forEach { event ->
                Row(
                    modifier = GlanceModifier.fillMaxWidth().padding(vertical = 2.dp),
                    verticalAlignment = Alignment.Vertical.CenterVertically,
                ) {
                    // An ISO datetime renders as a clock time, never as the raw string.
                    TimeFormat.shortTime(event.start)?.let { time ->
                        Text(
                            text = time,
                            modifier = GlanceModifier.width(48.dp),
                            style = Typo.textStyle("caption"),
                            maxLines = 1,
                        )
                    }
                    Text(
                        text = event.title,
                        style = Typo.textStyle("label", event.color),
                        maxLines = 1,
                    )
                }
            }
        }
    }

    @Composable
    fun ProgressNode(node: Node) {
        val value = (node.progressValue ?: 0.0).coerceIn(0.0, 1.0)
        Column(modifier = GlanceModifier.fillMaxWidth().padding(node.padding)) {
            Text(
                text = node.label ?: "Progress",
                style = Typo.textStyle("caption"),
                maxLines = 1,
            )
            LinearProgressIndicator(
                progress = value.toFloat(),
                modifier = GlanceModifier.fillMaxWidth().height(LayoutDefaults.PROGRESS_HEIGHT.dp),
                color = ColorProvider(accent),
                backgroundColor = ColorProvider(Typo.colorOr(null, Typo.HAIRLINE)),
            )
            if (node.showPercent == true) {
                Text(text = "${(value * 100).toInt()}%", style = Typo.textStyle("caption"))
            }
        }
    }

    private fun Action?.clickModifier(): GlanceModifier? {
        val action = this ?: return null
        return GlanceModifier.clickable(
            actionRunCallback<ActionCallbacks.EventAction>(
                actionParametersOf(
                    ActionParameters.Key<String>("event") to (action.event ?: action.kind),
                    ActionParameters.Key<String>("kind") to action.kind,
                    ActionParameters.Key<String>("itemId") to (action.itemId ?: ""),
                    ActionParameters.Key<String>("payload") to
                        (action.payload?.let { org.json.JSONObject(it as Map<*, *>).toString() } ?: "{}"),
                )
            )
        )
    }
}

@Composable
private fun StaleIndicatorNode() {
    Text(
        text = "Stale — reconnecting…",
        modifier = GlanceModifier.padding(bottom = 4.dp),
        style = Typo.textStyle("caption", Typo.DANGER),
        maxLines = 1,
    )
}

/** Shown when no layout could be loaded from cache, agent, or fixture. */
@Composable
fun ErrorStateNode(node: Node) {
    Column(modifier = GlanceModifier.fillMaxWidth()) {
        Text(text = node.value ?: "Connection error", style = Typo.textStyle("body"))
        Text(text = node.label ?: "Tap to reconnect", style = Typo.textStyle("caption"))
    }
}

private fun safeChildren(node: Node): List<Node> =
    if (node.type in CONTAINER_TYPES) node.children ?: emptyList() else emptyList()

/** Inset for a node that declares `padding`; `fallback` when it declares none. */
private fun GlanceModifier.padding(padding: Padding?, fallback: Int): GlanceModifier = padding(
    start = (padding?.start ?: fallback).dp,
    top = (padding?.top ?: fallback).dp,
    end = (padding?.end ?: fallback).dp,
    bottom = (padding?.bottom ?: fallback).dp,
)

/** No declared padding means no inset — the parent already spaced the children. */
private fun GlanceModifier.padding(padding: Padding?): GlanceModifier =
    if (padding == null) this
    else padding(
        start = (padding.start ?: 0).dp,
        top = (padding.top ?: 0).dp,
        end = (padding.end ?: 0).dp,
        bottom = (padding.bottom ?: 0).dp,
    )

private fun String?.columnAlignment(): Alignment.Horizontal = when (this) {
    "center" -> Alignment.Horizontal.CenterHorizontally
    "end", "trailing", "right" -> Alignment.Horizontal.End
    else -> Alignment.Horizontal.Start
}

private fun String?.rowAlignment(): Alignment.Vertical = when (this) {
    "center" -> Alignment.Vertical.CenterVertically
    "end", "bottom" -> Alignment.Vertical.Bottom
    else -> Alignment.Vertical.Top
}

/** `box` overlays its children, so `alignment` places them inside the box rather than in a line. */
private fun String?.boxAlignment(): Alignment = when (this) {
    "center" -> Alignment.Center
    "end", "bottom", "bottom_end" -> Alignment.BottomEnd
    "start", "top" -> Alignment.TopStart
    "fill", "stretch" -> Alignment.CenterStart
    else -> Alignment.CenterStart
}

private fun Color.luminance(): Float = 0.2126f * red + 0.7152f * green + 0.0722f * blue
