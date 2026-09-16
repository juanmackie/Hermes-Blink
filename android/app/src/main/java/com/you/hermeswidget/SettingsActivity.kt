package com.you.hermeswidget

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import kotlinx.coroutines.runBlocking
import org.json.JSONObject

class SettingsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        val urlEdit = findViewById<EditText>(R.id.backend_url_input)
        val agentTokenEdit = findViewById<EditText>(R.id.agent_token_input)
        val connectBtn = findViewById<Button>(R.id.connect_btn)

        val savedUrl = SecureStore.baseUrl(this)
        if (!savedUrl.isNullOrEmpty()) urlEdit.setText(savedUrl)

        connectBtn.setOnClickListener {
            val urlStr = urlEdit.text.toString().trim()
            val agentToken = agentTokenEdit.text.toString().trim()
            if (!urlStr.startsWith("http://") && !urlStr.startsWith("https://")) {
                Toast.makeText(this, "Hermes agent URL must start with http:// or https://", Toast.LENGTH_LONG).show()
                return@setOnClickListener
            }
            if (agentToken.isBlank()) {
                Toast.makeText(this, "Enter agent token", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            Toast.makeText(this, "Connecting...", Toast.LENGTH_SHORT).show()
            Thread {
                try {
                    val (code, body) = HermesApi.health(urlStr)
                    if (code == 200) {
                        // Auto-pair: mint a pairing code and pair
                        val mintRes = HermesApi.mintPairingCode(urlStr, agentToken)
                        if (mintRes.first != 200) {
                            runOnUiThread { Toast.makeText(this, "Cannot mint code (${mintRes.first})", Toast.LENGTH_LONG).show() }
                            return@Thread
                        }
                        val mintObj = JSONObject(mintRes.second!!)
                        val pairCode = mintObj.getString("code")
                        val (pairCode2, pairBody) = HermesApi.pair(urlStr, pairCode)
                        if (pairCode2 != 200) {
                            runOnUiThread { Toast.makeText(this, "Pair failed (${pairCode2})", Toast.LENGTH_LONG).show() }
                            return@Thread
                        }
                        val pairObj = JSONObject(pairBody!!)
                        val token = pairObj.getString("token")
                        val deviceId = pairObj.getString("deviceId")
                        SecureStore.save(this, urlStr, token, deviceId)
                        Config.setToken(this, token)
                        Config.setBackendUrl(this, urlStr)
                        Config.setWidgetId(this, "hermes-brief")
                        runOnUiThread {
                            Toast.makeText(this@SettingsActivity, "Connected! ($body)", Toast.LENGTH_LONG).show()
                        }
                    } else {
                        runOnUiThread {
                            Toast.makeText(this@SettingsActivity, "Failed (HTTP $code)", Toast.LENGTH_LONG).show()
                        }
                    }
                } catch (e: Exception) {
                    runOnUiThread {
                        Toast.makeText(this@SettingsActivity, "Error: ${e.message}", Toast.LENGTH_LONG).show()
                    }
                }
            }.start()
        }
    }
}
