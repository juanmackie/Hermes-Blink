package com.you.hermeswidget.widget

import android.content.Context
import android.util.Log
import androidx.glance.appwidget.updateAll
import com.you.hermeswidget.net.AppIdentity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Re-render every widget after an app upgrade.
 *
 * Field round 5, ask 5: a launcher keeps the RemoteViews it already has. After an
 * install-over-upgrade the instance on the home screen still holds the *old* composition,
 * including the old click targets, until something forces a redraw. A user who upgraded
 * and then tapped "Request update" could therefore be pressing a target that the new
 * build would never have rendered — an unmeasured failure mode, and an unmeasured one is
 * how the round-5 tap went missing.
 *
 * The upgrade is detected by comparing the installed build code with the one seen on the
 * last launch. Cheap, exact, and it needs no extra state: the version itself is the
 * marker.
 */
object WidgetRerender {
    private const val TAG = "HermesWidgetRerender"
    private const val PREFS = "hermes"
    private const val KEY_SEEN_BUILD = "widget_rerender_build_code"

    /**
     * Re-render when the installed build differs from the last one this process saw.
     * Returns true when a re-render was requested, so callers can log or surface it.
     */
    fun runIfVersionChanged(context: Context, force: Boolean = false): Boolean {
        val appContext = context.applicationContext
        val current = AppIdentity.of(appContext).appBuildCode ?: return false
        val prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val previous = prefs.getLong(KEY_SEEN_BUILD, -1L)
        if (previous == current && !force) return false
        prefs.edit().putLong(KEY_SEEN_BUILD, current).apply()
        if (previous >= 0) {
            Log.i(TAG, "build changed ${previous} -> $current; re-rendering all instances")
        } else {
            Log.i(TAG, "first launch on build $current; re-rendering all instances")
        }
        val widget = HermesWidget()
        CoroutineScope(Dispatchers.IO).launch {
            runCatching { widget.updateAll(appContext) }
                .onFailure { Log.w(TAG, "re-render after upgrade failed: ${it.message}") }
        }
        // The instance inventory is part of what the redraw reports, and the server should
        // learn about the new build's geometry at the same moment.
        WidgetInstanceReporter.reportAsync(appContext)
        return true
    }
}
