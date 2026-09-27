package com.you.hermeswidget

import com.you.hermeswidget.net.Config
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The sentence a human reads when a press appears to do nothing.
 *
 * Round 7, bug 1. The first version of this class asserted that "an outcome with reached at
 * zero is impossible" and reported it as `inconsistent`. It was not impossible: the
 * publication detail view has its own "Request update" button, an ordinary `Button` in an
 * Activity, which records the same outcome shape and structurally cannot increment the
 * Glance callback's counter. Diagnostics was therefore reporting the app as broken while it
 * was behaving correctly, and every reading of that line was wrong.
 *
 * Outcomes now carry their origin, so the branches are decided by data.
 */
class ActionVerdictTest {

    private fun outcome(source: String?, event: String = "request_update"): JSONObject =
        JSONObject()
            .put("event", event)
            .put("status", 200)
            .put("code", "ok")
            .apply { if (source != null) put("source", source) }

    private fun reached(
        count: Int = 0,
        exceptions: Int = 0,
        lastEvent: String = "",
        lastException: String = "",
    ) = JSONObject()
        .put("count", count)
        .put("exceptions", exceptions)
        .put("lastEvent", lastEvent)
        .put("lastException", lastException)

    @Test
    fun `a press that never reached the widget reads as such`() {
        val v = ActionVerdict.of(reached(count = 0), emptyList())
        assertTrue(v.pressNeverReachedWidget)
        assertFalse(v.pressReachedApp)
        assertTrue(v.line, v.line.contains("no press has reached the widget"))
    }

    @Test
    fun `app-origin outcomes do not read as inconsistent`() {
        // The bug: two presses of the in-app button, nothing from the pill. That is the
        // steady state of an app with two producers, and it used to print "inconsistent".
        val v = ActionVerdict.of(
            reached(count = 0),
            listOf(
                outcome(Config.SOURCE_IN_APP_BUTTON),
                outcome(Config.SOURCE_IN_APP_BUTTON),
            ),
        )
        assertFalse(
            "app-origin outcomes are not a contradiction: " + v.line,
            v.line.startsWith("inconsistent"),
        )
        assertTrue(v.line, v.line.contains("came"))
        assertTrue(v.line, v.line.contains("different control"))
        assertEquals(2, v.outcomes)
        assertEquals(0, v.widgetOutcomes)
        assertFalse(v.pressReachedApp)
    }

    @Test
    fun `an outcome the widget callback claims with a zero counter is still inconsistent`() {
        // The genuinely impossible case is kept: the record and the counter disagree.
        val v = ActionVerdict.of(
            reached(count = 0),
            listOf(outcome(Config.SOURCE_WIDGET_ACTION)),
        )
        assertTrue(v.line, v.line.startsWith("inconsistent"))
        assertEquals(1, v.widgetOutcomes)
    }

    @Test
    fun `records written before the origin field cannot be blamed on the widget`() {
        // Backward compatibility: an outcome with no `source` is unknown, and an unknown
        // outcome must not make the callback look inconsistent.
        val v = ActionVerdict.of(
            reached(count = 0),
            listOf(outcome(null), outcome("")),
        )
        assertEquals(0, v.widgetOutcomes)
        assertFalse(v.line, v.line.startsWith("inconsistent"))
        assertTrue(v.line, v.line.contains("no press has reached the widget"))
    }

    @Test
    fun `a press that reached the callback is reported as reached`() {
        val v = ActionVerdict.of(
            reached(count = 2, lastEvent = "request_update"),
            listOf(
                outcome(Config.SOURCE_WIDGET_ACTION),
                outcome(Config.SOURCE_WIDGET_ACTION),
            ),
        )
        assertTrue(v.pressReachedApp)
        assertFalse(v.pressNeverReachedWidget)
        assertTrue(v.line, v.line.contains("reached 2x"))
        assertTrue(v.line, v.line.contains("request_update"))
    }

    @Test
    fun `a callback that threw is reported as ours, not as a missing press`() {
        val v = ActionVerdict.of(
            reached(count = 1, exceptions = 1, lastException = "IllegalStateException: boom"),
            listOf(outcome(Config.SOURCE_WIDGET_ACTION)),
        )
        assertTrue(v.pressReachedApp)
        assertTrue(v.line, v.line.contains("threw"))
        assertTrue(v.line, v.line.contains("IllegalStateException"))
    }

    @Test
    fun `reaching the callback without an outcome means our handler stopped early`() {
        val v = ActionVerdict.of(reached(count = 1, lastEvent = "request_update"), emptyList())
        assertTrue(v.pressReachedApp)
        assertTrue(v.line, v.line.contains("stopped early"))
    }

    @Test
    fun `a missing record reads as no press rather than a crash`() {
        val v = ActionVerdict.of(null, emptyList())
        assertEquals(0, v.reached)
        assertTrue(v.pressNeverReachedWidget)
        assertTrue(v.line.isNotBlank())
    }

    @Test
    fun `the two producers have distinct, stable source names`() {
        // These strings are the whole fix: they are written into every record and read
        // back, so a rename is a data change and must be treated as one.
        assertEquals("widget_action", Config.SOURCE_WIDGET_ACTION)
        assertEquals("in_app_button", Config.SOURCE_IN_APP_BUTTON)
        assertNotEquals(
            "a press from the pill and a press from the app must be distinguishable",
            Config.SOURCE_WIDGET_ACTION, Config.SOURCE_IN_APP_BUTTON,
        )
    }
}
