package com.you.hermeswidget

import org.junit.Assert.assertTrue
import com.you.hermeswidget.widget.Wcag
import org.junit.Test
import java.io.File
import java.util.Locale

/**
 * The companion surfaces (main, pairing, settings, diagnostics) are the only part of this
 * app a person looks at for more than a second, and they are the part that silently rots:
 * a hard-coded `#FFFFFF` background survived through several rounds of widget work and made
 * the pairing screen unreadable in dark mode, and a hard-coded `Color.WHITE` body would
 * have done the same once the surface started following the device theme.
 *
 * These are cheap structural gates over the resource files, in the spirit of the widget
 * tests: the layouts must reference tokens, never literals, and the tokens must stay
 * legible in both modes.
 */
class AppSurfaceTest {

    private val res: File = locateRes()
    private val layouts = listOf(
        "layout/activity_main.xml",
        "layout/activity_pairing.xml",
        "layout/activity_settings.xml",
        "layout/activity_diagnostics.xml",
    )

    private fun locateRes(): File {
        var dir: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        repeat(4) {
            val candidate = dir?.resolve("src/main/res")
            if (candidate != null && candidate.isDirectory) return candidate
            dir = dir?.parentFile
        }
        throw AssertionError("could not locate src/main/res from ${System.getProperty("user.dir")}")
    }

    private fun read(path: String): String = File(res, path).readText()

    private fun colors(qualifier: String?): Map<String, String> {
        val file = File(res, if (qualifier == null) "values/app_colors.xml" else "values-$qualifier/app_colors.xml")
        val found = mutableMapOf<String, String>()
        Regex("<color name=\"([a-z_]+)\">(#[0-9A-Fa-f]{6})</color>")
            .findAll(file.readText())
            .forEach { found[it.groupValues[1]] = it.groupValues[2].lowercase(Locale.ROOT) }
        return found
    }

    @Test
    fun `no screen layout hard-codes a colour`() {
        val literal = Regex("#[0-9A-Fa-f]{6,8}\\b")
        for (path in layouts) {
            val offenders = literal.findAll(read(path)).map { it.value }.distinct().toList()
            assertTrue(
                "$path hard-codes $offenders; use a @color/app_* token so dark mode works",
                offenders.isEmpty(),
            )
        }
    }

    @Test
    fun `the programmatic publication view does not hard-code a colour either`() {
        // This one is built in Kotlin, which is exactly why it needs the gate: a
        // Color.WHITE body text on a themed surface would be invisible in light mode.
        val source = File(
            File(System.getProperty("user.dir") ?: ".").absoluteFile,
            "src/main/java/com/you/hermeswidget/PublicationActivity.kt",
        )
        assertTrue("PublicationActivity.kt moved; update this test", source.isFile)
        // Comments are stripped first: a comment that *mentions* Color.WHITE to explain
        // why it is gone is not a hard-coded colour, and a gate that flags it gets muted.
        val text = source.readText()
            .replace(Regex("""//.*"""), "")
            .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
        val offenders = Regex("""(Color\.(WHITE|BLACK)|Color\.rgb\()""").findAll(text).map { it.value }
            .distinct().toList()
        assertTrue("PublicationActivity.kt still hard-codes $offenders", offenders.isEmpty())
    }

    @Test
    fun `every colour the screens reference is defined in both modes`() {
        for (qualifier in listOf<String?>(null, "night")) {
            val defined = colors(qualifier).keys
            for (path in layouts) {
                for (match in Regex("@color/(app_[a-z_]+)").findAll(read(path))) {
                    val name = match.groupValues[1]
                    assertTrue(
                        "@color/$name is used in $path but not defined in " +
                            "${qualifier ?: "values"}",
                        name in defined,
                    )
                }
            }
        }
    }

    @Test
    fun `the tonal ladder is the one the design system specifies`() {
        val light = colors(null)
        val dark = colors("night")
        // Tonal elevation goes the *other way* in each mode, because light comes from
        // above: a raised surface is lighter on a dark canvas and darker on a light one.
        // Getting this backwards is how a "raised" card ends up recessed.
        assertTrue(
            "light: container must sit below the surface in luminance",
            Wcag.luminance(light.getValue("app_surface_container")) <
                Wcag.luminance(light.getValue("app_surface")),
        )
        assertTrue(
            "light: container_high must sit below container",
            Wcag.luminance(light.getValue("app_surface_container_high")) <
                Wcag.luminance(light.getValue("app_surface_container")),
        )
        assertTrue(
            "dark: container must sit above the surface in luminance",
            Wcag.luminance(dark.getValue("app_surface_container")) >
                Wcag.luminance(dark.getValue("app_surface")),
        )
        assertTrue(
            "dark: container_high must sit above container",
            Wcag.luminance(dark.getValue("app_surface_container_high")) >
                Wcag.luminance(dark.getValue("app_surface_container")),
        )
        // The dark set is the canonical one from the design system, token for token.
        assertEqualsHex("#131314", dark, "app_surface")
        assertEqualsHex("#1C1B1C", dark, "app_surface_container")
        assertEqualsHex("#2A2A2B", dark, "app_surface_container_high")
        assertEqualsHex("#E5E2E3", dark, "app_on_surface")
        assertEqualsHex("#C3C6D0", dark, "app_on_surface_variant")
        assertEqualsHex("#A8C7FA", dark, "app_primary")
        assertEqualsHex("#062E6F", dark, "app_on_primary")
    }

    @Test
    fun `every ink token clears AA on every surface it is painted on`() {
        for (qualifier in listOf<String?>(null, "night")) {
            val mode = colors(qualifier)
            val surfaces = listOf("app_surface", "app_surface_container", "app_surface_container_high")
            val inks = listOf("app_on_surface", "app_on_surface_variant", "app_primary")
            for (ink in inks) {
                for (surface in surfaces) {
                    val ratio = Wcag.contrast(mode.getValue(ink), mode.getValue(surface))
                    assertTrue(
                        "$ink on $surface in ${qualifier ?: "values"} measured " +
                            "${"%.2f".format(ratio)}:1 (need 4.5:1)",
                        ratio >= 4.5,
                    )
                }
            }
            // A filled button is the one place ink sits on a saturated fill.
            val onPrimary = Wcag.contrast(mode.getValue("app_on_primary"), mode.getValue("app_primary"))
            assertTrue(
                "on-primary on primary in ${qualifier ?: "values"} measured " +
                    "${"%.2f".format(onPrimary)}:1",
                onPrimary >= 4.5,
            )
        }
    }

    @Test
    fun `touch targets stay at the platform floor, not the 40dp the design system suggests`() {
        val dimens = read("values/app_dimens.xml")
        val touch = Regex("name=\"touch_target\">(\\d+)dp").find(dimens)?.groupValues?.get(1)
        assertTrue("app_dimens.xml has no touch_target", touch != null)
        assertTrue("touch target is $touch dp; the 48dp floor is not negotiable", touch!!.toInt() >= 48)
    }

    @Test
    fun `the widget root radius follows the launcher, with 28dp only as the fallback`() {
        // The design system calls 28dp non-negotiable for the widget root; on API 31+ we
        // resolve the platform radius instead, so 28dp is the floor for the fallback.
        val widgetTheme = File(
            File(System.getProperty("user.dir") ?: ".").absoluteFile,
            "src/main/java/com/you/hermeswidget/widget/WidgetTheme.kt",
        ).readText()
        assertTrue(
            "WidgetRadius must still resolve the system dimension first",
            widgetTheme.contains("system_app_widget_background_radius"),
        )
        val appDimens = read("values/app_dimens.xml")
        assertTrue(
            Regex("name=\"shape_widget_root\">(\\d+)dp").find(appDimens)?.groupValues?.get(1)?.toInt() == 28,
        )
    }

    private fun assertEqualsHex(expected: String, mode: Map<String, String>, name: String) {
        assertTrue("$name is not defined", name in mode)
        assertTrue(
            "$name is ${mode[name]}, expected $expected from the design system",
            mode[name].equals(expected, ignoreCase = true),
        )
    }
}
