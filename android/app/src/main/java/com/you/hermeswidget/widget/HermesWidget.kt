package com.you.hermeswidget.widget

import android.content.Context
import android.content.Intent
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.LocalContext
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.ColumnScope
import androidx.glance.layout.ContentScale
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.text.Text
import com.you.hermeswidget.PublicationActivity
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.ConnectionState
import com.you.hermeswidget.net.Publication
import com.you.hermeswidget.net.PublicationContent
import com.you.hermeswidget.net.PublicationFreshness
import com.you.hermeswidget.net.PublicationRepository
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.net.freshness
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val WIDGET_SCRIM = Color(0xA6FFFFFF)
private val renderAckScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

private data class WidgetSnapshot(
    val publication: Publication?,
    val legacyLayout: WidgetLayout?,
    val bitmap: android.graphics.Bitmap?,
    val paired: Boolean,
    val connectionState: ConnectionState,
)

class HermesWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val snapshot = withContext(Dispatchers.IO) { loadSnapshot(context) }
        provideContent {
            Box(
                modifier = GlanceModifier
                    .fillMaxSize()
                    .background(WIDGET_SCRIM)
            ) {
                when {
                    !snapshot.paired -> EmptyState(
                        "Pair this phone",
                        "Run hermes widget code on Hermes, then enter the short-lived pairing code."
                    )
                    snapshot.publication != null && !snapshot.publication.isExpired() ->
                        PublicationSurface(snapshot)
                    snapshot.publication?.isExpired() == true -> EmptyState(
                        "Publication expired",
                        "Open the app to refresh the connection."
                    )
                    snapshot.legacyLayout != null -> WidgetSurface(snapshot.legacyLayout)
                    else -> EmptyState(
                        "No publication yet",
                        "Useful Hermes updates will appear here automatically."
                    )
                }
            }
        }

        if (snapshot.paired && snapshot.publication?.isExpired() == false) {
            val (width, height) = WidgetDimensions.fromContext(context)
            renderAckScope.launch {
                if (PublicationRepository.acknowledgeRenderSubmitted(context, width, height)) {
                    Config.setDiagnosticTime(context, "render")
                }
            }
        }
    }

    private fun loadSnapshot(context: Context): WidgetSnapshot {
        val appContext = context.applicationContext
        val baseUrl = SecureStore.baseUrl(appContext)
        val token = SecureStore.token(appContext)
        val paired = !baseUrl.isNullOrBlank() && !token.isNullOrBlank()
        val publication = if (paired) PublicationRepository.loadCached(appContext) else null
        return WidgetSnapshot(
            publication = publication,
            legacyLayout = if (publication == null) loadLegacyLayout(appContext) else null,
            bitmap = publication?.let { PublicationImages.load(appContext, it) },
            paired = paired,
            connectionState = Config.getConnectionState(appContext),
        )
    }

    private fun loadLegacyLayout(context: Context): WidgetLayout? {
        val json = Config.getCachedLayout(context) ?: return null
        return runCatching { LayoutParser.parse(json) }.getOrNull()
    }
}

@Composable
private fun PublicationSurface(snapshot: WidgetSnapshot) {
    val publication = snapshot.publication ?: return
    val intent = Intent(LocalContext.current, PublicationActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    Column(
        modifier = GlanceModifier
            .fillMaxSize()
            .clickable(actionStartActivity(intent))
            .padding(12.dp)
    ) {
        Text(
            text = publication.title,
            modifier = GlanceModifier.fillMaxWidth(),
            style = Typo.textStyle("title"),
            maxLines = 2,
        )
        Text(
            text = publication.summary,
            modifier = GlanceModifier.fillMaxWidth().padding(top = 2.dp),
            style = Typo.textStyle("caption"),
            maxLines = 2,
        )
        PublicationBody(publication.content, snapshot.bitmap, publication.summary)
        Text(
            text = deliveryLabel(publication, snapshot.connectionState),
            modifier = GlanceModifier.fillMaxWidth().padding(top = 6.dp),
            style = Typo.textStyle("caption"),
            maxLines = 1,
        )
    }
}

@Composable
private fun ColumnScope.PublicationBody(
    content: PublicationContent,
    bitmap: android.graphics.Bitmap?,
    summary: String,
) {
    when (content) {
        is PublicationContent.Text -> Text(
            text = content.text,
            modifier = GlanceModifier.fillMaxWidth().defaultWeight().padding(top = 8.dp),
            style = Typo.textStyle("body"),
        )
        is PublicationContent.Image -> {
            if (bitmap == null) {
                Text(
                    text = "Visual unavailable offline",
                    modifier = GlanceModifier.fillMaxWidth().defaultWeight().padding(top = 8.dp),
                    style = Typo.textStyle("body", colorOverride = "#8E8E93"),
                )
            } else {
                Image(
                    provider = ImageProvider(bitmap),
                    contentDescription = summary,
                    modifier = GlanceModifier
                        .fillMaxWidth()
                        .defaultWeight()
                        .padding(top = 8.dp),
                    contentScale = ContentScale.Fit,
                )
            }
        }
    }
}

@Composable
private fun EmptyState(title: String, message: String) {
    Column(
        modifier = GlanceModifier.fillMaxSize().padding(16.dp),
        verticalAlignment = Alignment.Vertical.CenterVertically,
        horizontalAlignment = Alignment.Horizontal.Start,
    ) {
        Text(text = title, style = Typo.textStyle("title"), maxLines = 2)
        Text(
            text = message,
            modifier = GlanceModifier.fillMaxWidth().padding(top = 6.dp),
            style = Typo.textStyle("body"),
        )
    }
}

private fun deliveryLabel(publication: Publication, state: ConnectionState): String {
    val age = ageLabel(publication.publishedAtMillis(), System.currentTimeMillis())
    return when (state) {
        ConnectionState.UNPAIRED -> "Unpaired"
        ConnectionState.OFFLINE -> "Offline • cached $age ago"
        ConnectionState.REVOKED -> "Pairing expired • cached $age ago"
        ConnectionState.ERROR -> "Showing cached publication • $age old"
        ConnectionState.ONLINE -> when (publication.freshness()) {
            PublicationFreshness.FRESH -> "Fresh • updated $age ago"
            PublicationFreshness.AGED -> "Aged • updated $age ago"
            PublicationFreshness.STALE -> "Stale • updated $age ago"
            PublicationFreshness.EXPIRED -> "Expired"
        }
    }
}

private fun ageLabel(publishedAt: Long?, now: Long): String {
    if (publishedAt == null) return "unknown time"
    val seconds = ((now - publishedAt).coerceAtLeast(0L) / 1000L)
    return when {
        seconds < 60 -> "moments"
        seconds < 3_600 -> "${seconds / 60} min"
        seconds < 86_400 -> "${seconds / 3_600} hr"
        else -> "${seconds / 86_400} days"
    }
}
