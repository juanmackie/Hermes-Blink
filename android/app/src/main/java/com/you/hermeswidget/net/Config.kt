package com.you.hermeswidget.net

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

object Config {
    /** How many compositions to keep: enough to compare two presses, small enough to forget. */
    private const val COMPOSITION_HISTORY_LIMIT = 8

    private const val PREFS_NAME = "hermes_config"
    private const val KEY_BACKEND_URL = "backend_url"
    private const val KEY_WIDGET_ID = "widget_id"
    private const val KEY_TOKEN = "token"
    private const val KEY_LAYOUT_JSON = "layout_json"
    private const val KEY_PUBLICATION_JSON = "publication_json"
    private const val KEY_PUBLICATION_ETAG = "publication_etag"
    private const val KEY_ASSET_ETAG = "asset_etag"
    private const val KEY_ASSET_ID = "asset_id"
    private const val KEY_CONNECTION_STATE = "connection_state"
    private const val KEY_LAST_CHECKED_AT = "last_checked_at"
    private const val KEY_LAST_POLL_AT = "last_poll_at"
    private const val KEY_LAST_FETCH_AT = "last_fetch_at"
    // Distinct from KEY_LAST_CHECKED_AT: that is when the connection state was last
    // evaluated, this is when the server was last asked about the publication.
    private const val KEY_LAST_PUBLICATION_CHECK = "last_publication_check"
    private const val KEY_LAST_RENDER_AT = "last_render_at"
    private const val KEY_BATTERY_EXEMPTION = "battery_exemption"
    private const val KEY_PENDING_ACTIONS = "pending_actions"
    private const val KEY_PUSH_STATE = "push_state"
    private const val KEY_ATTENTION_RENDERED_REVISION = "attention_rendered_revision"
    private const val KEY_LAST_PUSH_WAKE = "last_push_wake"
    private const val KEY_ACTION_OUTCOMES = "action_outcomes"
    private const val KEY_LAST_COMPOSITION = "last_composition"
    private const val KEY_ACTION_REACHED = "action_reached"
    private const val KEY_COMPOSITIONS = "composition_history"

    private fun prefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    fun setBackendUrl(context: Context, url: String) {
        prefs(context).edit().putString(KEY_BACKEND_URL, url.trim()).apply()
    }

    fun getBackendUrl(context: Context): String? {
        val url = prefs(context).getString(KEY_BACKEND_URL, null)
        return url?.takeIf { it.isNotEmpty() }
    }

    fun setToken(context: Context, token: String?) {
        // Delegate to encrypted storage; never keep a plaintext copy in
        // ordinary SharedPreferences. See review finding 4 / Config.kt.
        val currentUrl = SecureStore.baseUrl(context) ?: getBackendUrl(context) ?: ""
        val currentDeviceId = SecureStore.deviceId(context) ?: ""
        if (token == null || token.isEmpty()) {
            SecureStore.clear(context)
            // Preserve URL in ordinary prefs for quick access
            setBackendUrl(context, currentUrl)
        } else {
            SecureStore.save(context, currentUrl, token, currentDeviceId)
        }
        // Always remove any leftover plaintext token copy from ordinary prefs.
        prefs(context).edit().remove(KEY_TOKEN).apply()
    }

    fun getToken(context: Context): String? {
        SecureStore.token(context)?.let { return it }
        // One-time migration: plaintext -> encrypted, then remove leftover
        val plaintext = getTokenFromPrefs(context)
        if (plaintext != null) {
            val url = getBackendUrl(context) ?: SecureStore.baseUrl(context) ?: ""
            val deviceId = SecureStore.deviceId(context) ?: ""
            SecureStore.save(context, url, plaintext, deviceId)
            prefs(context).edit().remove(KEY_TOKEN).apply()
            return plaintext
        }
        return null
    }

    private fun getTokenFromPrefs(context: Context): String? {
        val token = prefs(context).getString(KEY_TOKEN, "")
        return token?.takeIf { it.isNotEmpty() }
    }

    fun setWidgetId(context: Context, id: String) {
        prefs(context).edit().putString(KEY_WIDGET_ID, id).apply()
    }

    fun getWidgetId(context: Context): String {
        return prefs(context).getString(KEY_WIDGET_ID, "hermes-brief") ?: "hermes-brief"
    }

    fun setCachedLayout(context: Context, json: String) {
        prefs(context).edit().putString(KEY_LAYOUT_JSON, json).apply()
    }

    fun getCachedLayout(context: Context): String? {
        val json = prefs(context).getString(KEY_LAYOUT_JSON, null)
        return json?.takeIf { it.isNotEmpty() }
    }

    fun setCachedPublication(
        context: Context,
        json: String,
        publicationEtag: String?,
        assetId: String?,
        assetEtag: String?,
    ): Boolean {
        val editor = prefs(context).edit()
            .putString(KEY_PUBLICATION_JSON, json)
            .putString(KEY_PUBLICATION_ETAG, publicationEtag)
            .putString(KEY_ASSET_ID, assetId)
            .putString(KEY_ASSET_ETAG, assetEtag)
        return editor.commit()
    }

    fun getCachedPublication(context: Context): String? {
        return prefs(context).getString(KEY_PUBLICATION_JSON, null)
            ?.takeIf { it.isNotEmpty() }
    }

    fun getPublicationEtag(context: Context): String? {
        return prefs(context).getString(KEY_PUBLICATION_ETAG, null)
            ?.takeIf { it.isNotEmpty() }
    }

    fun getAssetEtag(context: Context, assetId: String): String? {
        if (prefs(context).getString(KEY_ASSET_ID, null) != assetId) return null
        return prefs(context).getString(KEY_ASSET_ETAG, null)?.takeIf { it.isNotEmpty() }
    }

    fun clearCachedPublication(context: Context): Boolean {
        return prefs(context).edit()
            .remove(KEY_PUBLICATION_JSON)
            .remove(KEY_PUBLICATION_ETAG)
            .remove(KEY_ASSET_ID)
            .remove(KEY_ASSET_ETAG)
            .commit()
    }

    fun assetFile(context: Context, assetId: String): File {
        require(ASSET_ID_PATTERN.matches(assetId)) { "invalid asset id" }
        return File(File(context.filesDir, "publication-assets"), assetId)
    }

    fun setConnectionState(context: Context, state: ConnectionState, checkedAt: Long) {
        prefs(context).edit()
            .putString(KEY_CONNECTION_STATE, state.name)
            .putLong(KEY_LAST_CHECKED_AT, checkedAt)
            .apply()
    }

    fun getConnectionState(context: Context): ConnectionState {
        val stored = prefs(context).getString(KEY_CONNECTION_STATE, null)
        return stored?.let { runCatching { ConnectionState.valueOf(it) }.getOrNull() }
            ?: if (SecureStore.token(context).isNullOrEmpty()) ConnectionState.UNPAIRED else ConnectionState.OFFLINE
    }

    fun getLastCheckedAt(context: Context): Long? {
        return prefs(context).getLong(KEY_LAST_CHECKED_AT, 0L).takeIf { it > 0L }
    }

    fun setDiagnosticTime(context: Context, key: String, at: Long = System.currentTimeMillis()) {
        // "fetch" is reserved for new content arriving. Asking the server and being told
        // "unchanged" is `checked`: conflating the two is what made a screen report a
        // healthy "Last fetch 26s ago" while the widget said the publication had expired.
        require(key in setOf("poll", "fetch", "render", "checked")) { "unknown diagnostic time" }
        prefs(context).edit().putLong(
            when (key) {
                "poll" -> KEY_LAST_POLL_AT
                "fetch" -> KEY_LAST_FETCH_AT
                "checked" -> KEY_LAST_PUBLICATION_CHECK
                else -> KEY_LAST_RENDER_AT
            }, at
        ).apply()
    }

    fun getDiagnosticTimes(context: Context): Map<String, Long?> = mapOf(
        "lastPollAt" to prefs(context).getLong(KEY_LAST_POLL_AT, 0L).takeIf { it > 0L },
        "lastFetchAt" to prefs(context).getLong(KEY_LAST_FETCH_AT, 0L).takeIf { it > 0L },
        "lastRenderAt" to prefs(context).getLong(KEY_LAST_RENDER_AT, 0L).takeIf { it > 0L },
    )

    fun setBatteryExemptionHint(context: Context, granted: Boolean) {
        prefs(context).edit().putBoolean(KEY_BATTERY_EXEMPTION, granted).apply()
    }

    fun getBatteryExemptionHint(context: Context): Boolean =
        prefs(context).getBoolean(KEY_BATTERY_EXEMPTION, false)

    /** A small bounded outbox for taps made while the private path is unavailable. */
    fun enqueuePendingAction(context: Context, action: JSONObject) {
        val id = action.optString("clientEventId")
        if (id.isBlank()) return
        val current = runCatching {
            JSONArray(prefs(context).getString(KEY_PENDING_ACTIONS, "[]") ?: "[]")
        }.getOrElse { JSONArray() }
        val next = JSONArray()
        for (index in 0 until current.length()) {
            val item = current.optJSONObject(index) ?: continue
            if (item.optString("clientEventId") != id) next.put(item)
        }
        next.put(action)
        while (next.length() > 50) next.remove(0)
        prefs(context).edit().putString(KEY_PENDING_ACTIONS, next.toString()).apply()
    }

    fun pendingActions(context: Context): List<JSONObject> {
        val array = runCatching {
            JSONArray(prefs(context).getString(KEY_PENDING_ACTIONS, "[]") ?: "[]")
        }.getOrElse { JSONArray() }
        return (0 until array.length()).mapNotNull { array.optJSONObject(it) }
    }

    fun removePendingAction(context: Context, clientEventId: String) {
        val current = runCatching {
            JSONArray(prefs(context).getString(KEY_PENDING_ACTIONS, "[]") ?: "[]")
        }.getOrElse { JSONArray() }
        val next = JSONArray()
        for (index in 0 until current.length()) {
            val item = current.optJSONObject(index) ?: continue
            if (item.optString("clientEventId") != clientEventId) next.put(item)
        }
        prefs(context).edit().putString(KEY_PENDING_ACTIONS, next.toString()).apply()
    }

    fun setPushState(context: Context, state: String, distributorPresent: Boolean?, failureReason: String? = null) {
        val value = JSONObject()
            .put("state", state)
            .put("distributorPresent", distributorPresent ?: JSONObject.NULL)
            .put("failureReason", failureReason ?: JSONObject.NULL)
            .put("updatedAt", System.currentTimeMillis())
        prefs(context).edit().putString(KEY_PUSH_STATE, value.toString()).apply()
    }

    fun getPushState(context: Context): JSONObject? = runCatching {
        JSONObject(prefs(context).getString(KEY_PUSH_STATE, "{}") ?: "{}")
    }.getOrNull()

    fun setLastPushWake(context: Context, at: Long = System.currentTimeMillis()) {
        prefs(context).edit().putLong(KEY_LAST_PUSH_WAKE, at).apply()
    }

    fun getLastPushWake(context: Context): Long? = prefs(context).getLong(KEY_LAST_PUSH_WAKE, 0L).takeIf { it > 0L }

    /**
     * A bounded local record of the last few widget-button outcomes.
     *
     * Round 5: a press that failed wrote nothing anywhere, so "the tap did nothing" had
     * no explanation on either side. This is the client half of that trail — the instance
     * that was tapped, the HTTP status, the server's own error code and a sentence — kept
     * locally and shown in Diagnostics. It never stores a token, a payload or content.
     */
    fun recordActionOutcome(
        context: Context,
        event: String,
        instanceId: String?,
        httpStatus: Int,
        code: String,
        message: String?,
        at: Long = System.currentTimeMillis(),
    ) {
        val entry = JSONObject()
            .put("event", event.take(64))
            .put("instanceId", instanceId ?: JSONObject.NULL)
            .put("status", httpStatus)
            .put("code", code.take(64))
            .put("message", (message ?: "").take(240))
            .put("at", at)
        val current = runCatching {
            JSONArray(prefs(context).getString(KEY_ACTION_OUTCOMES, "[]") ?: "[]")
        }.getOrElse { JSONArray() }
        val next = JSONArray()
        next.put(entry)
        // Newest first, bounded: ten is enough to see a pattern and small enough to forget.
        for (index in 0 until current.length()) {
            if (next.length() >= 10) break
            current.optJSONObject(index)?.let { next.put(it) }
        }
        prefs(context).edit().putString(KEY_ACTION_OUTCOMES, next.toString()).apply()
    }

    fun actionOutcomes(context: Context): List<JSONObject> = runCatching {
        val array = JSONArray(prefs(context).getString(KEY_ACTION_OUTCOMES, "[]") ?: "[]")
        (0 until array.length()).mapNotNull { array.optJSONObject(it) }
    }.getOrDefault(emptyList())

    /**
     * What the last widget composition actually drew.
     *
     * Field round 6: "Request update does not work" was unanswerable because we could not
     * tell a missing button from a tap that went nowhere. This records the band, whether
     * the action was part of it, and the scroll region it was given.
     */
    fun setLastComposition(
        context: Context,
        band: String,
        actionAvailable: Boolean,
        scrollHeightDp: Int,
        composedHeightDp: Float,
        cellHeightDp: Float?,
        source: String,
        at: Long = System.currentTimeMillis(),
    ) {
        prefs(context).edit().putString(
            KEY_LAST_COMPOSITION,
            JSONObject()
                .put("band", band)
                .put("actionAvailable", actionAvailable)
                .put("scrollHeightDp", scrollHeightDp)
                // The two numbers that must agree: what we composed for, and what the cell
                // is. Round 8's 316dp of scroll region in a 270dp cell is exactly the gap
                // between these, and it is now visible from the device.
                .put("composedHeightDp", composedHeightDp.toDouble())
                .put("cellHeightDp", (cellHeightDp ?: -1.0).toDouble())
                .put("geometrySource", source)
                .put("at", at)
                .toString(),
        ).apply()
    }

    fun getLastComposition(context: Context): JSONObject? = runCatching {
        JSONObject(prefs(context).getString(KEY_LAST_COMPOSITION, "{}") ?: "{}")
    }.getOrNull()?.takeIf { it.length() > 2 }

    /**
     * Every composition, newest first, bounded.
     *
     * Field round 11: one tap worked and the next did not, with nothing to compare. A
     * single "last composition" cannot answer "was the widget laid out the same way at
     * 15:23 as at 14:07?", which is the only question that separates a content change from
     * a geometry change from a lost tap. This keeps the trail of both, so the next report
     * can be pasted instead of photographed.
     */
    fun compositionHistory(context: Context): List<JSONObject> = runCatching {
        val array = JSONArray(prefs(context).getString(KEY_COMPOSITIONS, "[]") ?: "[]")
        (0 until array.length()).mapNotNull { array.optJSONObject(it) }
    }.getOrDefault(emptyList())

    fun recordComposition(
        context: Context,
        band: String,
        actionAvailable: Boolean,
        scrollHeightDp: Int,
        composedHeightDp: Float,
        cellHeightDp: Float?,
        source: String,
        instanceId: String?,
        at: Long = System.currentTimeMillis(),
    ) {
        val entry = JSONObject()
            .put("band", band)
            .put("action", actionAvailable)
            .put("scrollDp", scrollHeightDp)
            .put("composedDp", composedHeightDp.toDouble())
            .put("cellDp", (cellHeightDp ?: -1.0).toDouble())
            .put("source", source)
            .put("instance", instanceId ?: JSONObject.NULL)
            .put("at", at)
        val current = compositionHistory(context)
        val next = JSONArray()
        next.put(entry)
        for (index in 0 until current.size) {
            if (next.length() >= COMPOSITION_HISTORY_LIMIT) break
            current[index].let { next.put(it) }
        }
        prefs(context).edit().putString(KEY_COMPOSITIONS, next.toString()).apply()
    }

    /**
     * A widget action that reached `ActionCallbacks.EventAction.onAction`.
     *
     * This used to be counted in `HermesWidgetReceiver.onReceive`, which is where the
     * round-11 note said a press would be observed. It never was: Glance routes an
     * `actionRunCallback` broadcast to its own merged
     * `androidx.glance.appwidget.action.ActionCallbackBroadcastReceiver`, so that counter
     * was structurally incapable of incrementing. It read 0 on a real press and that 0 was
     * then read as evidence. A measurement that cannot fail must never be treated as one.
     *
     * So it is counted at the first statement of the callback, which is the only place
     * that observes the dispatch from inside this app, and it is named for what it
     * measures: "reached", not "fired". A press that leaves this unchanged never got past
     * the touch, whatever the reason.
     */
    fun recordActionReached(
        context: Context,
        event: String?,
        instanceId: String?,
        at: Long = System.currentTimeMillis(),
    ) {
        val current = runCatching {
            JSONObject(prefs(context).getString(KEY_ACTION_REACHED, "{}") ?: "{}")
        }.getOrElse { JSONObject() }
        current.put("count", current.optInt("count", 0) + 1)
        current.put("lastEvent", event ?: "-")
        current.put("lastInstance", instanceId ?: "-")
        current.put("exceptions", current.optInt("exceptions", 0))
        current.put("at", at)
        prefs(context).edit().putString(KEY_ACTION_REACHED, current.toString()).apply()
    }

    /** A callback that threw: counted separately, because it is the one failure the
     *  outcome trail would otherwise hide behind a partially-written record. */
    fun recordCallbackException(context: Context, error: String, at: Long = System.currentTimeMillis()) {
        val current = getActionReached(context) ?: JSONObject()
        current.put("count", current.optInt("count", 0))
        current.put("exceptions", current.optInt("exceptions", 0) + 1)
        current.put("lastException", error.take(120))
        current.put("at", at)
        prefs(context).edit().putString(KEY_ACTION_REACHED, current.toString()).apply()
    }

    fun getActionReached(context: Context): JSONObject? = runCatching {
        JSONObject(prefs(context).getString(KEY_ACTION_REACHED, "{}") ?: "{}")
    }.getOrNull()?.takeIf { it.length() > 2 }

    fun markAttentionRendered(context: Context, revision: Int): Boolean {
        val key = prefs(context)
        val previous = key.getInt(KEY_ATTENTION_RENDERED_REVISION, 0)
        if (previous == revision) return false
        key.edit().putInt(KEY_ATTENTION_RENDERED_REVISION, revision).apply()
        return true
    }
}
