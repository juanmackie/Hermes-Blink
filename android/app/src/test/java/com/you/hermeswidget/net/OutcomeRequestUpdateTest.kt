package com.you.hermeswidget.net

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A 200 that did not start a refresh must not read as "Update requested".
 *
 * The server records the tap first and then spawns `hermes cron run
 * hermes-widget-refresh`. When that spawn fails (missing binary, missing job,
 * auth) it returns 200 with trigger.error set. The old path treated every 2xx
 * as success, so a dead cron looked identical to a live one.
 */
class OutcomeRequestUpdateTest {

    private fun okBody(trigger: JSONObject?, request: JSONObject?): String =
        JSONObject()
            .put("ok", true)
            .apply { if (trigger != null) put("trigger", trigger) }
            .apply { if (request != null) put("request", request) }
            .toString()

    @Test
    fun `triggered refresh reads as success`() {
        val body = okBody(
            JSONObject().put("triggered", true).put("pid", 123),
            JSONObject().put("status", "triggered"),
        )
        val outcome = Outcome.forRequestUpdate(HttpResult(200, body))
        assertTrue(outcome.ok)
        assertEquals("ok", outcome.code)
    }

    @Test
    fun `coalesced duplicate without error reads as success`() {
        val body = okBody(
            JSONObject().put("triggered", false).put("duplicate", true),
            JSONObject().put("status", "triggered"),
        )
        val outcome = Outcome.forRequestUpdate(HttpResult(200, body))
        assertTrue(outcome.ok)
    }

    @Test
    fun `failed trigger reads as failure with the server error`() {
        val body = okBody(
            JSONObject().put("triggered", false).put("error", "hermes executable is not available"),
            JSONObject().put("status", "failed").put("error", "hermes executable is not available"),
        )
        val outcome = Outcome.forRequestUpdate(HttpResult(200, body))
        assertFalse(outcome.ok)
        assertEquals("refresh_not_triggered", outcome.code)
        assertTrue(outcome.message, outcome.message.contains("did not start"))
        assertTrue(outcome.message, outcome.message.contains("hermes executable"))
    }

    @Test
    fun `non-2xx still uses the HTTP mapping`() {
        val outcome = Outcome.forRequestUpdate(
            HttpResult(401, JSONObject().put("error", "unauthorized").toString()),
        )
        assertFalse(outcome.ok)
        assertEquals("unauthorized", outcome.code)
    }
}
