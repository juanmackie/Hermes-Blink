package com.you.hermeswidget

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
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
    private lateinit var composition: TextView

    /**
     * The whole widget-action trail as one block of text, for the clipboard.
     *
     * Field round 11: two presses, one worked, and the diagnosis needed a screenshot of a
     * phone. Every number that matters is on this device already, so asking for it should
     * not involve holding a phone up to a camera — "Copy widget trail" pastes the same
     * evidence in one action, with the instance, the geometry source, both heights, the
     * fire count and every recorded outcome.
     */
    fun widgetTrailReport(): String {
        val format = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        fun at(value: Long) = value.takeIf { it > 0 }?.let { format.format(Date(it)) } ?: "never"
        val identity = AppIdentity.of(this)
        val lines = mutableListOf(
            "Hermes widget trail",
            "app ${AppIdentity.describe(identity)} api ${identity.osSdk} " +
                "commit ${identity.appBuildSha ?: "unknown"}",
        )
        Config.compositionHistory(this).forEach { row ->
            lines += "composed ${at(row.optLong("at", 0L))} " +
                "instance=${row.optString("instance").ifBlank { "?" }} " +
                "band=${row.optString("band")} " +
                "action=${if (row.optBoolean("action", false)) "yes" else "NO"} " +
                "composed=${row.optDouble("composedDp", 0.0).toInt()}dp " +
                "cell=${row.optDouble("cellDp", -1.0).toInt()}dp " +
                "scroll=${row.optInt("scrollDp", 0)}dp source=${row.optString("source")}"
        }
        if (Config.compositionHistory(this).isEmpty()) lines += "composed: (none recorded)"
        val reached = Config.getActionReached(this)
        val outcomes = Config.actionOutcomes(this)
        val verdict = ActionVerdict.of(reached, Config.actionOutcomes(this))
        lines += "reached count=${reached?.optInt("count", 0) ?: 0} " +
            "exceptions=${reached?.optInt("exceptions", 0) ?: 0} " +
            "last=${at(reached?.optLong("at", 0L) ?: 0L)} " +
            "lastEvent=${reached?.optString("lastEvent")?.ifBlank { "-" } ?: "-"} " +
            "lastException=${reached?.optString("lastException")?.ifBlank { "-" } ?: "-"}"
        lines += "verdict: ${verdict.line}"
        lines += "  (outcomes: ${verdict.widgetOutcomes} from the widget pill, " +
            "${verdict.outcomes - verdict.widgetOutcomes} from the app button)"
        if (outcomes.isEmpty()) lines += "outcome: (none recorded)"
        outcomes.forEach { row ->
            lines += "outcome ${at(row.optLong("at", 0L))} " +
                "event=${row.optString("event")} " +
                "instance=${row.optString("instanceId").ifBlank { "?" }} " +
                "http=${row.optInt("status", -1)} code=${row.optString("code")} " +
                "note=${row.optString("message").take(80)}"
        }
        WidgetDimensions.allInstances(this).forEach { instance ->
            val spec = Breakpoints.spec(instance.widthDp.toFloat(), instance.heightDp.toFloat())
            lines += "inventory #${instance.instanceId} ${instance.widthDp}x${instance.heightDp}dp " +
                "class=${instance.sizeClass} band=${spec.band} action=${spec.showsRequestAction}"
        }
        return lines.joinToString("\n")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppIdentity.attach(this)
        setContentView(R.layout.activity_diagnostics)
        status = findViewById(R.id.diagnostics_status)
        exemption = findViewById(R.id.battery_exemption_status)
        pushState = findViewById(R.id.push_state_status)
        instances = findViewById(R.id.widget_instances_status)
        actions = findViewById(R.id.action_outcomes_status)
        composition = findViewById(R.id.widget_composition_status)
        findViewById<Button>(R.id.request_battery_exemption).setOnClickListener {
            requestExemption()
        }
        // The manual pin button always acts and always reports: it used to return silently
        // once an automatic offer had been made, which is what made it look broken.
        findViewById<Button>(R.id.pin_widget).setOnClickListener {
            WidgetPinning.offerNow(this, this)
        }
        findViewById<Button>(R.id.copy_widget_trail).setOnClickListener {
            val report = widgetTrailReport()
            val clipboard = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("hermes-widget-trail", report))
            Toast.makeText(this, R.string.diagnostics_copied, Toast.LENGTH_SHORT).show()
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
            append("Asked the server: ${label(times["lastPublicationCheck"])}\n")
            // "Last fetch" is new content arriving. A 304 is not a fetch, and counting
            // one is what made this screen disagree with the widget.
            append("New content: ${label(times["lastFetchAt"])}\n")
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
        renderComposition()
    }

    /**
     * The three links of the widget-action trail, in the order they happen:
     *
     *  1. **composed** — the last composition drew a button, and how tall its scroll region
     *     was. If this says `actionAvailable: false`, there was no button to press.
     *  2. **fired** — a tap produced an action broadcast that reached this process. Zero
     *     here means the tap landed somewhere else, most often on the surface, which opens
     *     the app.
     *  3. **outcome** — what the request actually got back (the list below).
     *
     * Any gap between them localises the failure to a layer instead of a guess.
     */
    private fun renderComposition() {
        val format = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        fun at(value: Long) = value.takeIf { it > 0 }?.let { format.format(Date(it)) } ?: "never"
        val last = Config.getLastComposition(this)
        val reached = Config.getActionReached(this)
        val band = last?.optString("band") ?: "?"
        val available = last?.optBoolean("actionAvailable", false) ?: false
        val scroll = last?.optInt("scrollHeightDp", 0) ?: 0
        val count = reached?.optInt("count", 0) ?: 0
        val exceptions = reached?.optInt("exceptions", 0) ?: 0
        val verdict = ActionVerdict.of(reached, Config.actionOutcomes(this))
        val history = Config.compositionHistory(this)
        composition.text = buildString {
            if (history.size > 1) {
                append("1. composed: ${history.size} recorded, newest first\n")
                for (row in history.take(4)) {
                    val at = row.optLong("at", 0L)
                    append("   ${at.takeIf { it > 0 }?.let { format.format(Date(it)) } ?: "?"} " +
                        "#${row.optString("instance").ifBlank { "?" }} " +
                        "${row.optString("band")} " +
                        "${if (row.optBoolean("action", false)) "action" else "NO ACTION"} " +
                        "${row.optDouble("composedDp", 0.0).toInt()}dp cell " +
                        "${row.optDouble("cellDp", -1.0).toInt()}dp " +
                        "scroll ${row.optInt("scrollDp", 0)}dp\n")
                }
            }
            append("last: band $band · " +
                if (available) "action available" else "NO ACTION DRAWN")
            if (scroll > 0) append(" · scroll region ${scroll}dp")
            append(" (${at(last?.optLong("at", 0L) ?: 0L)})\n")
            append("2. reached: $count widget action(s) reached the callback")
            if (count > 0) {
                append(" · last ${at(reached?.optLong("at", 0L) ?: 0L)}")
                append(" · ${reached?.optString("lastEvent")?.ifBlank { "-" } ?: "-"}")
            }
            append("\n3. outcome: see Widget actions below")
            append("\nverdict: ${verdict.line}")
        }
        Log.i(
            "HermesDiagnostics",
            "trail composed band=$band action=$available scroll=${scroll}d " +
                "reached=$count exceptions=$exceptions verdict=${verdict.line}",
        )
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
                    // The origin is what tells the two controls apart after the fact.
                    val source = row.optString("source").ifBlank { "unknown origin" }
                    append("\n$at · ${row.optString("event")} · instance $instance · " +
                        "HTTP $status · ${row.optString("code")} · $source")
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
