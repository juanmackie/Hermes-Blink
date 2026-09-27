package com.you.hermeswidget

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The sentence a human reads when a press appears to do nothing.
 *
 * Round 13: the "fired" counter was maintained in `HermesWidgetReceiver.onReceive`, which
 * never sees a Glance action broadcast — Glance delivers it to its own merged
 * `ActionCallbackBroadcastReceiver`. The counter read 0 after a real press, and that 0 was
 * then read as evidence that no broadcast had been produced. It was not evidence of
 * anything, because it could not have been anything else.
 *
 * These tests pin the replacement: the count is taken where the dispatch arrives, and the
 * two possible failures — the press never got here, or our handler went wrong — are stated
 * as different sentences so they cannot be conflated a third time.
 */
class ActionVerdictTest {

    private fun record(
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
    fun `a press that never reached the callback is named as a touch problem`() {
        // The case under investigation. It must not read like "our request failed", because
        // no request was made.
        val v = ActionVerdict.of(record(count = 0), outcomes = 0)
        assertFalse(v.pressReachedApp)
        assertTrue(v.line, v.line.contains("did not get past the surface"))
        assertTrue(
            "the verdict must not imply no press happened: ${v.line}",
            v.line.contains("either none was made"),
        )
        assertTrue(v.line, v.line.contains("either none was made"))
    }

    @Test
    fun `a press that reached us is stated as reached, with its outcome`() {
        val v = ActionVerdict.of(record(count = 2, lastEvent = "request_update"), outcomes = 2)
        assertTrue(v.pressReachedApp)
        assertTrue(v.line, v.line.contains("reached 2x"))
        assertTrue(v.line, v.line.contains("request_update"))
    }

    @Test
    fun `a handler that threw is reported as ours, not as a missing press`() {
        val v = ActionVerdict.of(
            record(count = 1, exceptions = 1, lastException = "IllegalStateException: boom"),
            outcomes = 1,
        )
        assertTrue(v.pressReachedApp)
        assertTrue(v.line, v.line.contains("callback threw"))
        assertTrue(v.line, v.line.contains("IllegalStateException"))
    }

    @Test
    fun `an outcome without a reached count is called inconsistent, not smoothed over`() {
        // Impossible by construction — outcomes are only written inside the callback — so
        // the only honest reading is that the trail is broken, and saying so is the point.
        val v = ActionVerdict.of(record(count = 0), outcomes = 3)
        assertTrue(v.line, v.line.contains("inconsistent"))
    }

    @Test
    fun `reaching the callback without an outcome means the handler stopped early`() {
        val v = ActionVerdict.of(record(count = 1, lastEvent = "request_update"), outcomes = 0)
        assertTrue(v.pressReachedApp)
        assertTrue(v.line, v.line.contains("stopped early"))
    }

    @Test
    fun `a missing record reads as no press rather than as a crash`() {
        val v = ActionVerdict.of(null, outcomes = 0)
        assertEquals(0, v.reached)
        assertFalse(v.pressReachedApp)
        assertTrue(v.line.isNotBlank())
    }
}
