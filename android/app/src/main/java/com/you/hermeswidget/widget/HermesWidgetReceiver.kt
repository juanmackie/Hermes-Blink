package com.you.hermeswidget.widget

import android.content.Context
import android.content.Intent
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.updateAll
import kotlinx.coroutines.runBlocking

/**
 * Must extend GlanceAppWidgetReceiver (not AppWidgetProvider): Glance needs to
 * register the instance and drive provideGlance, otherwise the widget never renders.
 */
class HermesWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = HermesWidget()

    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        // Declared in the manifest; lets the app/ADB force a re-render without a system update.
        // goAsync + background thread: never block the main thread on network I/O.
        if (intent.action == ACTION_REFRESH) {
            val pending = goAsync()
            Thread {
                try {
                    runBlocking { glanceAppWidget.updateAll(context) }
                } finally {
                    pending.finish()
                }
            }.start()
        }
    }

    companion object {
        const val ACTION_REFRESH = "com.you.hermeswidget.REFRESH"
    }
}
