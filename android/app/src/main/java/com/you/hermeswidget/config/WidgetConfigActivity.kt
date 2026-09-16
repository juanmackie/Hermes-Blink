package com.you.hermeswidget.config

import android.app.Activity
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import org.json.JSONObject

class WidgetConfigActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setResult(RESULT_CANCELED)

        val appWidgetId = intent?.extras?.getInt(
            AppWidgetManager.EXTRA_APPWIDGET_ID,
            AppWidgetManager.INVALID_APPWIDGET_ID
        ) ?: AppWidgetManager.INVALID_APPWIDGET_ID

        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) { finish(); return }

        Thread {
            try {
                val url = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this)
                val token = SecureStore.token(this)
                if (url.isNullOrEmpty() || token.isNullOrEmpty()) {
                    runOnUiThread { Toast.makeText(this, "Connect to your Hermes agent first", Toast.LENGTH_LONG).show() }
                    finish()
                    return@Thread
                }
                // Fetch available widgets from the user's Hermes agent widget server
                val (code, body) = HermesApi.fetchWidgets(url, token)
                if (code != 200 || body == null) {
                    runOnUiThread { Toast.makeText(this, "Cannot list widgets", Toast.LENGTH_LONG).show() }
                    finish()
                    return@Thread
                }
                val widgets = JSONObject(body).getJSONArray("widgets")
                val widgetId = if (widgets.length() > 0) widgets.getString(0) else "hermes-brief"
                Config.setWidgetId(this, widgetId)
                Config.setBackendUrl(this, url)
                Config.setToken(this, token)

                // Trigger first fetch and cache result
                val (fetchCode, fetchBody) = HermesApi.fetchWidget(url, widgetId, token)
                if (fetchCode == 200 && fetchBody != null) {
                    Config.setCachedLayout(this, fetchBody)
                }

                runOnUiThread {
                    val resultValue = Intent()
                    resultValue.putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
                    setResult(RESULT_OK, resultValue)
                    finish()
                }
            } catch (e: Exception) {
                runOnUiThread { Toast.makeText(this, "Error: ${e.message}", Toast.LENGTH_LONG).show() }
                finish()
            }
        }.start()
    }
}
