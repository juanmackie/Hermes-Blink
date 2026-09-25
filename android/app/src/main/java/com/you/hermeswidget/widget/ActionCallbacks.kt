package com.you.hermeswidget.widget

import android.content.Context
import androidx.glance.GlanceId
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.action.ActionParameters
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker

object ActionCallbacks {
    class EventAction : ActionCallback {
        override suspend fun onAction(
            context: Context, glanceId: GlanceId, parameters: ActionParameters
        ) {
            val widgetId = parameters[WidgetParams.widgetIdKey]
                ?: Config.getWidgetId(context)
            val event = parameters[WidgetParams.eventKey] ?: return
            val payload = parameters[WidgetParams.payloadKey]
            val url = Config.getBackendUrl(context) ?: return
            val token = SecureStore.token(context) ?: return
            HermesApi.postEvent(url, widgetId, event, payload ?: "{}", token)
            RefreshWorker.schedulePostTapPoll(context)
        }
    }
}

object WidgetParams {
    val widgetIdKey = ActionParameters.Key<String>("widgetId")
    val eventKey = ActionParameters.Key<String>("event")
    val payloadKey = ActionParameters.Key<String>("payload")
}
