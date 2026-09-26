package com.you.hermeswidget.push

import android.content.Context
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage

/**
 * UnifiedPush consumer.  The distributor only receives a content-free
 * `fetch` message from the host; this service deliberately ignores the message
 * bytes and starts the normal authenticated refresh path.
 */
class UnifiedPushService : PushService() {
    override fun onNewEndpoint(endpoint: PushEndpoint, instance: String) {
        val context = applicationContext
        Config.setPushState(context, "registered", true)
        val baseUrl = SecureStore.baseUrl(context) ?: Config.getBackendUrl(context) ?: return
        val token = SecureStore.token(context) ?: return
        Thread {
            val result = HermesApi.registerPushEndpoint(baseUrl, token, endpoint.url)
            if (result.code !in 200..299) {
                Config.setPushState(context, "failed", true, "endpoint registration failed (HTTP ${result.code})")
            }
        }.start()
    }

    override fun onMessage(message: PushMessage, instance: String) {
        Config.setLastPushWake(applicationContext)
        RefreshWorker.scheduleWake(applicationContext)
    }

    override fun onRegistrationFailed(reason: org.unifiedpush.android.connector.FailedReason, instance: String) {
        val context = applicationContext
        val reasonText = reason.name
        Config.setPushState(context, "failed", true, reasonText)
        val baseUrl = SecureStore.baseUrl(context) ?: Config.getBackendUrl(context) ?: return
        val token = SecureStore.token(context) ?: return
        Thread { HermesApi.reportPushState(baseUrl, token, "failed", true, reasonText) }.start()
    }

    override fun onUnregistered(instance: String) {
        val context = applicationContext
        Config.setPushState(context, "unregistered", true)
        val baseUrl = SecureStore.baseUrl(context) ?: Config.getBackendUrl(context) ?: return
        val token = SecureStore.token(context) ?: return
        Thread { HermesApi.clearPushEndpoint(baseUrl, token) }.start()
    }
}
