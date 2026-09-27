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
        if (intent.hasExtra(EXTRA_CALLBACK_CLASS)) {
            val callback = intent.getStringExtra(EXTRA_CALLBACK_CLASS)
            val id = intent.getIntExtra(EXTRA_APP_WIDGET_ID, -1)
            Config.recordActionFired(context, callback)
            Log.i(TAG, "action fired callback=$callback widgetId=$id")
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
    }
}
