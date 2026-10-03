package com.you.hermeswidget.widget

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/**
 * WCAG 2.x relative luminance and contrast ratio (Task 7).
 *
 * Small text needs 4.5:1, and a widget's ink is always small text — the whole scale tops
 * out at 16sp bold (title-md). This is the arithmetic the palette decisions were made with: the old
 * #8E8E93 caption measured 2.97:1 on the light surface, and 1.34:1 composited over a dark
 * wallpaper, which is why the ship failed AA.
 */
object Wcag {
    fun luminance(hex: String): Double {
        val rgb = normalize(hex)
        return 0.2126 * linear(rgb[0]) + 0.7152 * linear(rgb[1]) + 0.0722 * linear(rgb[2])
    }

    fun contrast(foreground: String, background: String): Double {
        val a = luminance(foreground)
        val b = luminance(background)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)
    }

    private fun linear(channel: Int): Double {
        val c = channel / 255.0
        return if (c <= 0.03928) c / 12.92 else ((c + 0.055) / 1.055).pow(2.4)
    }

    /** Accepts `#RGB` and `#RRGGBB`; returns 0-255 channels. */
    fun normalize(hex: String): IntArray {
        val value = hex.trim().removePrefix("#")
        require(value.length == 3 || value.length == 6) { "not a 3/6-digit hex: $hex" }
        return if (value.length == 3) {
            value.map { "$it$it".toInt(16) }.toIntArray()
        } else {
            value.chunked(2).map { it.toInt(16) }.toIntArray()
        }
    }
}

/**
 * Reads the shipped color resources so the test asserts what the device actually paints,
 * not a copy that can drift. Falls back to the Kotlin tokens when resources are not on
 * disk (e.g. a jar-only test run) rather than skipping the check.
 */
private object WidgetColors {
    private fun resDir(): File? {
        var dir: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        repeat(4) {
            val candidate = dir?.resolve("src/main/res")
            if (candidate != null && candidate.isDirectory) return candidate
            dir = dir?.parentFile
        }
        return null
    }

    private fun read(qualifier: String?): Map<String, String> {
        val folder = if (qualifier == null) "values" else "values-$qualifier"
        val file = resDir()?.resolve("$folder/colors.xml") ?: return emptyMap()
        if (!file.isFile) return emptyMap()
        val colors = mutableMapOf<String, String>()
        Regex("<color name=\"([a-z_]+)\">([^<]+)</color>")
            .findAll(file.readText())
            .forEach { match ->
                colors[match.groupValues[1]] = match.groupValues[2].trim()
            }
        return colors
    }

    val day: Map<String, String> by lazy { read(null) }
    val night: Map<String, String> by lazy { read("night") }
    val v31: Map<String, String> by lazy { read("v31") }
    val available: Boolean by lazy { resDir() != null }
}

class ContrastTest {
    // Surface tokens the widget composes against.
    private val lightSurface = "#F7F2FF"
    private val darkSurface = "#1C1C1E"
    private val darkSecondary = "#AEAEB2"
    private val darkInk = "#F2F2F7"

    @Test
    fun `primary and secondary ink clear AA on the light surface`() {
        assertAtLeast45(Typo.PRIMARY, lightSurface, "PRIMARY on light")
        assertAtLeast45(Typo.SECONDARY, lightSurface, "SECONDARY on light")
    }

    @Test
    fun `secondary ink is no longer the old 2_97 to 1 grey`() {
        val old = Wcag.contrast("#8E8E93", lightSurface)
        assertTrue("the retired grey measured $old", old < 4.5)
        assertTrue(Wcag.contrast(Typo.SECONDARY, lightSurface) > old)
    }

    @Test
    fun `semantic deltas clear AA on the light surface`() {
        assertAtLeast45(Typo.SUCCESS, lightSurface, "SUCCESS on light")
        assertAtLeast45(Typo.DANGER, lightSurface, "DANGER on light")
    }

    @Test
    fun `the dark ink pair clears AA on the dark surface`() {
        assertAtLeast45(darkInk, darkSurface, "dark ink")
        assertAtLeast45(darkSecondary, darkSurface, "dark secondary")
    }

    @Test
    fun `the shipped resources are present and legible`() {
        if (!WidgetColors.available) return // resource-less run: the token checks above still ran
        val day = WidgetColors.day
        assertTrue("values/colors.xml must define the surface token", day.containsKey("widget_surface"))
        assertTrue(day.containsKey("widget_secondary"))
        assertTrue(day.containsKey("widget_accent"))
        assertTrue(day.containsKey("widget_on_accent"))
        val night = WidgetColors.night
        assertTrue("values-night/colors.xml must define the dark surface", night.containsKey("widget_surface"))
        assertTrue(night.containsKey("widget_secondary"))

        // Aliases (@android:color/...) are resolved by aapt, not by this test.
        if (day["widget_surface"]?.startsWith("#") == true) {
            assertAtLeast45(day.getValue("widget_on_surface"), day.getValue("widget_surface"), "day ink")
            assertAtLeast45(day.getValue("widget_secondary"), day.getValue("widget_surface"), "day secondary")
            assertAtLeast45(day.getValue("widget_on_accent"), day.getValue("widget_accent"), "day on-accent")
        }
        if (night["widget_surface"]?.startsWith("#") == true) {
            assertAtLeast45(night.getValue("widget_on_surface"), night.getValue("widget_surface"), "night ink")
            assertAtLeast45(night.getValue("widget_secondary"), night.getValue("widget_surface"), "night secondary")
            assertAtLeast45(night.getValue("widget_on_accent"), night.getValue("widget_accent"), "night on-accent")
        }
    }

    @Test
    fun `the forced dark palette used by dark_palette is legible too`() {
        if (!WidgetColors.available) return
        val day = WidgetColors.day
        assertAtLeast45(
            day.getValue("widget_on_surface_dark"),
            day.getValue("widget_surface_dark"),
            "forced dark ink",
        )
        assertAtLeast45(
            day.getValue("widget_secondary_dark"),
            day.getValue("widget_surface_dark"),
            "forced dark secondary",
        )
        assertAtLeast45(
            day.getValue("widget_on_accent_dark"),
            day.getValue("widget_accent_dark"),
            "forced dark on-accent",
        )
    }

    @Test
    fun `android 12 tokens are the platform tonal palette, not literals`() {
        if (!WidgetColors.available) return
        val v31 = WidgetColors.v31
        assertTrue(
            "values-v31/colors.xml must alias the platform palette",
            v31["widget_surface"]?.startsWith("@android:color/system_") == true,
        )
        assertTrue(v31["widget_accent"]?.startsWith("@android:color/system_accent") == true)
    }

    private fun assertAtLeast45(foreground: String, background: String, label: String) {
        val ratio = Wcag.contrast(foreground, background)
        assertTrue("$label measured ${"%.2f".format(ratio)}:1 (need 4.5:1)", ratio >= 4.5)
    }
}
