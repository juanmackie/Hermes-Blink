package com.you.hermeswidget.widget

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import com.you.hermeswidget.work.RefreshWorker

/**
 * Exact-alarm fallback for a wake when WorkManager is deferred by Doze.
 * Android 12+ may withhold exact-alarm permission; in that case the receiver
 * still gets an allowed idle alarm and the periodic worker remains the final
 * fallback.  No publication content is placed in this alarm.
 */
class WakeAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        RefreshWorker.enqueueNow(context)
    }

    companion object {
        private const val REQUEST_CODE = 0x48524D57 // HRMW
        private const val WAKE_DELAY_MS = 5_000L

        fun schedule(context: Context) {
            val manager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
            val pending = PendingIntent.getBroadcast(
                context,
                REQUEST_CODE,
                Intent(context, WakeAlarmReceiver::class.java).setAction(ACTION_WAKE),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val triggerAt = System.currentTimeMillis() + WAKE_DELAY_MS
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !manager.canScheduleExactAlarms()) {
                manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pending)
            } else {
                manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pending)
            }
        }

        const val ACTION_WAKE = "com.you.hermeswidget.WAKE_FALLBACK"
    }
}
