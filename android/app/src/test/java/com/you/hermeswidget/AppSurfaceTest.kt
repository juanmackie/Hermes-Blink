package com.you.hermeswidget

import org.junit.Assert.assertEquals
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
        "layout/activity_publication.xml",
        "layout/item_publication_action.xml",
        "layout/item_publication_primary_action.xml",
        "layout/item_publication_text_action.xml",
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
        Regex("<color name=\"([a-z_]+)\">(#[0-9A-Fa-f]{6,8})</color>")
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
        // The M3 baseline, token for token, with the reference tone each value comes
        // from. A screenshot cannot tell you these; a spec reference can.
        assertEqualsHex("#FEF7FF", light, "app_surface")                 // neutral 98
        assertEqualsHex("#FFFFFF", light, "app_surface_container_lowest") // neutral 100
        assertEqualsHex("#F7F2FA", light, "app_surface_container_low")    // neutral 96
        assertEqualsHex("#F3EDF7", light, "app_surface_container")        // neutral 94
        assertEqualsHex("#ECE6F0", light, "app_surface_container_high")   // neutral 92
        assertEqualsHex("#E6E0E9", light, "app_surface_container_highest")// neutral 90
        assertEqualsHex("#1D1B20", light, "app_on_surface")               // neutral 10
        assertEqualsHex("#49454F", light, "app_on_surface_variant")       // nv 30
        assertEqualsHex("#6750A4", light, "app_primary")                  // primary 40
        assertEqualsHex("#B3261E", light, "app_error")                    // error 40

        assertEqualsHex("#141218", dark, "app_surface")                  // neutral 6
        assertEqualsHex("#0F0D13", dark, "app_surface_container_lowest")  // neutral 4
        assertEqualsHex("#1D1B20", dark, "app_surface_container_low")     // neutral 10
        assertEqualsHex("#211F26", dark, "app_surface_container")         // neutral 12
        assertEqualsHex("#2B2930", dark, "app_surface_container_high")    // neutral 17
        assertEqualsHex("#36343B", dark, "app_surface_container_highest") // neutral 22
        assertEqualsHex("#E6E0E9", dark, "app_on_surface")                // neutral 90
        assertEqualsHex("#CAC4D0", dark, "app_on_surface_variant")        // nv 80
        assertEqualsHex("#D0BCFF", dark, "app_primary")                   // primary 80
        assertEqualsHex("#F2B8B5", dark, "app_error")                     // error 80
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
    fun `every button background carries a press state`() {
        // Reported as "the buttons do nothing": they did act, but a plain <shape> has no
        // state layer, so a tap produced no ripple and read as a dead control.
        for (name in listOf("bg_button_filled", "bg_button_tonal", "bg_button_outlined")) {
            val xml = read("drawable/$name.xml")
            assertTrue(
                "$name.xml has no <ripple>; a button with no press state gives no " +
                    "feedback and reads as broken",
                xml.contains("<ripple"),
            )
            assertTrue("$name.xml has no mask; the ripple would spill past the pill", 
                xml.contains("@android:id/mask"))
        }
        // Each control's layer is its own ink, so a filled button's press state is
        // onPrimary over primary, not a single grey shared by everything.
        for (token in listOf(
            "app_state_layer_filled", "app_state_layer_tonal",
            "app_state_layer_outlined",
        )) {
            assertTrue(
                "$token must exist in the light scheme",
                colors(null).containsKey(token),
            )
        }
    }

    @Test
    fun `a focused field looks focused`() {
        assertTrue(
            "bg_field.xml has no focused state, so there is no cue which field is active",
            read("drawable/bg_field.xml").contains("state_focused"),
        )
    }

    @Test
    fun `the full container ladder exists and is monotonic in both directions`() {
        // M3 replaced elevation overlays with tonal containers, so every step of the ladder
        // has to exist and move the right way: down in light, up in dark.
        val ladder = listOf(
            "app_surface_container_lowest", "app_surface_container_low",
            "app_surface_container", "app_surface_container_high",
            "app_surface_container_highest",
        )
        val light = colors(null)
        val dark = colors("night")
        for (token in ladder) {
            assertTrue("$token missing from the light scheme", token in light)
            assertTrue("$token missing from the dark scheme", token in dark)
        }
        assertDescending(light, ladder, "light")
        assertAscending(dark, ladder, "dark")
    }

    @Test
    fun `non_text_and_state_tokens_clear_the_three_to_one_floor`() {
        // 4.5:1 is for text. Outlines, the status dot and the primary are non-text, and
        // Android's floor for those is 3:1.
        for ((qualifier, mode) in listOf<Pair<String?, Map<String, String>>>(
            null to colors(null), "night" to colors("night"),
        )) {
            for (token in listOf("app_outline", "app_primary", "widget_status_fresh")) {
                val tokenMap = if (token.startsWith("widget_")) widgetColors(qualifier) else mode
                if (tokenMap[token] == null || !tokenMap[token]!!.startsWith("#")) continue
                val ratio = Wcag.contrast(
                    tokenMap.getValue(token), mode.getValue("app_surface"),
                )
                assertTrue(
                    "$token on surface in ${qualifier ?: "values"} measured " +
                        "${"%.2f".format(ratio)}:1 (non-text floor is 3:1)",
                    ratio >= 3.0,
                )
            }
        }
    }

    @Test
    fun `state layers take each control's own foreground at the M3 opacities`() {
        // The layer sits on the control's fill, so it is that control's ink at 8-10%, not a
        // single on-surface grey applied to everything.
        for (qualifier in listOf<String?>(null, "night")) {
            val mode = colors(qualifier)
            for (token in listOf(
                "app_state_layer_filled", "app_state_layer_tonal",
                "app_state_layer_outlined",
            )) {
                assertTrue("$token missing in ${qualifier ?: "values"}", token in mode)
            }
        }
        // A layer must be visible against the fill it is drawn on: it is 10% of the
        // control's own ink, so it differs from the fill and from the ink.
        val light = colors(null)
        val fill = light.getValue("app_primary")
        val layer = light.getValue("app_state_layer_filled")
        assertTrue("the state layer must not equal its fill", layer != fill)
        assertTrue(
            "the state layer must differ from the ink it is made from",
            Wcag.contrast(layer, light.getValue("app_on_primary")) >= 1.05,
        )
    }

    @Test
    fun `the shape scale is the Material 3 corner radius scale`() {
        val dimens = read("values/app_dimens.xml")
        for (pair in listOf("xs" to 4, "sm" to 8, "md" to 12, "lg" to 16, "xl" to 28)) {
            val (name, dp) = pair
            assertTrue(
                "the M3 corner radius scale has $name = ${dp}dp",
                Regex("name=\"shape_" + name + "\">" + dp + "dp").containsMatchIn(dimens),
            )
        }
        assertTrue("the scale needs a full radius", "shape_full" in dimens)
        // Cards take `medium` per the MD3 component/shape mapping; the widget root
        // keeps `extra large` as its floor.
        assertTrue(
            "containers take a named scale step, not an off-scale value: " +
                "containers should use the medium radius (12dp)",
            dimens.contains("name=\"shape_container\">@dimen/shape_md"),
        )
        // Fields take `small` per the same mapping.
        assertTrue(
            "fields should use the small radius (8dp)",
            dimens.contains("name=\"shape_field\">@dimen/shape_sm"),
        )
    }

    @Test
    fun `type roles carry the M3 size, line height and tracking`() {
        val themes = read("values/themes.xml")
        // role -> (size sp, M3 line height sp). M3 line height is size + spacing extra;
        // android:lineHeight is API 28+ and this app supports 26, so the extra is explicit.
        val roles = listOf(
            Triple("TextTitle", 22, 28),
            Triple("TextBody", 16, 24),
            Triple("TextBodySmall", 14, 20),
            Triple("TextLabel", 12, 16),
        )
        val q = 34.toChar()   // a double quote, without an escape inside a python string
        for ((style, size, lineHeight) in roles) {
            val block = Regex("<style name=" + q + style + q + ".*?</style>", RegexOption.DOT_MATCHES_ALL)
                .find(themes)?.value
            assertTrue(style + " is missing from themes.xml", block != null)
            val sizeToken = "android:textSize" + q + ">" + size + "sp"
            assertTrue(style + " must be " + size + "sp, the M3 step", block!!.contains(sizeToken))
            val extraToken = "android:lineSpacingExtra" + q + ">"
            val extra = Regex(Regex.escape(extraToken) + "([0-9]+)sp").find(block)
                ?.groupValues?.get(1)?.toInt()
            assertTrue(style + " has no line height", extra != null)
            assertEquals(
                style + " must sit on the M3 " + lineHeight + "sp line",
                lineHeight,
                size + extra!!,
            )
            assertTrue(
                style + " carries M3 tracking",
                block.contains("android:letterSpacing"),
            )
        }
    }

    private fun widgetColors(qualifier: String?): Map<String, String> {        val file = res.resolve(
            if (qualifier == null) "values/colors.xml" else "values-$qualifier/colors.xml"
        )
        val found = mutableMapOf<String, String>()
        Regex("<color name=\"([a-z_]+)\">(#[0-9A-Fa-f]{6,8})</color>")
            .findAll(file.readText())
            .forEach { match ->
                found[match.groupValues[1]] = match.groupValues[2]
            }
        return found
    }

    private fun assertDescending(mode: Map<String, String>, ladder: List<String>, label: String) {
        for (i in 0 until ladder.size - 1) {
            assertTrue(
                "$label: ${ladder[i]} must be lighter than ${ladder[i + 1]}",
                Wcag.luminance(mode.getValue(ladder[i])) >
                    Wcag.luminance(mode.getValue(ladder[i + 1])),
            )
        }
    }

    private fun assertAscending(mode: Map<String, String>, ladder: List<String>, label: String) {
        for (i in 0 until ladder.size - 1) {
            assertTrue(
                "$label: ${ladder[i]} must be darker than ${ladder[i + 1]}",
                Wcag.luminance(mode.getValue(ladder[i])) <
                    Wcag.luminance(mode.getValue(ladder[i + 1])),
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
        // The widget root's fallback lives in the widget's dimens; the shared shape scale
        // (which has an `xl` step) is for the screens.
        val widgetDimens = read("values/dimens.xml")
        assertTrue(
            "the widget root fallback must be the M3 extra-large step (28dp)",
            Regex("name=\"widget_corner_radius\">(\\d+)dp").find(widgetDimens)
                ?.groupValues?.get(1)?.toInt() == 28,
        )
        val appDimens = read("values/app_dimens.xml")
        assertTrue(
            "the shared shape scale should carry the extra-large step too",
            Regex("name=\"shape_xl\">(\\d+)dp").find(appDimens)
                ?.groupValues?.get(1)?.toInt() == 28,
        )
    }

    private fun assertEqualsHex(expected: String, mode: Map<String, String>, name: String) {
        assertTrue("$name is not defined", name in mode)
        assertTrue(
            "$name is ${mode[name]}, expected $expected from the design system",
            mode[name].equals(expected, ignoreCase = true),
        )
    }

    // ------------------------------------------------------------------------------------
    // Material 3
    //
    // The palette was M3 before the components were: the tokens, the tonal ladder and the
    // type scale were the spec's, while every control was a platform widget with a
    // hand-drawn background. These gates hold the second half in place — the theme is a
    // real Theme.Material3, every role in it is mapped, and the screens are built from MD3
    // components — because the failure mode is silent: a screen with M3 colours and a
    // platform <Button> looks almost right and is not Material.
    // ------------------------------------------------------------------------------------

    @Test
    fun `the app theme is a Material 3 theme, not an AppCompat one wearing its colours`() {
        val themes = read("values/themes.xml")
        assertTrue(
            "AppTheme must be built on Theme.Material3; the palette alone is not MD3",
            Regex("""parent="Theme\.Material3\.[A-Za-z.]*"""").containsMatchIn(themes),
        )
        // NoActionBar is a decision, not an omission: the layouts carry an MD3 top app bar
        // (TopAppBar) instead, and leaving the platform action bar on as well would give
        // every screen two bars.
        assertTrue(
            "AppTheme must be the NoActionBar variant; the screens draw their own top app bar",
            themes.contains("parent=\"Theme.Material3.DayNight.NoActionBar\""),
        )
    }

    @Test
    fun `every MD3 colour role the theme uses is mapped to a Hermes token`() {
        // An unmapped role does not fall back to the app's palette: it falls back to the
        // Material library's static purple, which is how a screen ends up half Material You
        // and half stock. The role list is what the components actually read.
        val themes = read("values/themes.xml")
        val required = listOf(
            "colorPrimary", "colorOnPrimary", "colorPrimaryContainer", "colorOnPrimaryContainer",
            "colorSecondary", "colorOnSecondary", "colorSecondaryContainer", "colorOnSecondaryContainer",
            "colorTertiary", "colorOnTertiary", "colorTertiaryContainer", "colorOnTertiaryContainer",
            "colorSurface", "colorOnSurface", "colorSurfaceVariant", "colorOnSurfaceVariant",
            "colorSurfaceDim", "colorSurfaceBright",
            "colorSurfaceContainerLowest", "colorSurfaceContainerLow", "colorSurfaceContainer",
            "colorSurfaceContainerHigh", "colorSurfaceContainerHighest",
            "colorSurfaceInverse", "colorOnSurfaceInverse", "colorPrimaryInverse",
            "colorError", "colorOnError", "colorErrorContainer", "colorOnErrorContainer",
            "colorOutline", "colorOutlineVariant",
        )
        for (role in required) {
            val mapping = Regex("<item name=\"" + role + "\">([^<]+)</item>").find(themes)?.groupValues?.get(1)
            assertTrue("theme role $role is not mapped at all", mapping != null)
            assertTrue(
                "theme role $role maps to \"$mapping\"; it must be a @color/app_* token " +
                    "(or the platform's own dynamic system_* role)",
                mapping!!.startsWith("@color/app_") || mapping.startsWith("@android:color/system_"),
            )
        }
    }

    @Test
    fun `the secondary and tertiary pairs are defined in both modes and clear AA`() {
        // These roles arrived with the MD3 components. A container/ink pair is either
        // legible or it is the single most common MD3 mistake, so it is measured like the
        // primary pair above rather than trusted.
        for (qualifier in listOf<String?>(null, "night")) {
            val mode = colors(qualifier)
            for ((container, ink) in listOf(
                "app_secondary_container" to "app_on_secondary_container",
                "app_tertiary_container" to "app_on_tertiary_container",
                "app_error_container" to "app_on_error_container",
                "app_primary_container" to "app_on_primary_container",
            )) {
                val ratio = Wcag.contrast(mode.getValue(ink), mode.getValue(container))
                assertTrue(
                    "$ink on $container in ${qualifier ?: "values"} measured " +
                        "${"%.2f".format(ratio)}:1 (need 4.5:1)",
                    ratio >= 4.5,
                )
            }
            for ((fill, on) in listOf(
                "app_secondary" to "app_on_secondary",
                "app_tertiary" to "app_on_tertiary",
                "app_error" to "app_on_error",
            )) {
                val ratio = Wcag.contrast(mode.getValue(on), mode.getValue(fill))
                assertTrue(
                    "$on on $fill in ${qualifier ?: "values"} measured " +
                        "${"%.2f".format(ratio)}:1 (need 4.5:1)",
                    ratio >= 4.5,
                )
            }
            // The snackbar's own pair: inverse-on-surface on inverse-surface.
            val inverse = Wcag.contrast(
                mode.getValue("app_inverse_on_surface"),
                mode.getValue("app_inverse_surface"),
            )
            assertTrue(
                "app_inverse_on_surface on app_inverse_surface in ${qualifier ?: "values"} " +
                    "measured ${"%.2f".format(inverse)}:1",
                inverse >= 4.5,
            )
        }
    }

    @Test
    fun `every screen is built from MD3 components, not platform widgets`() {
        // A platform <Button> cannot take a colour role, a state layer or a corner step,
        // so a screen that mixes the two is a screen that renders differently from the
        // theme. Raw <EditText> cannot show a label in its outline at all.
        val platformWidgets = listOf(
            "<Button", "<EditText", "<CheckBox", "<Switch", "<ProgressBar",
            "<ImageButton", "<RadioButton", "<SeekBar", "<Spinner",
        )
        for (path in layouts) {
            for (widget in platformWidgets) {
                assertTrue(
                    "$path uses the platform $widget; use the MD3 component instead",
                    !read(path).contains(widget),
                )
            }
        }
    }

    @Test
    fun `every screen has an MD3 top app bar that insets its content`() {
        // NoActionBar moved the title out of the platform action bar, so a screen without a
        // top app bar of its own has no title and no way back. fitsSystemWindows is the
        // other half: targetSdk 35 is edge-to-edge, so a layout that does not apply the
        // window insets draws its first row under the status bar.
        val screens = layouts.filter { it.startsWith("layout/activity_") }
        for (path in screens) {
            val xml = read(path)
            assertTrue(
                "$path has no MaterialToolbar; AppTheme is NoActionBar, so this screen " +
                    "would have no title and no way back",
                xml.contains("MaterialToolbar"),
            )
            assertTrue(
                "$path does not use the shared MD3 top app bar style",
                xml.contains("style=\"@style/TopAppBar\""),
            )
            assertTrue(
                "$path does not apply the window insets (edge-to-edge on targetSdk 35)",
                xml.contains("android:fitsSystemWindows=\"true\""),
            )
        }
    }

    @Test
    fun `text fields are MD3 outlined boxes with a focus cue`() {
        // The bare-field version could only swap its whole background on focus, which is
        // why bg_field.xml needed a hand-written state_focused item. The MD3 box carries
        // the cue itself: the outline thickens to 2dp in primary, and the label moves up
        // into the outline. All three halves are asserted — the component in the layouts,
        // the stroke in the theme, and the *state* in the colour, because a single-colour
        // box still thickens and still fails to read as focus.
        for (path in listOf("layout/activity_settings.xml", "layout/activity_pairing.xml")) {
            val xml = read(path)
            assertTrue("$path has no TextInputLayout", xml.contains("TextInputLayout"))
            assertTrue(
                "$path does not use the shared MD3 outlined-box style",
                xml.contains("style=\"@style/TextField\""),
            )
        }
        val themes = read("values/themes.xml")
        for (item in listOf("boxStrokeWidthFocused", "boxStrokeWidth", "boxStrokeColor")) {
            assertTrue(
                "the outlined field has no $item, so there is no focus cue",
                themes.contains("<item name=\"$item\">"),
            )
        }
        // `medium` (12dp) is the MD3 step for a field. A card's 16dp would make the field
        // look like a card and the card look like a field.
        assertTrue(
            "the field must take the medium corner step, not the card's",
            themes.contains("<item name=\"boxCornerRadiusTopStart\">@dimen/shape_field"),
        )
        for (stateList in listOf("app_field_box_stroke", "app_field_hint_text")) {
            val selector = File(res, "color/$stateList.xml")
            assertTrue(
                "color/$stateList.xml is missing; a single-colour box thickens without " +
                    "reading as focus",
                selector.isFile,
            )
            val xml = selector.readText()
            assertTrue(
                "color/$stateList.xml has no focused state",
                xml.contains("android:state_focused=\"true\""),
            )
            assertTrue(
                "color/$stateList.xml does not move to the primary role on focus, which " +
                    "is the whole point of the cue",
                Regex("""state_focused="true"\s*/>\s*<item|state_focused="true"[^>]*/>""")
                    .find(xml) != null &&
                    Regex("android:color=\"@color/app_primary\"").find(xml) != null,
            )
        }
        assertTrue(
            "the field's stroke must come from the state list, not a flat colour",
            themes.contains("<item name=\"boxStrokeColor\">@color/app_field_box_stroke</item>"),
        )
    }

    @Test
    fun `no style name can inherit from a style that sits next to it`() {
        // A style named A.B inherits from A when A exists in the same file, unless it
        // declares a parent. `AppTheme.Base` next to `AppTheme` was a cycle waiting to
        // happen, and a cycle in a theme is a crash on every screen rather than a lint
        // warning — the one failure in this round that no JVM test could have caught.
        val themes = read("values/themes.xml")
        val declared = Regex("""<style name="([^"]+)"( parent="([^"]*)")?""")
            .findAll(themes)
            .map { it.groupValues[1] to it.groupValues[3] }
            .toList()
        val names = declared.map { it.first }.toSet()
        for ((name, parent) in declared) {
            val dot = name.lastIndexOf('.')
            if (dot < 0) continue
            val prefix = name.substring(0, dot)
            if (prefix !in names) continue   // the implicit parent does not exist: fine
            assertTrue(
                "style \"$name\" has an implicit parent \"$prefix\", which is declared in " +
                    "this file; name it differently or give it an explicit parent",
                parent.isNotEmpty(),
            )
        }
    }

    @Test
    fun `the corner scale the theme hands to components is the M3 scale`() {
        // The components do not each name a radius: they read the scale from the theme, so
        // the theme's scale has to be the spec's, or every component is quietly off it.
        val themes = read("values/themes.xml")
        for ((role, step) in listOf(
            "shapeAppearanceCornerExtraSmall" to "ExtraSmall",
            "shapeAppearanceCornerSmall" to "Small",
            "shapeAppearanceCornerMedium" to "Medium",
            "shapeAppearanceCornerLarge" to "Large",
            "shapeAppearanceCornerExtraLarge" to "ExtraLarge",
        )) {
            assertTrue(
                "$role is not mapped to the Hermes shape scale",
                themes.contains("<item name=\"$role\">@style/ShapeAppearance.Hermes.$step</item>"),
            )
            assertTrue(
                "ShapeAppearance.Hermes.$step does not take a step from the shared scale",
                Regex("<style name=\"ShapeAppearance\\.Hermes\\.$step\"[^>]*>\\s*<item name=\"cornerSize\">@dimen/shape_[a-z]+")
                    .containsMatchIn(themes),
            )
        }
    }

    @Test
    fun `depth is tone, so the MD2 elevation overlay is off`() {
        // ElevationOverlayEnabled tints a raised surface toward the primary hue. MD3
        // replaced that with the surfaceContainer ladder, and the palette here has all five
        // steps, so leaving the overlay on means every raised thing is tinted twice.
        val themes = read("values/themes.xml")
        assertTrue(
            "AppTheme must set elevationOverlayEnabled=false; depth is tone in MD3",
            themes.contains("<item name=\"elevationOverlayEnabled\">false</item>"),
        )
    }

    @Test
    fun `system bar icon polarity follows the device mode`() {
        // A style in values-night *replaces* the light one rather than merging, which is
        // why the polarity is a bool resource: one theme item, resolved per configuration.
        assertTrue(
            "AppTheme must read the polarity from @bool/app_light_system_bars",
            read("values/themes.xml").contains("<item name=\"android:windowLightStatusBar\">@bool/app_light_system_bars</item>"),
        )
        for (mode in listOf("values", "values-night")) {
            val bools = File(res, "$mode/bools.xml")
            assertTrue("$mode/bools.xml is missing", bools.isFile)
            val value = Regex("<bool name=\"app_light_system_bars\">(true|false)</bool>")
                .find(bools.readText())?.groupValues?.get(1)
            assertTrue("$mode/bools.xml does not define app_light_system_bars", value != null)
            assertEquals(
                "light mode has dark icons and night mode has light icons",
                if (mode == "values") "true" else "false",
                value,
            )
        }
    }

    @Test
    fun `no screen uses a type size that is not on the M3 scale`() {
        // 13sp, 15sp and 18sp were the three ad hoc sizes these screens carried. MD3's
        // scale is 11/12/14/16/22/24/28/32/36/45/57, and a size off the scale is a size
        // that belongs to no role — so it cannot be reasoned about, only looked at.
        val onScale = setOf("11", "12", "14", "16", "22", "24", "28", "32", "36", "45", "57")
        for (path in layouts) {
            for (match in Regex("""android:textSize="([0-9]+)sp"""").findAll(read(path))) {
                assertTrue(
                    "$path uses ${match.groupValues[1]}sp, which is not a step on the M3 " +
                        "type scale; use the type role that means it",
                    match.groupValues[1] in onScale,
                )
            }
        }
    }

    @Test
    fun `transient messages are snackbars, and a surviving toast says why`() {
        // MD3 replaced the toast with the snackbar: it is in the app's own surface, it can
        // carry an action, and it is anchored. The one case the snackbar cannot do is a
        // message for a screen that is closing, because there is nothing left to anchor it
        // to — and those call sites have to say so in the comment above them, or a reader
        // cannot tell a decision from an oversight.
        val sources = mapOf(
            "SettingsActivity.kt" to File(sourceDir(), "com/you/hermeswidget/SettingsActivity.kt"),
            "DiagnosticsActivity.kt" to File(sourceDir(), "com/you/hermeswidget/DiagnosticsActivity.kt"),
            "PublicationActivity.kt" to File(sourceDir(), "com/you/hermeswidget/PublicationActivity.kt"),
            "config/PairingActivity.kt" to File(sourceDir(), "com/you/hermeswidget/config/PairingActivity.kt"),
            "WidgetPinning.kt" to File(sourceDir(), "com/you/hermeswidget/WidgetPinning.kt"),
        )
        for ((name, file) in sources) {
            assertTrue("$name moved; update this test", file.isFile)
            val lines = file.readLines()
            val code = lines.joinToString("\n")
                .replace(Regex("""//.*"""), "")
                .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
            assertTrue(
                "$name has no snackbar: MD3's transient surface is the snackbar, not a toast",
                code.contains("Snackbar.make"),
            )
            lines.forEachIndexed { index, line ->
                if (!line.contains("Toast.makeText")) return@forEachIndexed
                val above = lines.drop((index - 6).coerceAtLeast(0)).take(6)
                    .filter { it.trimStart().startsWith("//") || it.trimStart().startsWith("*") || it.trimStart().startsWith("/*") }
                    .joinToString(" ").lowercase()
                assertTrue(
                    "$name:${index + 1} shows a toast with no comment above it saying why " +
                        "this message cannot be a snackbar",
                    above.contains("clos") && above.contains("snackbar"),
                )
            }
        }
    }

    private fun sourceDir(): File =
        File(File(System.getProperty("user.dir") ?: ".").absoluteFile, "src/main/java")
}
