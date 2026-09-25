package com.you.hermeswidget

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import com.you.hermeswidget.net.Config
import java.text.DateFormat
import java.util.Date

/** A deliberately boring diagnostics screen: timestamps and exemption state, no secrets. */
class DiagnosticsActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var exemption: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_diagnostics)
        status = findViewById(R.id.diagnostics_status)
        exemption = findViewById(R.id.battery_exemption_status)
        findViewById<Button>(R.id.request_battery_exemption).setOnClickListener {
            requestExemption()
        }
        findViewById<Button>(R.id.close_diagnostics).setOnClickListener { finish() }
    }

    override fun onResume() {
        super.onResume()
        val times = Config.getDiagnosticTimes(this)
        val format = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        fun label(value: Long?): String = value?.let { format.format(Date(it)) } ?: "never"
        status.text = buildString {
            append("Last poll: ${label(times["lastPollAt"])}\n")
            append("Last fetch: ${label(times["lastFetchAt"])}\n")
            append("Last render: ${label(times["lastRenderAt"])}\n")
            append("Last connection check: ${label(Config.getLastCheckedAt(this@DiagnosticsActivity))}")
        }
        val manager = getSystemService(POWER_SERVICE) as PowerManager
        val ignored = manager.isIgnoringBatteryOptimizations(packageName)
        Config.setBatteryExemptionHint(this, ignored)
        exemption.text = if (ignored) {
            "Battery optimisation exemption: held"
        } else {
            "Battery optimisation exemption: not held. Android may delay wakeups in Doze."
        }
    }

    private fun requestExemption() {
        val intent = Intent(
            Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
            Uri.parse("package:$packageName"),
        )
        runCatching { startActivity(intent) }.onFailure {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }
}
