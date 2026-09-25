package com.you.hermeswidget.work

import android.content.Context
import android.util.Log
import androidx.glance.appwidget.updateAll
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OutOfQuotaPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.ConnectionState
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.PublicationRepository
import com.you.hermeswidget.net.RefreshOutcome
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.widget.HermesWidget
import com.you.hermeswidget.widget.LayoutParser
import com.you.hermeswidget.widget.WakeAlarmReceiver
import com.you.hermeswidget.widget.WidgetDimensions
import java.util.concurrent.TimeUnit

class RefreshWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        Config.setDiagnosticTime(applicationContext, "poll")
        retryPendingActions()
        reportInventory()
        val result = runCatching { PublicationRepository.refresh(applicationContext) }
            .getOrElse { error ->
                // Without this the failure is invisible: the widget just stays stale and the
                // worker retries forever with the cause only in a swallowed exception.
                Log.w(TAG, "publication refresh threw; reporting offline and retrying", error)
                Config.setConnectionState(
                    applicationContext,
                    ConnectionState.OFFLINE,
                    System.currentTimeMillis(),
                )
                return Result.retry()
            }

        if (result.outcome.retry || result.outcome == RefreshOutcome.REVOKED) {
            Log.w(
                TAG,
                "publication refresh failed: outcome=${result.outcome} detail=${result.detail}",
            )
        }

        if (result.outcome == RefreshOutcome.EMPTY) refreshLegacyLayout()
        HermesWidget().updateAll(applicationContext)

        if (result.outcome == RefreshOutcome.UPDATED ||
            result.outcome == RefreshOutcome.NOT_MODIFIED
        ) {
            Config.setDiagnosticTime(applicationContext, "fetch")
            val (width, height) = WidgetDimensions.fromContext(applicationContext)
            if (PublicationRepository.acknowledgeRenderSubmitted(applicationContext, width, height)) {
                Config.setDiagnosticTime(applicationContext, "render")
            }
        }
        return if (result.outcome.retry) Result.retry() else Result.success()
    }

    private suspend fun retryPendingActions() {
        val baseUrl = SecureStore.baseUrl(applicationContext) ?: Config.getBackendUrl(applicationContext)
        val token = SecureStore.token(applicationContext)
        if (baseUrl.isNullOrBlank() || token.isNullOrBlank()) return
        for (action in Config.pendingActions(applicationContext)) {
            val event = action.optString("event")
            val itemId = action.optString("itemId")
            val clientEventId = action.optString("clientEventId")
            val revision = action.optInt("revision", 0)
            if (event.isBlank() || itemId.isBlank() || clientEventId.isBlank()) {
                Config.removePendingAction(applicationContext, clientEventId)
                continue
            }
            val result = HermesApi.postAction(
                baseUrl,
                Config.getWidgetId(applicationContext),
                event,
                itemId,
                action.optString("actionClass", "reversible"),
                revision,
                clientEventId,
                action.optBoolean("confirmOnDevice", false),
                token,
                action.optString("payload", "{}"),
            )
            if (result.code in 200..299 || (result.code in 400..499 && result.code !in listOf(408, 429))) {
                // A rejected stale/unknown intent must not poison the bounded outbox;
                // transient network, auth, and rate-limit failures remain retryable.
                Config.removePendingAction(applicationContext, clientEventId)
            }
        }
    }

    private suspend fun reportInventory() {
        val baseUrl = SecureStore.baseUrl(applicationContext) ?: Config.getBackendUrl(applicationContext)
        val token = SecureStore.token(applicationContext)
        if (baseUrl.isNullOrBlank() || token.isNullOrBlank()) return
        val instances = WidgetDimensions.allInstances(applicationContext).map {
            mapOf(
                "instanceId" to it.instanceId,
                "sizeClass" to it.sizeClass,
                "widthDp" to it.widthDp,
                "heightDp" to it.heightDp,
                "widthPx" to it.widthPx,
                "heightPx" to it.heightPx,
            )
        }
        HermesApi.reportInstances(baseUrl, Config.getWidgetId(applicationContext), instances, token)
    }

    private suspend fun refreshLegacyLayout() {
        val baseUrl = SecureStore.baseUrl(applicationContext) ?: Config.getBackendUrl(applicationContext)
        val token = SecureStore.token(applicationContext)
        if (baseUrl.isNullOrBlank() || token.isNullOrBlank()) return
        val (code, body) = HermesApi.fetchWidget(baseUrl, Config.getWidgetId(applicationContext), token)
        if (code != 200 || body == null) return
        runCatching { LayoutParser.parse(body) }
            .onSuccess { Config.setCachedLayout(applicationContext, body) }
    }

    companion object {
        private const val TAG = "HermesWidget"
        private const val PERIODIC_NAME = "hermes-refresh-periodic"
        private const val POST_TAP_NAME = "hermes-refresh-post-tap"
        private const val WAKE_NAME = "hermes-refresh-wake"
        const val IMMEDIATE_NAME = "hermes-refresh-immediate"

        private val networkConstraint = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        fun schedulePeriodic(context: Context) {
            val request = PeriodicWorkRequestBuilder<RefreshWorker>(15, TimeUnit.MINUTES)
                .setConstraints(networkConstraint)
                .setBackoffCriteria(androidx.work.BackoffPolicy.EXPONENTIAL, 15, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                PERIODIC_NAME,
                ExistingPeriodicWorkPolicy.UPDATE,
                request,
            )
        }

        fun scheduleWake(context: Context) {
            val request = OneTimeWorkRequestBuilder<RefreshWorker>()
                .setConstraints(networkConstraint)
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                WAKE_NAME,
                ExistingWorkPolicy.REPLACE,
                request,
            )
            WakeAlarmReceiver.schedule(context)
        }

        fun enqueueNow(context: Context) {
            val request = OneTimeWorkRequestBuilder<RefreshWorker>()
                .setConstraints(networkConstraint)
                .setBackoffCriteria(androidx.work.BackoffPolicy.EXPONENTIAL, 15, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                IMMEDIATE_NAME,
                ExistingWorkPolicy.REPLACE,
                request,
            )
        }

        fun schedulePostTapPoll(context: Context) {
            val request = OneTimeWorkRequestBuilder<RefreshWorker>()
                .setInitialDelay(2, TimeUnit.MINUTES)
                .setConstraints(networkConstraint)
                .setBackoffCriteria(androidx.work.BackoffPolicy.EXPONENTIAL, 15, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                POST_TAP_NAME,
                ExistingWorkPolicy.REPLACE,
                request,
            )
        }
    }
}
