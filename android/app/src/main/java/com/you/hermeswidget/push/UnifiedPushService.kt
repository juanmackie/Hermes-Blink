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
        val baseUrl = SecureStore.baseUrl(context) ?: Config.getBackendUrl(context) ?: return
        val token = SecureStore.token(context) ?: return
        Thread {
            HermesApi.registerPushEndpoint(baseUrl, token, endpoint.url)
        }.start()
    }

    override fun onMessage(message: PushMessage, instance: String) {
        RefreshWorker.scheduleWake(applicationContext)
    }

    override fun onRegistrationFailed(reason: org.unifiedpush.android.connector.FailedReason, instance: String) {
        // Periodic WorkManager polling remains the documented fallback.
    }

    override fun onUnregistered(instance: String) {
        val context = applicationContext
        val baseUrl = SecureStore.baseUrl(context) ?: Config.getBackendUrl(context) ?: return
        val token = SecureStore.token(context) ?: return
        Thread { HermesApi.clearPushEndpoint(baseUrl, token) }.start()
    }
}
