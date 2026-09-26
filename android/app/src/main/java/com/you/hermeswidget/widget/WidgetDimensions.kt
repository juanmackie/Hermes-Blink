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
    /** E2 union bounds for a handheld widget, in dp. */
    const val MIN_WIDTH_DP = 109
    const val MAX_WIDTH_DP = 624
    const val MIN_HEIGHT_DP = 56
    const val MAX_HEIGHT_DP = 422

    /** 4 columns start here; 3+ rows start here. */
    const val WIDE_MIN_DP = 245
    const val TALL_MIN_DP = 300

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
            val (widthPx, heightPx) = fromDp(widthDp, heightDp, density)
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

    /**
     * Size class from Android's canonical dp ranges (E2). The guide's ranges overlap —
     * 245-306 x 115-276 is both a 2x2 and a 4x2 — so the wider reading wins and a cell is
     * never described as smaller than the budget it actually has:
     *
     *  * 2x2: 109-306 wide and up to 3 rows (2x1 56-130, 2x2 115-276)
     *  * 2x4: 2 columns and 3+ rows tall
     *  * 4x2: 245+ wide (4x1 56-130, 4x2 115-276)
     *  * 4x4: 245+ wide and 3+ rows tall (4x3 185-422, 4x4 300-422)
     *  * custom: geometry outside the guide's ranges entirely
     *
     * The value set is the wire contract's {2x2, 4x2, 2x4, 4x4, custom}; host-side
     * `_size_class` normalizes to the same set.
     */
    fun sizeClass(widthDp: Int, heightDp: Int): String {
        if (widthDp !in MIN_WIDTH_DP..MAX_WIDTH_DP || heightDp !in MIN_HEIGHT_DP..MAX_HEIGHT_DP) {
            return "custom"
        }
        return when {
            widthDp >= WIDE_MIN_DP && heightDp >= TALL_MIN_DP -> "4x4"
            widthDp >= WIDE_MIN_DP -> "4x2"
            heightDp >= TALL_MIN_DP -> "2x4"
            else -> "2x2"
        }
    }

    /**
     * Per-instance geometry in px. [appWidgetId] is the instance being composed; passing
     * `null` deliberately asks for the largest instance (the only correct use of that is
     * a device-wide report, never a single widget's layout).
     */
    fun fromContext(context: Context, appWidgetId: Int? = null): Pair<Int, Int> {
        val manager = AppWidgetManager.getInstance(context)
        val component = ComponentName(context, HermesWidgetReceiver::class.java)
        val density = context.resources.displayMetrics.density
        val options = when (appWidgetId) {
            null -> manager.getAppWidgetIds(component).map { id -> manager.getAppWidgetOptions(id) }
            else -> listOf(manager.getAppWidgetOptions(appWidgetId))
        }
        val usable = options
            .filter { it != null }
            .map { bundle ->
                fromDp(
                    bundle.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 180),
                    bundle.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, 110),
                    density,
                )
            }
            .maxByOrNull { (width, height) -> width.toLong() * height }
        return usable ?: fromDp(180, 110, density)
    }

    fun fromDp(widthDp: Int, heightDp: Int, density: Float): Pair<Int, Int> =
        max(1, (widthDp.coerceAtLeast(1) * density).roundToInt()).coerceAtMost(16_384) to
            max(1, (heightDp.coerceAtLeast(1) * density).roundToInt()).coerceAtMost(16_384)
}
