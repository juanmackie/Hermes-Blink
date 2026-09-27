package com.you.hermeswidget

import android.app.Activity
import android.content.pm.ApplicationInfo
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import com.you.hermeswidget.config.PairingLink
import com.you.hermeswidget.net.AppIdentity
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.ConnectionState
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker
import org.json.JSONObject

class SettingsActivity : Activity() {
    private val ticker = Handler(Looper.getMainLooper())
    private var pairingInFlight = false
    private lateinit var statusLabel: TextView
    private lateinit var statusDetail: TextView
    private lateinit var statusDot: View

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppIdentity.attach(this)
        setContentView(R.layout.activity_settings)
        statusLabel = findViewById(R.id.pairing_status)
        statusDetail = findViewById(R.id.pairing_status_detail)
        statusDot = findViewById(R.id.pairing_status_dot)
        val urlEdit = findViewById<EditText>(R.id.backend_url_input)
        val codeEdit = findViewById<EditText>(R.id.pairing_code_input)
        val pairButton = findViewById<Button>(R.id.connect_btn)
        val labelEdit = findViewById<EditText>(R.id.device_label_input)
        val renameButton = findViewById<Button>(R.id.rename_btn)

        SecureStore.baseUrl(this)?.let { urlEdit.setText(it) }
        labelEdit.setText(PairingLink.deviceLabel())

        renameButton.setOnClickListener {
            val baseUrl = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this).orEmpty()
            val token = SecureStore.token(this)
            val label = labelEdit.text.toString().trim()
            if (token.isNullOrBlank()) {
                Toast.makeText(this, "Pair this phone before renaming it", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            if (label.isEmpty()) {
                Toast.makeText(this, "Enter a device name", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            Thread {
                val result = HermesApi.renameDevice(baseUrl, token, label)
                runOnUiThread {
                    if (result.code in 200..299) {
                        Toast.makeText(this, "Renamed to $label", Toast.LENGTH_SHORT).show()
                    } else {
                        Toast.makeText(this, "Rename failed (HTTP ${result.code})", Toast.LENGTH_LONG).show()
                    }
                }
            }.start()
        }

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
            pairingInFlight = true
            renderStatus()
            Toast.makeText(this, "Pairing securely…", Toast.LENGTH_SHORT).show()
            Thread {
                val result = runCatching {
                    val (code, body) = HermesApi.pair(baseUrl, code, PairingLink.deviceLabel())
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
                    pairingInFlight = false
                    // Read the new state before the toast, so the indicator is never
                    // showing "pairing…" behind a success dialog.
                    renderStatus()
                    result.onSuccess { deviceId ->
                        RefreshWorker.enqueueNow(this)
                        Toast.makeText(
                            this,
                            "Paired as $deviceId; waiting for the first publication",
                            Toast.LENGTH_LONG,
                        ).show()
                        // Discovery: one automatic pin offer after pairing, never blocking.
                        WidgetPinning.offerOnceAfterPairing(this, this)
                    }.onFailure { error ->
                        Toast.makeText(this, error.message ?: "Pairing failed", Toast.LENGTH_LONG).show()
                    }
                }
            }.start()
        }
    }

    /**
     * Poll the pairing state every 2s while this screen is open, and stop dead when it
     * is not. Two seconds is chosen because that is the interval a user watching a
     * "Pairing…" indicator expects to see change at; the tick reads only local state
     * (the encrypted store and prefs), so it costs nothing and cannot hammer the server.
     */
    override fun onResume() {
        super.onResume()
        renderStatus()
        ticker.postDelayed(statusPoll, STATUS_POLL_MS)
    }

    override fun onPause() {
        // A Settings screen left in the background must not keep waking the process.
        ticker.removeCallbacks(statusPoll)
        super.onPause()
    }

    private val statusPoll = object : Runnable {
        override fun run() {
            renderStatus()
            ticker.postDelayed(this, STATUS_POLL_MS)
        }
    }

    private fun renderStatus() {
        if (!::statusLabel.isInitialized) return
        val status = PairingStatus.read(this, pairingInFlight)
        statusLabel.text = status.summary
        val detail = status.detail
        statusDetail.text = detail.orEmpty()
        statusDetail.visibility = if (detail == null) View.GONE else View.VISIBLE
        val color = when (status.tone) {
            PairingStatus.Tone.PAIRED -> R.color.paired_indicator
            PairingStatus.Tone.WORKING -> R.color.app_primary
            PairingStatus.Tone.PROBLEM -> R.color.unpaired_indicator
            PairingStatus.Tone.UNPAIRED -> R.color.unpaired_indicator
        }
        statusDot.background?.mutate()?.setTint(ContextCompat.getColor(this, color))
    }

    private fun isAllowedUrl(value: String): Boolean {
        val allowedScheme = value.startsWith("https://") ||
            (value.startsWith("http://") &&
                applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
        if (!allowedScheme) return false
        return runCatching { java.net.URL(value).host.isNotBlank() }.getOrDefault(false)
    }


    private companion object {
        const val STATUS_POLL_MS = 2_000L
    }
}
