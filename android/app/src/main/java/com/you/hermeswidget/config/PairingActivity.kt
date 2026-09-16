package com.you.hermeswidget.config

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import com.you.hermeswidget.R
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import kotlinx.coroutines.runBlocking
import org.json.JSONObject

class PairingActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pairing)
        val codeInput = findViewById<EditText>(R.id.pairing_code_input)
        val pairBtn = findViewById<Button>(R.id.pair_btn)
        pairBtn.setOnClickListener {
            val code = codeInput.text.toString().trim()
            if (code.length < 8) {
                Toast.makeText(this, "Enter a valid pairing code", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val url = SecureStore.baseUrl(this)
            if (url.isNullOrEmpty()) {
                Toast.makeText(this, "Connect to your Hermes agent first (Settings)", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            pairBtn.isEnabled = false
            Thread {
                try {
                    val (code2, body) = HermesApi.pair(url, code)
                    if (code2 == 200) {
                        val obj = JSONObject(body!!)
                        val token = obj.getString("token")
                        val deviceId = obj.getString("deviceId")
                        SecureStore.save(this, url, token, deviceId)
                        // Phase D: onboarding completes with a real authenticated fetch + widget action.
                        HermesApi.fetchWidget(url, "hermes-brief", token)
                        runOnUiThread {
                            Toast.makeText(this, "Paired ($deviceId)", Toast.LENGTH_LONG).show()
                            finish()
                        }
                    } else {
                        runOnUiThread { Toast.makeText(this, "Pair failed ($code2)", Toast.LENGTH_SHORT).show() }
                    }
                } catch (e: Exception) {
                    runOnUiThread { Toast.makeText(this, "Error: ${e.message}", Toast.LENGTH_SHORT).show() }
                }
                runOnUiThread { pairBtn.isEnabled = true }
            }.start()
        }
    }
}
