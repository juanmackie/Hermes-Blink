package com.you.hermeswidget

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import android.util.Log
import com.you.hermeswidget.net.AppIdentity
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.widget.Breakpoints
import com.you.hermeswidget.widget.WidgetDimensions
import java.text.DateFormat
import java.util.Date

/** A deliberately boring diagnostics screen: timestamps and exemption state, no secrets. */
class DiagnosticsActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var exemption: TextView
    private lateinit var pushState: TextView
    private lateinit var instances: TextView
    private lateinit var actions: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppIdentity.attach(this)
        setContentView(R.layout.activity_diagnostics)
        status = findViewById(R.id.diagnostics_status)
        exemption = findViewById(R.id.battery_exemption_status)
        pushState = findViewById(R.id.push_state_status)
        instances = findViewById(R.id.widget_instances_status)
        actions = findViewById(R.id.action_outcomes_status)
        findViewById<Button>(R.id.request_battery_exemption).setOnClickListener {
            requestExemption()
        }
        // The manual pin button always acts and always reports: it used to return silently
        // once an automatic offer had been made, which is what made it look broken.
        findViewById<Button>(R.id.pin_widget).setOnClickListener {
            WidgetPinning.offerNow(this, this)
        }
        findViewById<Button>(R.id.close_diagnostics).setOnClickListener {
            // No transition: this is a plain screen closing, and the system animation on a
            // diagnostics panel read as the button not having worked.
            finish()
            overridePendingTransition(0, 0)
        }
    }

    override fun onResume() {
        super.onResume()
        val times = Config.getDiagnosticTimes(this)
        val format = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        fun label(value: Long?): String = value?.let { format.format(Date(it)) } ?: "never"
        // The build the user is running, so "which version is this?" is answerable by
        // reading the screen rather than by guessing which APK they installed.
        val identity = AppIdentity.of(this)
        status.text = buildString {
            append("App: ${AppIdentity.describe(identity)} \u00b7 Android API ${identity.osSdk}\n")
            identity.appBuildSha?.let { append("Build commit: $it\n") }
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
        val push = Config.getPushState(this)
        val state = push?.optString("state", "unknown") ?: "unknown"
        val distributor = push?.opt("distributorPresent")
        val present = when (distributor) {
            null, JSONObject.NULL -> "unknown"
            true -> "present"
            false -> "absent"
            else -> distributor.toString()
        }
        val failure = push?.optString("failureReason").orEmpty().takeIf { it.isNotEmpty() }
        val lastWake = Config.getLastPushWake(this)
        pushState.text = buildString {
            append("UnifiedPush: $state (distributor $present)")
            if (failure != null) append("; failure: $failure")
            append("\nLast wake received: ")
            append(lastWake?.let { format.format(Date(it)) } ?: "never")
        }
        renderInstances()
        renderActionOutcomes()
    }

    /**
     * The last few widget-button outcomes, newest first. This is the client-side answer
     * to "I pressed Request update and nothing happened": it says whether the tap was
     * sent, which instance sent it, what the server replied, and when.
     */
    private fun renderActionOutcomes() {
        val format = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        val rows = Config.actionOutcomes(this)
        actions.text = if (rows.isEmpty()) {
            "Widget actions: none recorded yet"
        } else {
            buildString {
                append("Widget actions (newest first):")
                for (row in rows) {
                    val status = row.optInt("status", -1)
                    val instance = row.optString("instanceId").ifBlank { "?" }
                    val at = row.optLong("at", 0L).takeIf { it > 0 }?.let { format.format(Date(it)) } ?: "?"
                    append("\n$at · ${row.optString("event")} · instance $instance · " +
                        "HTTP $status · ${row.optString("code")}")
                }
            }
        }
    }

    /**
     * The per-instance geometry that drives the band ladder, so "why did my 2x2 look
     * like a 4x4" is answerable on the device instead of guessed at. The widget itself
     * reads Glance's LocalSize first; this is the launcher-reported inventory that is the
     * fallback and the value the host receives, so both are worth one line here.
     */
    private fun renderInstances() {
        val rows = WidgetDimensions.allInstances(this)
        instances.text = if (rows.isEmpty()) {
            "Widget instances: none added yet"
        } else {
            buildString {
                append("Widget instances: ${rows.size}")
                for (row in rows) {
                    val spec = Breakpoints.spec(
                        row.widthDp.toFloat(),
                        row.heightDp.toFloat(),
                    )
                    append("\n#${row.instanceId} ${row.widthDp}x${row.heightDp}dp " +
                        "(${row.widthPx}x${row.heightPx}px) ${row.sizeClass} " +
                        "band=${spec.band} variant=${spec.band.variantKey} " +
                        "singleColumn=${spec.singleColumn}")
                }
            }
        }
        Log.i("HermesDiagnostics", instances.text.toString().replace("\n", " | "))
    }

    /**
     * Battery optimisation.
     *
     * Two things made this button look dead. It opened
     * ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, which modern Android routes to a
     * per-app screen that immediately returns for apps without a direct-exemption
     * entitlement — and `startActivity` does not throw, so the old `runCatching` fallback
     * never fired. And when the exemption was already held there was nothing to do, with
     * no message saying so.
     *
     * So: report "already exempt" first, open the system list (the screen that actually
     * works everywhere), and say what was opened.
     */
    private fun requestExemption() {
        val manager = getSystemService(POWER_SERVICE) as PowerManager
        if (manager.isIgnoringBatteryOptimizations(packageName)) {
            Toast.makeText(this, R.string.battery_exempt, Toast.LENGTH_SHORT).show()
            return
        }
        val opened = runCatching {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            true
        }.getOrDefault(false)
        Toast.makeText(
            this,
            if (opened) R.string.battery_settings_opened else R.string.battery_settings_failed,
            Toast.LENGTH_LONG,
        ).show()
        Log.i("HermesDiagnostics", "battery settings opened=$opened")
    }
}
