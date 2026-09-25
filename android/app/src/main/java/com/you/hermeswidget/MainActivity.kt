package com.you.hermeswidget

import android.content.Intent
import android.os.Bundle
import android.text.format.DateUtils
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.work.WorkManager
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.ConnectionState
import com.you.hermeswidget.net.PublicationRepository
import com.you.hermeswidget.net.PublicationFreshness
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.net.freshness
import com.you.hermeswidget.work.RefreshWorker
import org.unifiedpush.android.connector.UnifiedPush

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        findViewById<Button>(R.id.connect_btn).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        findViewById<Button>(R.id.diagnostics_btn).setOnClickListener {
            startActivity(Intent(this, DiagnosticsActivity::class.java))
        }
        WorkManager.getInstance(this)
            .getWorkInfosForUniqueWorkLiveData(RefreshWorker.IMMEDIATE_NAME)
            .observe(this) { renderStatus() }
        renderStatus()
    }

    override fun onResume() {
        super.onResume()
        RefreshWorker.schedulePeriodic(this)
        RefreshWorker.enqueueNow(this)
        registerUnifiedPushIfAvailable()
        renderStatus()
    }

    private fun registerUnifiedPushIfAvailable() {
        if (SecureStore.baseUrl(this).isNullOrBlank() || SecureStore.token(this).isNullOrBlank()) return
        runCatching {
            UnifiedPush.tryUseCurrentOrDefaultDistributor(this) { success ->
                if (success) {
                    UnifiedPush.register(applicationContext, messageForDistributor = "Hermes Widget")
                }
            }
        }
    }

    private fun renderStatus() {
        val statusText = findViewById<TextView>(R.id.status_text)
        val baseUrl = SecureStore.baseUrl(this)
        val token = SecureStore.token(this)
        if (baseUrl.isNullOrBlank() || token.isNullOrBlank()) {
            statusText.text = getString(R.string.status_disconnected)
            return
        }

        val state = Config.getConnectionState(this)
        val publication = PublicationRepository.loadCached(this)
        val checked = Config.getLastCheckedAt(this)?.let { timestamp ->
            DateUtils.getRelativeTimeSpanString(
                timestamp,
                System.currentTimeMillis(),
                DateUtils.MINUTE_IN_MILLIS,
            )
        }
        statusText.text = when {
            publication == null -> "Paired • no publication cached" +
                (checked?.let { " • checked $it" } ?: "")
            state == ConnectionState.REVOKED -> "Pairing expired • pair this phone again"
            state == ConnectionState.OFFLINE -> "Offline • showing the last successful publication"
            state == ConnectionState.ERROR -> "Connection problem • cached publication is still available"
            publication.isExpired() -> "Publication expired • waiting for a new update"
            else -> {
                val freshness = when (publication.freshness()) {
                    PublicationFreshness.FRESH -> "Fresh"
                    PublicationFreshness.AGED -> "Aged"
                    PublicationFreshness.STALE -> "Stale"
                    PublicationFreshness.EXPIRED -> "Expired"
                }
                val age = DateUtils.getRelativeTimeSpanString(
                    publication.publishedAtMillis() ?: System.currentTimeMillis(),
                    System.currentTimeMillis(),
                    DateUtils.MINUTE_IN_MILLIS,
                )
                "$freshness • ${publication.title} • updated $age"
            }
        }
    }
}
