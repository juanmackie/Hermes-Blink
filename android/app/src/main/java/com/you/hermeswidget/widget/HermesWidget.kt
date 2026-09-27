package com.you.hermeswidget.widget

import android.content.Context
import android.content.Intent
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.glance.GlanceId
import androidx.glance.ColorFilter
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalContext
import androidx.glance.LocalSize
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.lazy.LazyColumn
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.ContentScale
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import androidx.glance.text.Text
import androidx.glance.unit.ColorProvider
import com.you.hermeswidget.PublicationActivity
import com.you.hermeswidget.R
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

private val renderAckScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

private data class WidgetSnapshot(
    val publication: Publication?,
    val legacyLayout: WidgetLayout?,
    val bitmap: android.graphics.Bitmap?,
    val paired: Boolean,
    val connectionState: ConnectionState,
)

/** The sizes Glance composes for. Covers the E2 canonical extremes of every band. */
private val RESPONSIVE_SIZES = setOf(
    DpSize(110.dp, 56.dp),    // 2x1 floor
    DpSize(110.dp, 115.dp),   // 2x2 floor
    DpSize(306.dp, 276.dp),   // 2x2 max
    DpSize(245.dp, 130.dp),   // 4x1 / wide short
    DpSize(245.dp, 185.dp),   // 4x2 floor
    DpSize(624.dp, 276.dp),   // 4x2 max
    DpSize(624.dp, 422.dp),   // 4x4 max
    DpSize(407.dp, 412.dp),   // 4x4 typical (Pixel 10 Pro XL)
)

class HermesWidget : GlanceAppWidget() {
    override val sizeMode: SizeMode = SizeMode.Responsive(RESPONSIVE_SIZES)

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val snapshot = withContext(Dispatchers.IO) { loadSnapshot(context) }
        // Which widget instance this composition belongs to. Attributing a tap or a render
        // to a device but not an instance is what made round 5 unanswerable.
        val instanceId = WidgetInstanceIds.of(id)
        // The size the launcher actually gave this instance, for the render receipt.
        var composed: Pair<Float, Float>? = null
        provideContent {
            val localSize = LocalSize.current
            val spec = SizeGate.spec(localSize, context)
            if (composed == null) composed = WidgetSize.fromLocalSize(localSize)
            val dark = snapshot.publication?.darkPalette == true || WidgetTheme.isDark(context)
            Column(
                modifier = GlanceModifier
                    .fillMaxSize()
                    .widgetSurface(context, dark),
            ) {
                when {
                    !snapshot.paired -> EmptyState(
                        "Pair this phone",
                        "Run hermes widget code on Hermes, then enter the short-lived pairing code.",
                        spec = spec,
                        dark = dark,
                        accent = WidgetTheme.accent(context, dark, null),
                        ink = WidgetTheme.ink(context, dark),
                        secondary = WidgetTheme.secondary(context, dark),
                    )
                    snapshot.publication != null && !snapshot.publication.isExpired() ->
                        PublicationSurface(snapshot, spec, dark, instanceId)
                    snapshot.publication?.isExpired() == true -> EmptyState(
                        "Publication expired",
                        "Open the app to refresh the connection.",
                        spec = spec,
                        dark = dark,
                        accent = WidgetTheme.accent(context, dark, null),
                        ink = WidgetTheme.ink(context, dark),
                        secondary = WidgetTheme.secondary(context, dark),
                    )
                    snapshot.legacyLayout != null -> WidgetSurface(snapshot.legacyLayout)
                    else -> EmptyState(
                        "No publication yet",
                        "Useful Hermes updates will appear here automatically.",
                        spec = spec,
                        dark = dark,
                        accent = WidgetTheme.accent(context, dark, null),
                        ink = WidgetTheme.ink(context, dark),
                        secondary = WidgetTheme.secondary(context, dark),
                    )
                }
            }
        }

        if (snapshot.paired && snapshot.publication?.isExpired() == false) {
            val (width, height) = composed?.let { (widthDp, heightDp) ->
                WidgetSize.toPixels(context, widthDp, heightDp)
            } ?: WidgetDimensions.fromContext(context)
            renderAckScope.launch {
                if (PublicationRepository.acknowledgeRenderSubmitted(
                        context, width, height, instanceId = instanceId,
                    )
                ) {
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
private fun PublicationSurface(
    snapshot: WidgetSnapshot,
    spec: BandSpec,
    dark: Boolean,
    instanceId: String?,
) {
    val publication = snapshot.publication ?: return
    val context = LocalContext.current
    val ink = WidgetTheme.ink(context, dark)
    val secondary = WidgetTheme.secondary(context, dark)
    val accent = WidgetTheme.accent(context, dark, null)
    val variantKey = spec.variantKey { key -> publication.variants.containsKey(key) }
    val variant = publication.variants[variantKey]
    val title = variant?.title ?: publication.title
    val summary = variant?.summary ?: publication.summary
    val body = variant?.let { PublicationContent.Text(it.text) } ?: publication.content
    val intent = Intent(context, PublicationActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        // Carried so the in-app "Request update" button can name the instance it belongs
        // to, the same way the widget's own button now does.
        .putExtra(PublicationActivity.EXTRA_INSTANCE_ID, instanceId ?: "")
    // The outer target is "open the app"; the pinned footer keeps its own action and
    // Glance resolves the inner target first.
    Column(
        modifier = GlanceModifier
            .fillMaxSize()
            .padding(12.dp)
            .clickable(actionStartActivity(intent)),
    ) {
        HeaderRow(publication, snapshot.connectionState, spec, dark)
        LazyColumn(modifier = GlanceModifier.fillMaxWidth().defaultWeight()) {
            item {
                HeroBlock(
                    title = provenanceLabel(publication, title),
                    summary = summary,
                    spec = spec,
                    ink = ink,
                    secondary = secondary,
                )
            }
            if (spec.showsBody) {
                item {
                    PublicationBody(body, snapshot.bitmap, summary, ink, secondary, spec)
                }
            }
            if (spec.showsQuestion) {
                publication.question?.takeIf { it.status == "open" }?.let { question ->
                    item {
                        Text(
                            text = "Tap to answer: ${question.prompt}",
                            modifier = GlanceModifier.fillMaxWidth().padding(top = 4.dp),
                            style = Typo.textStyle("caption", accent),
                            maxLines = 1,
                        )
                    }
                }
            }
            if (spec.showsTicker) {
                publication.ticker?.takeUnless { it.decayed }?.let { ticker ->
                    item {
                        val rotating = ticker.rotation.firstOrNull { !it.pinned }
                        Text(
                            text = "${ticker.title} · ${rotating?.summary ?: ticker.summary}",
                            modifier = GlanceModifier.fillMaxWidth().padding(top = 4.dp),
                            style = Typo.textStyle("caption", secondary),
                            maxLines = 1,
                        )
                    }
                }
            }
        }
        if (spec.showsFooter) {
            FooterRow(publication, snapshot.connectionState, spec, dark)
        }
    }
}

/** Provenance badge + hero title + the one summary line. Every node is capped by band. */
@Composable
private fun HeroBlock(
    title: String,
    summary: String,
    spec: BandSpec,
    ink: ColorProvider,
    secondary: ColorProvider,
) {
    Column(modifier = GlanceModifier.fillMaxWidth()) {
        Text(
            text = title,
            modifier = GlanceModifier.fillMaxWidth(),
            style = Typo.textStyle("title", ink),
            maxLines = spec.heroMaxLines,
        )
        if (spec.summaryMaxLines > 0 && summary.isNotBlank()) {
            Text(
                text = summary,
                modifier = GlanceModifier.fillMaxWidth().padding(top = 2.dp),
                style = Typo.textStyle("caption", secondary),
                maxLines = spec.summaryMaxLines,
            )
        }
    }
}

/**
 * WL-3: the mark is always there, the wordmark when the cell has the width for it, and the
 * status dot carries freshness + connection state. The status *text* lives in the pinned
 * footer, never in the scroll region, and the dot's content description says the same
 * thing for TalkBack.
 */
@Composable
private fun HeaderRow(
    publication: Publication,
    connectionState: ConnectionState,
    spec: BandSpec,
    dark: Boolean,
) {
    val context = LocalContext.current
    val accent = WidgetTheme.accent(context, dark, null)
    Row(
        modifier = GlanceModifier
            .fillMaxWidth()
            .padding(bottom = 4.dp)
            .semantics { contentDescription = statusDescription(publication, connectionState) },
        verticalAlignment = Alignment.Vertical.CenterVertically,
    ) {
        Image(
            provider = ImageProvider(R.drawable.ic_hermes_mark),
            contentDescription = null,
            modifier = GlanceModifier.size(16.dp),
            // The mark follows the accent token, so it stays legible in every theme.
            colorFilter = ColorFilter.tint(accent),
        )
        if (spec.showsHeaderLabel) {
            Spacer(GlanceModifier.width(6.dp))
            Text(
                text = context.getString(R.string.widget_header_title),
                style = Typo.textStyle("label", WidgetTheme.ink(context, dark)),
                maxLines = 1,
            )
        }
        Spacer(GlanceModifier.width(6.dp))
        Box(
            modifier = GlanceModifier
                .size(6.dp)
                .background(WidgetTheme.status(context, statusLevel(publication, connectionState))),
        ) {}
    }
}

/**
 * WT-4: the status line and the request action are pinned outside the scroll region, so a
 * long publication can never push the only control below the fold. The action is a 48dp
 * touch target at band M and up, where the height budget allows it.
 */
@Composable
private fun FooterRow(
    publication: Publication,
    state: ConnectionState,
    spec: BandSpec,
    dark: Boolean,
) {
    val context = LocalContext.current
    Row(
        modifier = GlanceModifier.fillMaxWidth().padding(top = 4.dp),
        verticalAlignment = Alignment.Vertical.CenterVertically,
    ) {
        Text(
            text = deliveryLabel(publication, state),
            modifier = GlanceModifier.defaultWeight(),
            style = Typo.textStyle("caption", WidgetTheme.secondary(context, dark)),
            maxLines = 1,
        )
        if (spec.showsRequestAction) {
            Spacer(GlanceModifier.width(8.dp))
            Text(
                text = context.getString(R.string.widget_request_update),
                modifier = GlanceModifier
                    .height(48.dp)
                    .background(WidgetTheme.accent(context, dark, null))
                    .cornerRadius(8.dp)
                    .clickable(requestUpdateAction())
                    .padding(horizontal = 12.dp, vertical = 12.dp),
                style = Typo.textStyle("label", WidgetTheme.onAccent(context, dark, null)),
                maxLines = 1,
            )
        }
    }
}

/** D15: the body text stays uncapped and scrollable; only the image box is band-sized. */
@Composable
private fun PublicationBody(
    content: PublicationContent,
    bitmap: android.graphics.Bitmap?,
    summary: String,
    ink: ColorProvider,
    secondary: ColorProvider,
    spec: BandSpec,
) {
    when (content) {
        is PublicationContent.Text -> Text(
            text = content.text,
            modifier = GlanceModifier.fillMaxWidth().padding(top = 8.dp),
            style = Typo.textStyle("body", ink),
        )
        is PublicationContent.Image -> {
            if (bitmap == null) {
                Text(
                    text = "Visual unavailable offline",
                    modifier = GlanceModifier.fillMaxWidth().padding(top = 8.dp),
                    style = Typo.textStyle("caption", secondary),
                    maxLines = 1,
                )
            } else {
                Image(
                    provider = ImageProvider(bitmap),
                    contentDescription = summary.ifBlank {
                        LocalContext.current.getString(R.string.widget_image_description)
                    },
                    modifier = GlanceModifier.fillMaxWidth().height(spec.imageHeightDp.dp).padding(top = 8.dp),
                    contentScale = ContentScale.Fit,
                )
            }
        }
    }
}

@Composable
private fun EmptyState(
    title: String,
    message: String,
    spec: BandSpec,
    dark: Boolean,
    accent: ColorProvider,
    ink: ColorProvider,
    secondary: ColorProvider,
) {
    Column(
        modifier = GlanceModifier.fillMaxSize().padding(12.dp),
        verticalAlignment = Alignment.Vertical.CenterVertically,
        horizontalAlignment = Alignment.Horizontal.Start,
    ) {
        Text(text = title, style = Typo.textStyle("title", ink), maxLines = 2)
        Text(
            text = message,
            modifier = GlanceModifier.fillMaxWidth().padding(top = 4.dp),
            style = Typo.textStyle("body", secondary),
            maxLines = spec.bodyMaxLines.coerceIn(1, 3),
        )
        if (spec.showsRequestAction) {
            Spacer(GlanceModifier.height(8.dp))
            Text(
                text = LocalContext.current.getString(R.string.widget_request_update),
                modifier = GlanceModifier
                    .height(48.dp)
                    .background(accent)
                    .cornerRadius(8.dp)
                    .clickable(requestUpdateAction())
                    .padding(horizontal = 12.dp, vertical = 12.dp),
                style = Typo.textStyle("label", WidgetTheme.onAccent(LocalContext.current, dark, null)),
                maxLines = 1,
            )
        }
    }
}

private fun requestUpdateAction() = actionRunCallback<ActionCallbacks.EventAction>(
    actionParametersOf(
        WidgetParams.eventKey to "request_update",
        WidgetParams.kindKey to "request_update",
        WidgetParams.payloadKey to "{}",
    )
)

private fun statusLevel(publication: Publication, state: ConnectionState): WidgetTheme.StatusLevel {
    if (state == ConnectionState.OFFLINE ||
        state == ConnectionState.REVOKED ||
        state == ConnectionState.UNPAIRED
    ) {
        return WidgetTheme.StatusLevel.OFFLINE
    }
    return when (publication.freshness()) {
        PublicationFreshness.FRESH -> WidgetTheme.StatusLevel.FRESH
        PublicationFreshness.AGED -> WidgetTheme.StatusLevel.AGED
        PublicationFreshness.STALE, PublicationFreshness.EXPIRED -> WidgetTheme.StatusLevel.STALE
    }
}

/** What TalkBack reads for the header: exactly what the pinned footer spells out. */
private fun statusDescription(publication: Publication, state: ConnectionState): String {
    val age = ageLabel(publication.publishedAtMillis(), System.currentTimeMillis())
    val whenText = when (state) {
        ConnectionState.UNPAIRED -> "unpaired"
        ConnectionState.OFFLINE -> "offline, cached $age ago"
        ConnectionState.REVOKED -> "pairing expired, cached $age ago"
        ConnectionState.ERROR -> "connection error, cached $age old"
        ConnectionState.ONLINE -> when (publication.freshness()) {
            PublicationFreshness.FRESH -> "fresh, updated $age ago"
            PublicationFreshness.AGED -> "aged, updated $age ago"
            PublicationFreshness.STALE -> "stale, updated $age ago"
            PublicationFreshness.EXPIRED -> "expired"
        }
    }
    return "Hermes, $whenText"
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
