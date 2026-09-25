package com.you.hermeswidget.widget

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import kotlin.math.max
import kotlin.math.roundToInt

data class WidgetInstance(
    val instanceId: String,
    val sizeClass: String,
    val widthDp: Int,
    val heightDp: Int,
    val widthPx: Int,
    val heightPx: Int,
)

object WidgetDimensions {
    fun allInstances(context: Context): List<WidgetInstance> {
        val manager = AppWidgetManager.getInstance(context)
        val component = ComponentName(context, HermesWidgetReceiver::class.java)
        val density = context.resources.displayMetrics.density
        return manager.getAppWidgetIds(component).map { id ->
            val options = manager.getAppWidgetOptions(id)
            val widthDp = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 180)
                .coerceAtLeast(1)
            val heightDp = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, 110)
                .coerceAtLeast(1)
            val widthPx = fromDp(widthDp, heightDp, density).first
            val heightPx = fromDp(widthDp, heightDp, density).second
            WidgetInstance(
                instanceId = id.toString(),
                sizeClass = sizeClass(widthDp, heightDp),
                widthDp = widthDp,
                heightDp = heightDp,
                widthPx = widthPx,
                heightPx = heightPx,
            )
        }
    }

    private fun sizeClass(widthDp: Int, heightDp: Int): String = when {
        widthDp <= 160 && heightDp <= 160 -> "2x2"
        widthDp >= 220 && heightDp <= 180 -> "4x2"
        widthDp <= 180 && heightDp >= 220 -> "2x4"
        widthDp >= 220 && heightDp >= 220 -> "4x4"
        else -> "custom"
    }

    fun fromContext(context: Context): Pair<Int, Int> {
        val manager = AppWidgetManager.getInstance(context)
        val component = ComponentName(context, HermesWidgetReceiver::class.java)
        val density = context.resources.displayMetrics.density
        val options = manager.getAppWidgetIds(component).map { id ->
            manager.getAppWidgetOptions(id)
        }
        val usable = options.map { bundle ->
            fromDp(
                bundle.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 180),
                bundle.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, 110),
                density,
            )
        }.maxByOrNull { (width, height) -> width.toLong() * height }
        return usable ?: fromDp(180, 110, density)
    }

    fun fromDp(widthDp: Int, heightDp: Int, density: Float): Pair<Int, Int> =
        max(1, (widthDp.coerceAtLeast(1) * density).roundToInt()).coerceAtMost(16_384) to
            max(1, (heightDp.coerceAtLeast(1) * density).roundToInt()).coerceAtMost(16_384)
}
