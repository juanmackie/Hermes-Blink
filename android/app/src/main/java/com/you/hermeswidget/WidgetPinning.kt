package com.you.hermeswidget

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.util.Log
import android.widget.Toast

/**
 * Discovery and promotion (Android's widget guidance): a widget nobody adds is a widget
 * nobody sees, and the guidance's answer is `requestPinAppWidget` — the system shows its
 * own "add widget" sheet, so there is no in-app widget picker to build or maintain.
 *
 * Three rules keep this from becoming nagging:
 *  - it is only offered after a successful pairing, when the user is already in the flow;
 *  - it is never blocking: a launcher that cannot pin, or a user who dismisses the sheet,
 *    changes nothing else;
 *  - it is offered once per pairing, tracked in prefs, so a returning user is not asked
 *    again. Android 8+ requires the target to be in the foreground for the request, which
 *    is why the caller is an Activity, not a worker.
 */
object WidgetPinning {
    private const val TAG = "HermesWidgetPinning"
    private const val KEY_OFFERED = "widget_pin_offered"

    /** True when the launcher supports pinning this provider. */
    fun isSupported(context: Context): Boolean =
        AppWidgetManager.getInstance(context).isRequestPinAppWidgetSupported

    /** Offer the system pin sheet. Returns true when the request was accepted. */
    fun offer(context: Context, activity: android.app.Activity): Boolean {
        val provider = ComponentName(context, com.you.hermeswidget.widget.HermesWidgetReceiver::class.java)
        val manager = AppWidgetManager.getInstance(context)
        val prefs = context.getSharedPreferences("hermes", Context.MODE_PRIVATE)
        if (prefs.getBoolean(KEY_OFFERED, false)) return false
        val requested = runCatching { requestPin(manager, provider) }.getOrDefault(false)
        // Marked offered either way: if the launcher refused, asking again will not help.
        prefs.edit().putBoolean(KEY_OFFERED, true).apply()
        Log.i(TAG, "pin requested=$requested provider=$provider sdk=${android.os.Build.VERSION.SDK_INT}")
        if (requested) {
            Toast.makeText(
                context,
                context.getString(R.string.widget_pin_offered),
                Toast.LENGTH_LONG,
            ).show()
        }
        return requested
    }

    /**
     * API 33 replaced `requestPinAppWidget(ComponentName, PinAppWidgetRequest)` with
     * `(ComponentName, Bundle, PendingIntent)` and API 35 removed the old overload, so the
     * pre-33 path has to be reflective to keep compiling against compileSdk 35. Null extras
     * and a null callback are explicitly allowed: the system shows its own sheet and we
     * do not need the success broadcast.
     */
    @Suppress("PrivateApi")   // The only reflective path, and only on API 26-32.
    private fun requestPin(manager: AppWidgetManager, provider: ComponentName): Boolean =
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            manager.requestPinAppWidget(provider, null, null)
        } else {
            val legacy = Class.forName("android.appwidget.PinAppWidgetRequest\$Builder")
            val builder = legacy.getDeclaredConstructor().newInstance() as Any
            val request = legacy.let { it.enclosingClass }!!
                .getDeclaredMethod("build")
                .invoke(builder)
            val method = AppWidgetManager::class.java.getMethod(
                "requestPinAppWidget",
                ComponentName::class.java,
                request.javaClass,
            )
            method.invoke(manager, provider, request) as Boolean
        }

    /** Test/diagnostics seam: forget that we asked, so the next pair offers it again. */
    fun reset(context: Context) {
        context.getSharedPreferences("hermes", Context.MODE_PRIVATE).edit()
            .remove(KEY_OFFERED).apply()
    }
}
