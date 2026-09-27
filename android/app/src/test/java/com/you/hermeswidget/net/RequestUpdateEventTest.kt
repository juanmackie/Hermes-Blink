package com.you.hermeswidget.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The two `request_update` senders, told apart.
 *
 * Field round 13: the widget pill and the in-app button both posted the same payload, so a
 * `review` event followed by a `request_update` proved nothing about the pill — it was
 * equally consistent with the pill working and with the pill opening the app and the
 * in-app button being pressed inside it. The reviewer's source read is what settled it:
 * the pill had, on the evidence, never been proven to work.
 *
 * These tests pin the only thing available off-device: that the two paths carry different,
 * closed identities, so a recorded event can be attributed to the control that sent it.
 */
class RequestUpdateEventTest {

    @Test
    fun `the two senders declare different sources`() {
        val widget = RequestUpdateEvent.widgetBody("40")
        val inApp = RequestUpdateEvent.inAppBody("40")
        assertEquals("widget_action", widget.getString("source"))
        assertEquals("in_app_button", inApp.getString("source"))
        assertNotEquals(widget.getString("source"), inApp.getString("source"))
    }

    @Test
    fun `the source vocabulary is closed`() {
        assertEquals(2, RequestUpdateEvent.SOURCES.size)
        assertEquals(
            setOf("widget_action", "in_app_button"),
            RequestUpdateEvent.SOURCES,
        )
    }

    @Test
    fun `both carry the idempotency key and the instance when there is one`() {
        val widget = RequestUpdateEvent.widgetBody("40", clientEventId = "pill-1")
        assertEquals("pill-1", widget.getString("clientEventId"))
        assertEquals("40", widget.getString("instanceId"))
        assertEquals("40", RequestUpdateEvent.inAppBody("40").getString("instanceId"))
    }

    @Test
    fun `an unknown instance is omitted rather than sent empty`() {
        // The in-app button is reachable from a notification or the launcher, where there
        // is no widget instance; an empty string would look like instance "0" server-side.
        for (body in listOf(
            RequestUpdateEvent.widgetBody(null),
            RequestUpdateEvent.inAppBody(""),
        )) {
            assertNull(body.optString("instanceId", null))
            assertEquals(false, body.has("instanceId"))
        }
    }

    @Test
    fun `a client event id is generated when none is supplied`() {
        val first = RequestUpdateEvent.widgetBody("40").getString("clientEventId")
        val second = RequestUpdateEvent.widgetBody("40").getString("clientEventId")
        assertNotEquals("a generated id must not repeat", first, second)
        assertEquals(36, first.length)   // a UUID
    }
}
