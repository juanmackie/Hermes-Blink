package com.you.hermeswidget

import android.content.pm.ApplicationInfo
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.you.hermeswidget.config.PairingLink
import com.you.hermeswidget.net.AppIdentity
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.ConnectionState
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker
import org.json.JSONObject

class SettingsActivity : AppCompatActivity() {
    private val ticker = Handler(Looper.getMainLooper())
    private var pairingInFlight = false
    private lateinit var statusLabel: TextView
    private lateinit var statusDetail: TextView
    private lateinit var statusDot: View
    private lateinit var root: View
    private lateinit var urlField: TextInputLayout
    private lateinit var codeField: TextInputLayout
    private lateinit var labelField: TextInputLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppIdentity.attach(this)
        setContentView(R.layout.activity_settings)
        setSupportActionBar(findViewById<MaterialToolbar>(R.id.top_app_bar))
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        root = findViewById(android.R.id.content)
        urlField = findViewById(R.id.backend_url_field)
        codeField = findViewById(R.id.pairing_code_field)
        labelField = findViewById(R.id.device_label_field)
        statusLabel = findViewById(R.id.pairing_status)
        statusDetail = findViewById(R.id.pairing_status_detail)
        statusDot = findViewById(R.id.pairing_status_dot)
        val urlEdit = findViewById<TextInputEditText>(R.id.backend_url_input)
        val codeEdit = findViewById<TextInputEditText>(R.id.pairing_code_input)
        val pairButton = findViewById<View>(R.id.connect_btn)
        val labelEdit = findViewById<TextInputEditText>(R.id.device_label_input)
        val renameButton = findViewById<View>(R.id.rename_btn)

        SecureStore.baseUrl(this)?.let { urlEdit.setText(it) }
        labelEdit.setText(PairingLink.deviceLabel())

        renameButton.setOnClickListener {
            val baseUrl = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this).orEmpty()
            val token = SecureStore.token(this)
            val label = labelEdit.text.toString().trim()
            if (token.isNullOrBlank()) {
                // A problem with the form, on the form: MD3 puts a field-level error on
                // the field rather than in a floating message the user may not be looking
                // at. Nothing here is wrong with the name, so the error goes elsewhere.
                say("Pair this phone before renaming it")
                return@setOnClickListener
            }
            if (label.isEmpty()) {
                labelField.error = "Enter a device name"
                labelEdit.requestFocus()
                return@setOnClickListener
            }
            Thread {
                val result = HermesApi.renameDevice(baseUrl, token, label)
                runOnUiThread {
                    if (result.code in 200..299) {
                        say("Renamed to $label")
                    } else {
                        say("Rename failed (HTTP ${result.code})")
                    }
                }
            }.start()
        }

        pairButton.setOnClickListener {
            val baseUrl = urlEdit.text.toString().trim().trimEnd('/')
            val code = codeEdit.text.toString().trim()
            // Both problems below belong to one field each, so both are reported on the
            // field and the focus moves there, instead of both arriving as one toast the
            // user has to guess the subject of.
            if (!isAllowedUrl(baseUrl)) {
                urlField.error = "Use the HTTPS address of your Hermes widget server"
                urlEdit.requestFocus()
                return@setOnClickListener
            }
            if (!code.matches(Regex("[A-Za-z0-9-]{8,32}"))) {
                codeField.error = "Enter the short-lived pairing code from Hermes"
                codeEdit.requestFocus()
                return@setOnClickListener
            }
            // The field is accepted: the error has to go, or it stays on screen after a
            // corrected value and contradicts it.
            urlField.error = null
            codeField.error = null

            pairButton.isEnabled = false
            pairingInFlight = true
            renderStatus()
            say("Pairing securely…")
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
                    // Read the new state before the message, so the indicator is never
                    // showing "pairing…" behind a success notice.
                    renderStatus()
                    result.onSuccess { deviceId ->
                        RefreshWorker.enqueueNow(this)
                        say("Paired as $deviceId; waiting for the first publication")
                        // Discovery: one automatic pin offer after pairing, never blocking.
                        WidgetPinning.offerOnceAfterPairing(this, this)
                    }.onFailure { error ->
                        say(error.message ?: "Pairing failed")
                    }
                }
            }.start()
        }
    }

    /**
     * MD3's transient message: a snackbar rather than a toast. It sits in the app's own
     * surface, carries the app's type scale, and can carry an action — which matters here,
     * because "Pairing failed" without a way forward is a dead end the toast had too.
     */
    private fun say(message: String) {
        Snackbar.make(root, message, Snackbar.LENGTH_LONG).show()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
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
            // Waiting is not a fault: the phone is healthy, the host has nothing current.
            PairingStatus.Tone.WAITING -> R.color.app_primary
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
