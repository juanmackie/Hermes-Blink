package com.you.hermeswidget.widget

import android.content.Context
import androidx.glance.GlanceId
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.action.ActionParameters
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.Outcome
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker
import org.json.JSONObject
import java.util.UUID

object ActionCallbacks {
    class EventAction : ActionCallback {
        override suspend fun onAction(
            context: Context, glanceId: GlanceId, parameters: ActionParameters
        ) {
            val widgetId = parameters[WidgetParams.widgetIdKey]
                ?: Config.getWidgetId(context)
            val instanceId = WidgetInstanceIds.of(glanceId)
            // Log the entry before anything can bail out. Round 6 learned that a press
            // which produces no record is indistinguishable from a press that never
            // happened; this line is where that is settled.
            android.util.Log.i(
                "HermesWidgetAction",
                "onAction instance=$instanceId event=${parameters[WidgetParams.eventKey]} " +
                    "kind=${parameters[WidgetParams.kindKey]} " +
                    "hasPayload=${!parameters[WidgetParams.payloadKey].isNullOrBlank()}",
            )
            val event = parameters[WidgetParams.eventKey].also {
                if (it == null) {
                    // The last silent exit, removed: a callback with no event name is a
                    // wiring bug, and a wiring bug that says nothing is how a round is lost.
                    android.util.Log.e(
                        "HermesWidgetAction",
                        "callback fired with no event parameter " +
                            "kind=${parameters[WidgetParams.kindKey]}",
                    )
                    Config.recordActionOutcome(
                        context, "<no event>", instanceId, 0, "missing_event_parameter",
                        "the widget action was dispatched without an event name",
                    )
                }
            } ?: return
            val payload = parameters[WidgetParams.payloadKey]
            val kind = parameters[WidgetParams.kindKey] ?: event
            val itemId = parameters[WidgetParams.itemIdKey].orEmpty()
            val actionClass = parameters[WidgetParams.actionClassKey] ?: "reversible"
            val confirmOnDevice = parameters[WidgetParams.confirmOnDeviceKey] ?: false
            val url = SecureStore.baseUrl(context) ?: Config.getBackendUrl(context)
            val token = SecureStore.token(context)
            if (url == null || token == null) {
                // The silent exits of round 5. Log the reason and record it locally so
                // Diagnostics can show why a tap did nothing, instead of returning quietly.
                val outcome = if (url == null) Outcome.noServer() else Outcome.noToken()
                recordActionFailure(context, event, instanceId, outcome)
                return
            }
            val clientEventId = UUID.randomUUID().toString()
            val result = if (kind in setOf("approve", "snooze", "open")) {
                val publication = com.you.hermeswidget.net.PublicationRepository.loadCached(context)
                HermesApi.postAction(
                    url, widgetId, kind, itemId, actionClass,
                    publication?.revision ?: 0, clientEventId, confirmOnDevice, token, payload,
                )
            } else {
                val body = JSONObject()
                if (!payload.isNullOrBlank()) body.put("payload", JSONObject(payload))
                body.put("clientEventId", clientEventId)
                if (itemId.isNotBlank()) body.put("itemId", itemId)
                if (instanceId != null) body.put("instanceId", instanceId)
                HermesApi.postEventWithFields(url, widgetId, event, body, token)
            }
            if (result.code in 200..299) {
                val publication = com.you.hermeswidget.net.PublicationRepository.loadCached(context)
                if (publication != null) {
                    HermesApi.reportAttention(url, token, widgetId, publication.revision, taps = 1)
                }
                Config.recordActionOutcome(context, event, instanceId, 200, "ok", null)
                RefreshWorker.schedulePostTapPoll(context)
            } else {
                val outcome = Outcome.from(result, "Update requested", "Request update")
                recordActionFailure(context, event, instanceId, outcome)
                if (kind in setOf("approve", "snooze", "open") && itemId.isNotBlank()) {
                    val publication = com.you.hermeswidget.net.PublicationRepository.loadCached(context)
                    Config.enqueuePendingAction(context, JSONObject()
                        .put("event", kind)
                        .put("itemId", itemId)
                        .put("actionClass", actionClass)
                        .put("revision", publication?.revision ?: 0)
                        .put("clientEventId", clientEventId)
                        .put("confirmOnDevice", confirmOnDevice)
                        .put("payload", payload ?: "{}"))
                }
            }
        }

        private fun recordActionFailure(
            context: Context, event: String, instanceId: String?, outcome: Outcome
        ) {
            Config.recordActionOutcome(
                context, event, instanceId, outcome.httpStatus ?: -1, outcome.code, outcome.message,
            )
        }
    }
}

/**
 * The AppWidgetManager id behind a Glance id, when the platform gives us one.
 *
 * `AppWidgetId` is `@RestrictTo(LIBRARY_GROUP)`, so lint objects to naming it; the cast
 * is confined here and degrades to null (reported as "instance not attributed") rather
 * than failing the action. A missing id costs attribution, not the tap.
 */
@Suppress("RestrictedApi")
object WidgetInstanceIds {
    fun of(glanceId: GlanceId): String? = runCatching {
        val value = glanceId as? androidx.glance.appwidget.AppWidgetId ?: return null
        value.appWidgetId.takeIf { it >= 0 }?.toString()
    }.getOrNull()
}

object WidgetParams {
    val widgetIdKey = ActionParameters.Key<String>("widgetId")
    val eventKey = ActionParameters.Key<String>("event")
    val payloadKey = ActionParameters.Key<String>("payload")
    val kindKey = ActionParameters.Key<String>("kind")
    val itemIdKey = ActionParameters.Key<String>("itemId")
    val actionClassKey = ActionParameters.Key<String>("actionClass")
    val confirmOnDeviceKey = ActionParameters.Key<Boolean>("confirmOnDevice")
}
