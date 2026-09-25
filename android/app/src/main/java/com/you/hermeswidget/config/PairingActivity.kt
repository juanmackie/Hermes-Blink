package com.you.hermeswidget.config

import android.app.Activity
import android.os.Bundle
import android.os.CountDownTimer
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import com.you.hermeswidget.R
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.ConnectionState
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker
import org.json.JSONObject

class PairingActivity : Activity() {
    private var countdown: CountDownTimer? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pairing)
        val codeInput = findViewById<EditText>(R.id.pairing_code_input)
        val pairButton = findViewById<Button>(R.id.pair_btn)
        val expiry = findViewById<TextView>(R.id.pairing_expiry)

        // A QR code (or the CLI one-liner) can hand us the URL and code together.
        val deepLink = PairingLink.parse(intent?.data?.toString())
        val baseUrl = if (deepLink != null) {
            Config.setBackendUrl(this, deepLink.first)
            codeInput.setText(deepLink.second)
            deepLink.first
        } else {
            SecureStore.baseUrl(this) ?: Config.getBackendUrl(this).orEmpty()
        }

        if (baseUrl.isBlank()) {
            Toast.makeText(this, "Open Hermes settings and enter the server URL first", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        startCountdown(expiry)

        pairButton.setOnClickListener {
            val code = codeInput.text.toString().trim()
            if (!code.matches(Regex("[A-Za-z0-9-]{8,32}"))) {
                Toast.makeText(this, "Enter the short-lived pairing code", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            pairButton.isEnabled = false
            Thread {
                val result = runCatching {
                    val (status, body) = HermesApi.pair(baseUrl, code, PairingLink.deviceLabel())
                    if (status != 200 || body == null) error("pairing failed (HTTP $status)")
                    val json = JSONObject(body)
                    val token = json.getString("token")
                    val deviceId = json.getString("deviceId")
                    SecureStore.save(this, baseUrl, token, deviceId)
                    Config.setBackendUrl(this, baseUrl)
                    Config.setWidgetId(this, "hermes-brief")
                    Config.setConnectionState(this, ConnectionState.ONLINE, System.currentTimeMillis())
                    deviceId
                }
                runOnUiThread {
                    pairButton.isEnabled = true
                    result.onSuccess { deviceId ->
                        RefreshWorker.enqueueNow(this)
                        Toast.makeText(this, "Paired as $deviceId", Toast.LENGTH_LONG).show()
                        finish()
                    }.onFailure { error ->
                        Toast.makeText(this, error.message ?: "Pairing failed", Toast.LENGTH_LONG).show()
                    }
                }
            }.start()
        }
    }

    private fun startCountdown(view: TextView) {
        countdown?.cancel()
        countdown = object : CountDownTimer(CODE_TTL_MILLIS, 1000L) {
            override fun onTick(millisUntilFinished: Long) {
                val seconds = millisUntilFinished / 1000
                view.text = "Code expires in %d:%02d".format(seconds / 60, seconds % 60)
            }

            override fun onFinish() {
                view.text = "Code expired; mint a new one with hermes widget pair"
            }
        }.start()
    }

    override fun onDestroy() {
        countdown?.cancel()
        super.onDestroy()
    }

    private companion object {
        // Matches the host's 10-minute single-use pairing code.
        const val CODE_TTL_MILLIS = 10 * 60 * 1000L
    }
}
