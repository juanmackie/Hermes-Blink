package com.you.hermeswidget

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.work.RefreshWorker

/**
 * Phase 4 — Setup UX with honest connection states (Phase D).
 * States: connecting, connected, offline, stale, revoked, incompatible, retrying, needs_user_action.
 * Never substitutes sample content for a failed live connection.
 */
class MainActivity : android.app.Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        // Two-state screen: Not connected → Connect; Connected → status + Add widget
        val statusText = findViewById<TextView>(R.id.status_text)
        val savedUrl = Config.getBackendUrl(this)
        statusText.text = if (!savedUrl.isNullOrEmpty()) "Connected to $savedUrl" else "Not connected — connect to your Hermes agent widget server"
        val connectBtn = findViewById<Button>(R.id.connect_btn)
        connectBtn.setOnClickListener {
            val intent = android.content.Intent(this, SettingsActivity::class.java)
            startActivity(intent)
        }
        // E: unique 15-minute periodic refresh with network constraint
        try { RefreshWorker.schedulePeriodic(this) } catch (_: Exception) {}
    }
}
