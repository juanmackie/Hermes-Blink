package com.you.hermeswidget.widget

import android.content.Context
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore

/** Sends the current launcher geometry to the host immediately after a resize. */
object WidgetInstanceReporter {
    fun reportBlocking(context: Context) {
        val appContext = context.applicationContext
        val baseUrl = SecureStore.baseUrl(appContext) ?: Config.getBackendUrl(appContext) ?: return
        val token = SecureStore.token(appContext) ?: return
        val instances = WidgetDimensions.allInstances(appContext).map {
            mapOf(
                "instanceId" to it.instanceId,
                "sizeClass" to it.sizeClass,
                "widthDp" to it.widthDp,
                "heightDp" to it.heightDp,
                "widthPx" to it.widthPx,
                "heightPx" to it.heightPx,
            )
        }
        HermesApi.reportInstances(baseUrl, Config.getWidgetId(appContext), instances, token)
    }

    fun reportAsync(context: Context) {
        Thread({ runCatching { reportBlocking(context) } }, "hermes-widget-inventory").start()
    }
}
