package com.you.hermeswidget.widget

import android.content.Context
import android.content.Intent
import android.util.Log
import android.appwidget.AppWidgetManager
import android.os.Bundle
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.work.RefreshWorker

class HermesWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = HermesWidget()

    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        RefreshWorker.schedulePeriodic(context)
        RefreshWorker.enqueueNow(context)
        WidgetInstanceReporter.reportAsync(context)
    }

    override fun onAppWidgetOptionsChanged(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetId: Int,
        newOptions: Bundle,
    ) {
        super.onAppWidgetOptionsChanged(context, appWidgetManager, appWidgetId, newOptions)
        WidgetInstanceReporter.reportAsync(context)
    }

    override fun onReceive(context: Context, intent: Intent) {
        // Count a widget action *before* Glance dispatches it. A fire with no matching
        // outcome means the dispatch failed; no fire at all means the tap never produced a
        // broadcast, which is a different bug with a different fix.
        val callback = intent.actionCallbackClass()
        if (callback != null) {
            val id = intent.appWidgetIdExtra()
            Config.recordActionFired(context, callback)
            Log.i(TAG, "action fired callback=$callback widgetId=$id")
        } else if (intent.hasExtra(EXTRA_PARAMETERS)) {
            // A Glance action broadcast we could not name: count it anyway, and record the
            // extras, rather than letting the trail go quiet because a name changed.
            val keys = intent.extras?.keySet()?.sorted()?.joinToString(",").orEmpty()
            Config.recordActionFired(context, "unnamed($keys)")
            Log.i(TAG, "action fired with unnamed callback extras=$keys")
        }
        super.onReceive(context, intent)
        if (intent.action == ACTION_REFRESH) RefreshWorker.enqueueNow(context)
    }

    companion object {
        const val ACTION_REFRESH = "com.you.hermeswidget.REFRESH"
        private const val TAG = "HermesWidgetAction"

        // Extras Glance puts on an `actionRunCallback` broadcast. Spelled out because
        // the library exposes no public constant for them.
        private const val EXTRA_CALLBACK_CLASS = "ActionCallbackBroadcastReceiver:callbackClass"
        private const val EXTRA_APP_WIDGET_ID = "ActionCallbackBroadcastReceiver:appWidgetId"
        private const val EXTRA_PARAMETERS = "ActionCallbackBroadcastReceiver:parameters"

        /**
         * The callback class behind an action broadcast.
         *
         * Matched by suffix rather than by one exact string: these extras are internal to
         * the library, and a version that renames them would otherwise silence the trail
         * without a sound — which is how this signal was missed twice.
         */
        private fun Intent.actionCallbackClass(): String? {
            getStringExtra(EXTRA_CALLBACK_CLASS)?.let { return it }
            return extras?.keySet()
                ?.firstOrNull { it.endsWith(":callbackClass") || it == "callbackClass" }
                ?.let { key -> getStringExtra(key) }
        }

        private fun Intent.appWidgetIdExtra(): Int {
            getIntExtra(EXTRA_APP_WIDGET_ID, -1).takeIf { it >= 0 }?.let { return it }
            return extras?.keySet()
                ?.firstOrNull { it.endsWith(":appWidgetId") || it == "appWidgetId" }
                ?.let { key -> getIntExtra(key, -1) }
                ?: -1
        }
    }
}
