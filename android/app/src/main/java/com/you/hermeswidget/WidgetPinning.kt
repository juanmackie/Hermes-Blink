package com.you.hermeswidget

import android.app.Activity
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.util.Log
import com.google.android.material.snackbar.Snackbar
import com.you.hermeswidget.widget.HermesWidgetReceiver

/**
 * Discovery and promotion (Android's widget guidance): a widget nobody adds is a widget
 * nobody sees, and the guidance's answer is `requestPinAppWidget` — the system shows its
 * own "add widget" sheet, so there is no in-app widget picker to build or maintain.
 *
 * Two entry points, because "offered once" is right for an unsolicited prompt and wrong
 * for a button the user deliberately pressed:
 *
 *  - [offerOnceAfterPairing] is the automatic, non-nagging one: at most once per pairing.
 *  - [offerNow] is what the Diagnostics button calls. It always acts, and it always
 *    reports what happened — including "the launcher cannot pin this", which is the
 *    difference between a control that works and a control that looks broken.
 */
object WidgetPinning {
    private const val TAG = "HermesWidgetPinning"
    private const val KEY_OFFERED = "widget_pin_offered"

    /** What happened, so the UI can say something true rather than nothing. */
    enum class Result {
        /** The system accepted the request and is showing its sheet. */
        REQUESTED,

        /** The provider already has instances on the home screen. */
        ALREADY_ADDED,

        /** The launcher does not support pinning (some launchers and work profiles). */
        UNSUPPORTED,

        /** Supported, but the system refused this request. */
        REFUSED,
    }

    /** True when the launcher supports pinning this provider. */
    fun isSupported(context: Context): Boolean =
        AppWidgetManager.getInstance(context).isRequestPinAppWidgetSupported

    fun instanceCount(context: Context): Int {
        val provider = ComponentName(context, HermesWidgetReceiver::class.java)
        return AppWidgetManager.getInstance(context).getAppWidgetIds(provider).size
    }

    /** The automatic path: at most one prompt per pairing, and never blocking. */
    fun offerOnceAfterPairing(context: Context, activity: Activity): Result {
        val prefs = context.getSharedPreferences("hermes", Context.MODE_PRIVATE)
        if (prefs.getBoolean(KEY_OFFERED, false)) return Result.ALREADY_ADDED
        val result = request(context)
        // Recorded either way: a launcher that refused will refuse again, and a returning
        // user should not be asked a second time.
        prefs.edit().putBoolean(KEY_OFFERED, true).apply()
        if (result == Result.REQUESTED) {
            say(activity, context.getString(R.string.widget_pin_offered))
        }
        return result
    }

    /**
     * The manual path. Never suppressed: a user who presses this button is asking again,
     * and the old behaviour — a permanent silent `return false` once the pref was set — is
     * exactly what made the button look dead.
     */
    fun offerNow(context: Context, activity: Activity): Result {
        val result = request(context)
        Log.i(
            TAG,
            "manual pin request result=$result instances=${instanceCount(context)} " +
                "sdk=${android.os.Build.VERSION.SDK_INT}",
        )
        val message = when (result) {
            Result.REQUESTED -> context.getString(R.string.widget_pin_requested)
            Result.ALREADY_ADDED -> context.getString(R.string.widget_pin_already)
            Result.UNSUPPORTED -> context.getString(R.string.widget_pin_unsupported)
            Result.REFUSED -> context.getString(R.string.widget_pin_refused)
        }
        say(activity, message)
        return result
    }

    /**
     * MD3's transient message. Anchored to the activity rather than the application
     * context: a snackbar needs a view to attach to, and the platform toast this replaces
     * drew itself over whatever was on screen — including the system "add widget" sheet
     * that is on screen at exactly this moment.
     */
    private fun say(activity: Activity, message: String) {
        val anchor = activity.findViewById<android.view.View>(android.R.id.content) ?: return
        Snackbar.make(anchor, message, Snackbar.LENGTH_LONG).show()
    }

    private fun request(context: Context): Result {
        val manager = AppWidgetManager.getInstance(context)
        if (instanceCount(context) > 0) return Result.ALREADY_ADDED
        if (!manager.isRequestPinAppWidgetSupported) return Result.UNSUPPORTED
        val provider = ComponentName(context, HermesWidgetReceiver::class.java)
        val accepted = runCatching { requestPin(manager, provider) }.getOrDefault(false)
        return if (accepted) Result.REQUESTED else Result.REFUSED
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
            val request = legacy.enclosingClass!!
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
