package com.you.hermeswidget.widget

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.sp
import androidx.glance.text.FontWeight
import androidx.glance.text.TextAlign
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider

/**
 * Design tokens for publication text on the home-screen widget.
 *
 * `skills/widget/SKILL.md` documents this same table and
 * `scripts/check-contract-parity.py` fails when the two drift. Nothing else may invent a
 * type size or a color: an agent that writes a value the renderer does not honor gets a
 * silent no-op, which is worse than the field not existing.
 *
 * The scale is deliberately closed — four steps, three sizes, two weights. Adding a
 * free-form `fontSize` would let a layout drift off this table.
 */
object Typo {

    /** Ink on the widget surface (see WidgetTheme): the hero, body and label steps. */
    const val PRIMARY = "#000000"

    /**
     * Labels, captions, metadata. The old #8E8E93 measured 2.97:1 on the light surface and
     * 1.34:1 composited over a dark wallpaper, so it failed WCAG AA for 11sp text; this
     * token clears 4.5:1 on the light surface and the dark pair (#AEAEB2) clears 7.6:1.
     * ContrastTest keeps both honest.
     */
    const val SECONDARY = "#5F5F66"

    /** Deltas: AA-on-surface rather than the display-bright #34C759 / #FF3B30. */
    const val SUCCESS = "#1E7D3C"
    const val DANGER = "#C5221A"

    /** Track color for progress, divider default. */
    const val HAIRLINE = "#E5E5EA"

    /** One step of the closed type scale. */
    data class Spec(val sizeSp: Int, val weight: String, val colorHex: String)

    /**
     * The whole scale used by publication text: title 16 (title-md), body 14 (body-md),
     * label 12 (label-md), caption 11 (label-sm) — all MD3 steps.
     * Glance only ships FontWeight Normal/Medium/Bold, so `weight` is one of those three
     * and the scale keeps to two active weights (see the design skill).
     */
    val SCALE: Map<String, Spec> = mapOf(
        "title" to Spec(16, "bold", PRIMARY),
        "body" to Spec(14, "normal", PRIMARY),
        "label" to Spec(12, "medium", PRIMARY),
        "caption" to Spec(11, "normal", SECONDARY),
    )

    /** Absent or unknown `style` lands here — a newer payload must never crash the widget. */
    const val DEFAULT = "body"

    fun spec(style: String?): Spec = SCALE[style] ?: SCALE.getValue(DEFAULT)

    fun fontSize(style: String?): TextUnit = spec(style).sizeSp.sp

    /** Glance exposes exactly Normal, Medium and Bold; anything else reads as Normal. */
    fun fontWeight(name: String): FontWeight = when (name) {
        "medium" -> FontWeight.Medium
        "bold", "heavy" -> FontWeight.Bold
        else -> FontWeight.Normal
    }

    /** `alignment` accepts both the Glance-style and CSS-style names. */
    fun textAlign(alignment: String?): TextAlign = when (alignment) {
        "center" -> TextAlign.Center
        "end", "trailing", "right" -> TextAlign.End
        else -> TextAlign.Start
    }

    /**
     * The ink a node ends up with: an explicit override wins, otherwise the scale color,
     * otherwise black. A non-hex override is ignored (see [HexColor]).
     */
    fun resolvedColor(style: String?, colorOverride: String? = null): Color =
        HexColor.color(colorOverride)
            ?: HexColor.color(spec(style).colorHex)
            ?: Color.Black

    fun textStyle(
        style: String?,
        colorOverride: String? = null,
        alignment: String? = null,
    ): TextStyle = TextStyle(
        color = ColorProvider(resolvedColor(style, colorOverride)),
        fontSize = fontSize(style),
        fontWeight = fontWeight(spec(style).weight),
        textAlign = textAlign(alignment),
    )

    /**
     * A themed surface picks its own ColorProvider here
     * rather than inventing a second scale.
     */
    fun textStyle(style: String?, color: ColorProvider): TextStyle = TextStyle(
        color = color,
        fontSize = fontSize(style),
        fontWeight = fontWeight(spec(style).weight),
    )

    /** Uses [fallback] when [hex] is absent or not a plain 3/6-digit hex. */
    fun colorOr(hex: String?, fallback: String): Color =
        HexColor.color(hex) ?: HexColor.color(fallback) ?: Color.Black
}

/**
 * 3- or 6-digit `#RGB` / `#RRGGBB` only. 8-digit alpha hex, named colors and junk are ignored rather than
 * crashing, so a stricter server can never blank a user's widget.
 */
object HexColor {
    private val THREE = Regex("^#([0-9A-Fa-f]{3})$")
    private val SIX = Regex("^#([0-9A-Fa-f]{6})$")

    fun color(hex: String?): Color? {
        val value = hex?.trim() ?: return null
        THREE.matchEntire(value)?.let { match ->
            val digits = match.groupValues[1]
            return fromRgb(
                digits[0].digitToInt(16) * 17,
                digits[1].digitToInt(16) * 17,
                digits[2].digitToInt(16) * 17,
            )
        }
        SIX.matchEntire(value) ?: return null
        val rgb = value.substring(1)
        return fromRgb(
            rgb.substring(0, 2).toInt(16),
            rgb.substring(2, 4).toInt(16),
            rgb.substring(4, 6).toInt(16),
        )
    }

    private fun fromRgb(red: Int, green: Int, blue: Int): Color =
        Color(0xFF000000L or (red.toLong() shl 16) or (green.toLong() shl 8) or blue.toLong())
}
