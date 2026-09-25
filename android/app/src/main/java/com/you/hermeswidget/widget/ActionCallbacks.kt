package com.you.hermeswidget.widget

import android.content.Context
import androidx.glance.GlanceId
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.action.ActionParameters
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
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
            val event = parameters[WidgetParams.eventKey] ?: return
            val payload = parameters[WidgetParams.payloadKey]
            val kind = parameters[WidgetParams.kindKey] ?: event
            val itemId = parameters[WidgetParams.itemIdKey].orEmpty()
            val actionClass = parameters[WidgetParams.actionClassKey] ?: "reversible"
            val confirmOnDevice = parameters[WidgetParams.confirmOnDeviceKey] ?: false
            val url = SecureStore.baseUrl(context) ?: Config.getBackendUrl(context) ?: return
            val token = SecureStore.token(context) ?: return
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
                HermesApi.postEventWithFields(url, widgetId, event, body, token)
            }
            if (result.code in 200..299) {
                RefreshWorker.schedulePostTapPoll(context)
            } else if (kind in setOf("approve", "snooze", "open") && itemId.isNotBlank()) {
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
