package com.you.hermeswidget.config

import android.app.Activity
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker

class WidgetConfigActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setResult(RESULT_CANCELED)

        val appWidgetId = intent?.extras?.getInt(
            AppWidgetManager.EXTRA_APPWIDGET_ID,
            AppWidgetManager.INVALID_APPWIDGET_ID,
        ) ?: AppWidgetManager.INVALID_APPWIDGET_ID
        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            finish()
            return
        }

        val baseUrl = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this)
        val token = SecureStore.token(this)
        if (baseUrl.isNullOrBlank() || token.isNullOrBlank()) {
            Toast.makeText(this, "Pair this phone with Hermes before adding the widget", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        Config.setBackendUrl(this, baseUrl)
        Config.setWidgetId(this, "hermes-brief")
        RefreshWorker.enqueueNow(this)
        setResult(
            RESULT_OK,
            Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId),
        )
        finish()
    }
}
