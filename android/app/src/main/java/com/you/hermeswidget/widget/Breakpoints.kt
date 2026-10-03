package com.you.hermeswidget.widget

import androidx.compose.ui.unit.DpSize
import kotlin.math.roundToInt

/** The pinned chrome the scroll region must leave behind, in dp. */
internal const val SURFACE_PADDING_DP = 12f
internal const val HEADER_HEIGHT_DP = 20f            // 16dp mark + 4dp gap
internal const val HEADER_WITH_ACTION_DP = 52f       // 48dp MD3 M action + 4dp gap
internal const val FOOTER_HEIGHT_DP = 18f            // status line + 4dp gap
internal const val MIN_SCROLL_DP = 56f   // the height at which a body line is still legible

/**
 * The widget's content budget ladder.  Everything about "how much fits" lives here so the
 * renderer, the publisher budgets and the tests cannot disagree about the same instance.
 *
 * Height bands follow Android's canonical widget ranges (2x1/4x1 56-130dp, 2x2/4x2
 * 115-276dp, 4x3 185-422dp) rather than a single compact boolean: one boolean forced a
 * 4x1, a 4x2 and a 4x4 through the same layout, which is what clipped the action row.
 *
 * Nothing here is wire state.  The bands only decide which already-published `2x2` /
 * `4x2` / `4x4` variant text to show and how many lines of chrome are legal, so a
 * publisher never has to learn a new key.
 */
enum class WidgetBand(
    /** The variant key this band asks the publication for. */
    val variantKey: String,
    val heroMaxLines: Int,
    val summaryMaxLines: Int,
    /** 0 means the body is not rendered at all (Glance cannot scroll a 56dp cell usefully). */
    val bodyBudget: Int,
    val showsStatusDot: Boolean,
) {
    /** 4x1 / 2x1: mark, status dot and one hero line. Nothing else can be read. */
    XS("2x2", 1, 0, 0, true),

    /** 2x2 and the 4x2 floor: adds the one-line summary. */
    S("2x2", 2, 1, 0, true),

    /** 4x2 typical and 2x3: adds the body, the status line and the action. */
    M("4x2", 2, 1, 3, true),

    /** 4x3 / 4x4: adds the ticker and a longer body budget. */
    L("4x4", 2, 1, 8, true);

    val showsSummary: Boolean get() = summaryMaxLines > 0
    val showsBody: Boolean get() = bodyBudget > 0
    val showsHeaderLabel: Boolean get() = this != XS
    val showsFooter: Boolean get() = this >= M
    val showsRequestAction: Boolean get() = this >= M

    companion object {
        /** Canonical band edges in dp. Height drives the band; width is a guard, not a band. */
        const val XS_MAX_HEIGHT_DP = 130f
        const val S_MAX_HEIGHT_DP = 185f
        const val M_MAX_HEIGHT_DP = 300f

        /** Below this width there is no room for a split/hero row or a ticker column. */
        const val SINGLE_COLUMN_MAX_WIDTH_DP = 245f

        /**
         * Pinned chrome that a body cannot borrow from: header (16dp + 4dp), surface
         * padding (12dp top/bottom), two hero lines, one summary line and the footer
         * (48dp MD3 M action + 4dp gap).
         */
        const val CHROME_HEIGHT_DP = 16f + 4f + 24f + 2f * 22f + 15f + 48f + 4f

        fun of(heightDp: Float): WidgetBand = when {
            heightDp < XS_MAX_HEIGHT_DP -> XS
            heightDp < S_MAX_HEIGHT_DP -> S
            heightDp < M_MAX_HEIGHT_DP -> M
            else -> L
        }

        fun of(widthDp: Float, heightDp: Float): WidgetBand = of(heightDp)

        /** `null` for [DpSize.Unspecified] so the caller can fall back to inventory geometry. */
        fun ofOrNull(size: DpSize): WidgetBand? =
            if (size == DpSize.Unspecified || size.height.value <= 0f) null else of(size.height.value)
    }
}

/** A resolved ladder position: the band plus the width guard applied to it. */
data class BandSpec(
    val band: WidgetBand,
    val widthDp: Float,
    val heightDp: Float,
    /** `widthDp < 245` — one column, no split row, no ticker. */
    val singleColumn: Boolean,
) {
    val showsTicker: Boolean get() = band >= WidgetBand.L && !singleColumn
    val showsQuestion: Boolean get() = band >= WidgetBand.S && !singleColumn
    val showsBody: Boolean get() = band.bodyBudget > 0
    val bodyMaxLines: Int get() = band.bodyBudget
    val heroMaxLines: Int get() = if (singleColumn) 1 else band.heroMaxLines
    val summaryMaxLines: Int get() = band.summaryMaxLines
    val showsHeaderLabel: Boolean get() = band.showsHeaderLabel && !singleColumn
    val showsStatusDot: Boolean get() = band.showsStatusDot
    val showsFooter: Boolean get() = band.showsFooter
    val showsRequestAction: Boolean get() = band.showsRequestAction

    /**
     * Height of the scroll region, in dp, derived from the instance rather than left to
     * `defaultWeight()`.
     *
     * Field round 6: a weight-constrained Glance LazyColumn is measured by the platform at
     * draw time, and when that resolution does not land the way Compose would, the Column
     * is taller than the cell and the launcher clips the bottom — which is exactly where
     * the pinned action lives. Explicit arithmetic makes the layout deterministic: the
     * chrome is subtracted, so the footer is inside the cell by construction.
     */
    val scrollHeightDp: Int
        get() = (heightDp - chromeHeightDp).roundToInt().coerceIn(0, 4_096)

    /**
     * True when there is enough cell for a readable body after the chrome. Cells shorter
     * than the header plus a couple of lines still compose — the list simply has no room,
     * which is honest, and is what keeps `chrome + scroll <= cell` true at every size.
     */
    val hasReadableBody: Boolean get() = scrollHeightDp >= MIN_SCROLL_DP.toInt()

    /**
     * The pinned chrome this band reserves, in dp.
     *
     * The header grows to the action's height once the band has one (round 11): the action
     * is pinned there, above the scroll region, because a Glance lazy collection can
     * measure past the height we give it and push anything below it out of the cell.
     */
    val chromeHeightDp: Float
        get() = SURFACE_PADDING_DP * 2 +
            (if (showsRequestAction) HEADER_WITH_ACTION_DP else HEADER_HEIGHT_DP) +
            (if (showsFooter) FOOTER_HEIGHT_DP else 0f)

    /**
     * Band-driven image height (D15). S and M get a fixed budget; L is allowed to use the
     * height the chrome leaves over, so a photo never overflows the cell or leaves a dead
     * band at the bottom of a 4x4.
     */
    val imageHeightDp: Int
        get() = when (band) {
            WidgetBand.XS, WidgetBand.S -> 96
            WidgetBand.M -> 160
            WidgetBand.L -> (
                (heightDp - WidgetBand.CHROME_HEIGHT_DP).roundToInt().coerceIn(120, 240)
                )
        }

    /** The `variants` key to read, with the ladder's "next larger key" fallback. */
    fun variantKey(hasVariant: (String) -> Boolean): String = when (band) {
        WidgetBand.XS, WidgetBand.S -> if (hasVariant("2x2")) "2x2" else "4x2"
        WidgetBand.M -> if (hasVariant("4x2")) "4x2" else if (hasVariant("4x4")) "4x4" else "2x2"
        WidgetBand.L -> if (hasVariant("4x4")) "4x4" else if (hasVariant("4x2")) "4x2" else "2x2"
    }
}

object Breakpoints {
    /** The single entry point: dp geometry in, one resolved ladder position out. */
    fun spec(widthDp: Float, heightDp: Float): BandSpec = BandSpec(
        band = WidgetBand.of(widthDp, heightDp),
        widthDp = widthDp,
        heightDp = heightDp,
        singleColumn = widthDp < WidgetBand.SINGLE_COLUMN_MAX_WIDTH_DP,
    )
}
