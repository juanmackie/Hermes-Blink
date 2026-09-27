package com.you.hermeswidget.widget

import android.content.Context
import android.content.res.Resources
import android.os.Build
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.glance.GlanceModifier
import androidx.glance.appwidget.cornerRadius
import androidx.glance.background
import androidx.glance.unit.ColorProvider
import com.you.hermeswidget.R
import kotlin.math.roundToInt

/**
 * Device theme tokens (WC-1 / WC-2), resolved through the same qualified `colors.xml`
 * resources the rest of the app uses. Nothing here composites a wallpaper any more:
 * the light/dark pair comes from `-night`, Android 12+ picks the platform's own tonal
 * palette, and `dark_palette` picks the explicit dark pair because that is an author
 * decision rather than a device decision.
 */
object WidgetTheme {
    /** The host app's own `?android:attr/colorBackground` when it has one, else our token. */
    private fun themed(context: Context, attr: Int, fallback: Int): ColorProvider {
        val themedAttr = if (attr != 0) {
            val typed = context.obtainStyledAttributes(intArrayOf(attr))
            val resolved = typed.getColor(0, 0)
            typed.recycle()
            resolved
        } else {
            0
        }
        return if (themedAttr != 0) ColorProvider(Color(themedAttr)) else token(context, fallback)
    }

    /** `Context.getColor` is API 23+ and this module is minSdk 26, so this is a straight read. */
    fun token(context: Context, resId: Int): ColorProvider =
        ColorProvider(Color(context.resources.getColor(resId, context.theme)))

    fun surface(context: Context, dark: Boolean): ColorProvider =
        if (dark) token(context, R.color.widget_surface_dark)
        else themed(context, android.R.attr.colorBackground, R.color.widget_surface)

    fun ink(context: Context, dark: Boolean): ColorProvider =
        if (dark) token(context, R.color.widget_on_surface_dark)
        else themed(context, android.R.attr.textColorPrimary, R.color.widget_on_surface)

    fun secondary(context: Context, dark: Boolean): ColorProvider =
        if (dark) token(context, R.color.widget_secondary_dark) else token(context, R.color.widget_secondary)

    /** Accent: the publication's own `accentColor` wins, then the theme token. */
    fun accent(context: Context, dark: Boolean, publicationAccent: String?): ColorProvider {
        val authored = publicationAccent?.let { HexColor.color(it) }
        return if (authored != null) ColorProvider(authored)
        else if (dark) token(context, R.color.widget_accent_dark)
        else token(context, R.color.widget_accent)
    }

    /** Ink on top of the accent fill. An authored accent implies the contract's white ink. */
    fun onAccent(context: Context, dark: Boolean, publicationAccent: String?): ColorProvider {
        if (publicationAccent?.let { HexColor.color(it) } != null) {
            return ColorProvider(Color(if (dark) 0xFF1C1C1E.toInt() else 0xFFFFFFFF.toInt()))
        }
        return if (dark) token(context, R.color.widget_on_accent_dark)
        else token(context, R.color.widget_on_accent)
    }

    /** Status dot: a shape, not text, so it only has to read as healthy/attention/none. */
    fun status(context: Context, level: StatusLevel): ColorProvider = token(
        context,
        when (level) {
            StatusLevel.FRESH -> R.color.widget_status_fresh
            StatusLevel.AGED -> R.color.widget_status_aged
            StatusLevel.STALE -> R.color.widget_status_stale
            StatusLevel.OFFLINE -> R.color.widget_status_offline
        },
    )

    fun isDark(context: Context): Boolean =
        (context.resources.configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK) ==
            android.content.res.Configuration.UI_MODE_NIGHT_YES

    enum class StatusLevel { FRESH, AGED, STALE, OFFLINE }
}

/**
 * WS-2: the launcher already rounds widget surfaces, so the widget follows the platform's
 * own radius instead of its own literal. `getIdentifier(..., "dimen", "android")` is the
 * supported lookup — `getResourceName` never returns null and mis-resolves on older APIs.
 */
object WidgetRadius {
    private val SYSTEM_RADIUS_NAMES = arrayOf(
        "system_app_widget_background_radius",
        "system_app_widget_inner_radius",
    )

    fun resolve(context: Context): Dp {
        val density = context.resources.displayMetrics.density
        if (density > 0f && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            for (name in SYSTEM_RADIUS_NAMES) {
                val px = systemDimen(context, name)?.let { context.resources.getDimension(it) } ?: 0f
                if (px > 0f) return (px / density).dp
            }
        }
        return (context.resources.getDimension(R.dimen.widget_corner_radius) / density).dp
    }

    @Suppress("DiscouragedApi")  // getIdentifier is the supported platform-dimen lookup
    private fun systemDimen(context: Context, name: String): Int? = try {
        context.resources.getIdentifier(name, "dimen", "android").takeIf { it != 0 }
    } catch (_: Resources.NotFoundException) {
        null
    }
}

/** The composed instance's real geometry: Glance first, launcher inventory second. */
object WidgetSize {
    /** `null` for [DpSize.Unspecified] so the caller can fall back to inventory geometry. */
    fun fromLocalSize(size: DpSize): Pair<Float, Float>? {
        if (size == DpSize.Unspecified) return null
        if (size.width.value <= 0f || size.height.value <= 0f) return null
        return size.width.value to size.height.value
    }

    /**
     * The launcher's report for one instance, in dp. This is the authoritative geometry:
     * it is what the cell really is, it is refreshed on every resize, and it is the same
     * number the host receives and the bands are defined against.
     */
    fun fromInventory(context: Context, appWidgetId: Int?): Pair<Float, Float>? {
        val density = context.resources.displayMetrics.density
        if (density <= 0f) return null
        val (widthPx, heightPx) = WidgetDimensions.fromContext(context, appWidgetId)
        return (widthPx / density).roundToInt().toFloat() to (heightPx / density).roundToInt().toFloat()
    }

    /** The composed instance's px size for the render acknowledgement. */
    fun toPixels(context: Context, widthDp: Float, heightDp: Float): Pair<Int, Int> {
        val density = context.resources.displayMetrics.density
        return WidgetDimensions.fromDp(
            widthDp.roundToInt().coerceAtLeast(1),
            heightDp.roundToInt().coerceAtLeast(1),
            if (density > 0f) density else 1f,
        )
    }
}

/**
 * The one place a composed instance becomes a ladder position. A cell that reports no size
 * falls back to the narrowest useful geometry rather than a roomy default, because the
 * failure mode we care about is a clipped action, not a sparse 2x2.
 */
/**
 * Which geometry a composition is laid out for.
 *
 * Field round 8, P0: with `SizeMode.Responsive(sizes)` Glance composes for the *closest
 * sample in the set*, so `LocalSize` inside the composition reports that sample — not
 * the cell the launcher is actually drawing into. On a 407x270dp 4x2 cell the nearest
 * sample was 407x412, so the whole column was composed for 412dp: 142dp of it, including
 * the pinned footer and its action, sat outside the cell, taps fell through to the
 * launcher, and no action broadcast was ever produced.
 *
 * So the order is inverted: the launcher's own report for *this instance* is the truth
 * (it is the same geometry the host receives and the bands are defined against), the
 * responsive sample is only a hint, and the fixed fallback is a last resort.
 */
object SizeGate {
    const val FALLBACK_WIDTH_DP = 180f
    const val FALLBACK_HEIGHT_DP = 110f

    /** A size in dp, with where it came from — the source is recorded, not assumed. */
    data class Geometry(
        val widthDp: Float,
        val heightDp: Float,
        val source: Source,
    ) {
        enum class Source { INSTANCE_INVENTORY, RESPONSIVE_SAMPLE, FALLBACK }
    }

    /**
     * Pure so it can be tested against the exact failure: an instance report of 407x270
     * must win over a responsive sample of 407x412.
     */
    fun resolve(instance: Pair<Float, Float>?, sample: Pair<Float, Float>?): Geometry = when {
        instance != null -> Geometry(instance.first, instance.second, Geometry.Source.INSTANCE_INVENTORY)
        sample != null -> Geometry(sample.first, sample.second, Geometry.Source.RESPONSIVE_SAMPLE)
        else -> Geometry(FALLBACK_WIDTH_DP, FALLBACK_HEIGHT_DP, Geometry.Source.FALLBACK)
    }

    /**
     * @param instanceDp this widget instance's reported geometry, when it is known.
     * @param sampleDp what Glance's LocalSize says, i.e. the sample it composed for.
     */
    fun spec(
        instanceDp: Pair<Float, Float>?,
        sampleDp: Pair<Float, Float>?,
    ): Pair<BandSpec, Geometry> {
        val geometry = resolve(instanceDp, sampleDp)
        return Breakpoints.spec(geometry.widthDp, geometry.heightDp) to geometry
    }

    /** Resolves the real geometry for one instance, falling back through the chain. */
    fun specForInstance(
        context: Context,
        appWidgetId: Int?,
        sample: DpSize,
    ): Pair<BandSpec, Geometry> = spec(
        WidgetSize.fromInventory(context, appWidgetId),
        WidgetSize.fromLocalSize(sample),
    )
}

/** The themed, platform-rounded surface chain every widget state uses (WS-2). */
fun GlanceModifier.widgetSurface(context: Context, dark: Boolean): GlanceModifier =
    cornerRadius(WidgetRadius.resolve(context))
        .background(WidgetTheme.surface(context, dark))
