package com.you.hermeswidget.widget

import android.content.Context
import androidx.compose.ui.graphics.Color
import androidx.glance.GlanceModifier
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.background
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.provideContent
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.SecureStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import androidx.glance.layout.Alignment
import androidx.glance.layout.Column
import androidx.glance.layout.Spacer
import androidx.glance.layout.Row
import androidx.glance.layout.Box
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.width
import androidx.glance.layout.wrapContentHeight
import androidx.glance.layout.wrapContentWidth
import androidx.glance.text.Text
import androidx.glance.unit.Dimension
import androidx.glance.GlanceTheme
import androidx.glance.GlanceId
import androidx.glance.action.clickable

/**
 * Translucent scrim behind all widget content. RemoteViews cannot blur, so this is the
 * achievable equivalent: a frosted-glass tint that lifts black text off a busy wallpaper.
 * Glance's default text color is hardcoded black (TextDefaults), so this stays light in
 * light and dark mode alike. Tune alpha for more/less wallpaper show-through.
 */
private val WIDGET_SCRIM = Color(0xA6FFFFFF) // 65% white

/**
 * Phase 2 — Widget MVP (Glance renderer skeleton).
 * Renders nodes from the cached fixture JSON. Forward-compatible:
 * unknown types are skipped; missing fields fall back to safe defaults.
 */
class HermesWidget : GlanceAppWidget() {

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        // runCatching on every source: a malformed cache or network issue must never
        // escape before provideContent, or the widget renders nothing (infinite spinner).
        val layout = runCatching { loadFromCache(context) }.getOrNull()
            ?: runCatching { loadLive(context) }.getOrNull()
            ?: runCatching { loadFixture(context) }.getOrNull()
        provideContent {
            Box(
                modifier = GlanceModifier
                    .fillMaxSize()
                    .background(WIDGET_SCRIM)
            ) {
                if (layout != null) {
                    WidgetSurface(layout)
                } else {
                    ErrorStateNode(Node(type = "text", value = "Connect to your Hermes agent", label = "Open Hermes settings"))
                }
            }
        }
    }

    private fun parseLayout(jsonStr: String): WidgetLayout = LayoutParser.parse(jsonStr)

    private suspend fun loadFixture(context: Context): WidgetLayout? {
        return try {
            val jsonStr = context.assets.open("fixture_layout.json").bufferedReader().use { it.readText() }
            parseLayout(jsonStr)
        } catch (e: Exception) {
            null
        }
    }

    private suspend fun loadFromCache(context: Context): WidgetLayout? {
        val cached = Config.getCachedLayout(context)
        return cached?.let { parseLayout(it) }
    }

    private suspend fun loadLive(context: Context): WidgetLayout? = withContext(Dispatchers.IO) {
        val configUrl = Config.getBackendUrl(context)
        val secureUrl = SecureStore.baseUrl(context)
        val url = secureUrl ?: configUrl
        val widgetId = Config.getWidgetId(context)
        val secureToken = SecureStore.token(context)
        val configToken = Config.getToken(context)
        if (url.isNullOrEmpty()) return@withContext null

        // Try secure token first (paired device), then config token (re-paired/re-configured)
        val tokensToTry = listOfNotNull(secureToken, configToken)
                .filter { it.isNotEmpty() }
                .distinct()
        for (token in tokensToTry) {
            try {
                android.util.Log.d("HermesWidget", "loadLive: fetching widget $widgetId with token [redacted], url=${url?.take(30)}")
                val (code, body) = HermesApi.fetchWidget(url, widgetId, token)
                if (code != 200) {
                    android.util.Log.d("HermesWidget", "loadLive: HTTP $code for widget $widgetId detail=${body}")
                } else {
                    android.util.Log.d("HermesWidget", "loadLive: HTTP 200, cached layout length=${body?.length ?: 0}")
                }
                if (code == 200 && body != null) {
                    Config.setCachedLayout(context, body)
                    return@withContext parseLayout(body)
                }
                // 401/403 means revoked token; don't try this token again
                if (code == 401 || code == 403) {
                    // If secure token failed but config token remains, loop continues
                    continue
                }
            } catch (e: Exception) {
                android.util.Log.d("HermesWidget", "loadLive: ${e.javaClass.simpleName}: ${e.message}")
            }
        }
        // If no token worked, still return null so fixture can show
        null
    }
}
