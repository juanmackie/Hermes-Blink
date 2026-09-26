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
    val compact: Boolean,
)

class HermesWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val snapshot = withContext(Dispatchers.IO) { loadSnapshot(context) }
        provideContent {
            val dark = snapshot.publication?.darkPalette == true
            Box(
                modifier = GlanceModifier
                    .fillMaxSize()
                    .background(if (dark) Color(0xFF1C1C1E) else WIDGET_SCRIM)
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
        val (widthPx, heightPx) = WidgetDimensions.fromContext(appContext)
        val density = appContext.resources.displayMetrics.density
        val compact = (widthPx / density) < 200f && (heightPx / density) < 200f
        return WidgetSnapshot(
            publication = publication,
            legacyLayout = if (publication == null) loadLegacyLayout(appContext) else null,
            bitmap = publication?.let { PublicationImages.load(appContext, it) },
            paired = paired,
            connectionState = Config.getConnectionState(appContext),
            compact = compact,
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
    val ink = if (publication.darkPalette) "#F2F2F7" else "#000000"
    val secondary = if (publication.darkPalette) "#AEAEB2" else "#8E8E93"
    val variant = if (snapshot.compact) {
        publication.variants["2x2"]
    } else {
        publication.variants["4x2"] ?: publication.variants["4x4"]
    }
    val title = variant?.title ?: publication.title
    val summary = variant?.summary ?: publication.summary
    val body = variant?.let { PublicationContent.Text(it.text) } ?: publication.content
    val intent = Intent(LocalContext.current, PublicationActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    Column(
        modifier = GlanceModifier
            .fillMaxSize()
            .clickable(actionStartActivity(intent))
            .padding(12.dp)
    ) {
        Text(
            text = provenanceLabel(publication, title),
            modifier = GlanceModifier.fillMaxWidth(),
            style = Typo.textStyle("title", colorOverride = ink),
            maxLines = 2,
        )
        Text(
            text = summary,
            modifier = GlanceModifier.fillMaxWidth().padding(top = 2.dp),
            style = Typo.textStyle("caption", colorOverride = secondary),
            maxLines = 2,
        )
        PublicationBody(body, snapshot.bitmap, summary, ink)
        publication.question?.takeIf { it.status == "open" }?.let { question ->
            Text(
                text = "Tap to answer: ${question.prompt}",
                modifier = GlanceModifier.fillMaxWidth().padding(top = 4.dp),
                style = Typo.textStyle("caption", colorOverride = "#7C3AED"),
                maxLines = 1,
            )
        }
        if (!snapshot.compact) {
            publication.ticker?.takeUnless { it.decayed }?.let { ticker ->
                val rotating = ticker.rotation.firstOrNull { !it.pinned }
                Text(
                    text = "${ticker.title} · ${rotating?.summary ?: ticker.summary}",
                    modifier = GlanceModifier.fillMaxWidth().padding(top = 4.dp),
                    style = Typo.textStyle("caption", colorOverride = secondary),
                    maxLines = 1,
                )
            }
        }
        Text(
            text = deliveryLabel(publication, snapshot.connectionState),
            modifier = GlanceModifier.fillMaxWidth().padding(top = 6.dp),
            style = Typo.textStyle("caption", colorOverride = secondary),
            maxLines = 1,
        )
    }
}

@Composable
private fun ColumnScope.PublicationBody(
    content: PublicationContent,
    bitmap: android.graphics.Bitmap?,
    summary: String,
    ink: String = "#000000",
) {
    when (content) {
        is PublicationContent.Text -> Text(
            text = content.text,
            modifier = GlanceModifier.fillMaxWidth().defaultWeight().padding(top = 8.dp),
            style = Typo.textStyle("body", colorOverride = ink),
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

private fun provenanceLabel(publication: Publication, title: String = publication.title): String = when (publication.provenance) {
    "verified" -> "✓ $title"
    "from_price" -> "~price $title"
    "estimate" -> "est. $title"
    else -> title
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
