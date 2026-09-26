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
        super.onReceive(context, intent)
        if (intent.action == ACTION_REFRESH) RefreshWorker.enqueueNow(context)
    }

    companion object {
        const val ACTION_REFRESH = "com.you.hermeswidget.REFRESH"
    }
}
