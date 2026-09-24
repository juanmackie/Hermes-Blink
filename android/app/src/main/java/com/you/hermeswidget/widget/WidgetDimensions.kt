package com.you.hermeswidget.widget

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import kotlin.math.max
import kotlin.math.roundToInt

object WidgetDimensions {
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
