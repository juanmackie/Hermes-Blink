package com.you.hermeswidget.widget

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The request action's route, as far as a JVM test can reach it.
 *
 * Round 12: two presses on a real phone produced a `review` event and no
 * `request_update`. `review` is posted from exactly one place —
 * `PublicationActivity.recordTapAndRefresh()` — and `PublicationActivity` is started from
 * exactly one place, the `actionStartActivity` intent in `HermesWidget.kt`. So a `review`
 * event is proof that the press was handled by an "open the app" target, not the action.
 *
 * Two shapes have now lost the action, and both are properties of the *source*, which is
 * what this test asserts:
 *
 *  1. the action nested inside another clickable (the root column) — which of the two
 *     handled the press then depended on layout and timing;
 *  2. the action below a Glance `LazyColumn` — a RemoteViews collection view can measure
 *     past the height it is given, and when it does the button leaves the cell while a press
 *     aimed at it lands on the surface.
 *
 * Glance compositions cannot be executed off-device, so this asserts the composition's
 * structure rather than pretending to run it. What it does guarantee is that a future edit
 * cannot quietly reintroduce either shape.
 */
class RequestActionPlacementTest {

    private val source: String = locate("HermesWidget.kt")
    private val code: String = source
        .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
        .lines().filterNot { it.trimStart().startsWith("//") }
        .joinToString("\n")
        .let { Regex("""//.*""").replace(it, "") }

    /** The body of one function, up to the next `private fun` — no ordering assumed. */
    private fun bodyOf(function: String, until: String = ""): String {
        val start = code.indexOf("private fun $function")
        assertTrue("could not find $function in HermesWidget.kt", start >= 0)
        val next = Regex("""\nprivate fun """).find(code, start + 1)
        val end = when {
            until.isNotEmpty() -> code.indexOf("private fun $until", start + 1).let {
                if (it > start) it else next?.range?.first ?: code.length
            }
            next != null -> next.range.first
            else -> code.length
        }
        return code.substring(start, end)
    }

    private fun locate(name: String): String {
        var dir: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        repeat(4) {
            val candidate = dir?.resolve("src/main/java/com/you/hermeswidget/widget/$name")
            if (candidate != null && candidate.isFile) return candidate.readText()
            dir = dir?.parentFile
        }
        throw AssertionError("could not locate $name from ${System.getProperty("user.dir")}")
    }

    @Test
    fun `the surface is not a click target`() {
        // Shape 1: the action lives inside the surface, so a clickable on the surface means
        // two nested targets with no guaranteed winner.
        val surface = bodyOf("PublicationSurface(")
        val rootColumn = surface.substringAfter("Column(").substringBefore(") {")
        assertTrue(
            "the widget root must not be a click target: a press on the action then reaches " +
                "the surface and silently opens the app",
            !rootColumn.contains("clickable("),
        )
    }

    @Test
    fun `the action is a later sibling of the column that holds the scroll region`() {
        // Shape 2: the action must not live inside the Column that holds the LazyColumn, and
        // it must be declared after it so it is laid out and hit-tested last.
        val surface = bodyOf("PublicationSurface(")
        val column = surface.substringAfter("Column(").substringBefore("RequestActionRow(dark)")
        assertTrue(
            "the request action must not be composed inside the column that holds the " +
                "LazyColumn: a collection view can measure past its height and cover it",
            !column.contains("requestUpdateAction()"),
        )
        assertTrue(
            "the request action must be composed at all",
            surface.contains("RequestActionRow(dark)"),
        )
        assertTrue(
            "the action must be the last child of the root Box, so it is topmost",
            surface.indexOf("RequestActionRow(dark)") > surface.lastIndexOf("LazyColumn("),
        )
    }

    @Test
    fun `the overlay that carries the action is not itself clickable`() {
        // A non-clickable overlay lets the hero underneath keep its own taps; a clickable
        // one would be a third nested target.
        val action = bodyOf("RequestActionRow(")
        assertTrue(
            "the action row must not be a click target",
            !action.substringAfter("Row(").substringBefore("Spacer(").contains("clickable("),
        )
        assertTrue("the action must be a 48dp touch target", action.contains("height(48.dp)"))
    }

    @Test
    fun `the header reserves the strip the action overlays`() {
        val header = bodyOf("HeaderRow(")
        assertTrue(
            "the header must reserve the action's height or the hero sits underneath it",
            header.contains("spec.showsRequestAction") && header.contains("48.dp"),
        )
    }

    @Test
    fun `the geometry keeps the action inside the cell at every size`() {
        // The arithmetic, not the composition: whatever the collection does, the chrome plus
        // the list is bounded by the cell.
        for (height in listOf(185f, 200f, 250f, 270f, 276f, 300f, 412f, 422f)) {
            for (width in listOf(180f, 245f, 407f, 624f)) {
                val spec = Breakpoints.spec(width, height)
                assertTrue(
                    "${width}x$height: chrome ${spec.chromeHeightDp} + scroll " +
                        "${spec.scrollHeightDp} exceeds the cell",
                    spec.chromeHeightDp + spec.scrollHeightDp <= height,
                )
            }
        }
    }

    @Test
    fun `a review event can only come from the open-app target`() {
        // Documents the inference the diagnosis rests on: `review` is posted by the
        // activity, and the activity is only started by an actionStartActivity intent.
        val activity = locate("../PublicationActivity.kt")
        assertTrue(
            "the diagnosis assumes `review` is posted from the activity",
            activity.contains("\"review\""),
        )
        assertTrue(
            "the widget must still have exactly one open-app target, the hero",
            code.count { it == ' ' }.let { _ ->
                Regex("""actionStartActivity\(intent\)""").findAll(code).count() == 1
            },
        )
    }
}
