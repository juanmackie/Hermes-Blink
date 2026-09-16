package com.you.hermeswidget.work

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.widget.HermesWidget

class RefreshWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val url = Config.getBackendUrl(applicationContext) ?: return Result.success()
        val widgetId = Config.getWidgetId(applicationContext)
        val secureToken = com.you.hermeswidget.net.SecureStore.token(applicationContext)
        val configToken = Config.getToken(applicationContext)
        val tokensToTry = listOfNotNull(secureToken, configToken)
                .filter { it.isNotEmpty() }
                .distinct()
        var body: String? = null
        for (token in tokensToTry) {
            val (code, fetchedBody) = HermesApi.fetchWidget(url, widgetId, token)
            if (code == 200 && fetchedBody != null) {
                // Phase E: refresh completes only after a newer valid layout is stored.
                Config.setCachedLayout(applicationContext, fetchedBody)
                body = fetchedBody
                break
            }
            // Stop trying this token on auth errors
            if (code == 401 || code == 403) break
        }
        try {
            val manager = android.appwidget.AppWidgetManager.getInstance(applicationContext)
            val ids = manager.getAppWidgetIds(android.content.ComponentName(applicationContext, com.you.hermeswidget.widget.HermesWidgetReceiver::class.java))
            com.you.hermeswidget.widget.HermesWidgetReceiver().onUpdate(applicationContext, manager, ids)
        } catch (e: Exception) {
            // Best-effort refresh
        }
        return Result.success()
    }
}
