package com.you.hermeswidget.work

import android.content.Context
import androidx.work.*
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.widget.LayoutParser
import java.util.concurrent.TimeUnit

class RefreshWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val url = Config.getBackendUrl(applicationContext) ?: return Result.success()
        val widgetId = Config.getWidgetId(applicationContext)
        val secureToken = com.you.hermeswidget.net.SecureStore.token(applicationContext)
        val configToken = Config.getToken(applicationContext)
        val tokensToTry = listOfNotNull(secureToken, configToken)
                .filter { it.isNotEmpty() }
                .distinct()
        for (token in tokensToTry) {
            val (code, fetchedBody) = HermesApi.fetchWidget(url, widgetId, token)
            if (code == 200 && fetchedBody != null) {
                // Refresh completes only after a newer valid layout is stored — invalid cannot poison cache
                val parsed = try { LayoutParser.parse(fetchedBody) } catch (_: Exception) { null }
                if (parsed != null) {
                    Config.setCachedLayout(applicationContext, fetchedBody)
                    break
                }
            }
            if (code == 401 || code == 403) break
        }
        try {
            val manager = android.appwidget.AppWidgetManager.getInstance(applicationContext)
            val ids = manager.getAppWidgetIds(android.content.ComponentName(applicationContext, com.you.hermeswidget.widget.HermesWidgetReceiver::class.java))
            com.you.hermeswidget.widget.HermesWidgetReceiver().onUpdate(applicationContext, manager, ids)
        } catch (_: Exception) {
        }
        return Result.success()
    }

    companion object {
        private const val PERIODIC_NAME = "hermes-refresh-periodic"
        private const val POST_TAP_NAME = "hermes-refresh-post-tap"

        fun schedulePeriodic(context: Context) {
            val req = PeriodicWorkRequestBuilder<RefreshWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                PERIODIC_NAME, ExistingPeriodicWorkPolicy.UPDATE, req
            )
        }

        fun schedulePostTapPoll(context: Context) {
            // Bounded 2-minute post-tap completion check
            val req = OneTimeWorkRequestBuilder<RefreshWorker>()
                .setInitialDelay(2, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                POST_TAP_NAME, ExistingWorkPolicy.REPLACE, req
            )
        }
    }
}
