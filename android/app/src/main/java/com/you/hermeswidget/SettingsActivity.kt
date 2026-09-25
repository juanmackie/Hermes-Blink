package com.you.hermeswidget

import android.app.Activity
import android.content.pm.ApplicationInfo
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.ConnectionState
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker
import org.json.JSONObject

class SettingsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        val urlEdit = findViewById<EditText>(R.id.backend_url_input)
        val codeEdit = findViewById<EditText>(R.id.pairing_code_input)
        val pairButton = findViewById<Button>(R.id.connect_btn)

        SecureStore.baseUrl(this)?.let { urlEdit.setText(it) }

        pairButton.setOnClickListener {
            val baseUrl = urlEdit.text.toString().trim().trimEnd('/')
            val code = codeEdit.text.toString().trim()
            if (!isAllowedUrl(baseUrl)) {
                Toast.makeText(this, "Use the HTTPS address of your Hermes widget server", Toast.LENGTH_LONG).show()
                return@setOnClickListener
            }
            if (!code.matches(Regex("[A-Za-z0-9-]{8,32}"))) {
                Toast.makeText(this, "Enter the short-lived pairing code from Hermes", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            pairButton.isEnabled = false
            Toast.makeText(this, "Pairing securely…", Toast.LENGTH_SHORT).show()
            Thread {
                val result = runCatching {
                    val (code, body) = HermesApi.pair(baseUrl, code)
                    if (code != 200 || body == null) {
                        error("pairing failed (HTTP $code)")
                    }
                    val json = JSONObject(body)
                    val deviceToken = json.getString("token")
                    val deviceId = json.getString("deviceId")
                    SecureStore.save(this, baseUrl, deviceToken, deviceId)
                    Config.setBackendUrl(this, baseUrl)
                    Config.setWidgetId(this, "hermes-brief")
                    Config.setConnectionState(this, ConnectionState.ONLINE, System.currentTimeMillis())
                    deviceId
                }
                runOnUiThread {
                    pairButton.isEnabled = true
                    result.onSuccess { deviceId ->
                        RefreshWorker.enqueueNow(this)
                        Toast.makeText(
                            this,
                            "Paired as $deviceId; waiting for the first publication",
                            Toast.LENGTH_LONG,
                        ).show()
                    }.onFailure { error ->
                        Toast.makeText(this, error.message ?: "Pairing failed", Toast.LENGTH_LONG).show()
                    }
                }
            }.start()
        }
    }

    private fun isAllowedUrl(value: String): Boolean {
        val allowedScheme = value.startsWith("https://") ||
            (value.startsWith("http://") &&
                applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
        if (!allowedScheme) return false
        return runCatching { java.net.URL(value).host.isNotBlank() }.getOrDefault(false)
    }
}
