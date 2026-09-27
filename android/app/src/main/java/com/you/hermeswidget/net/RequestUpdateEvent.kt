package com.you.hermeswidget.net

import org.json.JSONObject
import java.util.UUID

/**
 * The two `request_update` senders, told apart.
 *
 * Field round 13: the widget pill and the in-app button both posted
 * `{"event": "request_update", "clientEventId": …, "instanceId": …}`, so the server could
 * not tell them apart. A `review` event followed by a `request_update` was therefore
 * ambiguous — consistent with the pill working *and* with the pill opening the app and the
 * user pressing the button inside it. The widget pill had, on the evidence, never been
 * proven to work at all.
 *
 * So each sender now declares itself. `source` is closed, additive, and stored with the
 * event: a support question "did the button on the home screen work?" becomes a query
 * instead of an inference from a neighbouring event.
 */
object RequestUpdateEvent {
    /** The button composed into the widget surface. */
    const val SOURCE_WIDGET_ACTION = "widget_action"

    /** The button inside the publication detail view. */
    const val SOURCE_IN_APP_BUTTON = "in_app_button"

    val SOURCES = setOf(SOURCE_WIDGET_ACTION, SOURCE_IN_APP_BUTTON)

    /**
     * The widget pill's payload. [instanceId] is the cell the press came from, which is
     * the same id the render receipt names.
     */
    fun widgetBody(instanceId: String?, clientEventId: String = UUID.randomUUID().toString()): JSONObject =
        JSONObject()
            .put("clientEventId", clientEventId)
            .apply { if (!instanceId.isNullOrBlank()) put("instanceId", instanceId) }
            .put("source", SOURCE_WIDGET_ACTION)

    /** The in-app button's payload. The instance is known only when the view was opened from the widget. */
    fun inAppBody(instanceId: String?, clientEventId: String = UUID.randomUUID().toString()): JSONObject =
        JSONObject()
            .put("clientEventId", clientEventId)
            .apply { if (!instanceId.isNullOrBlank()) put("instanceId", instanceId) }
            .put("source", SOURCE_IN_APP_BUTTON)
}
