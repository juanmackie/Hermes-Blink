package com.you.hermeswidget.config

import android.os.Bundle
import android.os.CountDownTimer
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.you.hermeswidget.R
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.ConnectionState
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker
import org.json.JSONObject

/**
 * The deep-link entry point: a QR code or the CLI one-liner lands here with the server URL
 * and a short-lived code.
 *
 * MD3 rather than the raw form it replaced: a top app bar with a way back, an outlined
 * text field (the label in the outline's cut-out, the focus cue in primary), a filled
 * action, and the two kinds of message separated the way the spec separates them — a bad
 * code is an error *on the field*, an outcome is a snackbar.
 */
class PairingActivity : AppCompatActivity() {
    private var countdown: CountDownTimer? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pairing)
        setSupportActionBar(findViewById<MaterialToolbar>(R.id.top_app_bar))
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        val codeField = findViewById<TextInputLayout>(R.id.pairing_code_field)
        val codeInput = findViewById<TextInputEditText>(R.id.pairing_code_input)
        val pairButton = findViewById<View>(R.id.pair_btn)
        val expiry = findViewById<TextView>(R.id.pairing_expiry)
        val root = findViewById<View>(android.R.id.content)

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
            // The screen is closing under this message, so a snackbar anchored to it would
            // never be seen: this is the one case where the platform toast is still right.
            Snackbar.make(root, "Open Hermes settings and enter the server URL first", Snackbar.LENGTH_LONG).show()
            finish()
            return
        }
        startCountdown(expiry)

        pairButton.setOnClickListener {
            val code = codeInput.text.toString().trim()
            if (!code.matches(Regex("[A-Za-z0-9-]{8,32}"))) {
                // On the field, not in a floating message: the user is looking at the
                // field they just typed into, and MD3 gives that place a first-class slot.
                codeField.error = "Enter the short-lived pairing code"
                codeInput.requestFocus()
                return@setOnClickListener
            }
            codeField.error = null
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
                        Snackbar.make(
                            root,
                            "Paired as $deviceId",
                            Snackbar.LENGTH_SHORT,
                        ).show()
                        finish()
                    }.onFailure { error ->
                        Snackbar.make(
                            root,
                            error.message ?: "Pairing failed",
                            Snackbar.LENGTH_LONG,
                        ).show()
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
                view.text = getString(R.string.pairing_code_expires_in, seconds / 60, seconds % 60)
            }

            override fun onFinish() {
                view.text = getString(R.string.pairing_code_expired)
            }
        }.start()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
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
