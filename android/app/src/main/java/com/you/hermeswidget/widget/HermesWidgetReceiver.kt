package com.you.hermeswidget.widget

import android.content.Context
import android.content.Intent
import android.appwidget.AppWidgetManager
import android.os.Bundle
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
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
        // Round 13: the action counter used to live here, and this receiver never sees an
        // action broadcast — Glance delivers `actionRunCallback` to its own merged
        // `androidx.glance.appwidget.action.ActionCallbackBroadcastReceiver`. The counter
        // could not increment, and its zero was read as evidence. It now lives in
        // ActionCallbacks.EventAction, which is where the dispatch actually arrives.
        super.onReceive(context, intent)
        if (intent.action == ACTION_REFRESH) RefreshWorker.enqueueNow(context)
    }

    companion object {
        const val ACTION_REFRESH = "com.you.hermeswidget.REFRESH"
    }
}
