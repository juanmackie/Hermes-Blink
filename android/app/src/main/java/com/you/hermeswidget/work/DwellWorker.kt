package com.you.hermeswidget.work

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import java.util.concurrent.TimeUnit

/**
 * Reports one dwell measurement for a detail view the user has left.
 *
 * This used to be a bare `Thread` started from `PublicationActivity.onStop`, which made the
 * "seen" signal best-effort in the one direction that cannot be repaired: the activity was
 * already backgrounded, so process death between the stop and the POST lost the measurement
 * permanently, and nothing was ever retried. Dwell is the only signal that separates "seen"
 * from "left on screen", so it is the one measurement that has to survive the process it
 * was measured in.
 *
 * The bucket is measured in the activity at the moment the view stopped being visible and
 * travels in the request data, because the worker runs later and cannot re-derive it.
 *
 * At-least-once by design: a request that reaches the server and is then killed before
 * returning success can be counted twice. That is the deliberate trade against losing every
 * offline measurement, and it is the trade the tap path already makes.
 */
class DwellWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val widgetId = inputData.getString(KEY_WIDGET_ID)
        val revision = inputData.getInt(KEY_REVISION, -1)
        val bucket = inputData.getString(KEY_BUCKET)
        if (widgetId == null || revision < 0 || bucket == null) {
            Log.w(TAG, "dwell request is missing its measurement; dropping it")
            return Result.failure()
        }
        val baseUrl = SecureStore.baseUrl(applicationContext) ?: Config.getBackendUrl(applicationContext)
        val token = SecureStore.token(applicationContext)
        // Retrying cannot invent a server URL or a device token, so this is terminal and
        // logged rather than a retry loop that would never succeed.
        if (baseUrl.isNullOrBlank() || token.isNullOrBlank()) {
            Log.w(TAG, "dwell not reported: no server URL or no device token")
            return Result.failure()
        }
        val result = runCatching { HermesApi.reportAttention(baseUrl, token, widgetId, revision, dwellBucket = bucket) }
            .getOrElse { error ->
                Log.w(TAG, "dwell report threw; retrying", error)
                null
            }
        return when {
            result == null -> Result.retry()
            result.code in 200..299 -> Result.success()
            runAttemptCount >= MAX_ATTEMPTS -> {
                Log.w(TAG, "dwell report gave up after $runAttemptCount attempts: HTTP ${result.code}")
                Result.failure()
            }
            else -> {
                Log.w(TAG, "dwell report failed: HTTP ${result.code}; retrying")
                Result.retry()
            }
        }
    }

    companion object {
        private const val TAG = "HermesDwell"
        private const val KEY_WIDGET_ID = "widgetId"
        private const val KEY_REVISION = "revision"
        private const val KEY_BUCKET = "bucket"
        private const val MAX_ATTEMPTS = 5

        private val networkConstraint = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        /**
         * One request per visit, deliberately *not* unique work. Two reads in a row are two
         * measurements, and a fixed name under either REPLACE or KEEP would drop one of them,
         * which is the exact failure this worker exists to remove.
         */
        fun enqueue(context: Context, widgetId: String, revision: Int, bucket: String) {
            val data = Data.Builder()
                .putString(KEY_WIDGET_ID, widgetId)
                .putInt(KEY_REVISION, revision)
                .putString(KEY_BUCKET, bucket)
                .build()
            val request = OneTimeWorkRequestBuilder<DwellWorker>()
                .setInputData(data)
                .setConstraints(networkConstraint)
                // BackoffPolicy.EXPENSIVE does not exist in work-runtime 2.9.1 (it was added
                // later), so the build was broken before this worker was reachable.
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueue(request)
        }
    }
}
