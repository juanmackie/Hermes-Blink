package com.you.hermeswidget.widget

import android.content.Context
import androidx.glance.GlanceId
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.action.ActionParameters
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.Outcome
import com.you.hermeswidget.net.RequestUpdateEvent
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker
import org.json.JSONObject
import java.util.UUID

object ActionCallbacks {
    class EventAction : ActionCallback {
        override suspend fun onAction(
            context: Context, glanceId: GlanceId, parameters: ActionParameters
        ) {
            // Counted first, before anything can throw or return. This is the only place in
            // the app that observes a Glance action dispatch: the broadcast is delivered to
            // Glance's own merged ActionCallbackBroadcastReceiver, never to
            // HermesWidgetReceiver, so counting it there counted nothing at all.
            val instanceId = runCatching { WidgetInstanceIds.of(glanceId) }.getOrNull()
            val eventName = runCatching { parameters[WidgetParams.eventKey] }.getOrNull()
            runCatching { Config.recordActionReached(context, eventName, instanceId) }
            android.util.Log.i(
                TAG,
                "action reached instance=$instanceId event=$eventName " +
                    "kind=${runCatching { parameters[WidgetParams.kindKey] }.getOrNull()}",
            )
            // Any throw from here is recorded rather than vanishing into a log nobody reads.
            try {
                handle(context, glanceId, parameters, instanceId, eventName)
            } catch (error: Throwable) {
                runCatching {
                    Config.recordCallbackException(
                        context, "${error.javaClass.simpleName}: ${error.message.orEmpty()}",
                    )
                    Config.recordActionOutcome(
                        context, eventName ?: "<no event>", instanceId, 0, "callback_exception",
                        error.message?.take(200),
                    )
                }
                android.util.Log.e(TAG, "action handler threw", error)
            }
        }

        private suspend fun handle(
            context: Context,
            glanceId: GlanceId,
            parameters: ActionParameters,
            instanceId: String?,
            eventFromEntry: String?,
        ) {
            val widgetId = parameters[WidgetParams.widgetIdKey]
                ?: Config.getWidgetId(context)
            val event = (eventFromEntry ?: parameters[WidgetParams.eventKey]).also {
                if (it == null) {
                    // The last silent exit, removed: a callback with no event name is a
                    // wiring bug, and a wiring bug that says nothing is how a round is lost.
                    android.util.Log.e(
                        TAG,
                        "callback fired with no event parameter " +
                            "kind=${parameters[WidgetParams.kindKey]}",
                    )
                    Config.recordActionOutcome(
                        context, "<no event>", instanceId, 0, "missing_event_parameter",
                        "the widget action was dispatched without an event name",
                        source = Config.SOURCE_WIDGET_ACTION,
                    )
                }
            } ?: return
            val payload = parameters[WidgetParams.payloadKey]
            val kind = parameters[WidgetParams.kindKey] ?: event
            val itemId = parameters[WidgetParams.itemIdKey].orEmpty()
            val actionClass = parameters[WidgetParams.actionClassKey] ?: "reversible"
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
                    publication?.revision ?: 0, clientEventId, token, payload,
                )
            } else {
                val body = JSONObject()
                if (!payload.isNullOrBlank()) body.put("payload", JSONObject(payload))
                body.put("clientEventId", clientEventId)
                if (itemId.isNotBlank()) body.put("itemId", itemId)
                if (instanceId != null) body.put("instanceId", instanceId)
                // Which control was pressed. A widget tap and the in-app button post the
                // same event, and only this field tells them apart afterwards.
                if (event == "request_update") {
                    body.put("source", RequestUpdateEvent.SOURCE_WIDGET_ACTION)
                }
                HermesApi.postEventWithFields(url, widgetId, event, body, token)
            }
            if (result.code in 200..299) {
                val publication = com.you.hermeswidget.net.PublicationRepository.loadCached(context)
                if (publication != null) {
                    HermesApi.reportAttention(url, token, widgetId, publication.revision, taps = 1)
                }
                Config.recordActionOutcome(
                    context, event, instanceId, 200, "ok", null,
                    source = Config.SOURCE_WIDGET_ACTION,
                )
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
                        .put("payload", payload ?: "{}"))
                }
            }
        }

        private fun recordActionFailure(
            context: Context, event: String, instanceId: String?, outcome: Outcome
        ) {
            Config.recordActionOutcome(
                context, event, instanceId, outcome.httpStatus ?: -1, outcome.code, outcome.message,
                source = Config.SOURCE_WIDGET_ACTION,
            )
        }

    }

    private const val TAG = "HermesWidgetAction"
}

/**
 * The AppWidgetManager id behind a Glance id, when the platform gives us one.
 *
 * `AppWidgetId` is `@RestrictTo(LIBRARY_GROUP)`, so lint objects to naming it; the cast
 * is confined here and degrades to null rather than failing the action. A missing id costs
 * attribution, not the tap.
 *
 * It is also how the widget asks the launcher how large *this* cell is: in Responsive
 * mode `LocalSize` reports the sample Glance composed for, not the cell, so the numeric
 * id is the only way to get the real geometry (see SizeGate).
 */
@Suppress("RestrictedApi")
object WidgetInstanceIds {
    /** The AppWidgetManager id behind a Glance id, or null when it is not an app widget. */
    fun idOf(glanceId: GlanceId): Int? = runCatching {
        (glanceId as? androidx.glance.appwidget.AppWidgetId)?.appWidgetId?.takeIf { it >= 0 }
    }.getOrNull()

    fun of(glanceId: GlanceId): String? = idOf(glanceId)?.toString()
}

object WidgetParams {
    val widgetIdKey = ActionParameters.Key<String>("widgetId")
    val eventKey = ActionParameters.Key<String>("event")
    val payloadKey = ActionParameters.Key<String>("payload")
    val kindKey = ActionParameters.Key<String>("kind")
    val itemIdKey = ActionParameters.Key<String>("itemId")
    val actionClassKey = ActionParameters.Key<String>("actionClass")
}
