package com.you.hermeswidget.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.glance.GlanceModifier
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.text.Text
import androidx.compose.ui.unit.dp

class Renderer(private val layout: WidgetLayout) {

    @Composable
    fun render(): @Composable () -> Unit = {
        val root = layout.root
        when (root.type) {
            "column" -> ColumnNode(root)
            "row" -> RowNode(root)
            "box" -> BoxNode(root)
            "list" -> ListNode(root)
            "text" -> TextNode(root)
            "button" -> ButtonNode(root)
            "stat" -> StatNode(root)
            "divider" -> DividerNode()
            "spacer" -> SpacerNode(root)
            "badge" -> BadgeNode(root)
            "calendar" -> CalendarNode(root)
            "progress" -> ProgressNode(root)
            "list_item" -> ListItemNode(root)
            else -> Spacer(modifier = GlanceModifier.size(0.dp))
        }
    }

    private fun safeChildren(node: Node): List<Node> =
        when (node.type) {
            "column", "row", "box", "list" -> node.children ?: emptyList()
            else -> emptyList()
        }
}

@Composable
fun WidgetSurface(layout: WidgetLayout) {
    val root = layout.root
    when (root.type) {
        "column" -> Column(modifier = GlanceModifier.fillMaxWidth().padding(16.dp)) {
            (root.children ?: emptyList()).forEach { child -> renderChild(child) }
        }
        else -> Text(text = "Unsupported root: ${root.type}")
    }
}

@Composable
fun renderChild(node: Node) {
    when (node.type) {
        "text" -> TextNode(node)
        "column" -> ColumnNode(node)
        "row" -> RowNode(node)
        "box" -> BoxNode(node)
        "list" -> ListNode(node)
        "divider" -> DividerNode()
        "spacer" -> SpacerNode(node)
        "badge" -> BadgeNode(node)
        "stat" -> StatNode(node)
        "progress" -> ProgressNode(node)
        "calendar" -> CalendarNode(node)
        "button" -> ButtonNode(node)
        "list_item" -> ListItemNode(node)
        else -> Spacer(modifier = GlanceModifier.size(0.dp))
    }
}

fun safeChildren(node: Node): List<Node> =
    when (node.type) {
        "column", "row", "box", "list" -> node.children ?: emptyList()
        else -> emptyList()
    }

@Composable
fun ColumnNode(node: Node) {
    Column(modifier = GlanceModifier.fillMaxWidth().padding(4.dp)) {
        safeChildren(node).forEach { renderChild(it) }
    }
}

@Composable
fun RowNode(node: Node) {
    Row(modifier = GlanceModifier.fillMaxWidth(), horizontalAlignment = Alignment.Horizontal.Start) {
        safeChildren(node).forEach { renderChild(it) }
    }
}

@Composable
fun BoxNode(node: Node) {
    Box(modifier = GlanceModifier.fillMaxWidth()) {
        safeChildren(node).forEach { renderChild(it) }
    }
}

@Composable
fun ListNode(node: Node) {
    Column(modifier = GlanceModifier.fillMaxWidth()) {
        val items = safeChildren(node)
        val max = node.maxItems ?: items.size
        items.take(max).forEach { renderChild(it) }
    }
}

@Composable
fun TextNode(node: Node) {
    val text = node.value ?: node.text ?: ""
    when (node.style) {
        "title" -> Text(text = text)
        "caption" -> Text(text = text)
        else -> Text(text = text)
    }
}

@Composable
fun ButtonNode(node: Node) {
    val action = node.action
    val modifier = if (action != null) {
        GlanceModifier.clickable(actionRunCallback<ActionCallbacks.EventAction>(
            actionParametersOf(
                ActionParameters.Key<String>("event") to (action.event ?: action.kind),
                ActionParameters.Key<String>("kind") to action.kind,
                ActionParameters.Key<String>("itemId") to (action.itemId ?: ""),
                ActionParameters.Key<String>("payload") to (action.payload?.let { org.json.JSONObject(it as Map<*, *>).toString() } ?: "{}"),
            )
        ))
    } else GlanceModifier
    Text(text = node.label ?: "Button", modifier = modifier)
}

@Composable
fun ListItemNode(node: Node) {
    val action = node.action
    val modifier = if (action != null) {
        GlanceModifier.clickable(actionRunCallback<ActionCallbacks.EventAction>(
            actionParametersOf(
                ActionParameters.Key<String>("event") to (action.event ?: action.kind),
                ActionParameters.Key<String>("kind") to action.kind,
                ActionParameters.Key<String>("itemId") to (action.itemId ?: node.id ?: ""),
                ActionParameters.Key<String>("payload") to (action.payload?.let { org.json.JSONObject(it as Map<*, *>).toString() } ?: "{}"),
            )
        ))
    } else GlanceModifier
    Column(modifier = modifier.fillMaxWidth().padding(8.dp)) {
        Text(text = node.title ?: "")
        if (!node.subtitle.isNullOrEmpty()) Text(text = node.subtitle ?: "")
        if (!node.trailingText.isNullOrEmpty()) Text(text = node.trailingText ?: "")
    }
}

@Composable
fun StatNode(node: Node) {
    Row(modifier = GlanceModifier.fillMaxWidth().padding(4.dp)) {
        Column {
            Text(text = node.label ?: "")
            Text(text = node.value ?: "")
            if (!node.delta.isNullOrEmpty()) {
                val dir = when (node.deltaDirection) {
                    "up" -> "▲"
                    "down" -> "▼"
                    else -> ""
                }
                Text(text = "$dir ${node.delta}")
            }
        }
    }
}

@Composable
fun BadgeNode(node: Node) {
    Text(text = node.text ?: "")
}

@Composable
fun DividerNode() {
    Spacer(modifier = GlanceModifier.fillMaxWidth().height(1.dp))
}

@Composable
fun SpacerNode(node: Node) {
    val h = (node.size ?: 8).dp
    Spacer(modifier = GlanceModifier.fillMaxWidth().height(h))
}

@Composable
fun CalendarNode(node: Node) {
    Column(modifier = GlanceModifier.fillMaxWidth().padding(4.dp)) {
        Text(text = "Calendar")
        val evs = node.events ?: emptyList()
        val max = node.maxItems ?: evs.size
        evs.take(max).forEach { ev ->
            Row(modifier = GlanceModifier.fillMaxWidth()) {
                Text(text = ev.title)
                if (!ev.start.isNullOrEmpty()) Text(text = " ${ev.start}")
            }
        }
        if (evs.isEmpty()) Text(text = "No events")
    }
}

@Composable
fun ProgressNode(node: Node) {
    val pct = ((node.progressValue ?: 0.0) * 100).toInt()
    Row(modifier = GlanceModifier.fillMaxWidth().padding(4.dp)) {
        Text(text = node.label ?: "Progress")
        Text(text = if (node.showPercent == true) " $pct%" else " ${(node.progressValue ?: 0.0)}")
    }
}

@Composable
fun StaleIndicatorNode(node: Node) {
    Row(modifier = GlanceModifier.fillMaxWidth()) {
        Text(text = "Stale — reconnecting...")
    }
}

@Composable
fun ErrorStateNode(node: Node) {
    Column(modifier = GlanceModifier.fillMaxWidth()) {
        Text(text = node.value ?: "Connection error")
        Text(text = node.label ?: "Tap to reconnect")
    }
}
